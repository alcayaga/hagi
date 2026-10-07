"""Media identification, title normalization, and 3-tier upgrade matching."""

import os
import re
import sys
from typing import Optional

from .plex import get_plex_metadata
from .subtitles import (
    SUBTITLE_ENCODINGS,
    _extract_file_lang,
    _normalize_lang_code,
    load_and_sanitize_subs,
)


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
            - file_title (str | None): A title extracted from the filename stem.
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
    is_fallback = False
    if not m_abs:
        m_abs = re.search(r"\b(\d{3,4})\b", clean_base)
        is_fallback = True
    if m_abs:
        cand = int(m_abs.group(1))
        is_year = is_fallback and (1900 <= cand <= 2099)
        if (
            cand not in (1080, 720, 480, 264, 265, 576, 2160)
            and not is_year
            and cand != season
            and cand != episode
        ):
            abs_episode = cand

    show_hint = None
    file_title = None
    stem = os.path.splitext(clean_base)[0].strip()
    stem_norm = re.sub(r"[._]+", " ", stem).strip()

    marker_pattern = re.compile(
        r"\b(?:season\s*\d+|s\d{1,3}e\d{1,4}|\d{1,2}x\d{1,4}|ep?\d+)\b",
        re.IGNORECASE,
    )

    m_pre_se = re.search(r"^(.+?)(?:\s*-\s*|\s+)(?:[sS]\d{1,3}[eE]\d{1,4}|\b\d{1,2}x\d{1,4}\b)", stem_norm)
    if m_pre_se:
        cand_t = m_pre_se.group(1).strip()
        if cand_t and not marker_pattern.search(cand_t):
            file_title = cand_t
    if not file_title:
        file_title_match = re.match(r"^([^\-]+?)\s*-\s*", stem_norm)
        if file_title_match:
            cand_show = file_title_match.group(1).strip()
            if cand_show and not marker_pattern.search(cand_show):
                file_title = cand_show

    if file_title:
        show_hint = file_title
    else:
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
        "file_title": file_title,
    }


