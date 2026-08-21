from unittest.mock import MagicMock, patch

import pytest

import db
import exporter


@pytest.fixture
def test_db():
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

        success, msg, audio_out, image_out, text = exporter.extract_media(
            sid, "/fake/out"
        )

        assert success is True
        assert audio_out.replace("\\", "/") == f"/fake/out/nadeshiko_audio_{sid}.mp3"
        assert image_out.replace("\\", "/") == f"/fake/out/nadeshiko_img_{sid}.jpg"
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
        patch("builtins.open", new_callable=MagicMock) as mock_open,
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
