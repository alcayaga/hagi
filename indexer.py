import os
import pysubs2
import subprocess
import tempfile
from db import get_db, add_media, add_sentences

def process_subs(conn, file_path, subs, media_type="subtitle"):
    media_id = add_media(conn, file_path, media_type)
    sentences = []
    
    for line in subs:
        text = line.plaintext.strip()
        if text:
            sentences.append((
                "unknown",
                line.start / 1000.0, 
                line.end / 1000.0, 
                text
            ))
            
    if sentences:
        add_sentences(conn, media_id, sentences)
        conn.commit()
        print(f"Indexed: {file_path} ({len(sentences)} lines)")

def index_directory(directory_path: str):
    conn = get_db()
    for root, _, files in os.walk(directory_path):
        for file in files:
            if file.startswith('._'):
                continue
                
            if file.endswith(('.ass', '.srt')):
                file_path = os.path.join(root, file)
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
                        
                    process_subs(conn, file_path, subs, "subtitle")
                except Exception as e:
                    print(f"Error indexing {file_path}: {e}")
                    
                    
            # Temporarily disabled MKV extraction for faster iteration
            # elif file.endswith('.mkv'):
            #     # Check if there is an external subtitle already handled
            #     base = os.path.splitext(file)[0]
            #     if any(os.path.exists(os.path.join(root, base + ext)) for ext in ['.ass', '.srt']):
            #         continue
            #     
            #     mkv_path = os.path.join(root, file)
            #     try:
            #         with tempfile.NamedTemporaryFile(suffix='.ass', delete=False) as temp_sub:
            #             temp_sub_path = temp_sub.name
            #             
            #         # Extract the first subtitle track
            #         result = subprocess.run([
            #             "ffmpeg", "-y", "-i", mkv_path, "-map", "0:s:0", temp_sub_path
            #         ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            #         
            #         if result.returncode == 0:
            #             subs = pysubs2.load(temp_sub_path)
            #             process_subs(conn, mkv_path, subs, "mkv_embedded")
            #         
            #         if os.path.exists(temp_sub_path):
            #             os.remove(temp_sub_path)
            #     except Exception as e:
            #         print(f"Error extracting from {mkv_path}: {e}")

