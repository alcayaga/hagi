"""Test module."""

import json
from unittest.mock import MagicMock, patch

import pytest

import db
import indexer


@pytest.fixture
def test_db():
    # Use in-memory DB for tests
    """Test function."""
    db.DB_PATH = ":memory:"
    conn = db.init_db()
    yield conn
    conn.close()


def test_incremental_indexing_skips(test_db):
    """Ensure files already present in the media table are not parsed again."""
    db.add_media(test_db, "/fake/path/episode1.srt", "subtitle")

    with patch("os.walk") as mock_walk, patch("indexer.get_db", return_value=test_db), patch("os.path.exists", return_value=True):
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
                """Test function."""
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
            sentences = test_db.execute("SELECT language FROM sentences ORDER BY id").fetchall()
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
        probe_output = {"streams": [{"tags": {"language": "jpn"}}, {"tags": {"language": "eng"}}]}
        mock_res = MagicMock()
        mock_res.stdout = json.dumps(probe_output)
        mock_res.returncode = 0
        mock_subrun.return_value = mock_res

        # Mock the pysubs2 parser
        mock_subs = MagicMock()
        mock_line = MagicMock()
        mock_line.plaintext = "こんにちは Embedded text"
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

def test_plex_cache_unpacking(test_db):
    """Ensure process_subs correctly unpacks 4 values from the plex_path_cache including episode_title."""
    # Seed the cache with a 4-tuple representing (show_title, season, episode, episode_title)
    indexer.plex_path_cache["episode1"] = ("My Show", 1, 5, "The Best Episode")
    
    with patch("indexer.get_db", return_value=test_db):
        with patch("indexer.pysubs2.load") as mock_load:
            mock_subs = MagicMock()
            mock_line = MagicMock()
            mock_line.start = 0
            mock_line.end = 1000
            mock_line.plaintext = "Testing tuple unpacking"
            mock_subs.__iter__.return_value = [mock_line]
            mock_load.return_value = mock_subs
            
            # This should not raise a ValueError
            indexer.process_subs(test_db, "/fake/path/episode1.srt", mock_subs, "subtitle", "eng")
            
            row = test_db.execute("SELECT show_title, episode_title FROM media WHERE path = '/fake/path/episode1.srt'").fetchone()
            assert row is not None
            assert row["show_title"] == "My Show"
            assert row["episode_title"] == "The Best Episode"

def test_mkv_subtitle_filtering(test_db):
    """Ensure we filter unwanted tracks, skip SDH, and catch Japanese mistagging."""
    with (
        patch("os.walk") as mock_walk,
        patch("indexer.get_db", return_value=test_db),
        patch("subprocess.run") as mock_subrun,
        patch("indexer.pysubs2.load") as mock_load,
    ):
        mock_walk.return_value = [("/fake/path", [], ["episode1.mkv"])]

        probe_output = {"streams": [
            {"index": 0, "tags": {"language": "fre"}}, # Skipped
            {"index": 1, "tags": {"language": "eng", "title": "Forced"}}, # Skipped because clean exists
            {"index": 2, "tags": {"language": "eng", "title": "Dialogue"}}, # Picked (clean)
            {"index": 3, "tags": {"language": "spa", "title": "Castilian"}}, # Skipped because Latin American exists
            {"index": 4, "tags": {"language": "spa", "title": "Latin American"}}, # Picked (Latin priority)
            {"index": 5, "tags": {"language": "jpn", "title": "Full Subtitles"}}, # Picked, but we will mock it to be English text!
            {"index": 6, "tags": {"language": "unknown"}}, # Picked, will be detected as English text!
        ]}
        mock_res = MagicMock()
        mock_res.stdout = json.dumps(probe_output)
        mock_res.returncode = 0
        mock_subrun.return_value = mock_res

        mock_subs = MagicMock()
        mock_line = MagicMock()
        mock_line.plaintext = "English text without Japanese chars"
        mock_line.start = 0
        mock_line.end = 1000
        mock_subs.__iter__.return_value = [mock_line]
        mock_load.return_value = mock_subs

        indexer.index_directory("/fake/path")

        # Verify subprocess was called 5 times total:
        # 1x ffprobe, 4x ffmpeg (eng, spa, jpn, unknown)
        assert mock_subrun.call_count == 5

        sentences = test_db.execute("SELECT language, text FROM sentences").fetchall()
        # English, Spanish, mistagged Japanese track, and untagged track
        assert len(sentences) == 4
        langs = [s["language"] for s in sentences]
        
        assert langs.count("eng") == 3
        assert langs.count("spa") == 1
        assert langs.count("jpn") == 0


def test_mkv_skip_all_subtitles(test_db):
    """Ensure media is still added even if no subtitle tracks match."""
    with (
        patch("os.walk") as mock_walk,
        patch("indexer.get_db", return_value=test_db),
        patch("subprocess.run") as mock_subrun,
    ):
        mock_walk.return_value = [("/fake/path", [], ["episode1.mkv"])]

        probe_output = {"streams": [
            {"index": 0, "tags": {"language": "fre"}},
            {"index": 1, "tags": {"language": "ger"}},
        ]}
        mock_res = MagicMock()
        mock_res.stdout = json.dumps(probe_output)
        mock_res.returncode = 0
        mock_subrun.return_value = mock_res

        indexer.index_directory("/fake/path")

        # Verify subprocess was called exactly 1 time (only ffprobe, no ffmpeg)
        assert mock_subrun.call_count == 1

        # Sentences should be empty
        sentences = test_db.execute("SELECT * FROM sentences").fetchall()
        assert len(sentences) == 0

        # But media should STILL be added!
        media = test_db.execute("SELECT * FROM media").fetchall()
        assert len(media) == 1
        assert media[0]["path"] == "/fake/path/episode1.mkv"

