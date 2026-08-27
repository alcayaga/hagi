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
        sentence = test_db.execute("SELECT id FROM sentences WHERE text = 'This is a test sentence.'").fetchone()
        sid = sentence["id"]

        success, _msg, audio_out, image_out, text = exporter.extract_media(sid, "/fake/out")

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

    def mock_exists(path):
        """Mock os.path.exists."""
        return True

    def mock_open(path, mode="r", *args, **kwargs):
        """Mock open()."""
        from io import BytesIO
        if "audio" in path:
            return BytesIO(b"fake_audio")
        return BytesIO(b"fake_img")

    with (
        patch("exporter.extract_media") as mock_extract,
        patch("exporter.db.get_db", return_value=test_db),
        patch("urllib.request.urlopen") as mock_urlopen,
        patch("os.path.exists", mock_exists),
        patch("builtins.open", mock_open)
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
            json.dumps({"result": "audio.mp3", "error": None}).encode("utf-8"),
            json.dumps({"result": "img.jpg", "error": None}).encode("utf-8"),
            json.dumps({"result": None, "error": None}).encode("utf-8"),
            json.dumps({"result": None, "error": None}).encode("utf-8")
        ]
        mock_response.__enter__.return_value = mock_response
        mock_urlopen.return_value = mock_response

        sentence = test_db.execute("SELECT id FROM sentences WHERE text = 'This is a test sentence.'").fetchone()
        sid = sentence["id"]

        success, msg = exporter.export_ankiconnect(sid, mock_config, "/fake/out")

        assert success is True
        assert mock_urlopen.call_count == 5

        # Verify findNotes request
        req1 = mock_urlopen.call_args_list[0][0][0]
        payload1 = json.loads(req1.data.decode("utf-8"))
        assert payload1["action"] == "findNotes"
        assert payload1["params"]["query"] == 'deck:"Mining" note:"Lapis"'

        # Verify storeMediaFile (audio) request
        req_audio = mock_urlopen.call_args_list[1][0][0]
        payload_audio = json.loads(req_audio.data.decode("utf-8"))
        assert payload_audio["action"] == "storeMediaFile"
        assert "audio.mp3" in payload_audio["params"]["path"]
        assert payload_audio["params"]["deleteExisting"] is False

        # Verify storeMediaFile (image) request
        req_image = mock_urlopen.call_args_list[2][0][0]
        payload_image = json.loads(req_image.data.decode("utf-8"))
        assert payload_image["action"] == "storeMediaFile"
        assert "img.jpg" in payload_image["params"]["path"]
        assert payload_image["params"]["deleteExisting"] is False

        # Verify updateNoteFields request
        req4 = mock_urlopen.call_args_list[3][0][0]
        payload4 = json.loads(req4.data.decode("utf-8"))
        assert payload4["action"] == "updateNoteFields"

        params = payload4["params"]["note"]
        assert params["id"] == 10002 # max id
        assert params["fields"]["Sentence"] == "Test Text"

        # 10.0 start time = 10s = [00:10]
        assert params["fields"]["MiscInfo"] == "Conan S01E10 - The Case [00:10]"
        assert params["fields"]["SentenceAudio"] == "[sound:audio.mp3]"
        assert params["fields"]["Picture"] == '<img src="img.jpg">'

        # Verify addTags request
        req5 = mock_urlopen.call_args_list[4][0][0]
        payload5 = json.loads(req5.data.decode("utf-8"))
        assert payload5["action"] == "addTags"
        assert payload5["params"]["notes"] == [10002]
        assert payload5["params"]["tags"] == "anime hagi"

