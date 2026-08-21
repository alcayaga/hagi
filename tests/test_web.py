from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import db
from web import app

client = TestClient(app)


@pytest.fixture
def test_db():
    db.DB_PATH = ":memory:"
    conn = db.init_db()
    media_id = db.add_media(conn, "/fake/video.mkv", "mkv_embedded")
    db.add_sentences(conn, media_id, [("jpn", 10.0, 15.0, "Hello web test")])
    yield conn
    conn.close()


def test_read_main():
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Nadeshiko Search" in response.text


def test_api_search(test_db):
    with patch("web.db.get_db", return_value=test_db):
        response = client.get("/api/search?q=web")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["text"] == "Hello web test"


def test_api_extract(test_db):
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
        response = client.post(
            "/api/extract/1", json={"pad_start": 0.5, "pad_end": 0.5}
        )
        assert response.status_code == 200
        data = response.json()

        assert data["success"] is True
        assert data["audio_url"] == "/media/audio.mp3"
        assert data["image_url"] == "/media/img.jpg"
