"""Subtitle loading, sanitization, encoding handling, and language detection."""

import re
from typing import Optional

import pysubs2

LANGUAGE_CODE_MAP: dict[str, str] = {
    "en": "eng",
    "eng": "eng",
    "ja": "jpn",
    "jpn": "jpn",
    "jp": "jpn",
    "es": "spa",
    "spa": "spa",
    "sp": "spa",
    "pt": "por",
    "por": "por",
    "fr": "fra",
    "fra": "fra",
    "fre": "fra",
    "de": "deu",
    "deu": "deu",
    "ger": "deu",
    "it": "ita",
    "ita": "ita",
    "zh": "zho",
    "zho": "zho",
    "chi": "zho",
    "ko": "kor",
    "kor": "kor",
    "ru": "rus",
    "rus": "rus",
}

SUBTITLE_ENCODINGS: tuple[str, ...] = (
    "utf-8-sig",
    "utf-8",
    "utf-16",
    "cp932",
    "shift_jis",
    "latin-1",
)


def load_and_sanitize_subs(file_path: str, encoding: str = "utf-8") -> pysubs2.SSAFile:
    """Load a subtitle file and sanitize invalid negative timestamps.

    Args:
        file_path (str): Path to the subtitle file.
        encoding (str): Encoding to use when reading the file. Defaults to "utf-8".

    Returns:
        pysubs2.SSAFile: Parsed subtitles object.
    """
    with open(file_path, "r", encoding=encoding) as f:
        lines = f.readlines()

    def replacer(match: re.Match) -> str:
        """Replace negative timestamps with zero time markers."""
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


def detect_text_language(text: str) -> str:
    """Heuristically detect the language of a raw text string.

    Args:
        text (str): Raw subtitle text to evaluate.

    Returns:
        str: Detected language code ('jpn', 'por', 'spa', 'eng', or 'unknown').
    """
    if not text:
        return "unknown"

    jp_chars = 0
    sp_chars = 0
    por_chars = 0
    total_chars = len(text)

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

    if jp_chars / total_chars > 0.05:
        return "jpn"
    if por_chars > sp_chars:
        return "por"
    if sp_chars > 0:
        return "spa"
    return "eng"


def detect_language(subs_obj: pysubs2.SSAFile) -> str:
    """Heuristically detect the language of a subtitle object.

    Args:
        subs_obj (pysubs2.SSAFile): Parsed subtitle object.

    Returns:
        str: Detected 3-letter language code or 'unknown'.
    """
    texts = []
    lines_checked = 0
    for line in subs_obj:
        text = line.plaintext.strip()
        if not text:
            continue
        texts.append(text)
        lines_checked += 1
        if lines_checked >= 50:
            break

    if not texts:
        return "unknown"
    return detect_text_language(" ".join(texts))


def _normalize_lang_code(code: Optional[str]) -> str:
    """Normalize a 2-letter or 3-letter language code to standard 3-letter representation.

    Args:
        code (Optional[str]): Language code to normalize.

    Returns:
        str: Normalized 3-letter language code or lowercase code if unrecognized.
    """
    if not code:
        return "unknown"
    code = code.lower().strip()
    return LANGUAGE_CODE_MAP.get(code, code)


def _extract_file_lang(path: str) -> Optional[str]:
    """Extract and normalize a language tag from a subtitle filename (e.g. .ja.srt -> jpn).

    Args:
        path (str): File path to inspect.

    Returns:
        Optional[str]: Normalized 3-letter language code or None if no valid tag found.
    """
    m = re.search(r"\.([a-zA-Z]{2,3})\.(?:srt|ass|vtt)$", path, re.IGNORECASE)
    if m:
        token = m.group(1).lower()
        return LANGUAGE_CODE_MAP.get(token)
    return None
