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
        patch("exporter.os.path.exists", return_value=True),
        patch("subprocess.run") as mock_subrun,
        patch("os.replace"),
    ):
        # Get the ID of the mock sentence
        sentence = test_db.execute("SELECT id FROM sentences WHERE text = 'This is a test sentence.'").fetchone()
        sid = sentence["id"]

        success, _msg, audio_out, image_out, text, is_cached = exporter.extract_media(sid, "/fake/out")

        assert success is True
        assert audio_out.replace("\\", "/") == f"/fake/out/hagi_audio_{sid}_0.250_0.000.mp3"
        assert image_out.replace("\\", "/") == f"/fake/out/hagi_img_{sid}_0.250_0.000.jpg"

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


@pytest.mark.parametrize(
    "probe_stdout, expected_map_idx",
    [
        # Case 1: Explicit Japanese tag
        ('{"streams": [{"tags": {"language": "spa"}}, {"tags": {"language": "jpn"}}, {"tags": {"language": "eng"}}]}', "0:a:1"),
        # Case 2: No tag, but default is 1 (Shaman King Flowers edge-case)
        (
            '{"streams": [{"tags": {"language": "spa"}}, '
            '{"tags": {"language": "und"}, "disposition": {"default": 1}}, '
            '{"tags": {"language": "por"}}]}',
            "0:a:1",
        ),
        # Case 3: Default is English, so we don't pick it, but pick the undefined one
        ('{"streams": [{"tags": {"language": "eng"}, "disposition": {"default": 1}}, {"tags": {"language": "und"}}]}', "0:a:1"),
        # Case 4: No tag, no default, pick undefined one
        ('{"streams": [{"tags": {"language": "spa"}}, {"tags": {"language": "und"}}]}', "0:a:1"),
        # Case 5: 'Japanese' in title
        ('{"streams": [{"tags": {"language": "und"}}, {"tags": {"language": "und", "title": "Japanese audio"}}]}', "0:a:1"),
        # Case 6: Fallback to 0 if all else fails
        ('{"streams": [{"tags": {"language": "spa"}}, {"tags": {"language": "spa"}}]}', "0:a:0"),
    ],
)
def test_extract_media_audio_stream_selection(test_db, probe_stdout, expected_map_idx):
    """Test that extract_media correctly selects the optimal audio track based on metadata."""

    def mock_run_side_effect(cmd, *args, **kwargs):
        mock_result = MagicMock()
        if "ffprobe" in cmd:
            mock_result.returncode = 0
            mock_result.stdout = probe_stdout
            return mock_result
        else:
            mock_result.returncode = 0
            return mock_result

    with (
        patch("exporter.db.get_db", return_value=test_db),
        patch("os.makedirs"),
        patch("exporter.os.path.exists", return_value=True),
        patch("subprocess.run", side_effect=mock_run_side_effect) as mock_subrun,
        patch("os.replace"),
    ):
        sentence = test_db.execute("SELECT id FROM sentences WHERE text = 'This is a test sentence.'").fetchone()
        sid = sentence["id"]

        success, _msg, _, _, _, _ = exporter.extract_media(sid, "/fake/out")
        assert success is True

        audio_call_args = mock_subrun.call_args_list[1][0][0]
        assert "ffmpeg" in audio_call_args
        assert "-map" in audio_call_args
        map_index = audio_call_args.index("-map")
        assert audio_call_args[map_index + 1] == expected_map_idx


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
            False,
        )

        mock_writer_instance = MagicMock()
        mock_csv_writer.return_value = mock_writer_instance

        success, msg, is_cached = exporter.export_anki(1, "/fake/out")

        assert success is True

        # Verify the correct Anki formatting tags were written to the TSV
        mock_writer_instance.writerow.assert_called_once_with(["Test Text", "[sound:audio.mp3]", "<img src='img.jpg'>"])


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
        "imageField": "Picture",
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
        patch("builtins.open", mock_open),
    ):
        mock_extract.return_value = (
            True,
            "Success",
            "/fake/out/audio.mp3",
            "/fake/out/img.jpg",
            "Test Text",
            False,
        )

        # We also need to add show info to the test_db to test source_info logic
        test_db.execute(
            "UPDATE media SET show_title = ?, season = ?, episode = ?, episode_title = ? WHERE id = ?",
            ("Conan", 1, 10, "The Case <&>", 1),
        )

        mock_response = MagicMock()
        # First call is findNotes, second is updateNoteFields
        mock_response.read.side_effect = [
            json.dumps({"result": [10001, 10002], "error": None}).encode("utf-8"),
            json.dumps({"result": "audio.mp3", "error": None}).encode("utf-8"),
            json.dumps({"result": "img.jpg", "error": None}).encode("utf-8"),
            json.dumps({"result": None, "error": None}).encode("utf-8"),
            json.dumps({"result": None, "error": None}).encode("utf-8"),
        ]
        mock_response.__enter__.return_value = mock_response
        mock_urlopen.return_value = mock_response

        sentence = test_db.execute("SELECT id FROM sentences WHERE text = 'This is a test sentence.'").fetchone()
        sid = sentence["id"]

        success, msg, is_cached = exporter.export_ankiconnect(sid, mock_config, "/fake/out")

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
        assert params["id"] == 10002  # max id
        assert params["fields"]["Sentence"] == "Test Text"

        expected = '<a href="http://localhost:8000/sentence/1">Conan S01E10 - The Case &lt;&amp;&gt; [00:10]</a>'
        assert params["fields"]["MiscInfo"] == expected
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
        "tags": ["anime", "hagi"],
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
        patch("builtins.open", mock_open),
    ):
        mock_extract.return_value = (True, "Success", "/fake/out/audio.mp3", "/fake/out/img.jpg", "Test Text", False)

        mock_response = MagicMock()
        mock_response.read.side_effect = [
            json.dumps({"result": "audio.mp3", "error": None}).encode("utf-8"),
            json.dumps({"result": "img.jpg", "error": None}).encode("utf-8"),
            json.dumps({"result": None, "error": None}).encode("utf-8"),
            json.dumps({"result": None, "error": None}).encode("utf-8"),
        ]
        mock_response.__enter__.return_value = mock_response
        mock_urlopen.return_value = mock_response

        sentence = test_db.execute("SELECT id FROM sentences WHERE text = 'This is a test sentence.'").fetchone()
        sid = sentence["id"]

        success, msg, is_cached = exporter.export_ankiconnect(sid, mock_config, "/fake/out", target_note_id=9999)

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
    mock_config = {"ankiConnectUrl": "http://127.0.0.1:8765"}

    with patch("exporter.extract_media") as mock_extract, patch("exporter.db.get_db", return_value=test_db):
        mock_extract.return_value = (True, "Success", "/fake/out/audio.mp3", "/fake/out/img.jpg", "Test Text", False)

        sentence = test_db.execute("SELECT id FROM sentences WHERE text = 'This is a test sentence.'").fetchone()
        sid = sentence["id"]

        success, msg, is_cached = exporter.export_ankiconnect(sid, mock_config, "/fake/out")
        assert success is False
        assert "Refusing to query all Anki notes" in msg