def test_build_plex_cache_filtering():
    """Ensure build_plex_cache respects plex_libraries from config.json."""
    from unittest.mock import mock_open
    
    # Create the mock setup inside
    with patch("indexer.plex") as mock_plex, patch("os.path.exists") as mock_exists:
        # Mock indexer.plex
        mock_section_anime = MagicMock()
        mock_section_anime.title = "Anime"
        mock_section_anime.key = "4"
        mock_section_anime.type = "show"
        mock_episode_anime = MagicMock()
        mock_episode_anime.grandparentTitle = "Anime Show"
        mock_episode_anime.parentIndex = 1
        mock_episode_anime.index = 1
        mock_episode_anime.title = "Ep 1"
        mock_part_anime = MagicMock()
        mock_part_anime.file = "/path/anime_ep1.mkv"
        mock_media_anime = MagicMock()
        mock_media_anime.parts = [mock_part_anime]
        mock_episode_anime.media = [mock_media_anime]
        mock_section_anime.search.return_value = [mock_episode_anime]

        mock_section_movies = MagicMock()
        mock_section_movies.title = "Movies"
        mock_section_movies.key = "5"
        mock_section_movies.type = "movie"
        mock_movie = MagicMock()
        mock_movie.title = "A Movie"
        mock_part_movie = MagicMock()
        mock_part_movie.file = "/path/movie1.mkv"
        mock_media_movie = MagicMock()
        mock_media_movie.parts = [mock_part_movie]
        mock_movie.media = [mock_media_movie]
        mock_section_movies.search.return_value = [mock_movie]
        
        mock_plex.library.sections.return_value = [mock_section_anime, mock_section_movies]
        mock_exists.return_value = True

        # 1. Test filtering by title "Anime"
        indexer._plex_cache_built = False
        indexer.plex_path_cache = {}
        with patch("builtins.open", mock_open(read_data='{"plex_libraries": ["Anime"]}')):
            indexer.build_plex_cache()
        assert "anime_ep1" in indexer.plex_path_cache
        assert "movie1" not in indexer.plex_path_cache

        # 2. Test filtering by ID "5"
        indexer._plex_cache_built = False
        indexer.plex_path_cache = {}
        with patch("builtins.open", mock_open(read_data='{"plex_libraries": ["5"]}')):
            indexer.build_plex_cache()
        assert "anime_ep1" not in indexer.plex_path_cache
        assert "movie1" in indexer.plex_path_cache
        
        # 3. Test no filter (empty config)
        indexer._plex_cache_built = False
        indexer.plex_path_cache = {}
        with patch("builtins.open", mock_open(read_data='{}')):
            indexer.build_plex_cache()
        assert "anime_ep1" in indexer.plex_path_cache
        assert "movie1" in indexer.plex_path_cache

def test_language_detection_por_spa():
    """Ensure Portuguese is distinguished from Spanish."""
    mock_subs_por = MagicMock()
    mock_line_por = MagicMock()
    mock_line_por.plaintext = "Sim, o caso está encerrado. Tudo graças ao detetive Mouri."
    mock_subs_por.__iter__.return_value = [mock_line_por]
    
    mock_subs_spa = MagicMock()
    mock_line_spa = MagicMock()
    mock_line_spa.plaintext = "Estación de la ciudad de Beika. ¿Qué pasa?"
    mock_subs_spa.__iter__.return_value = [mock_line_spa]
    
    assert indexer.detect_language(mock_subs_por) == "por"
    assert indexer.detect_language(mock_subs_spa) == "spa"

def test_incremental_indexing_removes_missing_files(test_db):
    """Ensure files that are in the database but no longer on disk are removed during indexing."""
    # Add a file that will be simulated as deleted
    deleted_media_id = db.add_media(test_db, "/fake/path/deleted_episode.srt", "subtitle")
    db.add_sentences(test_db, deleted_media_id, [("eng", 0, 1, "Deleted sentence")])
    
    # Add a file in a DIFFERENT directory that is also "deleted" but shouldn't be touched by the indexer
    other_media_id = db.add_media(test_db, "/other/path/other_episode.srt", "subtitle")
    db.add_sentences(test_db, other_media_id, [("eng", 0, 1, "Other sentence")])

    def mock_exists(path):
        if path == "/fake/path/deleted_episode.srt":
            return False
        if path == "/other/path/other_episode.srt":
            return False
        return True

    with patch("os.walk") as mock_walk, patch("indexer.get_db", return_value=test_db), patch("os.path.exists", side_effect=mock_exists):
        mock_walk.return_value = []
        indexer.index_directory("/fake/path")

    # The deleted file in /fake/path should be removed
    assert test_db.execute("SELECT COUNT(*) FROM media WHERE id = ?", (deleted_media_id,)).fetchone()[0] == 0
    # Its sentences should be cascaded
    assert test_db.execute("SELECT COUNT(*) FROM sentences WHERE media_id = ?", (deleted_media_id,)).fetchone()[0] == 0
    # Its FTS should be cascaded (trigger)
    assert test_db.execute("SELECT COUNT(*) FROM sentences_fts WHERE text = 'Deleted sentence'").fetchone()[0] == 0

    # The file in /other/path should still exist because we only indexed /fake/path
    assert test_db.execute("SELECT COUNT(*) FROM media WHERE id = ?", (other_media_id,)).fetchone()[0] == 1
    assert test_db.execute("SELECT COUNT(*) FROM sentences WHERE media_id = ?", (other_media_id,)).fetchone()[0] == 1

