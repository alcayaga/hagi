"""Module for indexing media files and subtitles."""

import bisect
import difflib
import json
import os
import re
import subprocess
import tempfile
import time
from typing import Optional

import pysubs2
from dotenv import load_dotenv

from .db import add_media, add_sentences, get_db, update_media_path

load_dotenv()

REFRESH_THRESHOLD_SECONDS = 2.0
DEFAULT_EXTRACT_TIMEOUT = 1800
DEFAULT_PROBE_TIMEOUT = 300
BITMAP_SUBTITLE_CODECS = {
    "hdmv_pgs_subtitle",
    "dvd_subtitle",
    "dvdsub",
    "dvb_subtitle",
    "dvbsub",
    "pgssub",
    "xsub",
}


def _load_config() -> Optional[dict]:
    """Load configuration from config.json if present.

    Returns:
        Optional[dict]: The parsed JSON configuration as a dictionary, or None
        if config.json does not exist.

    Raises:
        ValueError: If config.json does not contain a JSON object.
        OSError: If an error occurs while opening or reading config.json.
        json.JSONDecodeError: If config.json contains invalid JSON syntax.
    """
    if not os.path.exists("config.json"):
        return None
    with open("config.json", "r") as f:
        cfg = json.load(f)
        if not isinstance(cfg, dict):
            raise ValueError("config.json must contain a JSON object.")
        return cfg


def load_and_sanitize_subs(file_path, encoding="utf-8"):
    """Load a subtitle file and sanitize invalid negative timestamps.

    Args:
        file_path (str): Path to the subtitle file.
        encoding (str): Encoding to use when reading the file. Defaults to "utf-8".

    Returns:
        pysubs2.SSAFile: Parsed subtitles object.
    """
    with open(file_path, "r", encoding=encoding) as f:
        lines = f.readlines()

    def replacer(match):
        val = match.group(0)
        if "-" in val:
            if "," in val:
                return "00:00:00,000"
            return "0:00:00.00"
        return val

    pattern = re.compile(r"-?\d{1,2}:-?\d{1,2}:-?\d{1,2}[.,]-?\d{1,3}")

    sanitized_lines = []
    for line in lines:
        if line.startswith("Dialogue:") or "-->" in line:
            line = pattern.sub(replacer, line)
        sanitized_lines.append(line)

    content = "".join(sanitized_lines)
    return pysubs2.SSAFile.from_string(content)


_plex_instance = None
_plex_initialized = False


def _get_plex():
    global _plex_instance, _plex_initialized
    if _plex_initialized:
        return _plex_instance
    _plex_initialized = True
    try:
        from plexapi.server import PlexServer

        PLEX_URL = os.getenv("PLEX_URL")
        PLEX_TOKEN = os.getenv("PLEX_TOKEN")
        if PLEX_URL and PLEX_TOKEN:
            _plex_instance = PlexServer(PLEX_URL, PLEX_TOKEN)
    except Exception as e:
        print(f"Warning: Could not connect to Plex: {e}")
    return _plex_instance


plex_path_cache = {}


_plex_cache_built = False


def build_plex_cache():
    """Build the cache of Plex paths and metadata."""
    global _plex_cache_built
    if _plex_cache_built:
        return
    plex = _get_plex()
    if not plex:
        return
    print("Building Plex path mapping cache (this may take a moment)...")
    try:
        config = _load_config()
    except Exception as e:
        print(f"Error reading config.json for Plex libraries: {e}")
        return

    try:
        allowed_libraries = config.get("plex_libraries") if config else None

        for section in plex.library.sections():
            if allowed_libraries is not None:
                allowed_str = [str(x) for x in allowed_libraries]
                # section.key is typically an int/string ID (e.g. 4), section.title is string
                if str(section.title) not in allowed_str and str(section.key) not in allowed_str:
                    continue

            if section.type == "movie":
                movies = section.search(libtype="movie")
                for movie in movies:
                    for media in movie.media:
                        for part in media.parts:
                            cache_key = os.path.splitext(part.file)[0]
                            base_key = os.path.basename(cache_key)
                            val = (movie.title, 1, 1, movie.title)
                            plex_path_cache[cache_key] = val
                            if base_key in plex_path_cache:
                                if plex_path_cache[base_key] != val:
                                    plex_path_cache[base_key] = None
                            else:
                                plex_path_cache[base_key] = val
            elif section.type == "show":
                episodes = section.search(libtype="episode")
                for ep in episodes:
                    for media in ep.media:
                        for part in media.parts:
                            cache_key = os.path.splitext(part.file)[0]
                            base_key = os.path.basename(cache_key)
                            val = (
                                ep.grandparentTitle,
                                ep.parentIndex,
                                ep.index,
                                ep.title,
                            )
                            plex_path_cache[cache_key] = val
                            if base_key in plex_path_cache:
                                if plex_path_cache[base_key] != val:
                                    plex_path_cache[base_key] = None
                            else:
                                plex_path_cache[base_key] = val
        _plex_cache_built = True
    except Exception as e:
        print(f"Error building Plex cache: {e}")


SUPPORTED_LOCALES = {
    "en",
    "eng",
    "ja",
    "jp",
    "jpn",
    "es",
    "spa",
    "pt",
    "por",
    "fr",
    "fre",
    "fra",
    "de",
    "ger",
    "deu",
    "it",
    "ita",
    "ru",
    "rus",
    "zh",
    "chi",
    "zho",
    "ko",
    "kor",
    "ar",
    "ara",
}