def test_export_ankiconnect_multiple_exports(test_db):
    """Test that multiple exports of the same sentence do not overwrite media."""
    import json

    mock_config = {
        "ankiConnectUrl": "http://127.0.0.1:8765",
        "sentenceField": "Sentence",
        "audioField": "Audio",
        "imageField": "Picture",
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
        patch("builtins.open", mock_open),
    ):
        mock_extract.return_value = (True, "Success", "/fake/out/audio.mp3", "/fake/out/img.jpg", "Test Text", False)

        mock_response = MagicMock()
        mock_response.read.side_effect = [
            json.dumps({"result": "audio.mp3", "error": None}).encode("utf-8"),
            json.dumps({"result": "img.jpg", "error": None}).encode("utf-8"),
            json.dumps({"result": None, "error": None}).encode("utf-8"),
            json.dumps({"result": "audio_1.mp3", "error": None}).encode("utf-8"),
            json.dumps({"result": "img_1.jpg", "error": None}).encode("utf-8"),
            json.dumps({"result": None, "error": None}).encode("utf-8"),
        ]
        mock_response.__enter__.return_value = mock_response
        mock_urlopen.return_value = mock_response

        sentence = test_db.execute("SELECT id FROM sentences WHERE text = 'This is a test sentence.'").fetchone()
        sid = sentence["id"]

        success1, _, _ = exporter.export_ankiconnect(sid, mock_config, "/fake/out", target_note_id=9998)
        success2, _, _ = exporter.export_ankiconnect(sid, mock_config, "/fake/out", target_note_id=9999)

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
        patch("exporter.os.path.exists", return_value=True),
        patch("subprocess.run"),
        patch("os.replace"),
    ):
        # Bounding box will be [8.5, 16.5] for all tests since pad_start=1.5, pad_end=1.5 on target [10.0, 15.0]
        # 1. Japanese (No Punctuation)
        m1 = db.add_media(test_db, "/fake/path/ep2.mkv", "mkv_embedded")
        db.add_sentences(
            test_db,
            m1,
            [
                ("jpn", 7.0, 8.6, "本日はフライ氏の特産品である"),  # Midpoint 7.8 (Excluded: < 8.5, but overlaps > 8.5)
                ("jpn", 8.0, 10.0, "冷凍メンチ冷凍コロッケの"),  # Midpoint 9.0 (Included)
                ("jpn", 10.0, 15.0, "生産工場の完成記念スペシャルゲストとして"),  # Midpoint 12.5 (Included)
                ("jpn", 15.0, 17.0, "来ていただきとても光栄です。"),  # Midpoint 16.0 (Included)
                ("jpn", 16.4, 18.0, "名探偵毛利小五郎大先生を"),  # Midpoint 17.2 (Excluded: > 16.5, but overlaps < 16.5)
            ],
        )
        res = test_db.execute("SELECT id FROM sentences WHERE text = '生産工場の完成記念スペシャルゲストとして'").fetchone()
        sid_jpn_no_punct = res["id"]
        _, _, _, _, text1, _ = exporter.extract_media(sid_jpn_no_punct, "/fake/out", pad_start=1.5, pad_end=1.5)
        assert text1 == "冷凍メンチ冷凍コロッケの生産工場の完成記念スペシャルゲストとして来ていただきとても光栄です。"

        # 2. Japanese (With Punctuation)
        m2 = db.add_media(test_db, "/fake/path/ep3.mkv", "mkv_embedded")
        db.add_sentences(
            test_db,
            m2,
            [
                ("jpn", 7.0, 8.6, "前回のあらすじ"),  # Midpoint 7.8 (Excluded)
                ("jpn", 8.0, 10.0, "そうだ。"),  # Midpoint 9.0 (Included)
                ("jpn", 10.0, 15.0, "行くぞ"),  # Midpoint 12.5 (Included)
                ("jpn", 15.0, 17.0, "待って"),  # Midpoint 16.0 (Included)
                ("jpn", 16.4, 18.0, "次回予告"),  # Midpoint 17.2 (Excluded)
            ],
        )
        sid_jpn_punct = test_db.execute("SELECT id FROM sentences WHERE text = '行くぞ'").fetchone()["id"]
        _, _, _, _, text2, _ = exporter.extract_media(sid_jpn_punct, "/fake/out", pad_start=1.5, pad_end=1.5)
        assert text2 == "そうだ。<br/>行くぞ<br/>待って"

        # 3. English (Punctuation and Spacing)
        m3 = db.add_media(test_db, "/fake/path/ep4.mkv", "mkv_embedded")
        db.add_sentences(
            test_db,
            m3,
            [
                ("eng", 7.0, 8.6, "Previously on"),  # Midpoint 7.8 (Excluded)
                ("eng", 8.0, 10.0, "Hello\nthere"),  # Midpoint 9.0 (Included)
                ("eng", 10.0, 15.0, "How are you?"),  # Midpoint 12.5 (Included)
                ("eng", 15.0, 17.0, "Good."),  # Midpoint 16.0 (Included)
                ("eng", 16.4, 18.0, "Next time"),  # Midpoint 17.2 (Excluded)
            ],
        )
        sid_eng = test_db.execute("SELECT id FROM sentences WHERE text = 'How are you?'").fetchone()["id"]
        _, _, _, _, text3, _ = exporter.extract_media(sid_eng, "/fake/out", pad_start=1.5, pad_end=1.5)
        assert text3 == "Hello there. How are you? Good."


