"""Module for indexing media files and subtitles."""

import json
import os
import re
import subprocess
import tempfile

import pysubs2
from dotenv import load_dotenv

from .db import add_media, add_sentences, get_db

load_dotenv()

REFRESH_THRESHOLD_SECONDS = 2.0


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
    plex = _get_plex()
    if not plex or _plex_cache_built:
        return
    print("Building Plex path mapping cache (this may take a moment)...")
    try:
        allowed_libraries = None
        if os.path.exists("config.json"):
            with open("config.json", "r") as f:
                try:
                    config = json.load(f)
                    allowed_libraries = config.get("plex_libraries")
                except Exception as e:
                    print(f"Error reading config.json for Plex libraries: {e}")

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
    cursor = conn.execute("SELECT id, path FROM media")
    pruned_count = 0
    for row in cursor.fetchall():
        try:
            os.stat(row["path"])
        except OSError as e:
            if e.errno in (errno.ENOENT, errno.ENOTDIR):
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


def index_directory(directory_path: str):
    """Scan and index all subtitle and MKV files in a directory.

    Args:
        directory_path (str): Path to the directory to be indexed.
    """
    build_plex_cache()
    conn = get_db()

    # Clean up missing files that fall under the directory being indexed
    abs_dir = os.path.abspath(directory_path)
    like_pattern = abs_dir if abs_dir.endswith(os.sep) else f"{abs_dir}{os.sep}"
    like_pattern = like_pattern.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"

    cursor = conn.execute("SELECT id, path FROM media WHERE path LIKE ? ESCAPE '\\'", (like_pattern,))
    for row in cursor.fetchall():
        if not os.path.exists(row["path"]):
            print(f"Removing deleted file from database: {row['path']}")
            conn.execute("DELETE FROM sentences WHERE media_id = ?", (row["id"],))
            conn.execute("DELETE FROM media WHERE id = ?", (row["id"],))
    conn.commit()

    directory_path = abs_dir
    for root, _, files in os.walk(directory_path):
        for file in files:
            if file.startswith("._"):
                continue

            file_path = os.path.join(root, file)

            # Incremental indexing: skip if already in DB
            row = conn.execute("SELECT id, show_title, episode_title FROM media WHERE path = ?", (file_path,)).fetchone()
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

            if file.endswith((".ass", ".srt")):
                try:
                    subs = None
                    for enc in ["utf-8-sig", "utf-8", "utf-16", "shift_jis", "latin-1"]:
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
                    probe_cmd = [
                        "ffprobe",
                        "-v",
                        "error",
                        "-select_streams",
                        "s",
                        "-show_entries",
                        "stream=index:stream_tags=language,title",
                        "-of",
                        "json",
                        file_path,
                    ]
                    result = subprocess.run(probe_cmd, capture_output=True, text=True, timeout=60)
                    if result.returncode != 0:
                        print(f"ffprobe failed for {file_path}: {result.stderr}")
                        continue

                    streams = json.loads(result.stdout).get("streams", [])
                    if not streams:
                        # We process the mkv, so add it to the media table once to mark it as indexed even if tracks fail
                        show_title, season, episode, episode_title = get_plex_metadata(file_path)
                        add_media(
                            conn,
                            file_path,
                            "mkv_embedded",
                            show_title,
                            season,
                            episode,
                            episode_title,
                        )
                        conn.commit()
                        continue

                    eng_streams, spa_streams, jpn_streams, unk_streams = [], [], [], []
                    for stream in streams:
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
                    processed_any = False
                    had_timeout = False
                    temp_paths_to_clean = []
                    try:
                        for stream in selected_streams:
                            i = stream.get("index")
                            tags = stream.get("tags", {})
                            lang = tags.get("language", "unknown").lower()

                            fd, temp_sub_path = tempfile.mkstemp(suffix=".srt")
                            os.close(fd)
                            temp_paths_to_clean.append(temp_sub_path)

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
                            try:
                                ext_res = subprocess.run(
                                    ext_cmd,
                                    stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL,
                                    timeout=600,
                                )
                            except subprocess.TimeoutExpired:
                                if os.path.exists(temp_sub_path):
                                    os.remove(temp_sub_path)
                                temp_paths_to_clean.remove(temp_sub_path)
                                print(f"Timed out extracting track {i} from {file_path}")
                                had_timeout = True
                                continue

                            if ext_res.returncode == 0:
                                extracted_subs.append((temp_sub_path, lang, i))

                        if extracted_subs:
                            seen_langs = set()
                            for temp_sub_path, lang, i in extracted_subs:
                                try:
                                    subs = load_and_sanitize_subs(temp_sub_path)
                                    final_lang = lang

                                    detected_lang = detect_language(subs)
                                    # Verify Japanese tracks actually contain Japanese text (Anime dual-audio mistagging)
                                    if final_lang == "jpn" and detected_lang == "eng":
                                        final_lang = "eng"
                                    elif final_lang == "spa" and detected_lang == "por":
                                        final_lang = "por"

                                    if final_lang in {"unknown", "und", ""}:
                                        final_lang = detected_lang

                                    if final_lang not in ["eng", "spa", "jpn"]:
                                        continue  # Skip if the heuristic found it to be an unwanted language

                                    if final_lang in seen_langs:
                                        continue
                                    process_subs(conn, file_path, subs, "mkv_embedded", language=final_lang)
                                    seen_langs.add(final_lang)
                                    processed_any = True
                                except Exception as parse_e:
                                    print(f"Error parsing track {i} in {file_path}: {parse_e}")

                        if had_timeout:
                            conn.rollback()
                        else:
                            if not processed_any:
                                # Ensure the media is still added even if all subtitles were skipped
                                show_title, season, episode, episode_title = get_plex_metadata(file_path)
                                add_media(
                                    conn,
                                    file_path,
                                    "mkv_embedded",
                                    show_title,
                                    season,
                                    episode,
                                    episode_title,
                                )
                            conn.commit()
                    finally:
                        for temp_sub_path in temp_paths_to_clean:
                            if os.path.exists(temp_sub_path):
                                os.remove(temp_sub_path)

                except Exception as e:
                    print(f"Error extracting from {file_path}: {e}")

    conn.commit()