def get_plex_metadata(file_path):
    """Get Plex metadata, accounting for external subtitle language codes.

    Args:
        file_path (str): Path to the subtitle or media file.

    Returns:
        tuple: (show_title, season, episode, episode_title)
    """
    cache_key = os.path.splitext(file_path)[0]
    info = plex_path_cache.get(cache_key)
    if not info:
        base_name = os.path.basename(cache_key)
        if base_name in plex_path_cache:
            info = plex_path_cache[base_name]
        elif "." in base_name:
            parts = base_name.rsplit(".", 1)
            if len(parts) == 2:
                # Support base locales (e.g., 'en') and regional locales (e.g., 'en-us', 'pt_br')
                suffix = parts[1].lower().replace("_", "-")
                suffix_parts = suffix.split("-")
                base_suffix = suffix_parts[0]

                # Check if it's a valid base locale. If it has a region, ensure it is 2-letter alpha or 3-digit numeric
                is_valid = base_suffix in SUPPORTED_LOCALES
                if len(suffix_parts) > 1:
                    region = suffix_parts[1]
                    valid_region = (len(region) == 2 and region.isalpha()) or (len(region) == 3 and region.isdigit())
                    is_valid = is_valid and valid_region and len(suffix_parts) == 2

                if is_valid:
                    stripped = parts[0]
                    cache_key_stripped = os.path.join(os.path.dirname(file_path), stripped)
                    info = plex_path_cache.get(cache_key_stripped)
                    if not info:
                        info = plex_path_cache.get(stripped)
    return info or (None, None, None, None)


def process_subs(conn, file_path, subs, media_type="subtitle", language="unknown"):
    """Process subtitles and add them to the database.

    Args:
        conn: Database connection.
        file_path (str): Path to the subtitle file.
        subs: Parsed subtitles object.
        media_type (str, optional): Type of the media. Defaults to "subtitle".
        language (str, optional): Language of the subtitles. Defaults to "unknown".
    """
    show_title, season, episode, episode_title = get_plex_metadata(file_path)
    media_id = add_media(conn, file_path, media_type, show_title, season, episode, episode_title)
    sentences = []

    for line in subs:
        text = line.plaintext.strip()
        if text:
            sentences.append((language, line.start / 1000.0, line.end / 1000.0, text))

    if sentences:
        add_sentences(conn, media_id, sentences)
        print(f"Indexed: {file_path} [{language}] ({len(sentences)} lines)")


def detect_language(subs_obj):
    """Heuristically detect the language of a subtitle object."""
    jp_chars = 0
    sp_chars = 0
    por_chars = 0
    total_chars = 0

    lines_checked = 0
    for line in subs_obj:
        text = line.plaintext.strip()
        if not text:
            continue

        for char in text:
            code = ord(char)
            # Hiragana, Katakana, CJK Ideographs
            if 0x3040 <= code <= 0x309F or 0x30A0 <= code <= 0x30FF or 0x4E00 <= code <= 0x9FAF:
                jp_chars += 1
            elif char in "ñÑ¿¡":
                sp_chars += 2
            elif char in "áéíóúÁÉÍÓÚ":
                sp_chars += 1
            elif char in "ãõçêâôÃÕÇÊÂÔàèìòùÀÈÌÒÙ":
                por_chars += 2

        total_chars += len(text)
        lines_checked += 1
        if lines_checked >= 50:
            break

    if total_chars == 0:
        return "unknown"
    if jp_chars / total_chars > 0.05:
        return "jpn"
    if por_chars > sp_chars:
        return "por"
    if sp_chars > 0:
        return "spa"
    return "eng"


def prune_database():
    """Verify all media in the database and remove missing files globally."""
    import errno

    conn = get_db()
    cursor = conn.execute("SELECT id, path, type, show_title, season, episode, episode_title FROM media")
    pruned_count = 0
    for row in cursor.fetchall():
        try:
            os.stat(row["path"])
        except OSError as e:
            if e.errno in (errno.ENOENT, errno.ENOTDIR):
                parent_dir = os.path.dirname(row["path"])
                upgraded = False
                if os.path.isdir(parent_dir):
                    media_type = row["type"] or ("mkv_embedded" if row["path"].endswith(".mkv") else "subtitle")
                    cand_exts = (".mkv",) if media_type == "mkv_embedded" else (".srt", ".ass")
                    try:
                        for entry in os.scandir(parent_dir):
                            if entry.is_file() and entry.name.lower().endswith(cand_exts):
                                cand_path = entry.path
                                if not conn.execute("SELECT 1 FROM media WHERE path = ?", (cand_path,)).fetchone():
                                    matched_id = find_matching_media(
                                        conn, cand_path, media_type=media_type, missing_media_rows=[dict(row)]
                                    )
                                    if matched_id == row["id"]:
                                        print(f"Upgrading missing media {row['path']} -> {cand_path} during prune...")
                                        if refresh_media(conn, row["id"], cand_path):
                                            upgraded = True
                                            break
                    except Exception as scan_err:
                        print(f"Error checking directory {parent_dir}: {scan_err}")

                if not upgraded:
                    print(f"Removing missing file from database: {row['path']}")
                    conn.execute("DELETE FROM sentences WHERE media_id = ?", (row["id"],))
                    conn.execute("DELETE FROM media WHERE id = ?", (row["id"],))
                    pruned_count += 1
            else:
                print(f"Error accessing file {row['path']}: {e}")
    if pruned_count > 0:
        conn.commit()
        print(f"Pruned {pruned_count} missing media files.")
    else:
        print("No missing media files found.")