def test_extract_media_external_subtitle(test_db):
    """Test that extract_media correctly resolves the video path for an external subtitle using the Plex standard."""
    with (
        patch("exporter.db.get_db", return_value=test_db),
        patch("os.makedirs"),
        patch("subprocess.run") as mock_subrun,
        patch("os.replace"),
    ):
        # Create an external subtitle media
        m_id = db.add_media(test_db, "/fake/path/Belle (2021).en.srt", "subtitle")
        db.add_sentences(test_db, m_id, [("eng", 10.0, 15.0, "Testing external subtitle")])
        sid = test_db.execute("SELECT id FROM sentences WHERE text = 'Testing external subtitle'").fetchone()["id"]

        def mock_exists(path):
            """Mock os.path.exists so it only returns True for the stripped .mkv path."""
            if path == "/fake/path/Belle (2021).mkv":
                return True
            return False

        with patch("os.path.exists", side_effect=mock_exists):
            success, msg, audio_out, image_out, text, is_cached = exporter.extract_media(sid, "/fake/out")

            # Since subprocess.run is mocked, we expect success because the video path resolved
            assert success is True
            assert text == "Testing external subtitle"

        # Now test the fallback when no video matches the stripped path
        def mock_exists_fallback(path):
            """Mock os.path.exists so it falls back to .en.mkv and finds it."""
            if path == "/fake/path/Belle (2021).en.mkv":
                return True
            return False

        with patch("os.path.exists", side_effect=mock_exists_fallback):
            success, msg, _, _, _, _ = exporter.extract_media(sid, "/fake/out")
            assert success is True

        # Now test when both exist, stripped is preferred
        def mock_exists_both(path) -> bool:
            """Mock os.path.exists so it returns True for both the stripped and unstripped video paths."""
            if path in ["/fake/path/Belle (2021).mkv", "/fake/path/Belle (2021).en.mkv"]:
                return True
            return False

        with patch("os.path.exists", side_effect=mock_exists_both):
            success, _, _, _, _, _ = exporter.extract_media(sid, "/fake/out")
            assert success is True
            # Verify the stripped path was passed to ffprobe
            ffprobe_args = mock_subrun.call_args_list[-2][0][0]  # The ffprobe command
            assert "/fake/path/Belle (2021).mkv" in ffprobe_args


