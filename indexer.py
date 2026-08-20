import os
import pysubs2
import subprocess
import tempfile
import json
from db import get_db, add_media, add_sentences

def process_subs(conn, file_path, subs, media_type="subtitle", language="unknown"):
    media_id = add_media(conn, file_path, media_type)
    sentences = []
    
    for line in subs:
        text = line.plaintext.strip()
        if text:
            sentences.append((
                language,
                line.start / 1000.0, 
                line.end / 1000.0, 
                text
            ))
            
    if sentences:
        add_sentences(conn, media_id, sentences)
        conn.commit()
        print(f"Indexed: {file_path} [{language}] ({len(sentences)} lines)")

def index_directory(directory_path: str):
    conn = get_db()
    for root, _, files in os.walk(directory_path):
        for file in files:
            if file.startswith('._'):
                continue
                
            file_path = os.path.join(root, file)
            
            # Incremental indexing: skip if already in DB
            row = conn.execute("SELECT id FROM media WHERE path = ?", (file_path,)).fetchone()
            if row:
                print(f"Skipping (already indexed): {file_path}")
                continue
                
            if file.endswith(('.ass', '.srt')):
                try:
                    subs = None
                    for enc in ['utf-8', 'utf-16', 'utf-8-sig', 'latin-1', 'shift_jis']:
                        try:
                            subs = pysubs2.load(file_path, encoding=enc)
                            break
                        except UnicodeDecodeError:
                            continue
                            
                    if subs is None:
                        raise Exception("Failed to decode file with standard encodings.")
                        
                    # For external files, we could infer language from the filename (e.g. file.en.srt), but default to unknown
                    lang_hint = "unknown"
                    if ".en." in file.lower() or ".eng." in file.lower(): lang_hint = "eng"
                    elif ".ja." in file.lower() or ".jpn." in file.lower(): lang_hint = "jpn"
                    elif ".es." in file.lower() or ".spa." in file.lower(): lang_hint = "spa"
                    
                    process_subs(conn, file_path, subs, "subtitle", language=lang_hint)
                except Exception as e:
                    print(f"Error indexing {file_path}: {e}")
                    
            elif file.endswith('.mkv'):
                # Check if there is an external subtitle already handled
                base = os.path.splitext(file)[0]
                if any(os.path.exists(os.path.join(root, base + ext)) for ext in ['.ass', '.srt']):
                    continue
                
                try:
                    # Probe the mkv file for subtitle streams and their languages
                    probe_cmd = [
                        "ffprobe", "-v", "error", "-select_streams", "s", 
                        "-show_entries", "stream=index:stream_tags=language", 
                        "-of", "json", file_path
                    ]
                    probe_res = subprocess.run(probe_cmd, capture_output=True, text=True)
                    data = json.loads(probe_res.stdout)
                    streams = data.get('streams', [])
                    
                    if not streams:
                        continue
                        
                    # We process the mkv, so add it to the media table once to mark it as indexed even if tracks fail
                    add_media(conn, file_path, "mkv_embedded")
                    
                    for i, stream in enumerate(streams):
                        lang = stream.get('tags', {}).get('language', 'unknown')
                        
                        with tempfile.NamedTemporaryFile(suffix='.ass', delete=False) as temp_sub:
                            temp_sub_path = temp_sub.name
                            
                        # Extract this specific subtitle track
                        ext_res = subprocess.run([
                            "ffmpeg", "-y", "-i", file_path, "-map", f"0:s:{i}", temp_sub_path
                        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        
                        if ext_res.returncode == 0:
                            try:
                                subs = pysubs2.load(temp_sub_path)
                                process_subs(conn, file_path, subs, "mkv_embedded", language=lang)
                            except Exception as parse_e:
                                print(f"Error parsing track {i} in {file_path}: {parse_e}")
                        
                        if os.path.exists(temp_sub_path):
                            os.remove(temp_sub_path)
                            
                except Exception as e:
                    print(f"Error extracting from {file_path}: {e}")