def refresh_file(file_path: str):
    """Smart refresh an existing subtitle file, mapping new sentences to old ones to preserve IDs."""
    conn = get_db()
    abs_path = os.path.abspath(file_path)

    row = conn.execute("SELECT id FROM media WHERE path = ?", (abs_path,)).fetchone()
    if not row:
        print(f"File '{abs_path}' not found in database. Please run 'hagi index' instead.")
        return False
    media_id = row["id"]

    if not abs_path.lower().endswith((".ass", ".srt")):
        print("Refresh is currently only supported for standalone subtitle files (.srt, .ass).")
        return False

    subs = None
    for enc in ["utf-8-sig", "utf-8", "utf-16", "shift_jis", "latin-1"]:
        try:
            subs = load_and_sanitize_subs(abs_path, encoding=enc)
            break
        except UnicodeError:
            continue
        except Exception as e:
            print(f"Error parsing subtitle file {abs_path}: {e}")
            return False

    if subs is None:
        print(f"Failed to read subtitle file {abs_path} with known encodings.")
        return False

    # Parse new sentences
    new_sentences = []
    for line in subs:
        text = line.plaintext.strip()
        if text:
            new_sentences.append({"start_time": line.start / 1000.0, "end_time": line.end / 1000.0, "text": text})

    if not new_sentences:
        print("Aborting refresh: no valid sentences found in the subtitle file.")
        return False

    new_sentences.sort(key=lambda s: (s["start_time"], s["end_time"]))

    # Fetch existing sentences (chronologically ordered for the monotonic DP alignment)
    existing = conn.execute(
        "SELECT id, language, start_time, end_time, text FROM sentences WHERE media_id = ? ORDER BY start_time, end_time, id",
        (media_id,),
    ).fetchall()

    existing_list = [dict(row) for row in existing]

    N = len(new_sentences)
    M = len(existing_list)

    # We use a banded dynamic programming approach to find the optimal monotonic alignment
    # between the new and existing sentences. A full N x M grid would use quadratic memory,
    # so we restrict the search window (j) around the current index (i) based on time.
    # Since subtitles use absolute time, matching lines must have similar timestamps regardless
    # of how many lines were inserted or deleted before them.
    import bisect
    import difflib

    existing_times = [ex["start_time"] for ex in existing_list]

    prev_dp = {}
    curr_dp = {0: (0.0, 0.0)}

    back_ptr = [{} for _ in range(N + 1)]

    C_ins = (1000.0, 0.0)
    C_del = (1000.0, 0.0)

    for i in range(N + 1):
        if i > 0:
            prev_dp = curr_dp
            curr_dp = {}

        if i == 0:
            start_j, end_j = 0, M
        else:
            new_time = new_sentences[i - 1]["start_time"]
            # Search within a generous 15-second time band to route around massive insertions/deletions
            start_j = bisect.bisect_left(existing_times, new_time - 15.0)
            end_j = bisect.bisect_right(existing_times, new_time + 15.0)
            # Ensure the window includes the previous row's boundaries to keep the DP graph connected
            if i > 1:
                prev_time = new_sentences[i - 2]["start_time"]
                start_j = min(start_j, bisect.bisect_left(existing_times, prev_time - 15.0))
                end_j = max(end_j, bisect.bisect_right(existing_times, prev_time + 15.0))

        # Maintain a running minimum of (prev_cost - k * C_del) for eligible k <= j - 1
        # This reduces the predecessor search from O(W^2) to O(W).
        running_min_norm = (float("inf"), float("inf"))
        running_min_k = None
        
        # We also need a running minimum up to k <= j for insertions
        running_min_norm_ins = (float("inf"), float("inf"))
        running_min_k_ins = None
        
        # Initialize running minimums
        for k, p_cost in prev_dp.items():
            norm = (p_cost[0] - k * C_del[0], p_cost[1] - k * C_del[1])
            if k <= start_j - 1:
                if norm < running_min_norm:
                    running_min_norm = norm
                    running_min_k = k
                if norm < running_min_norm_ins:
                    running_min_norm_ins = norm
                    running_min_k_ins = k

        curr_backs = {}

        # Only iterate over the time-based window to keep memory and time linear
        for j in range(start_j, end_j + 1):
            if i == 0 and j == 0:
                continue

            best_cost = (float("inf"), float("inf"))
            best_back = None
            
            if i > 0:
                # Update insertion running minimum with k = j
                if j in prev_dp:
                    k = j
                    p_cost = prev_dp[k]
                    norm = (p_cost[0] - k * C_del[0], p_cost[1] - k * C_del[1])
                    if norm < running_min_norm_ins:
                        running_min_norm_ins = norm
                        running_min_k_ins = k

                # 2. Insert new_s[i-1] (skip new)
                # We can jump from any retained k <= j in prev_dp and then insert
                if running_min_k_ins is not None:
                    ins_cost = (
                        running_min_norm_ins[0] + j * C_del[0] + C_ins[0],
                        running_min_norm_ins[1] + j * C_del[1] + C_ins[1]
                    )
                    if ins_cost < best_cost:
                        best_cost = ins_cost
                        best_back = 1

            # 1. Match new_s[i-1] with existing[j-1]
            if i > 0 and j > 0:
                # Add newly eligible k = j - 1 to running minimum
                if (j - 1) in prev_dp:
                    k = j - 1
                    p_cost = prev_dp[k]
                    norm = (p_cost[0] - k * C_del[0], p_cost[1] - k * C_del[1])
                    if norm < running_min_norm:
                        running_min_norm = norm
                        running_min_k = k

                ex = existing_list[j - 1]
                new_s = new_sentences[i - 1]
                dist_start = abs(ex["start_time"] - new_s["start_time"])

                if dist_start <= REFRESH_THRESHOLD_SECONDS:
                    dist_end = min(abs(ex["end_time"] - new_s["end_time"]), REFRESH_THRESHOLD_SECONDS)

                    text_ratio = difflib.SequenceMatcher(None, ex["text"], new_s["text"]).ratio()
                    text_penalty = 1.0 - text_ratio

                    # Recover the actual jump cost from the normalized minimum
                    if running_min_k is not None:
                        best_k_cost = (
                            running_min_norm[0] + (j - 1) * C_del[0],
                            running_min_norm[1] + (j - 1) * C_del[1]
                        )
                        best_k = running_min_k
                        
                        match_cost = (best_k_cost[0] + dist_start + dist_end * 0.1 + text_penalty * 20.0, best_k_cost[1] + text_penalty)

                        if match_cost < best_cost:
                            best_cost = match_cost
                            best_back = (0, best_k)

            # 3. Delete existing[j-1] (skip old)
            if j > 0 and (j - 1) in curr_dp:
                prev_cost = curr_dp[j - 1]
                del_cost = (prev_cost[0] + C_del[0], prev_cost[1] + C_del[1])
                if del_cost < best_cost:
                    best_cost = del_cost
                    best_back = 2

            if best_cost[0] != float("inf"):
                curr_dp[j] = best_cost
                curr_backs[j] = best_back

        # Prune the state space to a strictly bounded beam width to prevent O(NxM) memory
        # We normalize the pruning key by subtracting `j * C_del` (the baseline deletion cost)
        # and we break ties by favoring advanced states (larger j) for connectivity.
        if len(curr_dp) > 300:
            def pruning_key(item):
                j_idx, cost = item
                return (cost[0] - j_idx * C_del[0], cost[1] - j_idx * C_del[1], -j_idx)
            
            best_items = sorted(curr_dp.items(), key=pruning_key)[:300]
            if 0 in curr_dp and 0 not in dict(best_items):
                best_items.append((0, curr_dp[0]))
                
            curr_dp = dict(best_items)
            
        # Only store back_ptr for the surviving states to enforce strict O(N * BeamWidth) memory
        back_ptr[i] = {k: curr_backs[k] for k in curr_dp if k in curr_backs}

    if M > 0 and not curr_dp:
        print("Refresh aborted: Could not align subtitle sentences (no valid paths).")
        return False

    # If the exact end state wasn't reached due to the window size,
    # find the closest reached state at the boundaries to backtrack from.
    if M not in back_ptr[N]:
        j = max(curr_dp.keys()) if curr_dp else 0
        i = N
    else:
        i, j = N, M

    matches = {}

    # Backtrack through the sparse matrix to recover the actual mapping from new -> old.
    while i > 0 or j > 0:
        if j not in back_ptr[i]:
            if i > 0:
                i -= 1
            else:
                j -= 1
            continue

        b = back_ptr[i][j]
        if isinstance(b, tuple) and b[0] == 0:
            matches[i - 1] = j - 1
            i -= 1
            j = b[1]
        elif b == 1:
            i -= 1
        else:
            j -= 1

    updates = []
    inserts = []
    matched_existing_indices = set(matches.values())

    stored_lang = existing_list[0]["language"] if existing_list else detect_language(subs)

    for idx, new_s in enumerate(new_sentences):
        if idx in matches:
            ex = existing_list[matches[idx]]
            updates.append((ex["language"], new_s["start_time"], new_s["end_time"], new_s["text"], ex["id"]))
        else:
            inserts.append((media_id, stored_lang, new_s["start_time"], new_s["end_time"], new_s["text"]))

    deletes = [(existing_list[k]["id"],) for k in range(M) if k not in matched_existing_indices]

    if updates:
        conn.executemany("UPDATE sentences SET language=?, start_time=?, end_time=?, text=? WHERE id=?", updates)
    if inserts:
        conn.executemany("INSERT INTO sentences (media_id, language, start_time, end_time, text) VALUES (?, ?, ?, ?, ?)", inserts)
    if deletes:
        conn.executemany("DELETE FROM sentences WHERE id=?", deletes)
        print(f"Deleted the following unmatched sentence IDs: {[d[0] for d in deletes]}")

    conn.commit()
    print(f"Refresh complete: {len(updates)} updated, {len(inserts)} inserted, {len(deletes)} deleted.")
    return True
