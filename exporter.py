"""Module for extracting media clips and exporting to Anki."""

import csv
import os
import subprocess
import urllib.request
import urllib.error
import json

import db


def extract_media(sentence_id: int, out_dir: str, pad_start: float = 0.25, pad_end: float = 0.0):
    """Extract audio and image for a given sentence.

    Args:
        sentence_id (int): ID of the sentence to extract.
        out_dir (str): Output directory for the extracted media.
        pad_start (float, optional): Seconds to pad before the start time. Defaults to 0.25.
        pad_end (float, optional): Seconds to pad after the end time. Defaults to 0.0.

    Returns:
        tuple: A tuple containing:
            - bool: Success status.
            - str: Status message.
            - str: Path to the extracted audio file.
            - str: Path to the extracted image file.
            - str: The sentence text.
            - bool: Whether the media was served from cache.
    """
    conn = db.get_db()
    target = conn.execute(
        """
        SELECT s.id, s.text, s.start_time, s.end_time, s.language, s.media_id, m.path
        FROM sentences s
        JOIN media m ON s.media_id = m.id
        WHERE s.id = ?
    """,
        (sentence_id,),
    ).fetchone()

    if not target:
        return False, "Sentence not found", None, None, None, False

    os.makedirs(out_dir, exist_ok=True)

    media_path = target["path"]
    if media_path.endswith(".mkv") or media_path.endswith(".mp4"):
        mkv_path = media_path
    else:
        # We assume the media path is an external subtitle file
        dir_name = os.path.dirname(media_path)
        base_name = os.path.splitext(os.path.basename(media_path))[0]

        video_exts = (".mkv", ".mp4", ".avi", ".m4v")
        possible_video_names = []
        if "." in base_name:
            possible_video_names.append(base_name.rsplit(".", 1)[0])
        possible_video_names.append(base_name)

        found_video = None
        for v_name in possible_video_names:
            for ext in video_exts:
                test_path = os.path.join(dir_name, v_name + ext)
                if os.path.exists(test_path):
                    found_video = test_path
                    break
            if found_video:
                break

        if found_video:
            mkv_path = found_video
        else:
            # Fallback
            mkv_path = os.path.join(dir_name, base_name + ".mkv")

    if not os.path.exists(mkv_path):
        return False, f"Video file not found: {mkv_path}", None, None, None, False

    # Timestamps
    if target["start_time"] is None or target["end_time"] is None:
        return False, f"Missing timestamp data for sentence {sentence_id}", None, None, None, False

    start = max(0, target["start_time"] - pad_start)
    end = target["end_time"] + pad_end
    duration = end - start
    midpoint = start + (duration / 2)

    # Grab overlapping text whose midpoint falls within the padded timeframe (matching UI logic)
    overlapping_sentences = conn.execute(
        """
        SELECT text
        FROM sentences
        WHERE media_id = ? AND language = ?
          AND ((COALESCE(start_time, 0) + COALESCE(end_time, start_time + 2.0)) / 2.0) >= ?
          AND ((COALESCE(start_time, 0) + COALESCE(end_time, start_time + 2.0)) / 2.0) <= ?
        ORDER BY ((COALESCE(start_time, 0) + COALESCE(end_time, start_time + 2.0)) / 2.0) ASC, id ASC
        """,
        (target["media_id"], target["language"], start, end),
    ).fetchall()

    if overlapping_sentences:
        import re

        def clean_text(text: str, lang: str) -> str:
            if not text:
                return ""
            if lang in ("jpn", "ja", "jp", "zho", "zh"):
                return re.sub(r"<br\s*/?>|[\r\n]+", "", text, flags=re.IGNORECASE)
            else:
                return re.sub(r"<br\s*/?>|[\r\n]+", " ", text, flags=re.IGNORECASE)

        lang = target["language"]
        combined_text = ""

        for s in overlapping_sentences:
            text_val = clean_text(s["text"], lang)
            if not text_val.strip():
                continue

            if not combined_text:
                combined_text = text_val
                continue

            if lang in ("jpn", "ja", "jp", "zho", "zh"):
                prev_trimmed = combined_text.strip()
                ends_sentence = bool(re.search(r"[だですまるかよねわぞ。！？]$", prev_trimmed))
                delimiter = "<br/>" if ends_sentence else ""
                combined_text = combined_text + delimiter + text_val
            else:
                prev_trimmed = combined_text.strip()
                if prev_trimmed and not re.search(r"[.!?…,;:]$", prev_trimmed):
                    prev_trimmed += "."
                combined_text = prev_trimmed + " " + text_val
    else:
        import re

        text_val = target["text"] if target["text"] else ""
        lang = target["language"]
        if lang in ("jpn", "ja", "jp", "zho", "zh"):
            combined_text = re.sub(r"<br\s*/?>|[\r\n]+", "", text_val, flags=re.IGNORECASE)
        else:
            combined_text = re.sub(r"<br\s*/?>|[\r\n]+", " ", text_val, flags=re.IGNORECASE)

    audio_out = os.path.join(out_dir, f"hagi_audio_{sentence_id}_{pad_start:.3f}_{pad_end:.3f}.mp3")
    image_out = os.path.join(out_dir, f"hagi_img_{sentence_id}_{pad_start:.3f}_{pad_end:.3f}.jpg")

    is_cached = False
    if os.path.exists(audio_out) and os.path.exists(image_out):
        try:
            os.utime(audio_out, None)
            os.utime(image_out, None)
            is_cached = True
            return True, "Media returned from cache", audio_out, image_out, combined_text, is_cached
        except Exception:
            pass

    try:
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

            jpn_idx = None
            default_idx = None
            und_idx = None

            for i, stream in enumerate(streams):
                tags = stream.get("tags", {})
                lang = tags.get("language", "").lower()
                title = tags.get("title", "").lower()

                if lang in ("jpn", "ja", "jp") or "japanese" in title:
                    jpn_idx = i
                    break

                if stream.get("disposition", {}).get("default", 0) == 1 and default_idx is None:
                    default_idx = i

                if lang in ("und", "unknown", "") and und_idx is None:
                    und_idx = i

            if jpn_idx is not None:
                audio_stream_idx = jpn_idx
            elif default_idx is not None:
                def_lang = streams[default_idx].get("tags", {}).get("language", "").lower()
                if def_lang in ("und", "unknown", ""):
                    audio_stream_idx = default_idx
                elif und_idx is not None:
                    audio_stream_idx = und_idx
            elif und_idx is not None:
                audio_stream_idx = und_idx

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
            combined_text,
            False,
        )
    except Exception as e:
        return False, str(e), None, None, None, False


