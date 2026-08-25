"""Test module."""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import db
from web import app

client = TestClient(app)


@pytest.fixture
def test_db():
    """Test function."""
    db.DB_PATH = ":memory:"
    conn = db.init_db()
    media_id = db.add_media(conn, "/fake/video.mkv", "mkv_embedded")
    db.add_sentences(conn, media_id, [("jpn", 10.0, 15.0, "Hello web test")])
    yield conn
    conn.close()


def test_read_main():
    """Test function."""
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Nadeshiko Search" in response.text


def test_api_search(test_db):
    """Test function."""
    with patch("web.db.get_db", return_value=test_db):
        response = client.get("/api/search?q=web")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["text"] == "Hello web test"
        assert data[0]["end_time"] == 15.0


def test_api_extract(test_db):
    """Test function."""
    with (
        patch("web.db.get_db", return_value=test_db),
        patch("web.exporter.extract_media") as mock_extract,
    ):
        # Make the extract_media function return a successful tuple
        mock_extract.return_value = (
            True,
            "Success",
            "/fake/media/audio.mp3",
            "/fake/media/img.jpg",
            "Hello web test",
        )

        # We assume the sentence ID is 1 since it's the first inserted in the memory DB
        response = client.post("/api/extract/1", json={"pad_start": 0.5, "pad_end": 0.5})
        assert response.status_code == 200
        data = response.json()

        assert data["success"] is True
        assert data["audio_url"] == "/media/audio.mp3"
        assert data["image_url"] == "/media/img.jpg"


@pytest.fixture
def dual_audio_db():
    """Fixture for dual audio context testing."""
    db.DB_PATH = ":memory:"
    conn = db.init_db()

    mkv_id = db.add_media(conn, "/fake/show.mkv", "mkv", "Test Show", 1, 1)
    db.add_sentences(conn, mkv_id, [("eng", 10.0, 15.0, "English translation")])

    ass_id = db.add_media(conn, "/fake/show.ass", "ass", "Test Show", 1, 1)
    db.add_sentences(conn, ass_id, [("jpn", 10.1, 14.9, "Japanese original")])

    yield conn
    conn.close()


def test_api_context_dual_audio(dual_audio_db):
    """Test function for dual audio context alignment."""
    with patch("web.db.get_db", return_value=dual_audio_db):
        response = client.get("/api/context/2")
        assert response.status_code == 200
        data = response.json()

        assert data["target_lang"] == "jpn"
        assert len(data["target_context"]) == 1
        assert data["target_context"][0]["text"] == "Japanese original"
        assert data["target_context"][0]["end_time"] == 14.9

        assert data["secondary_lang"] == "eng"
        assert len(data["secondary_context"]) == 1
        assert data["secondary_context"][0]["text"] == "English translation"
        assert data["secondary_context"][0]["end_time"] == 15.0
