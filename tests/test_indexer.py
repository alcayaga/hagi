import json
from unittest.mock import MagicMock, patch

import pytest

import db
import indexer


@pytest.fixture
def test_db():
    # Use in-memory DB for tests
    db.DB_PATH = ":memory:"
    conn = db.init_db()
    yield conn
    conn.close()


def test_incremental_indexing_skips(test_db):
    """Ensure files already present in the media table are not parsed again."""
    db.add_media(test_db, "/fake/path/episode1.srt", "subtitle")

    with patch("os.walk") as mock_walk, patch("indexer.get_db", return_value=test_db):
        mock_walk.return_value = [("/fake/path", [], ["episode1.srt"])]

        with patch("indexer.pysubs2.load") as mock_load:
            indexer.index_directory("/fake/path")

            mock_load.assert_not_called()


def test_language_detection_external_subs(test_db):
    """Ensure external subtitle files infer language correctly from their text content."""
    with patch("os.walk") as mock_walk, patch("indexer.get_db", return_value=test_db):
        mock_walk.return_value = [("/fake/path", [], ["ep1.srt", "ep2.srt", "ep3.srt"])]

        with patch("indexer.pysubs2.load") as mock_load:
            # Setup mock returns: English, Japanese, Spanish
            def mock_load_side_effect(path, **kwargs):
                mock_subs = MagicMock()
                mock_line = MagicMock()
                mock_line.start = 0
                mock_line.end = 1000

                if "ep1" in path:
                    mock_line.plaintext = "Just a normal english sentence."
                elif "ep2" in path:
                    mock_line.plaintext = "私は猫です"
                else:
                    mock_line.plaintext = "¿Dónde está la biblioteca?"

                mock_subs.__iter__.return_value = [mock_line]
                return mock_subs

            mock_load.side_effect = mock_load_side_effect

            indexer.index_directory("/fake/path")

            # Query the database to verify languages were applied correctly
            sentences = test_db.execute(
                "SELECT language FROM sentences ORDER BY id"
            ).fetchall()
            langs = [s["language"] for s in sentences]

            assert "eng" in langs
            assert "jpn" in langs
            assert "spa" in langs


def test_mkv_embedded_extraction(test_db):
    """Ensure MKV files are probed and multiple subtitle streams are extracted with proper tags."""
    with (
        patch("os.walk") as mock_walk,
        patch("indexer.get_db", return_value=test_db),
        patch("subprocess.run") as mock_subrun,
        patch("indexer.pysubs2.load") as mock_load,
    ):
        mock_walk.return_value = [("/fake/path", [], ["episode1.mkv"])]

        # Mock the ffprobe output: two streams, Japanese and English
        probe_output = {
            "streams": [{"tags": {"language": "jpn"}}, {"tags": {"language": "eng"}}]
        }
        mock_res = MagicMock()
        mock_res.stdout = json.dumps(probe_output)
        mock_res.returncode = 0
        mock_subrun.return_value = mock_res

        # Mock the pysubs2 parser
        mock_subs = MagicMock()
        mock_line = MagicMock()
        mock_line.plaintext = "Embedded text"
        mock_line.start = 0
        mock_line.end = 1000
        mock_subs.__iter__.return_value = [mock_line]
        mock_load.return_value = mock_subs

        indexer.index_directory("/fake/path")

        # Verify subprocess was called 3 times total:
        # 1x for ffprobe, 2x for ffmpeg (extracting jpn and eng)
        assert mock_subrun.call_count == 3

        # Verify the sentences were added with the correct languages
        sentences = test_db.execute("SELECT language, text FROM sentences").fetchall()
        assert len(sentences) == 2
        langs = [s["language"] for s in sentences]

        assert "jpn" in langs
        assert "eng" in langs


def test_add_media_lastrowid_bug(test_db):
    """Ensure add_media doesn't return the ID of a recently inserted sentence when adding a duplicate media path."""
    media_id_1 = db.add_media(test_db, "/path/to/video.mkv", "mkv_embedded")
    db.add_sentences(test_db, media_id_1, [("eng", 0, 1, "Hello")])
    media_id_2 = db.add_media(test_db, "/path/to/video.mkv", "mkv_embedded")

    assert media_id_1 == media_id_2
