"""Database lifecycle orchestration: indexing, refreshing, and pruning media."""

import os
import sys
from typing import Optional




def _get_indexer():
    """Return the parent hagi.indexer module to support dynamic mock patching."""
    import hagi.indexer
    return sys.modules.get("hagi.indexer") or hagi.indexer


def process_subs(conn, file_path: str, subs, media_type: str = "subtitle", language: str = "unknown") -> None:
    """Process subtitles and add them to the database.

    Args:
        conn: Database connection.
        file_path (str): Path to the subtitle file.
        subs: Parsed subtitles object.
        media_type (str, optional): Type of the media. Defaults to "subtitle".
        language (str, optional): Language of the subtitles. Defaults to "unknown".
    """
    idx = _get_indexer()
    show_title, season, episode, episode_title = idx.get_plex_metadata(file_path)
    media_id = idx.add_media(conn, file_path, media_type, show_title, season, episode, episode_title)
    sentences = []

    for line in subs:
        text = line.plaintext.strip()
        if text:
            sentences.append((language, line.start / 1000.0, line.end / 1000.0, text))

    if sentences:
        idx.add_sentences(conn, media_id, sentences)
        print(f"Indexed: {file_path} [{language}] ({len(sentences)} lines)")


def _infer_media_type(row) -> str:
    """Return the stored media type, or infer it from the file extension."""
    return row["type"] or ("mkv_embedded" if row["path"].lower().endswith(".mkv") else "subtitle")


def prune_database() -> None:
    """Verify all media in the database and remove missing files globally."""
    idx = _get_indexer()
    conn = idx.get_db()
    extract_timeout, probe_timeout = idx._resolve_timeouts()
    cursor = conn.execute("SELECT id, path, type, show_title, season, episode, episode_title FROM media")
    pruned_count = 0
    missing_media: dict[int, dict] = {}
    for row in cursor.fetchall():
        if idx.is_missing_file(row["path"]):
            row_dict = dict(row)
            row_dict["type"] = _infer_media_type(row)
            missing_media[row["id"]] = row_dict

    matched_cands: dict[str, Optional[int]] = {}
    for row_id, row in list(missing_media.items()):
        if row_id not in missing_media:
            continue
        parent_dir = os.path.dirname(row["path"])
        upgraded = False
        matched_failed = False
        if os.path.isdir(parent_dir):
            media_type = row["type"]
            cand_exts = (".mkv",) if media_type == "mkv_embedded" else (".srt", ".ass")
            try:
                for entry in os.scandir(parent_dir):
                    if entry.is_file() and entry.name.lower().endswith(cand_exts):
                        cand_path = entry.path
                        if not conn.execute("SELECT 1 FROM media WHERE path = ?", (cand_path,)).fetchone():
                            if cand_path not in matched_cands:
                                cand_missing = [m for m in missing_media.values() if m["type"] == media_type]
                                matched_cands[cand_path] = idx.find_matching_media(
                                    conn, cand_path, media_type=media_type, missing_media_rows=cand_missing
                                )
                            matched_id = matched_cands[cand_path]
                            if matched_id == row_id:
                                print(f"Upgrading missing media {row['path']} -> {cand_path} during prune...")
                                try:
                                    if idx.refresh_media(
                                        conn,
                                        row_id,
                                        cand_path,
                                        extract_timeout=extract_timeout,
                                        probe_timeout=probe_timeout,
                                    ):
                                        upgraded = True
                                        missing_media.pop(row_id, None)
                                        break
                                    else:
                                        print(
                                            f"Refresh failed for {cand_path}; retaining existing media {row_id}. "
                                            "Please run 'hagi refresh' manually."
                                        )
                                        matched_failed = True
                                        continue
                                except Exception as ref_err:
                                    print(
                                        f"Error refreshing {cand_path} during prune: {ref_err}; "
                                        f"retaining existing media {row_id}. Please run 'hagi refresh' manually."
                                    )
                                    conn.rollback()
                                    matched_failed = True
                                    continue
            except Exception as scan_err:
                print(f"Error checking directory {parent_dir}: {scan_err}")
                conn.rollback()
                matched_failed = True

        if not upgraded and not matched_failed:
            print(f"Removing missing file from database: {row['path']}")
            conn.execute("DELETE FROM sentences WHERE media_id = ?", (row_id,))
            conn.execute("DELETE FROM media WHERE id = ?", (row_id,))
            conn.commit()
            missing_media.pop(row_id, None)
            pruned_count += 1
    if pruned_count > 0:
        print(f"Pruned {pruned_count} missing media files.")
    else:
        print("No missing media files found.")