def export_anki(sentence_id: int, out_dir: str, pad_start: float = 0.25, pad_end: float = 0.0):
    """Export a sentence and its media for Anki.

    Args:
        sentence_id (int): ID of the sentence to export.
        out_dir (str): Output directory for the exported media and CSV.
        pad_start (float, optional): Seconds to pad before the start time. Defaults to 0.25.
        pad_end (float, optional): Seconds to pad after the end time. Defaults to 0.0.

    Returns:
        tuple: A tuple containing:
            - bool: Success status.
            - str: Status message.
    """
    success, msg, audio_out, image_out, text, is_cached = extract_media(sentence_id, out_dir, pad_start, pad_end)
    if not success:
        return False, msg, False

    try:
        # Write to CSV
        csv_path = os.path.join(out_dir, "anki_import.tsv")

        with open(csv_path, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f, delimiter="\t")
            audio_tag = f"[sound:{os.path.basename(audio_out)}]"
            img_tag = f"<img src='{os.path.basename(image_out)}'>"
            writer.writerow([text, audio_tag, img_tag])

        return True, f"Exported to {out_dir}", is_cached
    except Exception as e:
        return False, str(e), False


def export_ankiconnect(
    sentence_id: int,
    config: dict,
    out_dir: str,
    pad_start: float = 0.25,
    pad_end: float = 0.0,
    target_note_id: int | None = None,
    base_url: str | None = None,
    search_query: str | None = None,
):
    """Export sentence to AnkiConnect.

    Args:
        sentence_id (int): ID of the sentence.
        config (dict): AnkiConnect configuration dictionary.
        out_dir (str): Output directory for temporary media.
        pad_start (float, optional): Seconds to pad before start.
        pad_end (float, optional): Seconds to pad after end.
        target_note_id (int, optional): Specific Note ID to update.
        base_url (str, optional): Base URL of the web UI to serve media from.
        search_query (str, optional): Search query used to find the sentence, to highlight.

    Returns:
        tuple: (bool, str, bool) - Success status, message, and cache status.
    """
    success, msg, audio_out, image_out, text, is_cached = extract_media(sentence_id, out_dir, pad_start, pad_end)
    if not success:
        return False, msg, False

    if not isinstance(config, dict):
        return False, "Invalid configuration format: expected a dictionary.", False

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
            """
            SELECT m.show_title, m.season, m.episode, m.episode_title, s.start_time
            FROM sentences s
            JOIN media m ON s.media_id = m.id
            WHERE s.id = ?
            """,
            (sentence_id,),
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

            import html

            # Wrap source_info in an HTML permalink
            if base_url:
                link = f"{base_url.rstrip('/')}/sentence/{sentence_id}"
            else:
                link = f"http://localhost:8000/sentence/{sentence_id}"

            source_info = f'<a href="{html.escape(link, quote=True)}">{html.escape(source_info)}</a>'

        # Resolve note ID
        if not target_note_id:
            deck = config.get("deck", "")
            note_type = config.get("noteType", "")

            if not deck and not note_type:
                return (
                    False,
                    (
                        "Refusing to query all Anki notes. Please provide "
                        "'deck' or 'noteType' in config.json, or specify a target Note ID."
                    ),
                    False,
                )

            query_parts = []
            if deck:
                query_parts.append(f'deck:"{deck}"')
            if note_type:
                query_parts.append(f'note:"{note_type}"')
            query = " ".join(query_parts)

            notes = anki_request("findNotes", query=query)
            if not notes:
                return False, "No notes found to update.", False
            target_note_id = max(notes)

        # Generate highlighted text if search query is provided
        highlighted_text = text
        if search_query:
            import re

            tokens = re.findall(r"(?:[^\s\"']+|\"[^\"]*\"|'[^']*')+", search_query)

            clean_tokens = []
            for token in tokens:
                # Strip leading/trailing quotes before checking for negative terms
                token = token.strip("\"'")
                if token.startswith("-"):
                    continue
                if token:
                    clean_tokens.append(re.escape(token))

            if clean_tokens:
                pattern = re.compile(f"({'|'.join(clean_tokens)})", flags=re.IGNORECASE)
                # Split by HTML tags, properly ignoring > inside quotes and requiring valid tag start
                parts = re.split(r"(<[a-zA-Z/](?:[^>\"']|\"[^\"]*\"|'[^']*')*>)", highlighted_text)
                for i in range(len(parts)):
                    # Even indices are text segments, odd are HTML tags
                    if i % 2 == 0:
                        parts[i] = pattern.sub(r"<b>\1</b>", parts[i])
                highlighted_text = "".join(parts)

        # Prepare fields
        fields_to_update = {}
        sentence_field = config.get("sentenceField")
        sentence_highlighted_field = config.get("sentenceHighlightedField")
        source_field = config.get("sourceField")
        audio_field = config.get("audioField")
        image_field = config.get("imageField")

        if sentence_highlighted_field:
            if sentence_field == sentence_highlighted_field:
                return False, "sentenceField and sentenceHighlightedField must not be the same field", False
            if source_field == sentence_highlighted_field:
                return False, "sourceField and sentenceHighlightedField must not be the same field", False
            if audio_field == sentence_highlighted_field:
                return False, "audioField and sentenceHighlightedField must not be the same field", False
            if image_field == sentence_highlighted_field:
                return False, "imageField and sentenceHighlightedField must not be the same field", False

        if sentence_field:
            fields_to_update[sentence_field] = text
        if sentence_highlighted_field:
            fields_to_update[sentence_highlighted_field] = highlighted_text
        if source_field and source_info:
            fields_to_update[source_field] = source_info

        update_params = {"note": {"id": target_note_id, "fields": fields_to_update}}

        # Add media
        if audio_field and os.path.exists(audio_out):
            store_params = {"filename": os.path.basename(audio_out), "deleteExisting": False}
            if base_url:
                store_params["url"] = f"{base_url.rstrip('/')}/media/{os.path.basename(audio_out)}"
            else:
                store_params["path"] = os.path.abspath(audio_out)

            actual_filename = anki_request("storeMediaFile", **store_params)
            if actual_filename:
                current = fields_to_update.get(audio_field, "")
                fields_to_update[audio_field] = current + f"[sound:{actual_filename}]"

        if image_field and os.path.exists(image_out):
            store_params = {"filename": os.path.basename(image_out), "deleteExisting": False}
            if base_url:
                store_params["url"] = f"{base_url.rstrip('/')}/media/{os.path.basename(image_out)}"
            else:
                store_params["path"] = os.path.abspath(image_out)

            actual_filename = anki_request("storeMediaFile", **store_params)
            if actual_filename:
                current = fields_to_update.get(image_field, "")
                fields_to_update[image_field] = current + f'<img src="{actual_filename}">'

        anki_request("updateNoteFields", **update_params)

        # Add tags if configured
        tags = config.get("tags")
        if tags and isinstance(tags, list):
            tags_str = " ".join(tags)
            anki_request("addTags", notes=[target_note_id], tags=tags_str)

        return True, f"Successfully updated note {target_note_id} in Anki.", is_cached

    except Exception as e:
        return False, str(e), False


def cleanup_media_cache(out_dir: str, max_mb: int = 500):
    """Cleans up old extracted media files if they exceed the size limit."""
    if not os.path.exists(out_dir):
        return

    max_bytes = max_mb * 1024 * 1024
    files = []
    total_size = 0

    try:
        for entry in os.scandir(out_dir):
            if entry.is_file() and (entry.name.startswith("hagi_audio_") or entry.name.startswith("hagi_img_")):
                stat = entry.stat()
                files.append((entry.path, stat.st_mtime, stat.st_size))
                total_size += stat.st_size
    except Exception:
        return

    if total_size <= max_bytes:
        return

    # Sort by mtime ascending (oldest first)
    files.sort(key=lambda x: x[1])

    for path, mtime, size in files:
        if total_size <= max_bytes:
            break
        try:
            os.remove(path)
            total_size -= size
        except Exception:
            pass