def test_export_ankiconnect_highlight(test_db):
    """Test that export_ankiconnect highlights search terms correctly in a specific field."""
    import json

    mock_config = {
        "ankiConnectUrl": "http://127.0.0.1:8765",
        "deck": "Mining",
        "noteType": "Lapis",
        "sentenceField": "Sentence",
        "sentenceHighlightedField": "SentenceWithHighlight",
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
            "This is a cool test",
            False,
        )

        test_db.execute(
            "UPDATE media SET show_title = ?, season = ?, episode = ?, episode_title = ? WHERE id = ?",
            ("Conan", 1, 10, "The Case", 1),
        )
        sid = test_db.execute("SELECT id FROM sentences LIMIT 1").fetchone()["id"]

        class MockResponse:
            """Mock urllib response."""

            def read(self):
                """Return mocked JSON response bytes."""
                return json.dumps({"result": [9999], "error": None}).encode("utf-8")

            def __enter__(self):
                """Enter context."""
                return self

            def __exit__(self, *args):
                """Exit context."""
                pass

        mock_urlopen.return_value = MockResponse()

        success, msg, is_cached = exporter.export_ankiconnect(
            sid, mock_config, "/fake/out", target_note_id=9999, search_query="cool test"
        )
        assert success is True

        calls = mock_urlopen.call_args_list
        update_call = next(c for c in calls if b'"action": "updateNoteFields"' in c[0][0].data)
        data = json.loads(update_call[0][0].data.decode("utf-8"))
        fields = data["params"]["note"]["fields"]

        assert fields["Sentence"] == "This is a cool test"
        assert fields["SentenceWithHighlight"] == "This is a <b>cool</b> <b>test</b>"


