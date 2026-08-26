"""Module for extracting media clips and exporting to Anki."""

import csv
import os
import subprocess

import db


def extract_media(sentence_id: int, out_dir: str, pad_start: float = 0.5, pad_end: float = 0.5):
    """Extract audio and image for a given sentence.

    Args:
        sentence_id (int): ID of the sentence to extract.
        out_dir (str): Output directory for the extracted media.
        pad_start (float, optional): Seconds to pad before the start time. Defaults to 0.5.
        pad_end (float, optional): Seconds to pad after the end time. Defaults to 0.5.

    Returns:
        tuple: A tuple containing:
            - bool: Success status.
            - str: Status message.
            - str: Path to the extracted audio file.
            - str: Path to the extracted image file.
            - str: The sentence text.
    """
    conn = db.get_db()
    target = conn.execute(
        """
        SELECT s.id, s.text, s.start_time, s.end_time, m.path
        FROM sentences s
        JOIN media m ON s.media_id = m.id
        WHERE s.id = ?
    """,
        (sentence_id,),
    ).fetchone()

    if not target:
        return False, "Sentence not found", None, None, None

    os.makedirs(out_dir, exist_ok=True)

    media_path = target["path"]
    if media_path.endswith(".mkv"):
        mkv_path = media_path
    else:
        # We assume the media path is an external subtitle file
        base_path = os.path.splitext(media_path)[0]
        mkv_path = base_path + ".mkv"

    if not os.path.exists(mkv_path):
        return False, f"Video file not found: {mkv_path}", None, None, None

    # Timestamps
    if target["start_time"] is None or target["end_time"] is None:
        return False, f"Missing timestamp data for sentence {sentence_id}", None, None, None

    start = max(0, target["start_time"] - pad_start)
    end = target["end_time"] + pad_end
    duration = end - start
    midpoint = start + (duration / 2)

    audio_out = os.path.join(out_dir, f"hagi_audio_{sentence_id}.mp3")
    image_out = os.path.join(out_dir, f"hagi_img_{sentence_id}.jpg")

    try:
        import json

        # Probe for the Japanese audio track
        probe_cmd = [
            "ffprobe",
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_streams",
            "-select_streams",
            "a",
            mkv_path,
        ]
        probe_res = subprocess.run(probe_cmd, capture_output=True, text=True)

        audio_stream_idx = 0
        if probe_res.returncode == 0:
            streams = json.loads(probe_res.stdout).get("streams", [])
            for i, stream in enumerate(streams):
                lang = stream.get("tags", {}).get("language", "").lower()
                if lang in ("jpn", "ja", "jp"):
                    audio_stream_idx = i
                    break

        # Extract Audio using the detected stream index
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-ss",
                str(start),
                "-i",
                mkv_path,
                "-t",
                str(duration),
                "-q:a",
                "0",
                "-map",
                f"0:a:{audio_stream_idx}",
                audio_out,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )

        # Extract Image
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-ss",
                str(midpoint),
                "-i",
                mkv_path,
                "-vframes",
                "1",
                "-q:v",
                "2",
                image_out,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )

        return (
            True,
            "Media extracted successfully",
            audio_out,
            image_out,
            target["text"],
        )
    except Exception as e:
        return False, str(e), None, None, None


def export_anki(sentence_id: int, out_dir: str, pad_start: float = 0.5, pad_end: float = 0.5):
    """Export a sentence and its media for Anki.

    Args:
        sentence_id (int): ID of the sentence to export.
        out_dir (str): Output directory for the exported media and CSV.
        pad_start (float, optional): Seconds to pad before the start time. Defaults to 0.5.
        pad_end (float, optional): Seconds to pad after the end time. Defaults to 0.5.

    Returns:
        tuple: A tuple containing:
            - bool: Success status.
            - str: Status message.
    """
    success, msg, audio_out, image_out, text = extract_media(
        sentence_id, out_dir, pad_start, pad_end
    )
    if not success:
        return False, msg

    try:
        # Write to CSV
        csv_path = os.path.join(out_dir, "anki_import.tsv")

        with open(csv_path, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f, delimiter="\t")
            audio_tag = f"[sound:{os.path.basename(audio_out)}]"
            img_tag = f"<img src='{os.path.basename(image_out)}'>"
            writer.writerow([text, audio_tag, img_tag])

        return True, f"Exported to {out_dir}"
    except Exception as e:
        return False, str(e)
