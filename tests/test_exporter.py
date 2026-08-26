"""Test module."""

from unittest.mock import MagicMock, patch

import pytest

import db
import exporter


@pytest.fixture
def test_db():
    """Test function."""
    db.DB_PATH = ":memory:"
    conn = db.init_db()

    # Add a mock sentence for testing
    media_id = db.add_media(conn, "/fake/path/episode1.mkv", "mkv_embedded")
    db.add_sentences(conn, media_id, [("jpn", 10.0, 15.0, "This is a test sentence.")])

    yield conn
    conn.close()


def test_extract_media(test_db):
    """Test that extract_media correctly queries DB and calls ffmpeg with correct parameters."""
    with (
        patch("exporter.db.get_db", return_value=test_db),
        patch("os.makedirs"),
        patch("os.path.exists", return_value=True),
        patch("subprocess.run") as mock_subrun,
    ):
        # Get the ID of the mock sentence
        sentence = test_db.execute("SELECT id FROM sentences LIMIT 1").fetchone()
        sid = sentence["id"]

        success, msg, audio_out, image_out, text = exporter.extract_media(sid, "/fake/out")

        assert success is True
        assert audio_out.replace("\\", "/") == f"/fake/out/hagi_audio_{sid}.mp3"
        assert image_out.replace("\\", "/") == f"/fake/out/hagi_img_{sid}.jpg"
        assert text == "This is a test sentence."

        # Verify subprocess.run was called three times (ffprobe, ffmpeg audio, ffmpeg video)
        assert mock_subrun.call_count == 3

        # Verify the ffprobe command
        ffprobe_call_args = mock_subrun.call_args_list[0][0][0]
        assert "ffprobe" in ffprobe_call_args

        # Verify the audio extraction command used the correct single-stream map parameter (-map 0:a:0)
        # Assuming the mock returns a failed ffprobe so it falls back to 0
        audio_call_args = mock_subrun.call_args_list[1][0][0]
        assert "ffmpeg" in audio_call_args

        # It must use '0:a:0' (or the detected index) and absolutely not 'a' to prevent exit code 234
        assert "-map" in audio_call_args
        map_index = audio_call_args.index("-map")
        assert audio_call_args[map_index + 1] == "0:a:0"


def test_export_anki(test_db):
    """Test that export_anki generates the correct TSV line."""
    with (
        patch("exporter.extract_media") as mock_extract,
        patch("builtins.open", new_callable=MagicMock),
        patch("csv.writer") as mock_csv_writer,
    ):
        mock_extract.return_value = (
            True,
            "Success",
            "/fake/out/audio.mp3",
            "/fake/out/img.jpg",
            "Test Text",
        )

        mock_writer_instance = MagicMock()
        mock_csv_writer.return_value = mock_writer_instance

        success, msg = exporter.export_anki(1, "/fake/out")

        assert success is True

        # Verify the correct Anki formatting tags were written to the TSV
        mock_writer_instance.writerow.assert_called_once_with(
            ["Test Text", "[sound:audio.mp3]", "<img src='img.jpg'>"]
        )