def index_directory(
    directory_path: str,
    extract_timeout: Optional[int] = None,
    probe_timeout: Optional[int] = None,
):
    """Scan and index all subtitle and MKV files in a directory.

    Args:
        directory_path (str): Path to the directory to be indexed.
        extract_timeout (Optional[int]): Subprocess timeout in seconds for extracting
            subtitle tracks. If None, checks config.json ('extractTimeout' or 'extract_timeout')
            or defaults to DEFAULT_EXTRACT_TIMEOUT (1800s). Set to 0 or negative for unlimited.
        probe_timeout (Optional[int]): Subprocess timeout in seconds for ffprobe.
            If None, checks config.json ('probeTimeout' or 'probe_timeout') or
            defaults to DEFAULT_PROBE_TIMEOUT (300s). Set to 0 or negative for unlimited.
    """
    build_plex_cache()
    conn = get_db()
    try:
        config = _load_config() or {}
    except Exception as e:
        print(f"Error reading config.json: {e}")
        config = {}

    if extract_timeout is not None:
        effective_extract_timeout = None if extract_timeout <= 0 else extract_timeout
    else:
        cfg_extract = config.get("extractTimeout", config.get("extract_timeout"))
        if cfg_extract is not None:
            try:
                cfg_extract_val = int(cfg_extract)
                effective_extract_timeout = None if cfg_extract_val <= 0 else cfg_extract_val
            except (ValueError, TypeError, OverflowError):
                effective_extract_timeout = DEFAULT_EXTRACT_TIMEOUT
        else:
            effective_extract_timeout = DEFAULT_EXTRACT_TIMEOUT

    if probe_timeout is not None:
        effective_probe_timeout = None if probe_timeout <= 0 else probe_timeout
    else:
        cfg_probe = config.get("probeTimeout", config.get("probe_timeout"))
        if cfg_probe is not None:
            try:
                cfg_probe_val = int(cfg_probe)
                effective_probe_timeout = None if cfg_probe_val <= 0 else cfg_probe_val
            except (ValueError, TypeError, OverflowError):
                effective_probe_timeout = DEFAULT_PROBE_TIMEOUT
        else:
            effective_probe_timeout = DEFAULT_PROBE_TIMEOUT

    # Collect missing files that fall under the directory being indexed
    abs_dir = os.path.abspath(directory_path)
    like_pattern = abs_dir if abs_dir.endswith(os.sep) else f"{abs_dir}{os.sep}"
    like_pattern = like_pattern.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"

    cursor = conn.execute(
        "SELECT id, path, type, show_title, season, episode, episode_title FROM media WHERE path LIKE ? ESCAPE '\\'",
        (like_pattern,),
    )
    missing_media = {}
    for row in cursor.fetchall():
        if not os.path.exists(row["path"]):
            missing_media[row["id"]] = dict(row)

    directory_path = abs_dir
    for root, _, files in os.walk(directory_path):
        for file in files:
            if file.startswith("._"):
                continue

            file_path = os.path.join(root, file)

            # Incremental indexing: skip if already in DB
            row = conn.execute(
                "SELECT id, show_title, episode_title FROM media WHERE path = ?",
                (file_path,),
            ).fetchone()
            if row:
                show_title, season, episode, episode_title = get_plex_metadata(file_path)
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
                    conn.commit()
                    print(f"Backfilled Plex metadata for: {file_path}")
                else:
                    print(f"Skipping (already indexed): {file_path}")
                continue

            media_type = "mkv_embedded" if file.endswith(".mkv") else ("subtitle" if file.endswith((".ass", ".srt")) else None)
            if not media_type:
                continue

            # Check if this new file upgrades an existing missing media entry
            matched_mid = None
            if missing_media:
                candidate_missing = [m for m in missing_media.values() if m["type"] == media_type]
                if candidate_missing:
                    matched_mid = find_matching_media(
                        conn,
                        file_path,
                        media_type=media_type,
                        missing_media_rows=candidate_missing,
                    )

            if matched_mid is not None:
                old_info = missing_media[matched_mid]
                print(f"Upgrading media {old_info['path']} -> {file_path} (preserving sentence IDs)...")
                if refresh_media(
                    conn,
                    matched_mid,
                    file_path,
                    extract_timeout=effective_extract_timeout,
                    probe_timeout=effective_probe_timeout,
                ):
                    del missing_media[matched_mid]
                    continue

            if file.endswith((".ass", ".srt")):
                try:
                    subs = None
                    for enc in ["utf-8-sig", "utf-8", "utf-16", "cp932", "shift_jis", "latin-1"]:
                        try:
                            subs = load_and_sanitize_subs(file_path, encoding=enc)
                            break
                        except UnicodeDecodeError:
                            continue

                    if subs:
                        lang_hint = detect_language(subs)
                        process_subs(conn, file_path, subs, "subtitle", language=lang_hint)
                        conn.commit()
                    else:
                        print(f"Failed to decode subtitle file: {file_path}")
                except Exception as e:
                    print(f"Error indexing {file_path}: {e}")

            elif file.endswith(".mkv"):
                try:
                    subs_by_lang, had_timeout = extract_mkv_subtitles(
                        file_path,
                        extract_timeout=effective_extract_timeout,
                        probe_timeout=effective_probe_timeout,
                    )
                    if had_timeout:
                        conn.rollback()
                    else:
                        show_title, season, episode, episode_title = get_plex_metadata(file_path)
                        media_id = add_media(
                            conn,
                            file_path,
                            "mkv_embedded",
                            show_title,
                            season,
                            episode,
                            episode_title,
                        )
                        for lang, sentences_list in subs_by_lang.items():
                            sentence_tuples = [
                                (lang, s["start_time"], s["end_time"], s["text"])
                                for s in sentences_list
                            ]
                            if sentence_tuples:
                                add_sentences(conn, media_id, sentence_tuples)
                                print(f"Indexed: {file_path} [{lang}] ({len(sentence_tuples)} lines)")
                        conn.commit()
                except Exception as e:
                    print(f"Error extracting from {file_path}: {e}")

    # Remove remaining missing files that were not upgraded
    for mid, m in missing_media.items():
        print(f"Removing deleted file from database: {m['path']}")
        conn.execute("DELETE FROM sentences WHERE media_id = ?", (mid,))
        conn.execute("DELETE FROM media WHERE id = ?", (mid,))
    conn.commit()


