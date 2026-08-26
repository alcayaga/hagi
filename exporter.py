"""Module for extracting media clips and exporting to Anki."""

import csv
import os
import subprocess
import urllib.request
import urllib.error
import json

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



def export_ankiconnect(
    sentence_id: int,
    config: dict,
    out_dir: str,
    pad_start: float = 0.5,
    pad_end: float = 0.5,
    target_note_id: int = None,
):
    """Export sentence to AnkiConnect.

    Args:
        sentence_id (int): ID of the sentence.
        config (dict): AnkiConnect configuration dictionary.
        out_dir (str): Output directory for temporary media.
        pad_start (float, optional): Seconds to pad before start.
        pad_end (float, optional): Seconds to pad after end.
        target_note_id (int, optional): Specific Note ID to update.

    Returns:
        tuple: (bool, str) - Success status and message.
    """
    success, msg, audio_out, image_out, text = extract_media(
        sentence_id, out_dir, pad_start, pad_end
    )
    if not success:
        return False, msg

    anki_url = config.get("ankiConnectUrl", "http://127.0.0.1:8765")

    def anki_request(action, **params):
        """Helper to send requests to AnkiConnect API."""
        req_data = json.dumps({"action": action, "version": 6, "params": params}).encode("utf-8")
        req = urllib.request.Request(anki_url, req_data, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10.0) as response:
                res = json.loads(response.read().decode("utf-8"))
                if res.get("error"):
                    raise Exception(res["error"])
                return res.get("result")
        except Exception as e:
            raise Exception(f"AnkiConnect error: {e}")

    try:
        # Fetch metadata for source_info
        conn = db.get_db()
        meta = conn.execute(
            '''
            SELECT m.show_title, m.season, m.episode, m.episode_title, s.start_time
            FROM sentences s
            JOIN media m ON s.media_id = m.id
            WHERE s.id = ?
            ''',
            (sentence_id,)
        ).fetchone()

        source_info = ""
        if meta:
            show_part = ""
            if meta["show_title"]:
                show_part += meta["show_title"]

            ep_part = ""
            if meta["season"] is not None and meta["episode"] is not None:
                ep_part = f"S{meta['season']:02d}E{meta['episode']:02d}"
            elif meta["episode"] is not None:
                ep_part = f"Ep {meta['episode']:02d}"

            if show_part and ep_part:
                show_part += f" {ep_part}"
            elif ep_part:
                show_part = ep_part

            title_part = ""
            if meta["episode_title"]:
                title_part = meta["episode_title"]

            time_str = ""
            if meta["start_time"] is not None:
                time_str = f"[{int(meta['start_time'] // 60):02d}:{int(meta['start_time'] % 60):02d}]"

            parts = []
            if show_part:
                parts.append(show_part)
            if title_part:
                # If there's a show part, separate with a dash, otherwise just add title
                if show_part:
                    parts.append(f"- {title_part}")
                else:
                    parts.append(title_part)
            if time_str:
                parts.append(time_str)

            source_info = " ".join(parts)

        # Resolve note ID
        if not target_note_id:
            deck = config.get("deck", "")
            note_type = config.get("noteType", "")

            if not deck and not note_type:
                return False, ("Refusing to query all Anki notes. Please provide "
                               "'deck' or 'noteType' in config.json, or specify a target Note ID.")

            query_parts = []
            if deck:
                query_parts.append(f"deck:\"{deck}\"")
            if note_type:
                query_parts.append(f"note:\"{note_type}\"")
            query = " ".join(query_parts)

            notes = anki_request("findNotes", query=query)
            if not notes:
                return False, "No notes found to update."
            target_note_id = max(notes)

        # Prepare fields
        fields_to_update = {}
        sentence_field = config.get("sentenceField")
        source_field = config.get("sourceField")

        if sentence_field:
            fields_to_update[sentence_field] = text
        if source_field and source_info:
            fields_to_update[source_field] = source_info

        update_params = {
            "note": {
                "id": target_note_id,
                "fields": fields_to_update
            }
        }

        # Add media
        audio_field = config.get("audioField")
        if audio_field:
            update_params["note"]["audio"] = [{
                "path": os.path.abspath(audio_out),
                "filename": os.path.basename(audio_out),
                "fields": [audio_field]
            }]

        image_field = config.get("imageField")
        if image_field:
            update_params["note"]["picture"] = [{
                "path": os.path.abspath(image_out),
                "filename": os.path.basename(image_out),
                "fields": [image_field]
            }]

        anki_request("updateNoteFields", **update_params)

        # Add tags if configured
        tags = config.get("tags")
        if tags and isinstance(tags, list):
            tags_str = " ".join(tags)
            anki_request("addTags", notes=[target_note_id], tags=tags_str)

        return True, f"Successfully updated note {target_note_id} in Anki."

    except Exception as e:
        return False, str(e)