def test_export_ankiconnect(test_db):
    """Test that export_ankiconnect properly interacts with AnkiConnect API."""
    import json

    mock_config = {
        "ankiConnectUrl": "http://127.0.0.1:8765",
        "deck": "Mining",
        "noteType": "Lapis",
        "sentenceField": "Sentence",
        "sourceField": "MiscInfo",
        "tags": ["anime", "hagi"],
        "audioField": "SentenceAudio",
        "imageField": "Picture"
    }

    with (
        patch("exporter.extract_media") as mock_extract,
        patch("exporter.db.get_db", return_value=test_db),
        patch("urllib.request.urlopen") as mock_urlopen,
    ):
        mock_extract.return_value = (
            True,
            "Success",
            "/fake/out/audio.mp3",
            "/fake/out/img.jpg",
            "Test Text",
        )

        # We also need to add show info to the test_db to test source_info logic
        test_db.execute(
            "UPDATE media SET show_title = ?, season = ?, episode = ?, episode_title = ? WHERE id = ?",
            ("Conan", 1, 10, "The Case", 1)
        )

        mock_response = MagicMock()
        # First call is findNotes, second is updateNoteFields
        mock_response.read.side_effect = [
            json.dumps({"result": [10001, 10002], "error": None}).encode("utf-8"),
            json.dumps({"result": None, "error": None}).encode("utf-8"),
            json.dumps({"result": None, "error": None}).encode("utf-8")
        ]
        mock_response.__enter__.return_value = mock_response
        mock_urlopen.return_value = mock_response

        sentence = test_db.execute("SELECT id FROM sentences LIMIT 1").fetchone()
        sid = sentence["id"]

        success, msg = exporter.export_ankiconnect(sid, mock_config, "/fake/out")

        assert success is True
        assert mock_urlopen.call_count == 3

        # Verify findNotes request
        req1 = mock_urlopen.call_args_list[0][0][0]
        payload1 = json.loads(req1.data.decode("utf-8"))
        assert payload1["action"] == "findNotes"
        assert payload1["params"]["query"] == 'deck:"Mining" note:"Lapis"'

        # Verify updateNoteFields request
        req2 = mock_urlopen.call_args_list[1][0][0]
        payload2 = json.loads(req2.data.decode("utf-8"))
        assert payload2["action"] == "updateNoteFields"

        params = payload2["params"]["note"]
        assert params["id"] == 10002 # max id
        assert params["fields"]["Sentence"] == "Test Text"

        # 10.0 start time = 10s = [00:10]
        assert params["fields"]["MiscInfo"] == "Conan S01E10 - The Case [00:10]"

        assert params["audio"][0]["fields"] == ["SentenceAudio"]
        assert "audio.mp3" in params["audio"][0]["path"]

        assert params["picture"][0]["fields"] == ["Picture"]
        assert "img.jpg" in params["picture"][0]["path"]

        # Verify addTags request
        req3 = mock_urlopen.call_args_list[2][0][0]
        payload3 = json.loads(req3.data.decode("utf-8"))
        assert payload3["action"] == "addTags"
        assert payload3["params"]["notes"] == [10002]
        assert payload3["params"]["tags"] == "anime hagi"

def test_export_ankiconnect_with_note_id(test_db):
    """Test that export_ankiconnect skips findNotes when target_note_id is provided."""
    import json

    mock_config = {
        "ankiConnectUrl": "http://127.0.0.1:8765",
        "sentenceField": "Sentence",
        "tags": ["anime", "hagi"]
    }

    with (
        patch("exporter.extract_media") as mock_extract,
        patch("exporter.db.get_db", return_value=test_db),
        patch("urllib.request.urlopen") as mock_urlopen,
    ):
        mock_extract.return_value = (
            True, "Success", "/fake/out/audio.mp3", "/fake/out/img.jpg", "Test Text"
        )

        mock_response = MagicMock()
        mock_response.read.side_effect = [
            json.dumps({"result": None, "error": None}).encode("utf-8"),
            json.dumps({"result": None, "error": None}).encode("utf-8")
        ]
        mock_response.__enter__.return_value = mock_response
        mock_urlopen.return_value = mock_response

        sentence = test_db.execute("SELECT id FROM sentences LIMIT 1").fetchone()
        sid = sentence["id"]

        success, msg = exporter.export_ankiconnect(sid, mock_config, "/fake/out", target_note_id=9999)

        assert success is True

        # Should have called updateNoteFields and addTags
        assert mock_urlopen.call_count == 2
        req = mock_urlopen.call_args_list[0][0][0]
        payload = json.loads(req.data.decode("utf-8"))
        assert payload["action"] == "updateNoteFields"
        assert payload["params"]["note"]["id"] == 9999

        req2 = mock_urlopen.call_args_list[1][0][0]
        payload2 = json.loads(req2.data.decode("utf-8"))
        assert payload2["action"] == "addTags"
        assert payload2["params"]["notes"] == [9999]
        assert payload2["params"]["tags"] == "anime hagi"

