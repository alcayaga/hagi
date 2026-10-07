"""MKV subtitle stream probing and extraction via ffprobe and ffmpeg."""

import json
import os
import subprocess
import sys
import tempfile
import time
from typing import Optional

from .subtitles import detect_language, load_and_sanitize_subs

BITMAP_SUBTITLE_CODECS = {
    "hdmv_pgs_subtitle",
    "dvd_subtitle",
    "dvdsub",
    "dvb_subtitle",
    "dvbsub",
    "pgssub",
    "xsub",
}


def select_best_stream(stream_list: list[dict], is_spanish: bool = False) -> Optional[dict]:
    """Select the highest priority subtitle stream from a candidate list.

    Args:
        stream_list (list[dict]): List of subtitle stream metadata dictionaries.
        is_spanish (bool, optional): Whether to prioritize Latin American Spanish. Defaults to False.

    Returns:
        Optional[dict]: Selected stream dictionary, or None if candidate list is empty.
    """
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


def extract_mkv_subtitles(
    file_path: str,
    extract_timeout: Optional[int] = None,
    probe_timeout: Optional[int] = None,
    allow_partial: bool = True,
) -> tuple[Optional[dict[str, list[dict]]], bool, bool]:
    """Extract supported subtitle tracks (jpn, eng, spa) from an MKV file.

    Probes embedded subtitle streams and extracts them using ffmpeg into temporary files,
    detecting and normalizing track languages.

    Args:
        file_path (str): Path to the MKV file.
        extract_timeout (Optional[int]): Subprocess timeout in seconds for subtitle extraction.
        probe_timeout (Optional[int]): Subprocess timeout in seconds for ffprobe.
        allow_partial (bool): Whether to preserve partially extracted output if fallback
            extraction fails. Defaults to True for initial indexing; False should be used
            for refreshes to prevent truncated tracks from causing sentence deletions.

    Returns:
        tuple[Optional[dict[str, list[dict]]], bool, bool]: A tuple containing:
            - Optional[dict[str, list[dict]]]: Dictionary mapping language code to sentence dicts with
              'start_time', 'end_time', and 'text', or None if extraction failed.
            - bool: True if an extraction subprocess timed out, False otherwise.
            - bool: True if ffprobe succeeded, False if ffprobe failed or timed out.
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
    try:
        result = subprocess.run(probe_cmd, capture_output=True, text=True, timeout=probe_timeout)
    except subprocess.TimeoutExpired:
        print(f"Timed out probing {file_path}")
        return {}, True, False

    if result.returncode != 0:
        print(f"ffprobe failed for {file_path}: {result.stderr}")
        return {}, False, False

    streams = json.loads(result.stdout).get("streams", [])
    if not streams:
        return {}, False, True

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
    indexer = sys.modules.get("hagi.indexer")
    load_subs_fn = (
        getattr(indexer, "load_and_sanitize_subs", load_and_sanitize_subs)
        if indexer
        else load_and_sanitize_subs
    )
    detect_lang_fn = (
        getattr(indexer, "detect_language", detect_language)
        if indexer
        else detect_language
    )

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
                            elif allow_partial and os.path.exists(temp_sub_path) and os.path.getsize(temp_sub_path) > 0:
                                extracted_subs.append((temp_sub_path, lang, i))
                            elif not allow_partial:
                                print(f"Extraction failed for track {i} from {file_path}")
                                return None, False, True
                        except subprocess.TimeoutExpired:
                            print(f"Timed out extracting track {i} from {file_path}")
                            had_timeout = True
                            if not allow_partial:
                                return None, True, True
                            break
            except subprocess.TimeoutExpired:
                print(f"Timed out extracting subtitles from {file_path}")
                had_timeout = True
                if not allow_partial:
                    return None, True, True

        subs_by_lang = {}
        if extracted_subs:
            seen_langs = set()
            for temp_sub_path, lang, i in extracted_subs:
                try:
                    subs = load_subs_fn(temp_sub_path)
                    if not subs or not any(line.plaintext.strip() for line in subs):
                        continue
                    final_lang = lang

                    detected_lang = detect_lang_fn(subs)
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
                    if not allow_partial:
                        return None, False, True

        return subs_by_lang, had_timeout, True
    finally:
        for temp_sub_path in temp_paths_to_clean:
            if os.path.exists(temp_sub_path):
                os.remove(temp_sub_path)