def titles_match(title1: Optional[str], title2: Optional[str]) -> bool:
    """Check if two show titles match, handling common prefixes while rejecting trailing additions.

    Normalizes titles into words, requires exact equality or allows leading prefix variations
    (e.g., 'Detective Conan' and 'Conan'), while strictly rejecting titles where the longer title
    adds trailing tokens (e.g., 'Naruto' and 'Naruto Shippuden').

    Args:
        title1 (Optional[str]): First title.
        title2 (Optional[str]): Second title.

    Returns:
        bool: True if titles match, False otherwise.
    """
    if not title1 or not title2:
        return False

    t1_clean = re.sub(r"\{[^\}]*\}|\([0-9]{4}\)|\[[^\]]*\]", "", title1).strip().lower()
    t2_clean = re.sub(r"\{[^\}]*\}|\([0-9]{4}\)|\[[^\]]*\]", "", title2).strip().lower()

    tokens1 = re.findall(r"\w+", t1_clean)
    tokens2 = re.findall(r"\w+", t2_clean)

    if not tokens1 or not tokens2:
        return False

    if tokens1 == tokens2:
        return True

    shorter, longer = (tokens1, tokens2) if len(tokens1) < len(tokens2) else (tokens2, tokens1)

    # Shorter must match the tail of the longer title (allowing only leading prefixes like 'The', 'Detective')
    # and strictly disallow trailing tokens (e.g. 'Shippuden', 'Season 2')
    if longer[-len(shorter):] == shorter:
        prefix = tuple(longer[:-len(shorter)])
        if prefix in (("the",), ("detective",)):
            return True

    return False


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
    indexer = sys.modules.get("hagi.indexer")
    get_plex_meta_fn = (
        getattr(indexer, "get_plex_metadata", get_plex_metadata)
        if indexer
        else get_plex_metadata
    )
    load_subs_fn = (
        getattr(indexer, "load_and_sanitize_subs", load_and_sanitize_subs)
        if indexer
        else load_and_sanitize_subs
    )
    extract_lang_fn = (
        getattr(indexer, "_extract_file_lang", _extract_file_lang)
        if indexer
        else _extract_file_lang
    )
    norm_lang_fn = (
        getattr(indexer, "_normalize_lang_code", _normalize_lang_code)
        if indexer
        else _normalize_lang_code
    )
    parse_ids_fn = (
        getattr(indexer, "parse_media_identifiers", parse_media_identifiers)
        if indexer
        else parse_media_identifiers
    )
    titles_match_fn = (
        getattr(indexer, "titles_match", titles_match)
        if indexer
        else titles_match
    )

    if missing_media_rows is None:
        rows = conn.execute(
            "SELECT id, path, type, show_title, season, episode, episode_title FROM media WHERE type = ? OR type IS NULL",
            (media_type,),
        ).fetchall()
        missing_media_rows = []
        for r in rows:
            if not os.path.exists(r["path"]):
                r_dict = dict(r)
                r_dict["type"] = r["type"] or (
                    "mkv_embedded" if r["path"].endswith(".mkv") else "subtitle"
                )
                missing_media_rows.append(r_dict)

    candidates = [
        c
        for c in missing_media_rows
        if (c.get("type") or ("mkv_embedded" if c.get("path", "").endswith(".mkv") else "subtitle")) == media_type
    ]
    new_lang_tag = extract_lang_fn(new_file_path)
    if new_lang_tag:
        if media_type == "subtitle":
            candidates = [
                c for c in candidates
                if not extract_lang_fn(c["path"]) or extract_lang_fn(c["path"]) == new_lang_tag
            ]
            if candidates:
                cand_ids = [c["id"] for c in candidates]
                ph = ",".join("?" for _ in cand_ids)
                lang_rows = conn.execute(
                    f"SELECT DISTINCT media_id, language FROM sentences WHERE media_id IN ({ph})",
                    cand_ids,
                ).fetchall()
                stored_by_mid: dict[int, set[str]] = {}
                for r in lang_rows:
                    if r["language"]:
                        stored_by_mid.setdefault(r["media_id"], set()).add(norm_lang_fn(r["language"]))
                candidates = [
                    c for c in candidates
                    if not stored_by_mid.get(c["id"]) or new_lang_tag in stored_by_mid[c["id"]]
                ]
        else:
            candidates = [
                c for c in candidates
                if not extract_lang_fn(c["path"]) or extract_lang_fn(c["path"]) == new_lang_tag
            ]
    if not candidates:
        return None

    show_title, season, episode, _ = get_plex_meta_fn(new_file_path)
    file_ids = parse_ids_fn(new_file_path)

    if season is None:
        season = file_ids["season"]
    if episode is None:
        episode = file_ids["episode"]
    abs_ep = file_ids["abs_episode"]
    show_hint = show_title or file_ids["show_hint"]

    # Priority 1: Match on season and episode (requiring show title confirmation if available)
    if season is not None and episode is not None:
        matched_se = [c for c in candidates if c.get("season") == season and c.get("episode") == episode]
        if show_hint:
            matched_show = [
                c for c in matched_se
                if c.get("show_title") and titles_match_fn(show_hint, c["show_title"])
            ]
            if len(matched_show) == 1:
                return matched_show[0]["id"]
        else:
            matched_no_conflict = [c for c in matched_se if not c.get("show_title")]
            if len(matched_no_conflict) == 1:
                return matched_no_conflict[0]["id"]

    # Priority 2: Check matching folder/parent folder for same episode or absolute episode
    new_abs = os.path.abspath(new_file_path)
    new_dir = os.path.dirname(new_abs)
    is_season_dir = bool(re.search(r"Season\s*\d+", os.path.basename(new_dir), re.IGNORECASE))

    dir_candidates = []
    for c in candidates:
        c_path = os.path.abspath(c["path"])
        c_dir = os.path.dirname(c_path)
        same_dir = (c_dir == new_dir)
        same_season_parent = (
            is_season_dir
            and bool(re.search(r"Season\s*\d+", os.path.basename(c_dir), re.IGNORECASE))
            and os.path.dirname(c_dir) == os.path.dirname(new_dir)
        )
        if same_dir or same_season_parent:
            if show_hint:
                c_show = c.get("show_title") or parse_ids_fn(c_path).get("show_hint")
                if c_show and not titles_match_fn(show_hint, c_show):
                    continue
            dir_candidates.append(c)

    if abs_ep is not None:
        matched_abs = []
        for c in dir_candidates:
            c_ids = parse_ids_fn(c["path"])
            if c_ids.get("abs_episode") == abs_ep:
                c_season = c.get("season") if c.get("season") is not None else c_ids.get("season")
                c_episode = c.get("episode") if c.get("episode") is not None else c_ids.get("episode")
                if season is not None and c_season is not None and season != c_season:
                    continue
                if episode is not None and c_episode is not None and episode != c_episode:
                    continue
                matched_abs.append(c)
        if len(matched_abs) == 1:
            return matched_abs[0]["id"]

    if episode is not None:
        matched_ep = []
        for c in dir_candidates:
            c_ids = parse_ids_fn(c["path"])
            if c_ids.get("episode") == episode and (season is None or c_ids.get("season") == season):
                matched_ep.append(c)
        if len(matched_ep) == 1:
            return matched_ep[0]["id"]

    # Priority 3: Subtitle content fingerprinting
    if sample_sentences is None and os.path.isfile(new_file_path):
        if new_file_path.lower().endswith((".srt", ".ass")):
            subs_sample = None
            for enc in SUBTITLE_ENCODINGS:
                try:
                    subs_sample = load_subs_fn(new_file_path, encoding=enc)
                    break
                except UnicodeDecodeError:
                    continue
                except Exception:
                    continue
            if subs_sample:
                eligible = [
                    line.plaintext.strip()
                    for line in subs_sample
                    if len(line.plaintext.strip()) >= 10
                ]
                if len(eligible) <= 10:
                    sample_sentences = eligible
                else:
                    step = len(eligible) / 10.0
                    sample_sentences = [eligible[int(k * step)] for k in range(10)]

    if sample_sentences and candidates:
        fp_candidates = []
        for c in candidates:
            c_ids = parse_ids_fn(c["path"])
            c_s = c.get("season") if c.get("season") is not None else c_ids.get("season")
            c_e = c.get("episode") if c.get("episode") is not None else c_ids.get("episode")
            c_abs = c_ids.get("abs_episode")

            if season is not None and c_s is not None and season != c_s:
                continue
            if episode is not None and c_e is not None and episode != c_e:
                continue
            if abs_ep is not None and c_abs is not None and abs_ep != c_abs:
                continue
            fp_candidates.append(c)

        clean_samples = list({s.strip() for s in sample_sentences if len(s.strip()) >= 10})[:10]
        candidate_ids = [c["id"] for c in fp_candidates]
        if clean_samples and candidate_ids:
            ph_ids = ",".join("?" for _ in candidate_ids)
            ph_texts = ",".join("?" for _ in clean_samples)
            query = f"""
                SELECT media_id, COUNT(DISTINCT text) as match_count
                FROM sentences
                WHERE media_id IN ({ph_ids}) AND text IN ({ph_texts})
                GROUP BY media_id
                ORDER BY match_count DESC
                LIMIT 2
            """
            rows = conn.execute(query, candidate_ids + clean_samples).fetchall()
            if rows:
                best_match = rows[0]
                min_matches = max(2, len(clean_samples) // 2)
                if best_match["match_count"] >= min_matches:
                    if len(rows) == 1:
                        return best_match["media_id"]
                    elif best_match["match_count"] > rows[1]["match_count"]:
                        return best_match["media_id"]

    return None
