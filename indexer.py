"""Module for indexing media files and subtitles."""

import json
import os
import subprocess
import tempfile

import pysubs2
from dotenv import load_dotenv

from db import add_media, add_sentences, get_db

load_dotenv()

plex = None
plex_path_cache = {}

try:
    from plexapi.server import PlexServer

    PLEX_URL = os.getenv("PLEX_URL")
    PLEX_TOKEN = os.getenv("PLEX_TOKEN")
    if PLEX_URL and PLEX_TOKEN:
        plex = PlexServer(PLEX_URL, PLEX_TOKEN)
except Exception as e:
    print(f"Warning: Could not connect to Plex: {e}")


_plex_cache_built = False

def build_plex_cache():
    global _plex_cache_built
    if not plex or _plex_cache_built:
        return
    print("Building Plex path mapping cache (this may take a moment)...")
    try:
        for section in plex.library.sections():
            if section.type == "movie":
                movies = section.search(libtype="movie")
                for movie in movies:
                    for media in movie.media:
                        for part in media.parts:
                            base_name = os.path.splitext(os.path.basename(part.file))[0]
                            plex_path_cache[base_name] = (movie.title, 1, 1, movie.title)
            elif section.type == "show":
                episodes = section.search(libtype="episode")
                for ep in episodes:
                    for media in ep.media:
                        for part in media.parts:
                            base_name = os.path.splitext(os.path.basename(part.file))[0]
                            plex_path_cache[base_name] = (
                                ep.grandparentTitle,
                                ep.parentIndex,
                                ep.index,
                                ep.title,
                            )
        _plex_cache_built = True
    except Exception as e:
        print(f"Error building Plex cache: {e}")


def process_subs(conn, file_path, subs, media_type="subtitle", language="unknown"):
    """Process subtitles and add them to the database.

    Args:
        conn: Database connection.
        file_path (str): Path to the subtitle file.
        subs: Parsed subtitles object.
        media_type (str, optional): Type of the media. Defaults to "subtitle".
        language (str, optional): Language of the subtitles. Defaults to "unknown".
    """
    base_name = os.path.splitext(os.path.basename(file_path))[0]
    show_title, season, episode, episode_title = plex_path_cache.get(base_name, (None, None, None, None))
    media_id = add_media(conn, file_path, media_type, show_title, season, episode, episode_title)
    sentences = []

    for line in subs:
        text = line.plaintext.strip()
        if text:
            sentences.append((language, line.start / 1000.0, line.end / 1000.0, text))

    if sentences:
        add_sentences(conn, media_id, sentences)
        print(f"Indexed: {file_path} [{language}] ({len(sentences)} lines)")


def index_directory(directory_path: str):
    """Scan and index all subtitle and MKV files in a directory.

    Args:
        directory_path (str): Path to the directory to be indexed.
    """
    build_plex_cache()
    conn = get_db()
    for root, _, files in os.walk(directory_path):
        for file in files:
            if file.startswith("._"):
                continue

            file_path = os.path.join(root, file)
            base_name = os.path.splitext(os.path.basename(file_path))[0]

            # Incremental indexing: skip if already in DB
            row = conn.execute(
                "SELECT id, show_title, episode_title FROM media WHERE path = ?", (file_path,)
            ).fetchone()
            if row:
                show_title, season, episode, episode_title = plex_path_cache.get(base_name, (None, None, None, None))
                updated = False
                if row["show_title"] is None and show_title:
                    conn.execute(
                        "UPDATE media SET show_title=?, season=?, episode=? WHERE id=?",
                        (show_title, season, episode, row["id"]),
                    )
                    updated = True
                
                if ("episode_title" not in row.keys() or not row["episode_title"]) and episode_title:
                    conn.execute(
                        "UPDATE media SET episode_title=? WHERE id=?",
                        (episode_title, row["id"]),
                    )
                    updated = True
                    
                if updated:
                    print(f"Backfilled Plex metadata for: {file_path}")
                else:
                    print(f"Skipping (already indexed): {file_path}")
                continue

            if file.endswith((".ass", ".srt")):
                try:
                    subs = None
                    for enc in ["utf-8", "utf-16", "utf-8-sig", "latin-1", "shift_jis"]:
                        try:
                            subs = pysubs2.load(file_path, encoding=enc)
                            break
                        except UnicodeDecodeError:
                            continue

                    if subs:
                        # Detect language based on the actual text content
                        def detect_language(subs_obj):
                            jp_chars = 0
                            sp_chars = 0
                            total_chars = 0

                            lines_checked = 0
                            for line in subs_obj:
                                text = line.plaintext.strip()
                                if not text:
                                    continue

                                for char in text:
                                    code = ord(char)
                                    # Hiragana, Katakana, CJK Ideographs
                                    if (
                                        0x3040 <= code <= 0x309F
                                        or 0x30A0 <= code <= 0x30FF
                                        or 0x4E00 <= code <= 0x9FAF
                                    ):
                                        jp_chars += 1
                                    elif char in "áéíóúñÁÉÍÓÚÑ¿¡":
                                        sp_chars += 1

                                total_chars += len(text)
                                lines_checked += 1
                                if lines_checked >= 50:
                                    break

                            if total_chars == 0:
                                return "unknown"
                            if jp_chars / total_chars > 0.05:
                                return "jpn"
                            if sp_chars > 0:
                                return "spa"
                            return "eng"

                        lang_hint = detect_language(subs)
                        process_subs(conn, file_path, subs, "subtitle", language=lang_hint)
                    else:
                        print(f"Failed to decode subtitle file: {file_path}")
                except Exception as e:
                    print(f"Error indexing {file_path}: {e}")

            elif file.endswith(".mkv"):
                try:
                    probe_cmd = [
                        "ffprobe",
                        "-v",
                        "error",
                        "-select_streams",
                        "s",
                        "-show_entries",
                        "stream=index:stream_tags=language",
                        "-of",
                        "json",
                        file_path,
                    ]
                    result = subprocess.run(probe_cmd, capture_output=True, text=True)
                    if result.returncode != 0:
                        print(f"ffprobe failed for {file_path}: {result.stderr}")
                        continue

                    streams = json.loads(result.stdout).get("streams", [])
                    if not streams:
                        # We process the mkv, so add it to the media table once to mark it as indexed even if tracks fail
                        show_title, season, episode, episode_title = plex_path_cache.get(base_name, (None, None, None, None))
                        add_media(conn, file_path, "mkv_embedded", show_title, season, episode, episode_title)
                        continue

                    for stream in streams:
                        i = stream.get("index")
                        tags = stream.get("tags", {})
                        lang = tags.get("language", "unknown")

                        fd, temp_sub_path = tempfile.mkstemp(suffix=".srt")
                        os.close(fd)

                        ext_cmd = [
                            "ffmpeg",
                            "-y",
                            "-i",
                            file_path,
                            "-map",
                            f"0:{i}",
                            "-c:s",
                            "srt",
                            temp_sub_path,
                        ]
                        ext_res = subprocess.run(
                            ext_cmd,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                        )

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

    conn.commit()