def test_cache_and_cleanup(test_db):
    """Test that extract_media skips extraction on cache hit and cleanup works."""
    import os
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        # Create dummy media and sentence
        sid = test_db.execute("SELECT id FROM sentences LIMIT 1").fetchone()["id"]

        # Test 1: First extraction
        with (
            patch("exporter.db.get_db", return_value=test_db),
            patch("exporter.os.path.exists", return_value=True),
            patch("subprocess.run") as mock_run,
        ):
            # Make sure ffprobe succeeds
            mock_probe = MagicMock()
            mock_probe.returncode = 0
            mock_probe.stdout = '{"streams": [{"tags": {"language": "jpn"}}]}'

            def mock_side_effect(cmd, *args, **kwargs):
                if "ffprobe" in cmd:
                    return mock_probe
                # For ffmpeg create a dummy file
                if "mp3" in cmd[-1]:
                    with open(cmd[-1], "w") as f:
                        f.write("audio")
                elif "jpg" in cmd[-1]:
                    with open(cmd[-1], "w") as f:
                        f.write("image")
                res = MagicMock()
                res.returncode = 0
                return res

            mock_run.side_effect = mock_side_effect

            success, msg, a_out, i_out, text, is_cached = exporter.extract_media(sid, tmpdir)
            assert success is True
            assert is_cached is False
            assert mock_run.call_count == 3

        # Change mtime of generated files back in time
        import time

        old_time = time.time() - 10000
        os.utime(a_out, (old_time, old_time))
        os.utime(i_out, (old_time, old_time))

        # Test 2: Second extraction (Cache Hit)
        with (
            patch("exporter.db.get_db", return_value=test_db),
            patch("exporter.os.path.exists", return_value=True),
            patch("subprocess.run") as mock_run,
        ):
            success, msg, a_out2, i_out2, text, is_cached = exporter.extract_media(sid, tmpdir)
            assert success is True
            assert is_cached is True
            assert mock_run.call_count == 0  # Should NOT call ffmpeg

            # Check if utime was updated
            assert os.stat(a_out2).st_mtime > old_time

        # Test 3: Cleanup
        # Create a large dummy file
        dummy = os.path.join(tmpdir, "hagi_audio_9999_0.250_0.000.mp3")
        with open(dummy, "wb") as f:
            f.write(b"0" * (6 * 1024 * 1024))  # 6 MB

        os.utime(dummy, (time.time() - 5000, time.time() - 5000))

        exporter.cleanup_media_cache(tmpdir, max_mb=5)
        # The 6MB file should be deleted because 6MB > 5MB, leaving the newer cached files intact
        assert not os.path.exists(dummy)
        assert os.path.exists(a_out)
