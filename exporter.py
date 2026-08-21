import os
import subprocess
import csv
import db

def extract_media(sentence_id: int, out_dir: str):
    conn = db.get_db()
    target = conn.execute("""
        SELECT s.id, s.text, s.start_time, s.end_time, m.path
        FROM sentences s
        JOIN media m ON s.media_id = m.id
        WHERE s.id = ?
    """, (sentence_id,)).fetchone()
    
    if not target:
        return False, "Sentence not found", None, None
        
    os.makedirs(out_dir, exist_ok=True)
    
    media_path = target['path']
    if media_path.endswith('.mkv'):
        mkv_path = media_path
    else:
        # We assume the media path is an external subtitle file
        base_path = os.path.splitext(media_path)[0]
        mkv_path = base_path + ".mkv"
    
    if not os.path.exists(mkv_path):
        return False, f"Video file not found: {mkv_path}", None, None
        
    # Timestamps
    start = max(0, target['start_time'] - 0.5) # 0.5s padding
    end = target['end_time'] + 0.5
    duration = end - start
    midpoint = start + (duration / 2)
    
    audio_out = os.path.join(out_dir, f"nadeshiko_audio_{sentence_id}.mp3")
    image_out = os.path.join(out_dir, f"nadeshiko_img_{sentence_id}.jpg")
    
    try:
        # Extract Audio
        subprocess.run([
            "ffmpeg", "-y", "-ss", str(start), "-i", mkv_path,
            "-t", str(duration), "-q:a", "0", "-map", "0:a:0", audio_out
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        
        # Extract Image
        subprocess.run([
            "ffmpeg", "-y", "-ss", str(midpoint), "-i", mkv_path,
            "-vframes", "1", "-q:v", "2", image_out
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        
        return True, "Media extracted successfully", audio_out, image_out, target['text']
    except Exception as e:
        return False, str(e), None, None, None

def export_anki(sentence_id: int, out_dir: str):
    success, msg, audio_out, image_out, text = extract_media(sentence_id, out_dir)
    if not success:
        return False, msg
        
    try:
        # Write to CSV
        csv_path = os.path.join(out_dir, "anki_import.tsv")
        
        with open(csv_path, 'a', newline='', encoding='utf-8') as f:
            writer = csv.writer(f, delimiter='\t')
            audio_tag = f"[sound:{os.path.basename(audio_out)}]"
            img_tag = f"<img src='{os.path.basename(image_out)}'>"
            writer.writerow([text, audio_tag, img_tag])
            
        return True, f"Exported to {out_dir}"
    except Exception as e:
        return False, str(e)