def test_export_ankiconnect_with_note_id(test_db):
    """Test that export_ankiconnect skips findNotes when target_note_id is provided."""
    import json

    mock_config = {
        "ankiConnectUrl": "http://127.0.0.1:8765",
        "sentenceField": "Sentence",
        "audioField": "Audio",
        "imageField": "Picture",
        "tags": ["anime", "hagi"]
    }

    def mock_exists(path):
        """Mock os.path.exists."""
        return True

    def mock_open(path, mode="r", *args, **kwargs):
        """Mock open()."""
        from io import BytesIO
        if "audio" in path:
            return BytesIO(b"fake_audio")
        return BytesIO(b"fake_img")

    with (
        patch("exporter.extract_media") as mock_extract,
        patch("exporter.db.get_db", return_value=test_db),
        patch("urllib.request.urlopen") as mock_urlopen,
        patch("os.path.exists", mock_exists),
        patch("builtins.open", mock_open)
    ):
        mock_extract.return_value = (
            True, "Success", "/fake/out/audio.mp3", "/fake/out/img.jpg", "Test Text"
        )

        mock_response = MagicMock()
        mock_response.read.side_effect = [
            json.dumps({"result": "audio.mp3", "error": None}).encode("utf-8"),
            json.dumps({"result": "img.jpg", "error": None}).encode("utf-8"),
            json.dumps({"result": None, "error": None}).encode("utf-8"),
            json.dumps({"result": None, "error": None}).encode("utf-8")
        ]
        mock_response.__enter__.return_value = mock_response
        mock_urlopen.return_value = mock_response

        sentence = test_db.execute("SELECT id FROM sentences WHERE text = 'This is a test sentence.'").fetchone()
        sid = sentence["id"]

        success, msg = exporter.export_ankiconnect(sid, mock_config, "/fake/out", target_note_id=9999)

        assert success is True

        # Should have called storeMediaFile twice, updateNoteFields, and addTags
        assert mock_urlopen.call_count == 4
        req = mock_urlopen.call_args_list[2][0][0]
        payload = json.loads(req.data.decode("utf-8"))
        assert payload["action"] == "updateNoteFields"
        assert payload["params"]["note"]["id"] == 9999

        req2 = mock_urlopen.call_args_list[3][0][0]
        payload2 = json.loads(req2.data.decode("utf-8"))
        assert payload2["action"] == "addTags"
        assert payload2["params"]["notes"] == [9999]
        assert payload2["params"]["tags"] == "anime hagi"


def test_export_ankiconnect_unconstrained(test_db):
    """Test that export_ankiconnect rejects unconstrained searches."""
    mock_config = {
        "ankiConnectUrl": "http://127.0.0.1:8765"
    }

    with patch("exporter.extract_media") as mock_extract, \
         patch("exporter.db.get_db", return_value=test_db):
        mock_extract.return_value = (
            True, "Success", "/fake/out/audio.mp3", "/fake/out/img.jpg", "Test Text"
        )

        sentence = test_db.execute("SELECT id FROM sentences WHERE text = 'This is a test sentence.'").fetchone()
        sid = sentence["id"]

        success, msg = exporter.export_ankiconnect(sid, mock_config, "/fake/out")
        assert success is False
        assert "Refusing to query all Anki notes" in msg