def index_directory(
    directory_path: str,
    extract_timeout: Optional[int] = None,
    probe_timeout: Optional[int] = None,
) -> None:
    """Scan and index all subtitle and MKV files in a directory.

    Args:
        directory_path (str): Path to the directory to be indexed.
        extract_timeout (Optional[int]): Subprocess timeout in seconds for extracting
            subtitle tracks. If None, checks config.json or defaults to 1800s. Set <=0 for unlimited.
        probe_timeout (Optional[int]): Subprocess timeout in seconds for ffprobe.
            If None, checks config.json or defaults to 300s. Set <=0 for unlimited.
    """
    idx = _get_indexer()
    idx.build_plex_cache()
    conn = idx.get_db()
    effective_extract_timeout, effective_probe_timeout = idx._resolve_timeouts(
        extract_timeout, probe_timeout
    )

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
        if idx.is_missing_file(row["path"]):
            row_dict = dict(row)
            row_dict["type"] = _infer_media_type(row)
            missing_media[row["id"]] = row_dict
    failed_upgrades = set()

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
                show_title, season, episode, episode_title = idx.get_plex_metadata(file_path)
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

            file_lower = file.lower()
            media_type = (
                "mkv_embedded"
                if file_lower.endswith(".mkv")
                else ("subtitle" if file_lower.endswith((".ass", ".srt")) else None)
            )
            if not media_type:
                continue

            # Check if this new file upgrades an existing missing media entry
            matched_mid = None
            if missing_media:
                candidate_missing = [m for m in missing_media.values() if m["type"] == media_type]
                if candidate_missing:
                    matched_mid = idx.find_matching_media(
                        conn,
                        file_path,
                        media_type=media_type,
                        missing_media_rows=candidate_missing,
                    )

            if matched_mid is not None:
                old_info = missing_media[matched_mid]
                print(f"Upgrading media {old_info['path']} -> {file_path} (preserving sentence IDs)...")
                try:
                    if idx.refresh_media(
                        conn,
                        matched_mid,
                        file_path,
                        extract_timeout=effective_extract_timeout,
                        probe_timeout=effective_probe_timeout,
                    ):
                        del missing_media[matched_mid]
                        failed_upgrades.discard(matched_mid)
                        continue
                    else:
                        print(
                            f"Refresh failed for {file_path}; keeping media {matched_mid} available for other candidates. "
                            "Skipping re-indexing as new media to protect permalinks."
                        )
                        failed_upgrades.add(matched_mid)
                        continue
                except Exception as ref_err:
                    print(
                        f"Error upgrading {file_path} into media {matched_mid}: {ref_err}; "
                        "keeping media available for other candidates."
                    )
                    conn.rollback()
                    failed_upgrades.add(matched_mid)
                    continue

            if media_type == "subtitle":
                try:
                    subs = None
                    for enc in idx.SUBTITLE_ENCODINGS:
                        try:
                            subs = idx.load_and_sanitize_subs(file_path, encoding=enc)
                            break
                        except UnicodeDecodeError:
                            continue
                        except Exception:
                            continue

                    if subs:
                        lang_hint = idx.detect_language(subs)
                        idx.process_subs(conn, file_path, subs, "subtitle", language=lang_hint)
                        conn.commit()
                    else:
                        print(f"Failed to decode subtitle file: {file_path}")
                except Exception as e:
                    print(f"Error indexing {file_path}: {e}")
                    conn.rollback()

            elif media_type == "mkv_embedded":
                try:
                    subs_by_lang, had_timeout, probe_success = idx.extract_mkv_subtitles(
                        file_path,
                        extract_timeout=effective_extract_timeout,
                        probe_timeout=effective_probe_timeout,
                    )
                    if had_timeout or not probe_success or subs_by_lang is None:
                        conn.rollback()
                    else:
                        show_title, season, episode, episode_title = idx.get_plex_metadata(file_path)
                        media_id = idx.add_media(
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
                                idx.add_sentences(conn, media_id, sentence_tuples)
                                print(f"Indexed: {file_path} [{lang}] ({len(sentence_tuples)} lines)")
                        conn.commit()
                except Exception as e:
                    print(f"Error extracting from {file_path}: {e}")
                    conn.rollback()

    # Remove remaining missing files that were not upgraded
    for mid, m in list(missing_media.items()):
        if mid in failed_upgrades:
            print(f"Retaining missing file {m['path']} (media {mid}) due to failed upgrade attempt.")
            continue
        print(f"Removing deleted file from database: {m['path']}")
        conn.execute("DELETE FROM sentences WHERE media_id = ?", (mid,))
        conn.execute("DELETE FROM media WHERE id = ?", (mid,))
    conn.commit()


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
        conn: Database connection.
        media_id (int): ID of the media record to refresh.
        new_file_path (str): Path to the replacement file.
        extract_timeout (Optional[int]): Timeout for subtitle extraction.
        probe_timeout (Optional[int]): Timeout for ffprobe.

    Returns:
        bool: True if refresh succeeded, False otherwise.
    """
    idx = _get_indexer()
    abs_path = os.path.abspath(new_file_path)
    if not os.path.isfile(abs_path):
        print(f"Target path is not a file: {abs_path}")
        return False

    row = conn.execute(
        "SELECT id, path, type, show_title, season, episode, episode_title FROM media WHERE id = ?",
        (media_id,),
    ).fetchone()
    if not row:
        print(f"Media ID {media_id} not found in database.")
        return False

    existing_owner = conn.execute("SELECT id FROM media WHERE path = ?", (abs_path,)).fetchone()
    if existing_owner and existing_owner["id"] != media_id:
        print(f"Target path {abs_path} is already associated with media ID {existing_owner['id']}.")
        return False

    media_type = row["type"]
    if abs_path.lower().endswith(".mkv") and media_type == "subtitle":
        print(f"File type mismatch: {abs_path} is an MKV but media {media_id} is '{media_type}'.")
        return False
    if abs_path.lower().endswith((".ass", ".srt")) and media_type == "mkv_embedded":
        print(f"File type mismatch: {abs_path} is a subtitle but media {media_id} is '{media_type}'.")
        return False

    if abs_path.lower().endswith(".mkv"):
        subs_by_lang, had_timeout, probe_success = idx.extract_mkv_subtitles(
            abs_path,
            extract_timeout=extract_timeout,
            probe_timeout=probe_timeout,
            allow_partial=False,
        )
        if had_timeout:
            print(f"Timed out extracting subtitles from {abs_path}")
            return False
        if not probe_success:
            print(f"ffprobe failed for {abs_path}; aborting refresh to prevent data loss.")
            return False
        if subs_by_lang is None:
            print(f"Subtitle extraction failed for {abs_path}; aborting refresh to prevent data loss.")
            return False

        total_updates = total_inserts = total_deletes = 0
        if subs_by_lang:
            # Reconcile relabeled tracks or clean up obsolete languages from database
            stored_lang_rows = conn.execute(
                "SELECT DISTINCT language FROM sentences WHERE media_id = ?",
                (media_id,),
            ).fetchall()
            stored_langs = {r[0] for r in stored_lang_rows if r[0]}

            matched_new_langs = set(subs_by_lang.keys()) & stored_langs
            unmatched_stored = stored_langs - matched_new_langs
            unmatched_new = set(subs_by_lang.keys()) - matched_new_langs

            # Check if an unmatched stored track was relabeled to an unmatched new track
            for old_l in list(unmatched_stored):
                old_rows = conn.execute(
                    "SELECT text FROM sentences WHERE media_id = ? AND language = ? LIMIT 50",
                    (media_id, old_l),
                ).fetchall()
                old_texts = {r["text"].strip().lower() for r in old_rows if r["text"]}
                for new_l in list(unmatched_new):
                    new_texts = {s["text"].strip().lower() for s in subs_by_lang[new_l] if s.get("text")}
                    overlap = len(old_texts & new_texts)
                    if overlap >= 5 or (overlap >= 3 and old_texts and overlap / len(old_texts) >= 0.2):
                        conn.execute(
                            "UPDATE sentences SET language = ? WHERE media_id = ? AND language = ?",
                            (new_l, media_id, old_l),
                        )
                        unmatched_stored.remove(old_l)
                        unmatched_new.remove(new_l)
                        break

            # Preserve existing sentences for unmapped languages absent from subs_by_lang
            if unmatched_stored:
                print(
                    f"Retaining existing sentences for unmapped languages: {', '.join(sorted(unmatched_stored))} "
                    f"in media {media_id}."
                )

            for lang, new_sentences in subs_by_lang.items():
                existing = conn.execute(
                    "SELECT start_time, text FROM sentences WHERE media_id = ? AND language = ? ORDER BY start_time",
                    (media_id, lang),
                ).fetchall()
                offset = (
                    idx.estimate_timestamp_offset([dict(r) for r in existing], new_sentences)
                    if existing
                    else 0.0
                )
                up, ins, d = idx.align_and_update_sentences(
                    conn, media_id, lang, new_sentences, global_offset=offset
                )
                if up < 0:
                    print(f"Alignment failed for media {media_id} [{lang}]; rolling back.")
                    conn.rollback()
                    return False
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
        for enc in idx.SUBTITLE_ENCODINGS:
            try:
                subs = idx.load_and_sanitize_subs(abs_path, encoding=enc)
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

        detected_lang = idx.detect_language(subs)
        norm_detected = idx._normalize_lang_code(detected_lang)
        stored_langs = [
            r[0]
            for r in conn.execute(
                "SELECT DISTINCT language FROM sentences WHERE media_id = ?",
                (media_id,),
            ).fetchall()
            if r[0]
        ]
        norm_stored_map = {idx._normalize_lang_code(code): code for code in stored_langs}

        new_tag = idx._extract_file_lang(abs_path)
        old_tag = idx._extract_file_lang(row["path"])
        if new_tag and old_tag and new_tag != old_tag:
            print(
                f"Aborting refresh: subtitle language tag '{new_tag}' does not match "
                f"existing media language tag '{old_tag}' for media {media_id}."
            )
            return False

        if norm_detected in norm_stored_map:
            lang = norm_stored_map[norm_detected]
        elif stored_langs:
            stored_sample_rows = conn.execute(
                "SELECT text FROM sentences WHERE media_id = ? LIMIT 20",
                (media_id,),
            ).fetchall()
            stored_sample_text = "".join(r["text"] for r in stored_sample_rows if r["text"])
            stored_detected = idx.detect_text_language(stored_sample_text)
            new_sample_text = "".join(s["text"] for s in new_sentences[:20])
            new_detected = idx.detect_text_language(new_sample_text)

            has_stored_jp = any(
                0x3040 <= ord(c) <= 0x30FF or 0x4E00 <= ord(c) <= 0x9FAF
                for c in stored_sample_text
            )
            has_new_jp = any(
                0x3040 <= ord(c) <= 0x30FF or 0x4E00 <= ord(c) <= 0x9FAF
                for c in new_sample_text
            )

            diff_jp = has_stored_jp != has_new_jp and stored_sample_text
            diff_detected = (
                stored_detected != "unknown"
                and new_detected != "unknown"
                and stored_detected != new_detected
                and bool(stored_sample_text)
            )

            if (new_tag and new_tag not in norm_stored_map) or diff_jp or diff_detected:
                print(
                    f"Aborting refresh: detected language '{detected_lang}' does not match "
                    f"stored languages {stored_langs} for media {media_id}."
                )
                return False
            lang = stored_langs[0]
        else:
            lang = detected_lang

        existing = conn.execute(
            "SELECT start_time, text FROM sentences WHERE media_id = ? AND language = ? ORDER BY start_time",
            (media_id, lang),
        ).fetchall()
        offset = (
            idx.estimate_timestamp_offset([dict(r) for r in existing], new_sentences)
            if existing
            else 0.0
        )
        up, ins, d = idx.align_and_update_sentences(
            conn, media_id, lang, new_sentences, global_offset=offset
        )
        if up < 0:
            print(f"Alignment failed for media {media_id} [{lang}]; rolling back.")
            conn.rollback()
            return False
        print(f"Refreshed subtitle media {media_id} [{lang}]: {up} updated, {ins} inserted, {d} deleted.")

    else:
        print(f"Unsupported file format for refresh: {abs_path}")
        return False

    show_title, season, episode, episode_title = idx.get_plex_metadata(abs_path)
    idx.update_media_path(conn, media_id, abs_path, show_title, season, episode, episode_title)
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
        old_path (Optional[str], optional): Optional path to the old file being replaced.
        media_id (Optional[int], optional): Optional explicit media ID to target.
        extract_timeout (Optional[int], optional): Timeout for subtitle extraction.
        probe_timeout (Optional[int], optional): Timeout for ffprobe.

    Returns:
        bool: True if refresh succeeded, False otherwise.
    """
    idx = _get_indexer()
    conn = idx.get_db()
    abs_path = os.path.abspath(file_path)
    extract_timeout, probe_timeout = idx._resolve_timeouts(
        extract_timeout, probe_timeout
    )

    if os.path.isdir(abs_path):
        if old_path or media_id is not None:
            print("Cannot specify --old or --media-id when refreshing a directory.")
            return False
        refreshed_any = False
        all_missing = []
        for r in conn.execute(
            "SELECT id, path, type, show_title, season, episode, episode_title FROM media"
        ).fetchall():
            if idx.is_missing_file(r["path"]):
                r_dict = dict(r)
                r_dict["type"] = _infer_media_type(r)
                all_missing.append(r_dict)
        missing_by_type = {
            "mkv_embedded": [m for m in all_missing if m.get("type") == "mkv_embedded"],
            "subtitle": [m for m in all_missing if m.get("type") == "subtitle"],
        }
        for root, _, files in os.walk(abs_path):
            for file in files:
                if file.startswith("._") or not file.lower().endswith((".mkv", ".srt", ".ass")):
                    continue
                sub_path = os.path.join(root, file)
                abs_sub = os.path.abspath(sub_path)
                existing_row = conn.execute("SELECT id FROM media WHERE path = ?", (abs_sub,)).fetchone()
                if existing_row:
                    try:
                        if idx.refresh_media(
                            conn,
                            existing_row["id"],
                            abs_sub,
                            extract_timeout=extract_timeout,
                            probe_timeout=probe_timeout,
                        ):
                            refreshed_any = True
                    except Exception as ref_err:
                        print(f"Error refreshing existing media {abs_sub}: {ref_err}")
                        conn.rollback()
                    continue

                mtype = "mkv_embedded" if sub_path.lower().endswith(".mkv") else "subtitle"
                cands = missing_by_type.get(mtype, [])
                matched_id = idx.find_matching_media(conn, abs_sub, media_type=mtype, missing_media_rows=cands)
                if matched_id is not None:
                    try:
                        if idx.refresh_media(
                            conn,
                            matched_id,
                            abs_sub,
                            extract_timeout=extract_timeout,
                            probe_timeout=probe_timeout,
                        ):
                            refreshed_any = True
                            missing_by_type[mtype] = [c for c in cands if c["id"] != matched_id]
                    except Exception as ref_err:
                        print(f"Error refreshing {abs_sub} into media {matched_id}: {ref_err}")
                        conn.rollback()
        return refreshed_any

    target_id = media_id
    if target_id is None and old_path:
        old_abs = os.path.abspath(old_path)
        row = conn.execute("SELECT id FROM media WHERE path = ?", (old_abs,)).fetchone()
        if row:
            target_id = row["id"]
        else:
            print(f"Specified old file path '{old_abs}' not found in database.")
            return False

    if target_id is None:
        row = conn.execute("SELECT id FROM media WHERE path = ?", (abs_path,)).fetchone()
        if row:
            target_id = row["id"]
        else:
            media_type = "mkv_embedded" if abs_path.lower().endswith(".mkv") else "subtitle"
            target_id = idx.find_matching_media(conn, abs_path, media_type=media_type)

    if target_id is None:
        print(f"File '{abs_path}' not found in database and no matching media could be identified.")
        return False

    try:
        return idx.refresh_media(
            conn,
            target_id,
            abs_path,
            extract_timeout=extract_timeout,
            probe_timeout=probe_timeout,
        )
    except Exception as ref_err:
        print(f"Error refreshing {abs_path} into media {target_id}: {ref_err}")
        conn.rollback()
        return False
