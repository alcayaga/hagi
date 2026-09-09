"""Nadeshiko API integration module."""

import json
import logging
import time
import urllib.request
import urllib.error

logger = logging.getLogger(__name__)

# Simple in-memory cache: (timestamp, list_of_ids)
_FAVORITE_MEDIA_CACHE = {}
CACHE_TTL = 3600  # 1 hour


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Custom HTTP redirect handler that explicitly prevents following redirects."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        """Prevent redirects to protect the Authorization header."""
        return None  # Explicitly prevent following redirects


def _make_request(url, api_key, method="GET", data=None):
    """Helper to make urllib requests to Nadeshiko API."""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "Hagi/1.0 (Integration)",
    }

    req_data = json.dumps(data).encode("utf-8") if data else None
    req = urllib.request.Request(url, data=req_data, headers=headers, method=method)

    opener = urllib.request.build_opener(NoRedirectHandler)

    try:
        with opener.open(req, timeout=10) as response:
            return json.loads(response.read().decode("utf-8")), None
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace")
        if len(error_body) > 200:
            error_body = error_body[:200] + "..."
        return None, f"HTTP {e.code}: {error_body}"
    except Exception as e:
        return None, str(e)


def get_favorite_media(api_key: str) -> list[str]:
    """Fetch user's starred media public IDs, using a cache."""
    global _FAVORITE_MEDIA_CACHE

    now = time.time()
    if api_key in _FAVORITE_MEDIA_CACHE:
        timestamp, favorites = _FAVORITE_MEDIA_CACHE[api_key]
        if now - timestamp < CACHE_TTL:
            return favorites

    url = "https://api.nadeshiko.co/v1/user/favorite-media"
    resp, error = _make_request(url, api_key)

    if error or not isinstance(resp, dict):
        # Return stale cache on error, or empty list
        return _FAVORITE_MEDIA_CACHE.get(api_key, (0, []))[1]

    favorite_media = resp.get("favoriteMedia", [])
    if not isinstance(favorite_media, list):
        favorite_media = []

    favorites = [m.get("publicId") for m in favorite_media if isinstance(m, dict)]
    _FAVORITE_MEDIA_CACHE[api_key] = (now, favorites)
    return favorites


def _get_title(media_info: dict, title_language: str) -> str:
    """Get the title of a media based on the preferred language with fallbacks."""
    if title_language == "japanese":
        return media_info.get("nameJa") or media_info.get("nameRomaji") or media_info.get("nameEn") or "Unknown Title"
    if title_language == "english":
        return media_info.get("nameEn") or media_info.get("nameRomaji") or media_info.get("nameJa") or "Unknown Title"

    # Default to romaji
    return media_info.get("nameRomaji") or media_info.get("nameEn") or media_info.get("nameJa") or "Unknown Title"


def search_global_stats(api_key: str, query: str, title_language: str = "romaji") -> list[dict]:
    """Search for a word and return matching media ordered by favorite status."""
    url = "https://api.nadeshiko.co/v1/search/stats"
    data = {
        "query": {
            "search": query
        },
        "include": ["media"]
    }

    resp, error = _make_request(url, api_key, method="POST", data=data)
    if error or not isinstance(resp, dict):
        logger.error(f"Nadeshiko API Error: {error}")
        return []

    favorites = get_favorite_media(api_key)

    media_stats = resp.get("media", [])
    if not isinstance(media_stats, list):
        media_stats = []
    includes_data = resp.get("includes") or {}
    includes_media = includes_data.get("media", {}) if isinstance(includes_data, dict) else {}
    if not isinstance(includes_media, dict):
        includes_media = {}

    results = []
    for stat in media_stats:
        if not isinstance(stat, dict):
            continue
        public_id = stat.get("mediaPublicId")
        if not public_id:
            continue

        media_info = includes_media.get(public_id, {})
        if not isinstance(media_info, dict):
            media_info = {}

        is_starred = public_id in favorites

        match_count = stat.get("matchCount", 0)
        try:
            match_count = int(match_count)
        except (ValueError, TypeError):
            match_count = 0

        results.append({
            "publicId": public_id,
            "slug": media_info.get("slug", ""),
            "title": _get_title(media_info, title_language),
            "coverUrl": media_info.get("coverUrl", ""),
            "matchCount": match_count,
            "isStarred": is_starred
        })

    # Sort: Starred first, then by matchCount descending
    results.sort(key=lambda x: (not x["isStarred"], -x["matchCount"]))

    return results
