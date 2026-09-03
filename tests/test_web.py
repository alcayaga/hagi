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
    assert "Hagi Search" in response.text

    response = client.get("/search/web")
    assert response.status_code == 200
    assert "Hagi Search" in response.text

    response = client.get("/search/and/or")
    assert response.status_code == 200
    assert "Hagi Search" in response.text

    response = client.get("/sentence/1")
    assert response.status_code == 200
    assert "Hagi Search" in response.text

    response = client.get("/context/1")
    assert response.status_code == 200
    assert "Hagi Search" in response.text


def test_api_sentence(test_db):
    """Test function."""
    with patch("web.db.get_db", return_value=test_db):
        response = client.get("/api/sentence/1")
        assert response.status_code == 200
        data = response.json()
        assert data["text"] == "Hello web test"

        response = client.get("/api/sentence/999")
        assert response.status_code == 404


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
            False,
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


def test_export_anki_endpoint(test_db):
    """Test the POST /api/anki endpoint."""
    import json

    # We mock os.path.exists and open to simulate a local config.json
    def mock_exists(path):
        """Mock os.path.exists."""
        if path == "config.json":
            return True
        return False

    def mock_open(path, mode="r", *args, **kwargs):
        """Mock open()."""
        if path == "config.json":
            from io import StringIO

            return StringIO(json.dumps({"ankiConnectUrl": "mock"}))
        return open(path, mode, *args, **kwargs)

    with (
        patch("web.db.get_db", return_value=test_db),
        patch("os.path.exists", mock_exists),
        patch("builtins.open", mock_open),
        patch("web.exporter.export_ankiconnect") as mock_ankiconnect,
    ):
        mock_ankiconnect.return_value = (True, "Successfully updated note", False)

        response = client.post(
            "/api/anki/1",
            json={"pad_start": 0.2, "pad_end": 0.8},
        )
        assert response.status_code == 200
        assert response.json()["success"] is True
        assert response.json()["message"] == "Successfully updated note"

        # Verify it passed the correct params down to export_ankiconnect
        mock_ankiconnect.assert_called_once()
        args = mock_ankiconnect.call_args[0]
        assert args[0] == 1  # sentence_id
        assert args[1] == {"ankiConnectUrl": "mock"}  # config
        assert args[3] == 0.2  # pad_start
        assert args[4] == 0.8  # pad_end
        assert mock_ankiconnect.call_args.kwargs["base_url"] == "http://localhost:8000"


def test_export_anki_endpoint_with_nid(test_db):
    """Test the POST /api/anki endpoint when target_note_id is provided."""
    import json

    def mock_exists(path):
        """Mock os.path.exists."""
        return path == "config.json"

    def mock_open(path, mode="r", *args, **kwargs):
        """Mock open()."""
        if path == "config.json":
            from io import StringIO

            return StringIO(json.dumps({"ankiConnectUrl": "mock"}))
        return open(path, mode, *args, **kwargs)

    with (
        patch("web.db.get_db", return_value=test_db),
        patch("os.path.exists", mock_exists),
        patch("builtins.open", mock_open),
        patch("web.exporter.export_ankiconnect") as mock_ankiconnect,
    ):
        mock_ankiconnect.return_value = (True, "Successfully updated note", False)

        response = client.post(
            "/api/anki/1",
            json={"pad_start": 0.2, "pad_end": 0.8, "target_note_id": 12345},
        )
        assert response.status_code == 200
        assert response.json()["success"] is True

        mock_ankiconnect.assert_called_once()
        assert mock_ankiconnect.call_args.kwargs["target_note_id"] == 12345


def test_export_anki_endpoint_invalid_nid(test_db):
    """Test the POST /api/anki endpoint when target_note_id is invalid (e.g. 0 or string)."""
    # 0 is invalid since gt=0
    response = client.post(
        "/api/anki/1",
        json={"pad_start": 0.2, "pad_end": 0.8, "target_note_id": 0},
    )
    assert response.status_code == 422

    # Negative is invalid
    response = client.post(
        "/api/anki/1",
        json={"pad_start": 0.2, "pad_end": 0.8, "target_note_id": -5},
    )
    assert response.status_code == 422

    # Non-integer string is invalid (Pydantic will reject or try to parse, but "abc" fails)
    response = client.post(
        "/api/anki/1",
        json={"pad_start": 0.2, "pad_end": 0.8, "target_note_id": "abc"},
    )
    assert response.status_code == 422


def test_export_anki_endpoint_invalid_config(test_db):
    """Test the POST /api/anki endpoint with invalid config (e.g. array)."""
    import json

    def mock_exists(path):
        """Mock os.path.exists."""
        if path == "config.json":
            return True
        return False

    def mock_open(path, mode="r", *args, **kwargs):
        """Mock open()."""
        if path == "config.json":
            from io import StringIO

            return StringIO(json.dumps([]))
        return open(path, mode, *args, **kwargs)

    with (
        patch("web.db.get_db", return_value=test_db),
        patch("os.path.exists", mock_exists),
        patch("builtins.open", mock_open),
        patch("web.exporter.extract_media") as mock_extract,
    ):
        mock_extract.return_value = (True, "Success", "/fake/out/audio.mp3", "/fake/out/img.jpg", "Test Text", False)

        response = client.post(
            "/api/anki/1",
            json={"pad_start": 0.2, "pad_end": 0.8},
        )
        assert response.status_code == 500
        assert "Invalid configuration format" in response.json()["detail"]


def test_export_anki_endpoint_invalid_media_urls():
    """Test that malformed mediaBaseUrl config triggers the localhost fallback."""
    invalid_urls = [
        "http://:8000",  # Missing host
        "http://localhost:abc",  # Invalid port
        "http://",  # Missing host
        "http://example.com/?q=1",  # Has query
        "http://example.com/#frag",  # Has fragment
        "http://example.com/?",  # Bare query delimiter
        "http://example.com/#",  # Bare fragment delimiter
    ]

    for url in invalid_urls:

        def mock_exists(path):
            return path == "config.json"

        def mock_open(*args, url=url, **kwargs):
            from io import StringIO
            import json

            return StringIO(json.dumps({"mediaBaseUrl": url, "deck": "Default", "noteType": "Basic"}))

        with (
            patch("os.path.exists", mock_exists),
            patch("builtins.open", mock_open),
            patch("web.exporter.export_ankiconnect") as mock_anki,
            patch("web.exporter.extract_media") as mock_extract,
        ):
            mock_anki.return_value = (True, "Success", False)
            mock_extract.return_value = (True, "Success", "/fake/out/audio.mp3", "/fake/out/img.jpg", "Test Text", False)
            client.post("/api/anki/1", json={"pad_start": 0.2, "pad_end": 0.8})
            assert mock_anki.call_args.kwargs["base_url"] == "http://localhost:8000"


def test_api_extract_exception_exposure(test_db):
    """Test that POST /api/extract returns generic 500 errors."""
    with patch("web.db.get_db", return_value=test_db), patch("web.exporter.extract_media") as mock_extract:
        # Mock exporter returning a generic string
        mock_extract.return_value = (False, "An internal error occurred during extraction.", None, None, None, False)

        response = client.post("/api/extract/1", json={"pad_start": 0.5, "pad_end": 0.5})
        assert response.status_code == 500
        assert response.json()["detail"] == "An internal error occurred during extraction."
