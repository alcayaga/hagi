"""Module for indexing media files and subtitles."""

from ..db import (
    add_media,
    add_sentences,
    get_db,
    update_media_path,
)
from .alignment import (
    REFRESH_THRESHOLD_SECONDS,
    align_and_update_sentences,
    estimate_timestamp_offset,
)
from .config import (
    DEFAULT_EXTRACT_TIMEOUT,
    DEFAULT_PLEX_TIMEOUT,
    DEFAULT_PROBE_TIMEOUT,
    _load_config,
    _resolve_timeouts,
    is_missing_file,
)
from .extractor import (
    BITMAP_SUBTITLE_CODECS,
    extract_mkv_subtitles,
    select_best_stream,
)
from .lifecycle import (
    expand_glob_pattern,
    filter_covered_paths,
    index_directory,
    process_subs,
    prune_database,
    refresh_file,
    refresh_media,
)
from .matching import (
    find_matching_media,
    parse_media_identifiers,
    titles_match,
)
from .plex import (
    SUPPORTED_LOCALES,
    PlexError,
    build_plex_cache,
    get_plex_metadata,
    plex_path_cache,
    _get_plex,
    _plex_cache_built,
    _plex_initialized,
    _plex_instance,
)
from .subtitles import (
    LANGUAGE_CODE_MAP,
    SUBTITLE_ENCODINGS,
    detect_language,
    detect_text_language,
    load_and_sanitize_subs,
    _extract_file_lang,
    _normalize_lang_code,
)

__all__ = [
    "DEFAULT_EXTRACT_TIMEOUT",
    "DEFAULT_PROBE_TIMEOUT",
    "DEFAULT_PLEX_TIMEOUT",
    "BITMAP_SUBTITLE_CODECS",
    "LANGUAGE_CODE_MAP",
    "SUBTITLE_ENCODINGS",
    "SUPPORTED_LOCALES",
    "REFRESH_THRESHOLD_SECONDS",
    "PlexError",
    "plex_path_cache",
    "build_plex_cache",
    "get_plex_metadata",
    "load_and_sanitize_subs",
    "detect_language",
    "detect_text_language",
    "extract_mkv_subtitles",
    "select_best_stream",
    "parse_media_identifiers",
    "titles_match",
    "find_matching_media",
    "estimate_timestamp_offset",
    "align_and_update_sentences",
    "process_subs",
    "expand_glob_pattern",
    "filter_covered_paths",
    "refresh_media",
    "refresh_file",
    "index_directory",
    "prune_database",
    "get_db",
    "add_media",
    "add_sentences",
    "update_media_path",
    "is_missing_file",
    "_load_config",
    "_resolve_timeouts",
    "_get_plex",
    "_normalize_lang_code",
    "_extract_file_lang",
    "_plex_cache_built",
    "_plex_initialized",
    "_plex_instance",
]
