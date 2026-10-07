"""Plex Media Server integration, path cache, and metadata resolution."""

import os
import sys
from typing import Optional

from dotenv import load_dotenv

from .config import DEFAULT_PLEX_TIMEOUT, _load_config

load_dotenv()


class PlexError(Exception):
    """Raised when an error occurs while communicating with Plex."""


_plex_instance = None
_plex_initialized = False
plex_path_cache: dict[str, tuple | None] = {}
_plex_cache_built = False

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


def _is_plex_initialized() -> bool:
    """Check if Plex connection is initialized."""
    indexer = sys.modules.get("hagi.indexer")
    if indexer and hasattr(indexer, "_plex_initialized"):
        return bool(indexer._plex_initialized)
    return _plex_initialized


def _get_active_plex_instance():
    """Retrieve the active PlexServer instance."""
    indexer = sys.modules.get("hagi.indexer")
    if indexer and hasattr(indexer, "_plex_instance"):
        return indexer._plex_instance
    return _plex_instance


def _set_active_plex_instance(inst, initialized: bool) -> None:
    """Store the active PlexServer instance and initialized state."""
    global _plex_instance, _plex_initialized
    _plex_instance = inst
    _plex_initialized = initialized
    indexer = sys.modules.get("hagi.indexer")
    if indexer:
        indexer._plex_instance = inst
        indexer._plex_initialized = initialized


def _is_cache_built() -> bool:
    """Check if the Plex library cache is built."""
    indexer = sys.modules.get("hagi.indexer")
    if indexer and hasattr(indexer, "_plex_cache_built"):
        return bool(indexer._plex_cache_built)
    return _plex_cache_built


def _set_cache_built(built: bool) -> None:
    """Update cache built state."""
    global _plex_cache_built
    _plex_cache_built = built
    indexer = sys.modules.get("hagi.indexer")
    if indexer:
        indexer._plex_cache_built = built


def _get_path_cache() -> dict[str, tuple | None]:
    """Retrieve the path-to-metadata cache dict."""
    indexer = sys.modules.get("hagi.indexer")
    if indexer and hasattr(indexer, "plex_path_cache"):
        return indexer.plex_path_cache
    return plex_path_cache


def _get_plex():
    """Connect to Plex server if configured.

    Returns:
        Optional[PlexServer]: PlexServer instance if configured, or None if not configured.

    Raises:
        PlexError: If Plex credentials are provided but connecting fails, or configuration is partial.
    """
    if _is_plex_initialized():
        return _get_active_plex_instance()

    PLEX_URL = os.getenv("PLEX_URL")
    PLEX_TOKEN = os.getenv("PLEX_TOKEN")
    if not PLEX_URL and not PLEX_TOKEN:
        _set_active_plex_instance(None, True)
        return None
    if bool(PLEX_URL) != bool(PLEX_TOKEN):
        raise PlexError("Both PLEX_URL and PLEX_TOKEN must be set to connect to Plex.")

    plex_timeout = DEFAULT_PLEX_TIMEOUT
    indexer = sys.modules.get("hagi.indexer")
    load_cfg = getattr(indexer, "_load_config", _load_config) if indexer else _load_config
    try:
        config = load_cfg()
        if config:
            raw_cfg_timeout = config.get("plex_timeout") or config.get("plexTimeout")
            if raw_cfg_timeout is not None and int(raw_cfg_timeout) > 0:
                plex_timeout = int(raw_cfg_timeout)
    except Exception as e:
        print(f"Warning: Failed to load plex_timeout from config.json: {e}")

    env_timeout = os.getenv("PLEX_TIMEOUT")
    if env_timeout:
        try:
            val = int(env_timeout)
            if val > 0:
                plex_timeout = val
        except ValueError:
            print(f"Warning: Invalid PLEX_TIMEOUT environment variable: {env_timeout}")

    try:
        from plexapi.server import PlexServer

        inst = PlexServer(PLEX_URL, PLEX_TOKEN, timeout=plex_timeout)
        _set_active_plex_instance(inst, True)
        return inst
    except Exception as e:
        _set_active_plex_instance(None, False)
        raise PlexError(f"Could not connect to Plex: {e}") from e


def build_plex_cache() -> None:
    """Build the cache of Plex paths and metadata.

    Raises:
        PlexError: If connecting to Plex or querying Plex libraries fails.
    """
    if _is_cache_built():
        return

    indexer = sys.modules.get("hagi.indexer")
    plex_fn = getattr(indexer, "_get_plex", _get_plex) if indexer else _get_plex
    plex = plex_fn()
    if not plex:
        return

    print("Building Plex path mapping cache (this may take a moment)...")
    load_cfg = getattr(indexer, "_load_config", _load_config) if indexer else _load_config
    try:
        config = load_cfg()
    except Exception as e:
        raise PlexError(f"Error reading config.json for Plex libraries: {e}") from e

    active_cache = _get_path_cache()
    try:
        allowed_libraries = config.get("plex_libraries") if config else None

        for section in plex.library.sections():
            if allowed_libraries is not None:
                allowed_str = [str(x) for x in allowed_libraries]
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
                            active_cache[cache_key] = val
                            if base_key in active_cache:
                                if active_cache[base_key] != val:
                                    active_cache[base_key] = None
                            else:
                                active_cache[base_key] = val
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
                            active_cache[cache_key] = val
                            if base_key in active_cache:
                                if active_cache[base_key] != val:
                                    active_cache[base_key] = None
                            else:
                                active_cache[base_key] = val
        _set_cache_built(True)
    except Exception as e:
        _set_cache_built(False)
        active_cache.clear()
        raise PlexError(f"Error building Plex cache: {e}") from e


def get_plex_metadata(file_path: str) -> tuple[Optional[str], Optional[int], Optional[int], Optional[str]]:
    """Get Plex metadata, accounting for external subtitle language codes.

    Args:
        file_path (str): Path to the subtitle or media file.

    Returns:
        tuple[Optional[str], Optional[int], Optional[int], Optional[str]]:
            Tuple of (show_title, season, episode, episode_title).
    """
    active_cache = _get_path_cache()

    cache_key = os.path.splitext(file_path)[0]
    info = active_cache.get(cache_key)
    if not info:
        base_name = os.path.basename(cache_key)
        if base_name in active_cache:
            info = active_cache[base_name]
        elif "." in base_name:
            parts = base_name.rsplit(".", 1)
            if len(parts) == 2:
                suffix = parts[1].lower().replace("_", "-")
                suffix_parts = suffix.split("-")
                base_suffix = suffix_parts[0]

                is_valid = base_suffix in SUPPORTED_LOCALES
                if len(suffix_parts) > 1:
                    region = suffix_parts[1]
                    valid_region = (len(region) == 2 and region.isalpha()) or (len(region) == 3 and region.isdigit())
                    is_valid = is_valid and valid_region and len(suffix_parts) == 2

                if is_valid:
                    stripped = parts[0]
                    cache_key_stripped = os.path.join(os.path.dirname(file_path), stripped)
                    info = active_cache.get(cache_key_stripped)
                    if not info:
                        info = active_cache.get(stripped)
    return info or (None, None, None, None)
