"""Tests for the nadeshiko API integration module."""

import json
import time
from unittest import mock

import nadeshiko


def test_get_favorite_media_success():
    """Test fetching favorite media successfully."""
    # Reset cache
    nadeshiko._FAVORITE_MEDIA_CACHE = (0, [])

    mock_resp = {
        "favoriteMedia": [
            {"publicId": "media1", "nameRomaji": "Show 1"},
            {"publicId": "media2", "nameRomaji": "Show 2"}
        ]
    }

    with mock.patch("urllib.request.OpenerDirector.open") as mock_urlopen:
        cm = mock.MagicMock()
        cm.read.return_value = json.dumps(mock_resp).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = cm

        favorites = nadeshiko.get_favorite_media("dummy_key")

        assert len(favorites) == 2
        assert "media1" in favorites
        assert "media2" in favorites


def test_get_favorite_media_error():
    """Test fetching favorite media with an error."""
    nadeshiko._FAVORITE_MEDIA_CACHE = (0, [])

    with mock.patch("urllib.request.OpenerDirector.open") as mock_urlopen:
        mock_urlopen.side_effect = Exception("Network error")

        favorites = nadeshiko.get_favorite_media("dummy_key")

        assert favorites == []


def test_search_global_stats():
    """Test searching global stats and sorting."""
    # Pre-populate cache so media1 is starred
    nadeshiko._FAVORITE_MEDIA_CACHE = (time.time(), ["media1"])

    mock_resp = {
        "media": [
            {"mediaPublicId": "media2", "matchCount": 50},
            {"mediaPublicId": "media1", "matchCount": 10},
            {"mediaPublicId": "media3", "matchCount": 100}
        ],
        "includes": {
            "media": {
                "media1": {"nameRomaji": "Starred Show", "slug": "starred-show"},
                "media2": {"nameRomaji": "Normal Show", "slug": "normal-show"},
                "media3": {"nameRomaji": "Popular Show", "slug": "popular-show"}
            }
        }
    }

    with mock.patch("urllib.request.OpenerDirector.open") as mock_urlopen:
        cm = mock.MagicMock()
        cm.read.return_value = json.dumps(mock_resp).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = cm

        results = nadeshiko.search_global_stats("dummy_key", "test")

        assert len(results) == 3
        # media1 should be first because it's starred
        assert results[0]["publicId"] == "media1"
        assert results[0]["isStarred"] is True

        # media3 should be second because it has more matchCount (100 vs 50)
        assert results[1]["publicId"] == "media3"
        assert results[1]["isStarred"] is False
        assert results[1]["matchCount"] == 100

        # media2 should be last
        assert results[2]["publicId"] == "media2"
        assert results[2]["isStarred"] is False
        assert results[2]["matchCount"] == 50