def parse_media_identifiers(file_path: str) -> dict:
    """Extract season, episode, absolute episode number, and show hint from a file path.

    Supports standard TV formatting (SxxExx, xxXxx), anime absolute numbering (e.g. - 1207 -),
    and parent directory structures (e.g. Season NN).

    Args:
        file_path (str): File path to parse.

    Returns:
        dict: A dictionary containing:
            - season (int | None): The detected season number.
            - episode (int | None): The detected episode number.
            - abs_episode (int | None): The detected absolute episode number.
            - show_hint (str | None): A normalized show title hint.
    """
    base_name = os.path.basename(file_path)
    clean_base = re.sub(r"\[[^\]]*\]|\([^\)]*\)", " ", base_name)
    parent_dir = os.path.basename(os.path.dirname(os.path.abspath(file_path)))

    season = None
    episode = None
    abs_episode = None

    # Check SxxExx or sxxexx
    m_se = re.search(r"[sS](\d{1,3})[eE](\d{1,4})", base_name)
    if m_se:
        season = int(m_se.group(1))
        episode = int(m_se.group(2))
    else:
        # Check xxXxx (e.g. 34x21)
        m_x = re.search(r"\b(\d{1,2})x(\d{1,4})\b", base_name)
        if m_x:
            season = int(m_x.group(1))
            episode = int(m_x.group(2))

    # Check parent directory for Season XX if season is still None
    if season is None:
        m_p = re.search(r"Season\s*(\d{1,3})", parent_dir, re.IGNORECASE)
        if m_p:
            season = int(m_p.group(1))

    # Check absolute episode number (e.g. " - 1207 - " or " - 1207 [" or " 1207 ")
    m_abs = re.search(r"(?:-\s*)(\d{2,4})(?:\s*-|\s*\[|\s*\.|\b)", clean_base)
    if not m_abs:
        m_abs = re.search(r"\b(\d{3,4})\b", clean_base)
    if m_abs:
        cand = int(m_abs.group(1))
        if cand not in (1080, 720, 480, 264, 265, 576, 2160) and cand != season and cand != episode:
            abs_episode = cand

    show_hint = None
    file_title_match = re.match(r"^([^\-]+?)\s*-\s*", clean_base.strip())
    if file_title_match:
        cand_show = file_title_match.group(1).strip()
        if cand_show and not re.match(r"^(?:season|s\d|ep?\d)", cand_show, re.IGNORECASE):
            show_hint = cand_show

    if not show_hint:
        dir_part = parent_dir
        if re.search(r"Season\s*\d+", dir_part, re.IGNORECASE):
            dir_part = os.path.basename(os.path.dirname(os.path.dirname(os.path.abspath(file_path))))
        cleaned_show = re.sub(r"\{[^\}]*\}|\([0-9]{4}\)|\[[^\]]*\]", "", dir_part).strip()
        if (
            cleaned_show
            and cleaned_show.lower() not in ("anime", "tv", "tv shows", "series", "cartoons", "media", "downloads")
        ):
            show_hint = cleaned_show

    return {
        "season": season,
        "episode": episode,
        "abs_episode": abs_episode,
        "show_hint": show_hint,
    }


def extract_mkv_subtitles(
    file_path: str,
    extract_timeout: Optional[int] = None,
    probe_timeout: Optional[int] = None,
) -> tuple[dict[str, list[dict]], bool]:
    """Extract supported subtitle tracks (jpn, eng, spa) from an MKV file.

    Probes embedded subtitle streams and extracts them using ffmpeg into temporary files,
    detecting and normalizing track languages.

    Args:
        file_path (str): Path to the MKV file.
        extract_timeout (Optional[int]): Subprocess timeout in seconds for subtitle extraction.
        probe_timeout (Optional[int]): Subprocess timeout in seconds for ffprobe.

    Returns:
        tuple[dict[str, list[dict]], bool]: A tuple containing:
            - dict[str, list[dict]]: Dictionary mapping language code to sentence dicts with
              'start_time', 'end_time', and 'text'.
            - bool: True if an extraction subprocess timed out, False otherwise.
    """
    probe_cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "s",
        "-show_entries",
        "stream=index,codec_name:stream_tags=language,title",
        "-of",
        "json",
        file_path,
    ]
    result = subprocess.run(probe_cmd, capture_output=True, text=True, timeout=probe_timeout)
    if result.returncode != 0:
        print(f"ffprobe failed for {file_path}: {result.stderr}")
        return {}, False

    streams = json.loads(result.stdout).get("streams", [])
    if not streams:
        return {}, False

    eng_streams, spa_streams, jpn_streams, unk_streams = [], [], [], []
    for stream in streams:
        codec = (stream.get("codec_name") or "").lower()
        if codec in BITMAP_SUBTITLE_CODECS:
            continue
        lang = stream.get("tags", {}).get("language", "unknown").lower()
        if lang == "eng":
            eng_streams.append(stream)
        elif lang == "spa":
            spa_streams.append(stream)
        elif lang == "jpn":
            jpn_streams.append(stream)
        elif lang in ["unknown", "und", ""]:
            unk_streams.append(stream)

    def select_best_stream(stream_list, is_spanish=False):
        """Select the highest priority subtitle stream from a candidate list."""
        if not stream_list:
            return None
        clean = [
            s
            for s in stream_list
            if not any(
                x in s.get("tags", {}).get("title", "").lower() for x in ["forced", "sdh", "dubtitle", "signs"]
            )
        ]
        pool = clean if clean else stream_list
        if is_spanish:
            for s in pool:
                if any(x in s.get("tags", {}).get("title", "").lower() for x in ["latin", "latam"]):
                    return s
        return pool[0]

    selected_streams = []
    best_eng = select_best_stream(eng_streams, is_spanish=False)
    best_spa = select_best_stream(spa_streams, is_spanish=True)
    best_jpn = select_best_stream(jpn_streams, is_spanish=False)

    if best_eng:
        selected_streams.append(best_eng)
    if best_spa:
        selected_streams.append(best_spa)
    if best_jpn:
        selected_streams.append(best_jpn)
    selected_streams.extend(unk_streams)

    extracted_subs = []
    had_timeout = False
    temp_paths_to_clean = []
    try:
        stream_targets = []
        for stream in selected_streams:
            i = stream.get("index")
            tags = stream.get("tags", {})
            lang = tags.get("language", "unknown").lower()

            fd, temp_sub_path = tempfile.mkstemp(suffix=".srt")
            os.close(fd)
            temp_paths_to_clean.append(temp_sub_path)
            stream_targets.append((temp_sub_path, lang, i))

        if stream_targets:
            ext_cmd = [
                "ffmpeg",
                "-y",
                "-seekable",
                "0",
                "-i",
                file_path,
            ]
            for temp_sub_path, lang, i in stream_targets:
                ext_cmd.extend(["-map", f"0:{i}", "-c:s", "srt", temp_sub_path])

            batch_timeout = (
                extract_timeout * len(stream_targets)
                if extract_timeout is not None
                else None
            )
            batch_started = time.monotonic()
            try:
                ext_res = subprocess.run(
                    ext_cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=batch_timeout,
                )
                if ext_res.returncode == 0:
                    for temp_sub_path, lang, i in stream_targets:
                        extracted_subs.append((temp_sub_path, lang, i))
                else:
                    for temp_sub_path, lang, i in stream_targets:
                        fallback_timeout = None
                        if extract_timeout is not None:
                            remaining_batch_time = (
                                extract_timeout * len(stream_targets)
                                - (time.monotonic() - batch_started)
                            )
                            if remaining_batch_time <= 0:
                                had_timeout = True
                                break
                            fallback_timeout = min(
                                extract_timeout,
                                remaining_batch_time,
                            )

                        fd, retry_sub_path = tempfile.mkstemp(suffix=".srt")
                        os.close(fd)
                        temp_paths_to_clean.append(retry_sub_path)

                        single_cmd = [
                            "ffmpeg",
                            "-y",
                            "-seekable",
                            "0",
                            "-i",
                            file_path,
                            "-map",
                            f"0:{i}",
                            "-c:s",
                            "srt",
                            retry_sub_path,
                        ]
                        try:
                            s_res = subprocess.run(
                                single_cmd,
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                                timeout=fallback_timeout,
                            )
                            if s_res.returncode == 0:
                                extracted_subs.append((retry_sub_path, lang, i))
                            elif os.path.exists(temp_sub_path) and os.path.getsize(temp_sub_path) > 0:
                                extracted_subs.append((temp_sub_path, lang, i))
                        except subprocess.TimeoutExpired:
                            print(f"Timed out extracting track {i} from {file_path}")
                            had_timeout = True
                            break
            except subprocess.TimeoutExpired:
                print(f"Timed out extracting subtitles from {file_path}")
                had_timeout = True

        subs_by_lang = {}
        if extracted_subs:
            seen_langs = set()
            for temp_sub_path, lang, i in extracted_subs:
                try:
                    subs = load_and_sanitize_subs(temp_sub_path)
                    if not subs or not any(line.plaintext.strip() for line in subs):
                        continue
                    final_lang = lang

                    detected_lang = detect_language(subs)
                    if final_lang == "jpn" and detected_lang == "eng":
                        final_lang = "eng"
                    elif final_lang == "spa" and detected_lang == "por":
                        final_lang = "por"

                    if final_lang in {"unknown", "und", ""}:
                        final_lang = detected_lang

                    if final_lang not in ["eng", "spa", "jpn"]:
                        continue

                    if final_lang in seen_langs:
                        continue

                    sentences = []
                    for line in subs:
                        text = line.plaintext.strip()
                        if text:
                            sentences.append({
                                "start_time": line.start / 1000.0,
                                "end_time": line.end / 1000.0,
                                "text": text,
                            })
                    if sentences:
                        subs_by_lang[final_lang] = sentences
                        seen_langs.add(final_lang)
                except Exception as parse_e:
                    print(f"Error parsing track {i} in {file_path}: {parse_e}")

        return subs_by_lang, had_timeout
    finally:
        for temp_sub_path in temp_paths_to_clean:
            if os.path.exists(temp_sub_path):
                os.remove(temp_sub_path)