def test_export_ankiconnect_multiple_exports(test_db):
    """Test that multiple exports of the same sentence do not overwrite media."""
    import json

    mock_config = {
        "ankiConnectUrl": "http://127.0.0.1:8765",
        "sentenceField": "Sentence",
        "audioField": "Audio",
        "imageField": "Picture"
    }

    def mock_exists(path):
        """Mock os.path.exists."""
        return True

    def mock_open(path, mode="r", *args, **kwargs):
        """Mock open()."""
        from io import BytesIO
        return BytesIO(b"fake_data")

    with (
        patch("exporter.extract_media") as mock_extract,
        patch("exporter.db.get_db", return_value=test_db),
        patch("urllib.request.urlopen") as mock_urlopen,
        patch("os.path.exists", mock_exists),
        patch("builtins.open", mock_open)
    ):
        mock_extract.return_value = (
            True, "Success", "/fake/out/audio.mp3", "/fake/out/img.jpg", "Test Text"
        )

        mock_response = MagicMock()
        mock_response.read.side_effect = [
            json.dumps({"result": "audio.mp3", "error": None}).encode("utf-8"),
            json.dumps({"result": "img.jpg", "error": None}).encode("utf-8"),
            json.dumps({"result": None, "error": None}).encode("utf-8"),

            json.dumps({"result": "audio_1.mp3", "error": None}).encode("utf-8"),
            json.dumps({"result": "img_1.jpg", "error": None}).encode("utf-8"),
            json.dumps({"result": None, "error": None}).encode("utf-8")
        ]
        mock_response.__enter__.return_value = mock_response
        mock_urlopen.return_value = mock_response

        sentence = test_db.execute("SELECT id FROM sentences WHERE text = 'This is a test sentence.'").fetchone()
        sid = sentence["id"]

        success1, _ = exporter.export_ankiconnect(sid, mock_config, "/fake/out", target_note_id=9998)
        success2, _ = exporter.export_ankiconnect(sid, mock_config, "/fake/out", target_note_id=9999)

        assert success1 is True
        assert success2 is True

        assert mock_urlopen.call_count == 6

        # Verify first export
        req_audio1 = mock_urlopen.call_args_list[0][0][0]
        payload_audio1 = json.loads(req_audio1.data.decode("utf-8"))
        assert payload_audio1["params"]["deleteExisting"] is False

        req_note1 = mock_urlopen.call_args_list[2][0][0]
        payload_note1 = json.loads(req_note1.data.decode("utf-8"))
        assert payload_note1["params"]["note"]["id"] == 9998
        assert payload_note1["params"]["note"]["fields"]["Audio"] == "[sound:audio.mp3]"

        # Verify second export
        req_note2 = mock_urlopen.call_args_list[5][0][0]
        payload_note2 = json.loads(req_note2.data.decode("utf-8"))
        assert payload_note2["params"]["note"]["id"] == 9999
        assert payload_note2["params"]["note"]["fields"]["Audio"] == "[sound:audio_1.mp3]"


def test_extract_media_concatenation(test_db):
    """Test that extract_media correctly concatenates overlapping sentences by language grammar."""
    with (
        patch("exporter.db.get_db", return_value=test_db),
        patch("os.makedirs"),
        patch("os.path.exists", return_value=True),
        patch("subprocess.run"),
    ):
        # 1. Japanese (No Punctuation)
        m1 = db.add_media(test_db, "/fake/path/ep2.mkv", "mkv_embedded")
        db.add_sentences(test_db, m1, [
            ("jpn", 8.0, 10.0, "冷凍メンチ冷凍コロッケの"),
            ("jpn", 10.0, 15.0, "生産工場の完成記念スペシャルゲストとして"),
            ("jpn", 15.0, 17.0, "来ていただきとても光栄です。")
        ])
        res = test_db.execute("SELECT id FROM sentences WHERE text = '生産工場の完成記念スペシャルゲストとして'").fetchone()
        sid_jpn_no_punct = res["id"]
        _, _, _, _, text1 = exporter.extract_media(sid_jpn_no_punct, "/fake/out")
        assert text1 == "冷凍メンチ冷凍コロッケの生産工場の完成記念スペシャルゲストとして来ていただきとても光栄です。"

        # 2. Japanese (With Punctuation)
        m2 = db.add_media(test_db, "/fake/path/ep3.mkv", "mkv_embedded")
        db.add_sentences(test_db, m2, [
            ("jpn", 8.0, 10.0, "そうだ。"),
            ("jpn", 10.0, 15.0, "行くぞ"),
            ("jpn", 15.0, 17.0, "待って")
        ])
        sid_jpn_punct = test_db.execute("SELECT id FROM sentences WHERE text = '行くぞ'").fetchone()["id"]
        _, _, _, _, text2 = exporter.extract_media(sid_jpn_punct, "/fake/out")
        assert text2 == "そうだ。<br/>行くぞ<br/>待って"

        # 3. English (Punctuation and Spacing)
        m3 = db.add_media(test_db, "/fake/path/ep4.mkv", "mkv_embedded")
        db.add_sentences(test_db, m3, [
            ("eng", 8.0, 10.0, "Hello\nthere"),
            ("eng", 10.0, 15.0, "How are you?"),
            ("eng", 15.0, 17.0, "Good.")
        ])
        sid_eng = test_db.execute("SELECT id FROM sentences WHERE text = 'How are you?'").fetchone()["id"]
        _, _, _, _, text3 = exporter.extract_media(sid_eng, "/fake/out")
        assert text3 == "Hello there. How are you? Good."