def estimate_timestamp_offset(
    existing_sentences: list[dict],
    new_sentences: list[dict],
    max_shift: float = 30.0,
) -> float:
    """Estimate a global constant timestamp offset between existing and new sentences.

    Compares exact or near-identical text matches to determine if a uniform time shift exists
    between two different media releases (e.g., due to trimmed or added sponsor cards).

    Args:
        existing_sentences (list[dict]): Chronologically ordered list of existing sentence dicts.
        new_sentences (list[dict]): Chronologically ordered list of new sentence dicts.
        max_shift (float): Maximum plausible time shift in seconds to consider. Defaults to 30.0.

    Returns:
        float: The detected median timestamp offset (new_start - old_start), or 0.0 if negligible.
    """
    if not existing_sentences or not new_sentences:
        return 0.0

    ex_lookup = {}
    for ex in existing_sentences:
        t = ex.get("text", "").strip()
        if len(t) >= 8 and "start_time" in ex and ex["start_time"] is not None:
            ex_lookup.setdefault(t, []).append(ex["start_time"])

    candidate_diffs = []
    for new_s in new_sentences:
        t = new_s["text"].strip()
        if t in ex_lookup:
            for old_start in ex_lookup[t]:
                diff = new_s["start_time"] - old_start
                if abs(diff) <= max_shift:
                    candidate_diffs.append(diff)

    min_required = min(3, len(existing_sentences))
    if not candidate_diffs or len(candidate_diffs) < min_required:
        return 0.0

    candidate_diffs.sort()
    median_diff = candidate_diffs[len(candidate_diffs) // 2]

    consensus_count = sum(1 for d in candidate_diffs if abs(d - median_diff) <= 0.5)
    if consensus_count >= min_required and (consensus_count / len(candidate_diffs)) >= 0.4:
        if abs(median_diff) > 0.3:
            return round(median_diff, 3)

    return 0.0


def align_and_update_sentences(
    conn,
    media_id: int,
    language: str,
    new_sentences: list[dict],
    global_offset: float = 0.0,
) -> tuple[int, int, int]:
    """Align new sentences against existing sentences for a specific media_id and language.

    Uses a banded dynamic programming alignment algorithm with beam pruning to preserve
    existing sentence IDs across retimed, split, or merged lines.

    Args:
        conn (sqlite3.Connection): Database connection.
        media_id (int): Target media ID.
        language (str): Subtitle language code (e.g. 'jpn', 'eng', 'spa').
        new_sentences (list[dict]): List of new sentence dicts with 'start_time', 'end_time', 'text'.
        global_offset (float): Pre-estimated constant time offset between releases. Defaults to 0.0.

    Returns:
        tuple[int, int, int]: (updates_count, inserts_count, deletes_count)
    """
    query = (
        "SELECT id, language, start_time, end_time, text FROM sentences "
        "WHERE media_id = ? AND language = ? ORDER BY start_time, id"
    )
    existing = conn.execute(query, (media_id, language)).fetchall()
    existing_list = [dict(row) for row in existing]

    N = len(new_sentences)
    M = len(existing_list)

    if M == 0:
        inserts = [(media_id, language, s["start_time"], s["end_time"], s["text"]) for s in new_sentences]
        if inserts:
            conn.executemany(
                "INSERT INTO sentences (media_id, language, start_time, end_time, text) VALUES (?, ?, ?, ?, ?)",
                inserts,
            )
        return (0, len(inserts), 0)

    if N == 0:
        deletes = [(ex["id"],) for ex in existing_list]
        if deletes:
            conn.executemany("DELETE FROM sentences WHERE id=?", deletes)
        return (0, 0, len(deletes))

    adjusted_existing_times = [ex["start_time"] + global_offset for ex in existing_list]

    prev_dp = {}
    curr_dp = {0: (0.0, 0.0, 0.0)}

    back_ptr = [{} for _ in range(N + 1)]

    C_ins = (1.0, 0.0, 0.0)
    C_del = (1.0, 0.0, 0.0)

    for i in range(N + 1):
        if i > 0:
            prev_dp = curr_dp
            curr_dp = {}

        if i == 0:
            start_j, end_j = 0, M
        else:
            new_time = new_sentences[i - 1]["start_time"]
            start_j = bisect.bisect_left(adjusted_existing_times, new_time - 15.0)
            end_j = bisect.bisect_right(adjusted_existing_times, new_time + 15.0)
            if i > 1:
                prev_time = new_sentences[i - 2]["start_time"]
                start_j = min(start_j, bisect.bisect_left(adjusted_existing_times, prev_time - 15.0))
                end_j = max(end_j, bisect.bisect_right(adjusted_existing_times, prev_time + 15.0))

        running_min_norm = (float("inf"), float("inf"), float("inf"))
        running_min_k = None

        running_min_norm_ins = (float("inf"), float("inf"), float("inf"))
        running_min_k_ins = None

        for k, p_cost in prev_dp.items():
            norm = (p_cost[0] - k * C_del[0], p_cost[1] - k * C_del[1], p_cost[2] - k * C_del[2])
            if k <= start_j - 1:
                if norm < running_min_norm:
                    running_min_norm = norm
                    running_min_k = k
                if norm < running_min_norm_ins:
                    running_min_norm_ins = norm
                    running_min_k_ins = k

        curr_backs = {}

        for j in range(start_j, end_j + 1):
            if i == 0 and j == 0:
                continue

            best_cost = (float("inf"), float("inf"), float("inf"))
            best_back = None

            if i > 0:
                if j in prev_dp:
                    k = j
                    p_cost = prev_dp[k]
                    norm = (p_cost[0] - k * C_del[0], p_cost[1] - k * C_del[1], p_cost[2] - k * C_del[2])
                    if norm < running_min_norm_ins:
                        running_min_norm_ins = norm
                        running_min_k_ins = k

                if running_min_k_ins is not None:
                    ins_cost = (
                        running_min_norm_ins[0] + j * C_del[0] + C_ins[0],
                        running_min_norm_ins[1] + j * C_del[1] + C_ins[1],
                        running_min_norm_ins[2] + j * C_del[2] + C_ins[2],
                    )
                    if ins_cost < best_cost:
                        best_cost = ins_cost
                        best_back = (1, running_min_k_ins)

            if i > 0 and j > 0:
                if (j - 1) in prev_dp:
                    k = j - 1
                    p_cost = prev_dp[k]
                    norm = (p_cost[0] - k * C_del[0], p_cost[1] - k * C_del[1], p_cost[2] - k * C_del[2])
                    if norm < running_min_norm:
                        running_min_norm = norm
                        running_min_k = k

                ex = existing_list[j - 1]
                new_s = new_sentences[i - 1]
                dist_start = abs((ex["start_time"] + global_offset) - new_s["start_time"])

                if dist_start <= REFRESH_THRESHOLD_SECONDS:
                    dist_end = min(abs((ex["end_time"] + global_offset) - new_s["end_time"]), REFRESH_THRESHOLD_SECONDS)

                    text_ratio = difflib.SequenceMatcher(None, ex["text"], new_s["text"]).ratio()
                    text_penalty = 1.0 - text_ratio

                    if running_min_k is not None:
                        best_k_cost = (
                            running_min_norm[0] + (j - 1) * C_del[0],
                            running_min_norm[1] + (j - 1) * C_del[1],
                            running_min_norm[2] + (j - 1) * C_del[2],
                        )
                        best_k = running_min_k

                        match_cost = (
                            best_k_cost[0],
                            best_k_cost[1] + dist_start + dist_end * 0.1,
                            best_k_cost[2] + text_penalty,
                        )

                        if match_cost < best_cost:
                            best_cost = match_cost
                            best_back = (0, best_k)

            if j > 0 and (j - 1) in curr_dp:
                prev_cost = curr_dp[j - 1]
                del_cost = (prev_cost[0] + C_del[0], prev_cost[1] + C_del[1], prev_cost[2] + C_del[2])
                if del_cost < best_cost:
                    best_cost = del_cost
                    prev_back = curr_backs.get(j - 1)
                    if isinstance(prev_back, tuple) and prev_back[0] == 2:
                        best_back = prev_back
                    else:
                        best_back = (2, j - 1, prev_back)

            if best_cost[0] != float("inf"):
                curr_dp[j] = best_cost
                curr_backs[j] = best_back

        if len(curr_dp) > 300:
            def pruning_key_high(item):
                """Pruning key prioritizing highest progress states."""
                j_idx, cost = item
                return (cost[0] - j_idx * C_del[0], cost[1] - j_idx * C_del[1], cost[2] - j_idx * C_del[2], -j_idx)

            def pruning_key_low(item):
                """Pruning key prioritizing lower progress states."""
                j_idx, cost = item
                return (cost[0] - j_idx * C_del[0], cost[1] - j_idx * C_del[1], cost[2] - j_idx * C_del[2], j_idx)

            best_items = dict(sorted(curr_dp.items(), key=pruning_key_high)[:150])
            best_items.update(dict(sorted(curr_dp.items(), key=pruning_key_low)[:150]))

            if 0 in curr_dp and 0 not in best_items:
                best_items[0] = curr_dp[0]

            curr_dp = dict(best_items)

        back_ptr[i] = {k: curr_backs[k] for k in curr_dp if k in curr_backs}

    if M > 0 and not curr_dp:
        print(f"Alignment notice: Could not align subtitle sentences for language {language}.")
        return (0, 0, 0)

    if M not in back_ptr[N]:
        j = max(curr_dp.keys()) if curr_dp else 0
        i = N
    else:
        i, j = N, M

    matches = {}

    while i > 0 or j > 0:
        if j not in back_ptr[i]:
            if i > 0:
                i -= 1
            else:
                j -= 1
            continue

        b = back_ptr[i][j]
        if isinstance(b, tuple):
            if b[0] == 0:
                matches[i - 1] = j - 1
                i -= 1
                j = b[1]
            elif b[0] == 1:
                i -= 1
                j = b[1]
            elif b[0] == 2:
                origin_j, prev_back = b[1], b[2]
                if isinstance(prev_back, tuple):
                    if prev_back[0] == 0:
                        matches[i - 1] = origin_j - 1
                        i -= 1
                        j = prev_back[1]
                    elif prev_back[0] == 1:
                        i -= 1
                        j = prev_back[1]
                    else:
                        j -= 1
                else:
                    j -= 1
        else:
            j -= 1

    updates = []
    inserts = []
    matched_existing_indices = set(matches.values())

    for idx, new_s in enumerate(new_sentences):
        if idx in matches:
            ex = existing_list[matches[idx]]
            updates.append((language, new_s["start_time"], new_s["end_time"], new_s["text"], ex["id"]))
        else:
            inserts.append((media_id, language, new_s["start_time"], new_s["end_time"], new_s["text"]))

    deletes = [(existing_list[k]["id"],) for k in range(M) if k not in matched_existing_indices]

    if updates:
        conn.executemany("UPDATE sentences SET language=?, start_time=?, end_time=?, text=? WHERE id=?", updates)
    if inserts:
        conn.executemany(
            "INSERT INTO sentences (media_id, language, start_time, end_time, text) VALUES (?, ?, ?, ?, ?)",
            inserts,
        )
    if deletes:
        conn.executemany("DELETE FROM sentences WHERE id=?", deletes)

    return (len(updates), len(inserts), len(deletes))


def find_matching_media(
    conn,
    new_file_path: str,
    media_type: str = "mkv_embedded",
    missing_media_rows: Optional[list] = None,
    sample_sentences: Optional[list[str]] = None,
) -> Optional[int]:
    """Find a missing media record in the database that corresponds to an upgraded file.

    Evaluates candidates in order of priority:
    1. Metadata match: (show_title, season, episode).
    2. File identifier match: Season and episode or absolute episode within the same show/directory.
    3. Subtitle content fingerprinting: Shared distinctive sentences with missing candidate rows.

    Args:
        conn (sqlite3.Connection): Database connection.
        new_file_path (str): Path to the new media file.
        media_type (str): Expected media type ('mkv_embedded' or 'subtitle'). Defaults to 'mkv_embedded'.
        missing_media_rows (Optional[list]): Pre-filtered list of missing media rows from DB.
        sample_sentences (Optional[list[str]]): Sample subtitle text strings for fingerprinting.

    Returns:
        Optional[int]: The matching media ID, or None if no match is found.
    """
    if missing_media_rows is None:
        rows = conn.execute(
            "SELECT id, path, type, show_title, season, episode, episode_title FROM media WHERE type = ?",
            (media_type,),
        ).fetchall()
        missing_media_rows = [dict(r) for r in rows if not os.path.exists(r["path"])]

    candidates = [c for c in missing_media_rows if c.get("type") == media_type]
    if not candidates:
        return None

    show_title, season, episode, _ = get_plex_metadata(new_file_path)
    file_ids = parse_media_identifiers(new_file_path)

    if season is None:
        season = file_ids["season"]
    if episode is None:
        episode = file_ids["episode"]
    abs_ep = file_ids["abs_episode"]
    show_hint = show_title or file_ids["show_hint"]

    # Priority 1: Match on season and episode
    if season is not None and episode is not None:
        matched_se = [c for c in candidates if c.get("season") == season and c.get("episode") == episode]
        if len(matched_se) == 1:
            return matched_se[0]["id"]
        elif len(matched_se) > 1 and show_hint:
            norm_hint = re.sub(r"[^\w]", "", show_hint.lower())
            matched_show = [
                c for c in matched_se
                if c.get("show_title") and norm_hint in re.sub(r"[^\w]", "", c["show_title"].lower())
            ]
            if len(matched_show) == 1:
                return matched_show[0]["id"]

    # Priority 2: Check matching folder/parent folder for same episode or absolute episode
    new_abs = os.path.abspath(new_file_path)
    new_dir = os.path.dirname(new_abs)
    dir_candidates = [
        c for c in candidates
        if os.path.dirname(os.path.abspath(c["path"])) == new_dir
        or os.path.dirname(os.path.dirname(os.path.abspath(c["path"]))) == os.path.dirname(new_dir)
    ]

    if abs_ep is not None:
        matched_abs = []
        for c in dir_candidates:
            c_ids = parse_media_identifiers(c["path"])
            if c_ids.get("abs_episode") == abs_ep:
                matched_abs.append(c)
        if len(matched_abs) == 1:
            return matched_abs[0]["id"]

    if episode is not None:
        matched_ep = []
        for c in dir_candidates:
            c_ids = parse_media_identifiers(c["path"])
            if c_ids.get("episode") == episode and (season is None or c_ids.get("season") == season):
                matched_ep.append(c)
        if len(matched_ep) == 1:
            return matched_ep[0]["id"]

    # Priority 3: Subtitle content fingerprinting
    if sample_sentences and candidates:
        clean_samples = [s.strip() for s in sample_sentences if len(s.strip()) >= 10][:5]
        candidate_ids = [c["id"] for c in candidates]
        if clean_samples and candidate_ids:
            ph_ids = ",".join("?" for _ in candidate_ids)
            ph_texts = ",".join("?" for _ in clean_samples)
            query = f"""
                SELECT media_id, COUNT(*) as match_count
                FROM sentences
                WHERE media_id IN ({ph_ids}) AND text IN ({ph_texts})
                GROUP BY media_id
                ORDER BY match_count DESC
                LIMIT 1
            """
            row = conn.execute(query, candidate_ids + clean_samples).fetchone()
            if row and row["match_count"] >= 2:
                return row["media_id"]

    return None


def refresh_media(
    conn,
    media_id: int,
    new_file_path: str,
    extract_timeout: Optional[int] = None,
    probe_timeout: Optional[int] = None,
) -> bool:
    """Refresh a media record with a new or upgraded file (MKV or standalone subtitle).

    Extracts subtitles from the new file, performs per-language DP alignment against existing
    sentences for that media_id to preserve sentence IDs, and updates media.path and metadata.

    Args:
        conn (sqlite3.Connection): Database connection.
        media_id (int): ID of the media record to refresh.
        new_file_path (str): Path to the replacement file.
        extract_timeout (Optional[int]): Timeout for subtitle extraction.
        probe_timeout (Optional[int]): Timeout for ffprobe.

    Returns:
        bool: True if refresh succeeded, False otherwise.
    """
    abs_path = os.path.abspath(new_file_path)
    row = conn.execute(
        "SELECT id, path, type, show_title, season, episode, episode_title FROM media WHERE id = ?",
        (media_id,),
    ).fetchone()
    if not row:
        print(f"Media ID {media_id} not found in database.")
        return False

    if abs_path.lower().endswith(".mkv"):
        subs_by_lang, had_timeout = extract_mkv_subtitles(
            abs_path,
            extract_timeout=extract_timeout,
            probe_timeout=probe_timeout,
        )
        if had_timeout:
            print(f"Timed out extracting subtitles from {abs_path}")
            return False

        total_updates = total_inserts = total_deletes = 0
        if subs_by_lang:
            for lang, new_sentences in subs_by_lang.items():
                existing = conn.execute(
                    "SELECT start_time, text FROM sentences WHERE media_id = ? AND language = ? ORDER BY start_time",
                    (media_id, lang),
                ).fetchall()
                offset = (
                    estimate_timestamp_offset([dict(r) for r in existing], new_sentences)
                    if existing
                    else 0.0
                )
                up, ins, d = align_and_update_sentences(
                    conn, media_id, lang, new_sentences, global_offset=offset
                )
                total_updates += up
                total_inserts += ins
                total_deletes += d

            print(
                f"Refreshed MKV media {media_id}: {total_updates} updated, "
                f"{total_inserts} inserted, {total_deletes} deleted across {len(subs_by_lang)} languages."
            )
        else:
            print(f"Refreshed MKV media {media_id} path (no subtitle tracks to align).")

    elif abs_path.lower().endswith((".ass", ".srt")):
        subs = None
        for enc in ["utf-8-sig", "utf-8", "utf-16", "cp932", "shift_jis", "latin-1"]:
            try:
                subs = load_and_sanitize_subs(abs_path, encoding=enc)
                break
            except (IOError, OSError) as e:
                print(f"Error accessing file {abs_path}: {e}")
                return False
            except Exception:
                continue

        if not subs:
            print(f"Failed to read or parse subtitle file {abs_path}.")
            return False

        new_sentences = []
        for line in subs:
            text = line.plaintext.strip()
            if text:
                new_sentences.append({
                    "start_time": line.start / 1000.0,
                    "end_time": line.end / 1000.0,
                    "text": text,
                })
        new_sentences.sort(key=lambda s: s["start_time"])

        if not new_sentences:
            print("Aborting refresh: no valid sentences found in subtitle file.")
            return False

        existing_lang_row = conn.execute(
            "SELECT language FROM sentences WHERE media_id = ? LIMIT 1",
            (media_id,),
        ).fetchone()
        lang = existing_lang_row["language"] if existing_lang_row else detect_language(subs)

        existing = conn.execute(
            "SELECT start_time, text FROM sentences WHERE media_id = ? AND language = ? ORDER BY start_time",
            (media_id, lang),
        ).fetchall()
        offset = (
            estimate_timestamp_offset([dict(r) for r in existing], new_sentences)
            if existing
            else 0.0
        )
        up, ins, d = align_and_update_sentences(
            conn, media_id, lang, new_sentences, global_offset=offset
        )
        print(f"Refreshed subtitle media {media_id} [{lang}]: {up} updated, {ins} inserted, {d} deleted.")

    else:
        print(f"Unsupported file format for refresh: {abs_path}")
        return False

    show_title, season, episode, episode_title = get_plex_metadata(abs_path)
    update_media_path(conn, media_id, abs_path, show_title, season, episode, episode_title)
    conn.commit()
    return True


def refresh_file(
    file_path: str,
    old_path: Optional[str] = None,
    media_id: Optional[int] = None,
    extract_timeout: Optional[int] = None,
    probe_timeout: Optional[int] = None,
) -> bool:
    """Smart refresh an existing subtitle or media file, mapping new sentences to preserve IDs.

    Supports single MKV or subtitle files as well as directory paths.

    Args:
        file_path (str): Path to the new file or directory.
        old_path (Optional[str]): Optional path to the old file being replaced.
        media_id (Optional[int]): Optional explicit media ID to target.
        extract_timeout (Optional[int]): Timeout for subtitle extraction.
        probe_timeout (Optional[int]): Timeout for ffprobe.

    Returns:
        bool: True if refresh succeeded, False otherwise.
    """
    conn = get_db()
    abs_path = os.path.abspath(file_path)

    if os.path.isdir(abs_path):
        refreshed_any = False
        for root, _, files in os.walk(abs_path):
            for file in files:
                if file.startswith("._") or not file.lower().endswith((".mkv", ".srt", ".ass")):
                    continue
                sub_path = os.path.join(root, file)
                if refresh_file(sub_path, extract_timeout=extract_timeout, probe_timeout=probe_timeout):
                    refreshed_any = True
        return refreshed_any

    target_id = media_id
    if target_id is None and old_path:
        old_abs = os.path.abspath(old_path)
        row = conn.execute("SELECT id FROM media WHERE path = ?", (old_abs,)).fetchone()
        if row:
            target_id = row["id"]

    if target_id is None:
        row = conn.execute("SELECT id FROM media WHERE path = ?", (abs_path,)).fetchone()
        if row:
            target_id = row["id"]
        else:
            media_type = "mkv_embedded" if abs_path.lower().endswith(".mkv") else "subtitle"
            target_id = find_matching_media(conn, abs_path, media_type=media_type)

    if target_id is None:
        print(f"File '{abs_path}' not found in database and no matching media could be identified.")
        return False

    return refresh_media(
        conn,
        target_id,
        abs_path,
        extract_timeout=extract_timeout,
        probe_timeout=probe_timeout,
    )
