"""Test module."""

import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from hagi import db
from hagi import indexer


@pytest.fixture
def test_db(monkeypatch):
    # Use in-memory DB for tests
    """Test function."""
    db.DB_PATH = ":memory:"
    conn = db.init_db()
    monkeypatch.setattr(indexer, "_plex_cache_built", True)
    yield conn
    conn.close()


def test_incremental_indexing_skips(test_db):
    """Ensure files already present in the media table are not parsed again."""
    db.add_media(test_db, "/fake/path/episode1.srt", "subtitle")

    with (
        patch("os.walk") as mock_walk,
        patch("hagi.indexer.get_db", return_value=test_db),
        patch("os.path.exists", return_value=True),
    ):
        mock_walk.return_value = [("/fake/path", [], ["episode1.srt"])]

        with patch("hagi.indexer.load_and_sanitize_subs") as mock_load:
            indexer.index_directory("/fake/path")

            mock_load.assert_not_called()


def test_language_detection_external_subs(test_db):
    """Ensure external subtitle files infer language correctly from their text content."""
    with patch("os.walk") as mock_walk, patch("hagi.indexer.get_db", return_value=test_db):
        mock_walk.return_value = [("/fake/path", [], ["ep1.srt", "ep2.srt", "ep3.srt"])]

        with patch("hagi.indexer.load_and_sanitize_subs") as mock_load:
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
        patch("hagi.indexer.get_db", return_value=test_db),
        patch("subprocess.run") as mock_subrun,
        patch("hagi.indexer.load_and_sanitize_subs") as mock_load,
    ):
        mock_walk.return_value = [("/fake/path", [], ["episode1.mkv"])]

        # Mock the ffprobe output: two streams, Japanese and English
        probe_output = {
            "streams": [
                {"index": 1, "tags": {"language": "jpn"}},
                {"index": 2, "tags": {"language": "eng"}},
            ]
        }
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

        # Verify subprocess was called 2 times total:
        # 1x for ffprobe, 1x for ffmpeg multi-output (extracting jpn and eng in one pass)
        assert mock_subrun.call_count == 2
        extract_call = mock_subrun.call_args_list[1][0][0]
        assert "-map" in extract_call
        assert "0:1" in extract_call
        assert "0:2" in extract_call

        # Verify the sentences were added with the correct languages
        sentences = test_db.execute("SELECT language, text FROM sentences").fetchall()
        assert len(sentences) == 2
        langs = [s["language"] for s in sentences]

        assert "jpn" in langs
        assert "eng" in langs


def test_mkv_extraction_rolls_back_entire_mkv_on_track_timeout(test_db):
    """Ensure that if one subtitle track times out, the entire MKV is rolled back for retry."""
    with (
        patch("os.walk") as mock_walk,
        patch("hagi.indexer.get_db", return_value=test_db),
        patch("subprocess.run") as mock_subrun,
        patch("hagi.indexer.load_and_sanitize_subs") as mock_load,
    ):
        mock_walk.return_value = [("/fake/path", [], ["episode1.mkv"])]

        probe_result = MagicMock(
            stdout=json.dumps(
                {
                    "streams": [
                        {"index": 1, "tags": {"language": "eng"}},
                        {"index": 2, "tags": {"language": "jpn"}},
                    ]
                }
            ),
            returncode=0,
        )
        mock_subrun.side_effect = [
            probe_result,
            subprocess.TimeoutExpired(cmd="ffmpeg", timeout=120),
        ]

        mock_subs = MagicMock()
        mock_line = MagicMock(plaintext="こんにちは", start=0, end=1000)
        mock_subs.__iter__.return_value = [mock_line]
        mock_load.return_value = mock_subs

        indexer.index_directory("/fake/path")

        assert mock_subrun.call_count == 2
        mock_load.assert_not_called()
        sentences = test_db.execute("SELECT language, text FROM sentences").fetchall()
        assert [(row["language"], row["text"]) for row in sentences] == []

        media = test_db.execute("SELECT * FROM media").fetchall()
        assert len(media) == 0


def test_add_media_lastrowid_bug(test_db):
    """Ensure add_media doesn't return the ID of a recently inserted sentence when adding a duplicate media path."""
    media_id_1 = db.add_media(test_db, "/path/to/video.mkv", "mkv_embedded")
    db.add_sentences(test_db, media_id_1, [("eng", 0, 1, "Hello")])
    media_id_2 = db.add_media(test_db, "/path/to/video.mkv", "mkv_embedded")

    assert media_id_1 == media_id_2


def test_plex_cache_unpacking(test_db):
    """Ensure process_subs correctly unpacks 4 values from the plex_path_cache including episode_title."""
    # Seed the cache with a 4-tuple representing (show_title, season, episode, episode_title)
    indexer.plex_path_cache["/fake/path/episode1"] = ("My Show", 1, 5, "The Best Episode")

    with patch("hagi.indexer.get_db", return_value=test_db):
        with patch("hagi.indexer.load_and_sanitize_subs") as mock_load:
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
        patch("hagi.indexer.get_db", return_value=test_db),
        patch("subprocess.run") as mock_subrun,
        patch("hagi.indexer.load_and_sanitize_subs") as mock_load,
    ):
        mock_walk.return_value = [("/fake/path", [], ["episode1.mkv"])]

        probe_output = {
            "streams": [
                {"index": 0, "tags": {"language": "fre"}},  # Skipped
                {
                    "index": 1,
                    "tags": {"language": "eng", "title": "Forced"},
                },  # Skipped because clean exists
                {"index": 2, "tags": {"language": "eng", "title": "Dialogue"}},  # Picked (clean)
                {
                    "index": 3,
                    "tags": {"language": "spa", "title": "Castilian"},
                },  # Skipped because Latin American exists
                {
                    "index": 4,
                    "tags": {"language": "spa", "title": "Latin American"},
                },  # Picked (Latin priority)
                {
                    "index": 5,
                    "tags": {"language": "jpn", "title": "Full Subtitles"},
                },  # Picked, but we will mock it to be English text!
                {
                    "index": 6,
                    "tags": {"language": "unknown"},
                },  # Picked, will be detected as English text!
            ]
        }
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

        # Verify subprocess was called 2 times total:
        # 1x ffprobe, 1x ffmpeg multi-output (eng, spa, jpn, unknown)
        assert mock_subrun.call_count == 2

        sentences = test_db.execute("SELECT language, text FROM sentences").fetchall()
        # English, Spanish (mistagged Japanese track and untagged track were deduplicated since they evaluate to English)
        assert len(sentences) == 2
        langs = [s["language"] for s in sentences]

        assert langs.count("eng") == 1
        assert langs.count("spa") == 1
        assert langs.count("jpn") == 0


def test_mkv_skip_all_subtitles(test_db):
    """Ensure media is still added even if no subtitle tracks match."""
    with (
        patch("os.walk") as mock_walk,
        patch("hagi.indexer.get_db", return_value=test_db),
        patch("subprocess.run") as mock_subrun,
    ):
        mock_walk.return_value = [("/fake/path", [], ["episode1.mkv"])]

        probe_output = {
            "streams": [
                {"index": 0, "tags": {"language": "fre"}},
                {"index": 1, "tags": {"language": "ger"}},
            ]
        }
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


def test_mkv_skip_all_subtitles_due_to_timeout(test_db):
    """Ensure media is NOT added if all subtitle tracks time out during extraction."""
    with (
        patch("os.walk") as mock_walk,
        patch("hagi.indexer.get_db", return_value=test_db),
        patch("subprocess.run") as mock_subrun,
    ):
        mock_walk.return_value = [("/fake/path", [], ["episode1.mkv"])]

        probe_output = {
            "streams": [
                {"index": 0, "tags": {"language": "eng"}},
            ]
        }
        probe_result = MagicMock()
        probe_result.stdout = json.dumps(probe_output)
        probe_result.returncode = 0

        mock_subrun.side_effect = [
            probe_result,
            subprocess.TimeoutExpired(cmd="ffmpeg", timeout=120),
        ]

        indexer.index_directory("/fake/path")

        # Verify subprocess was called exactly 2 times (ffprobe and ffmpeg)
        assert mock_subrun.call_count == 2

        # Sentences should be empty
        sentences = test_db.execute("SELECT * FROM sentences").fetchall()
        assert len(sentences) == 0

        # Media should NOT be added since it failed via timeout!
        media = test_db.execute("SELECT * FROM media").fetchall()
        assert len(media) == 0


def test_build_plex_cache_filtering():
    """Ensure build_plex_cache respects plex_libraries from config.json."""
    from unittest.mock import mock_open

    # Create the mock setup inside
    with patch("hagi.indexer._get_plex") as mock_get_plex, patch("os.path.exists") as mock_exists:
        mock_plex = mock_get_plex.return_value
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
        assert "/path/anime_ep1" in indexer.plex_path_cache
        assert "/path/movie1" not in indexer.plex_path_cache

        # 2. Test filtering by ID "5"
        indexer._plex_cache_built = False
        indexer.plex_path_cache = {}
        with patch("builtins.open", mock_open(read_data='{"plex_libraries": ["5"]}')):
            indexer.build_plex_cache()
        assert "/path/anime_ep1" not in indexer.plex_path_cache
        assert "/path/movie1" in indexer.plex_path_cache

        # 3. Test no filter (empty config)
        indexer._plex_cache_built = False
        indexer.plex_path_cache = {}
        with patch("builtins.open", mock_open(read_data="{}")):
            indexer.build_plex_cache()
        assert "/path/anime_ep1" in indexer.plex_path_cache
        assert "/path/movie1" in indexer.plex_path_cache

        # 4. Test read error stops cache building to prevent unauthenticated all-library caching
        indexer._plex_cache_built = False
        indexer.plex_path_cache = {}
        with patch("builtins.open", mock_open(read_data="INVALID_JSON")):
            indexer.build_plex_cache()
        assert len(indexer.plex_path_cache) == 0


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

    with (
        patch("os.walk") as mock_walk,
        patch("hagi.indexer.get_db", return_value=test_db),
        patch("os.path.exists", side_effect=mock_exists),
    ):
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


def test_get_plex_metadata_external_subtitles():
    """Ensure get_plex_metadata correctly strips language suffixes to find Plex metadata."""
    # Seed the cache with a movie base name and a basename test
    indexer.plex_path_cache["/fake/path/Belle (2021)"] = ("Belle", None, None, "Belle")
    indexer.plex_path_cache["Detective Conan - S34E27"] = ("Detective Conan", 34, 27, "Witness")

    # Exact match should work
    res1 = indexer.get_plex_metadata("/fake/path/Belle (2021).mkv")
    assert res1 == ("Belle", None, None, "Belle")

    # Subtitle with language suffix should work by stripping it
    res2 = indexer.get_plex_metadata("/fake/path/Belle (2021).en.srt")
    assert res2 == ("Belle", None, None, "Belle")

    # Unknown movie should return Nones
    res3 = indexer.get_plex_metadata("/fake/path/Unknown (2021).en.srt")
    assert res3 == (None, None, None, None)

    # Basename match should work when absolute path fails
    res4 = indexer.get_plex_metadata("/different/mount/Detective Conan - S34E27.mkv")
    assert res4 == ("Detective Conan", 34, 27, "Witness")

    # Basename match with stripped language should work
    res5 = indexer.get_plex_metadata("/different/mount/Detective Conan - S34E27.en.srt")
    assert res5 == ("Detective Conan", 34, 27, "Witness")

    # Regional locale should be stripped
    res6 = indexer.get_plex_metadata("/different/mount/Detective Conan - S34E27.en-US.srt")
    assert res6 == ("Detective Conan", 34, 27, "Witness")

    # Non-locale suffix should NOT inherit metadata
    res7 = indexer.get_plex_metadata("/fake/path/Belle (2021).commentary.srt")
    assert res7 == (None, None, None, None)

    # Malformed tail should NOT inherit metadata
    res8 = indexer.get_plex_metadata("/different/mount/Detective Conan - S34E27.en-commentary.srt")
    assert res8 == (None, None, None, None)

    # Invalid regional suffixes should NOT inherit metadata
    indexer.plex_path_cache["Movie"] = ("Mock Movie", None, None, "Mock Title")
    res9 = indexer.get_plex_metadata("/different/mount/Movie.en-a.srt")
    assert res9 == (None, None, None, None)

    res10 = indexer.get_plex_metadata("/different/mount/Movie.en-abc.srt")
    assert res10 == (None, None, None, None)


def test_load_and_sanitize_subs():
    """Ensure load_and_sanitize_subs clamps negative timestamps to 0 and parses successfully."""
    import tempfile
    import os

    test_ass = """[Script Info]
ScriptType: v4.00+

[V4+ Styles]
Format: Name, Fontname, Fontsize
Style: Default,Arial,20

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:-27.-60,0:00:-25.-60,Default,,0,0,0,,Hello Negative ASS
"""
    test_srt = """1
00:00:-02,000 --> 00:00:-01,000
Hello Negative SRT

2
00:00:01,000 --> 00:00:02,000
The marker is 00:00:-02,000
"""

    with tempfile.NamedTemporaryFile("w", suffix=".ass", delete=False) as f_ass:
        f_ass.write(test_ass)
        ass_name = f_ass.name

    with tempfile.NamedTemporaryFile("w", suffix=".srt", delete=False) as f_srt:
        f_srt.write(test_srt)
        srt_name = f_srt.name

    try:
        # These should not raise ValueError and should clamp timestamps to 0
        subs_ass = indexer.load_and_sanitize_subs(ass_name)
        assert len(subs_ass) == 1
        assert subs_ass[0].start == 0
        assert subs_ass[0].end == 0
        assert subs_ass[0].plaintext == "Hello Negative ASS"

        subs_srt = indexer.load_and_sanitize_subs(srt_name)
        assert len(subs_srt) == 2
        assert subs_srt[0].start == 0
        assert subs_srt[0].end == 0
        assert subs_srt[0].plaintext == "Hello Negative SRT"

        # Verify text containing negative timestamps is not altered
        assert subs_srt[1].plaintext == "The marker is 00:00:-02,000"

    finally:
        os.remove(ass_name)
        os.remove(srt_name)


def test_mkv_extraction_default_timeout(test_db):
    """Ensure ffmpeg extraction and ffprobe default to 1800s and 300s timeouts."""
    with (
        patch("os.walk") as mock_walk,
        patch("hagi.indexer.get_db", return_value=test_db),
        patch("subprocess.run") as mock_subrun,
        patch("hagi.indexer.load_and_sanitize_subs") as mock_load,
        patch("hagi.indexer._load_config", return_value={}),
    ):
        mock_walk.return_value = [("/fake/path", [], ["episode1.mkv"])]

        probe_result = MagicMock(
            stdout=json.dumps({"streams": [{"index": 1, "tags": {"language": "eng"}}]}),
            returncode=0,
        )
        extraction_result = MagicMock(returncode=0)
        mock_subrun.side_effect = [probe_result, extraction_result]

        mock_subs = MagicMock()
        mock_line = MagicMock(plaintext="Hello", start=0, end=1000)
        mock_subs.__iter__.return_value = [mock_line]
        mock_load.return_value = mock_subs

        indexer.index_directory("/fake/path")

        assert mock_subrun.call_count == 2
        # First call is ffprobe (default 300s)
        assert mock_subrun.call_args_list[0].kwargs.get("timeout") == 300
        # Second call is ffmpeg extraction (default 1800s)
        assert mock_subrun.call_args_list[1].kwargs.get("timeout") == 1800


def test_mkv_extraction_custom_timeout_param(test_db):
    """Ensure custom extract_timeout and probe_timeout parameters are forwarded."""
    with (
        patch("os.walk") as mock_walk,
        patch("hagi.indexer.get_db", return_value=test_db),
        patch("subprocess.run") as mock_subrun,
        patch("hagi.indexer.load_and_sanitize_subs") as mock_load,
    ):
        mock_walk.return_value = [("/fake/path", [], ["episode1.mkv"])]

        probe_result = MagicMock(
            stdout=json.dumps({"streams": [{"index": 1, "tags": {"language": "eng"}}]}),
            returncode=0,
        )
        extraction_result = MagicMock(returncode=0)
        mock_subrun.side_effect = [probe_result, extraction_result]

        mock_subs = MagicMock()
        mock_line = MagicMock(plaintext="Hello", start=0, end=1000)
        mock_subs.__iter__.return_value = [mock_line]
        mock_load.return_value = mock_subs

        indexer.index_directory("/fake/path", extract_timeout=3600, probe_timeout=600)

        assert mock_subrun.call_count == 2
        assert mock_subrun.call_args_list[0].kwargs.get("timeout") == 600
        assert mock_subrun.call_args_list[1].kwargs.get("timeout") == 3600


def test_mkv_extraction_zero_timeout_means_unlimited(test_db):
    """Ensure passing 0 for extract_timeout and probe_timeout sets timeout to None."""
    with (
        patch("os.walk") as mock_walk,
        patch("hagi.indexer.get_db", return_value=test_db),
        patch("subprocess.run") as mock_subrun,
        patch("hagi.indexer.load_and_sanitize_subs") as mock_load,
    ):
        mock_walk.return_value = [("/fake/path", [], ["episode1.mkv"])]

        probe_result = MagicMock(
            stdout=json.dumps({"streams": [{"index": 1, "tags": {"language": "eng"}}]}),
            returncode=0,
        )
        extraction_result = MagicMock(returncode=0)
        mock_subrun.side_effect = [probe_result, extraction_result]

        mock_subs = MagicMock()
        mock_line = MagicMock(plaintext="Hello", start=0, end=1000)
        mock_subs.__iter__.return_value = [mock_line]
        mock_load.return_value = mock_subs

        indexer.index_directory("/fake/path", extract_timeout=0, probe_timeout=0)

        assert mock_subrun.call_count == 2
        assert mock_subrun.call_args_list[0].kwargs.get("timeout") is None
        assert mock_subrun.call_args_list[1].kwargs.get("timeout") is None


def test_mkv_extraction_config_timeout(test_db):
    """Ensure extractTimeout and probeTimeout in config.json are respected."""
    with (
        patch("os.walk") as mock_walk,
        patch("hagi.indexer.get_db", return_value=test_db),
        patch("subprocess.run") as mock_subrun,
        patch("hagi.indexer.load_and_sanitize_subs") as mock_load,
        patch("hagi.indexer._load_config", return_value={"extractTimeout": 2400, "probeTimeout": 450}),
    ):
        mock_walk.return_value = [("/fake/path", [], ["episode1.mkv"])]

        probe_result = MagicMock(
            stdout=json.dumps({"streams": [{"index": 1, "tags": {"language": "eng"}}]}),
            returncode=0,
        )
        extraction_result = MagicMock(returncode=0)
        mock_subrun.side_effect = [probe_result, extraction_result]

        mock_subs = MagicMock()
        mock_line = MagicMock(plaintext="Hello", start=0, end=1000)
        mock_subs.__iter__.return_value = [mock_line]
        mock_load.return_value = mock_subs

        indexer.index_directory("/fake/path")

        assert mock_subrun.call_count == 2
        assert mock_subrun.call_args_list[0].kwargs.get("timeout") == 450
        assert mock_subrun.call_args_list[1].kwargs.get("timeout") == 2400


def test_mkv_extraction_config_snake_case_and_zero_timeout(test_db):
    """Ensure extract_timeout and probe_timeout snake_case keys and zero values are respected."""
    with (
        patch("os.walk") as mock_walk,
        patch("hagi.indexer.get_db", return_value=test_db),
        patch("subprocess.run") as mock_subrun,
        patch("hagi.indexer.load_and_sanitize_subs") as mock_load,
        patch("hagi.indexer._load_config", return_value={"extract_timeout": "0", "probe_timeout": "invalid"}),
    ):
        mock_walk.return_value = [("/fake/path", [], ["episode1.mkv"])]

        probe_result = MagicMock(
            stdout=json.dumps({"streams": [{"index": 1, "tags": {"language": "eng"}}]}),
            returncode=0,
        )
        extraction_result = MagicMock(returncode=0)
        mock_subrun.side_effect = [probe_result, extraction_result]

        mock_subs = MagicMock()
        mock_line = MagicMock(plaintext="Hello", start=0, end=1000)
        mock_subs.__iter__.return_value = [mock_line]
        mock_load.return_value = mock_subs

        indexer.index_directory("/fake/path")

        assert mock_subrun.call_count == 2
        # "invalid" should fall back to default 300
        assert mock_subrun.call_args_list[0].kwargs.get("timeout") == 300
        # "0" should convert to None (unlimited)
        assert mock_subrun.call_args_list[1].kwargs.get("timeout") is None


def test_load_config_behaviors():
    """Ensure _load_config handles missing files, parse errors, and non-dict content."""
    from unittest.mock import mock_open

    # 1. Missing file returns None
    with patch("os.path.exists", return_value=False):
        assert indexer._load_config() is None

    # 2. Valid dictionary returns dict
    with patch("os.path.exists", return_value=True), patch("builtins.open", mock_open(read_data='{"extractTimeout": 1200}')):
        assert indexer._load_config() == {"extractTimeout": 1200}

    # 3. Invalid JSON raises json.JSONDecodeError
    with patch("os.path.exists", return_value=True), patch("builtins.open", mock_open(read_data="NOT_JSON")):
        with pytest.raises(json.JSONDecodeError):
            indexer._load_config()

    # 4. Non-dict JSON raises ValueError
    with patch("os.path.exists", return_value=True), patch("builtins.open", mock_open(read_data="[1, 2, 3]")):
        with pytest.raises(ValueError, match="must contain a JSON object"):
            indexer._load_config()


def test_mkv_single_pass_multi_output_command(test_db):
    """Ensure ffmpeg command uses -seekable 0 and extracts multiple streams in a single pass."""
    with (
        patch("os.walk") as mock_walk,
        patch("hagi.indexer.get_db", return_value=test_db),
        patch("subprocess.run") as mock_subrun,
        patch("hagi.indexer.load_and_sanitize_subs") as mock_load,
    ):
        mock_walk.return_value = [("/fake/path", [], ["episode1.mkv"])]

        probe_output = {
            "streams": [
                {"index": 1, "codec_name": "subrip", "tags": {"language": "jpn"}},
                {"index": 2, "codec_name": "subrip", "tags": {"language": "eng"}},
            ]
        }
        probe_res = MagicMock(returncode=0, stdout=json.dumps(probe_output))
        extract_res = MagicMock(returncode=0)
        mock_subrun.side_effect = [probe_res, extract_res]

        mock_subs = MagicMock()
        mock_line = MagicMock()
        mock_line.plaintext = "Test subtitle line"
        mock_line.start = 0
        mock_line.end = 1000
        mock_subs.__iter__.return_value = [mock_line]
        mock_load.return_value = mock_subs

        indexer.index_directory("/fake/path")

        assert mock_subrun.call_count == 2
        extract_call_args = mock_subrun.call_args_list[1][0][0]

        # Verify -seekable 0 appears before -i
        assert "-seekable" in extract_call_args
        seekable_idx = extract_call_args.index("-seekable")
        assert extract_call_args[seekable_idx + 1] == "0"
        i_idx = extract_call_args.index("-i")
        assert seekable_idx < i_idx

        # Verify both streams mapped in single command
        assert "-map" in extract_call_args
        assert "0:1" in extract_call_args
        assert "0:2" in extract_call_args

        # Verify batch timeout is scaled by number of streams (2 streams * 1800s default = 3600s)
        assert mock_subrun.call_args_list[1].kwargs.get("timeout") == 3600


def test_mkv_skips_bitmap_subtitle_codecs(test_db):
    """Ensure bitmap subtitle codecs (PGS, VobSub, etc.) are skipped during stream selection."""
    with (
        patch("os.walk") as mock_walk,
        patch("hagi.indexer.get_db", return_value=test_db),
        patch("subprocess.run") as mock_subrun,
        patch("hagi.indexer.load_and_sanitize_subs") as mock_load,
    ):
        mock_walk.return_value = [("/fake/path", [], ["episode1.mkv"])]

        probe_output = {
            "streams": [
                {"index": 0, "codec_name": "hdmv_pgs_subtitle", "tags": {"language": "jpn"}},
                {"index": 1, "codec_name": "dvd_subtitle", "tags": {"language": "eng"}},
                {"index": 2, "codec_name": "dvb_subtitle", "tags": {"language": "spa"}},
                {"index": 3, "codec_name": "subrip", "tags": {"language": "jpn"}},
            ]
        }
        probe_res = MagicMock(returncode=0, stdout=json.dumps(probe_output))
        extract_res = MagicMock(returncode=0)
        mock_subrun.side_effect = [probe_res, extract_res]

        mock_subs = MagicMock()
        mock_line = MagicMock()
        mock_line.plaintext = "Japanese text こんにちは"
        mock_line.start = 0
        mock_line.end = 1000
        mock_subs.__iter__.return_value = [mock_line]
        mock_load.return_value = mock_subs

        indexer.index_directory("/fake/path")

        assert mock_subrun.call_count == 2
        extract_call_args = mock_subrun.call_args_list[1][0][0]

        # Only stream 3 (subrip) should be mapped; streams 0, 1, and 2 skipped
        assert "0:3" in extract_call_args
        assert "0:0" not in extract_call_args
        assert "0:1" not in extract_call_args
        assert "0:2" not in extract_call_args


def test_mkv_single_pass_fallback_on_failure(test_db):
    """Ensure extraction falls back to per-track ffmpeg calls if multi-output fails."""
    with (
        patch("os.walk") as mock_walk,
        patch("hagi.indexer.get_db", return_value=test_db),
        patch("subprocess.run") as mock_subrun,
        patch("hagi.indexer.load_and_sanitize_subs") as mock_load,
    ):
        mock_walk.return_value = [("/fake/path", [], ["episode1.mkv"])]

        probe_output = {
            "streams": [
                {"index": 1, "codec_name": "subrip", "tags": {"language": "jpn"}},
                {"index": 2, "codec_name": "subrip", "tags": {"language": "eng"}},
            ]
        }
        probe_res = MagicMock(returncode=0, stdout=json.dumps(probe_output))
        multi_fail_res = MagicMock(returncode=1)
        single_success_1 = MagicMock(returncode=0)
        single_success_2 = MagicMock(returncode=0)
        mock_subrun.side_effect = [
            probe_res,
            multi_fail_res,
            single_success_1,
            single_success_2,
        ]

        jpn_line = MagicMock()
        jpn_line.plaintext = "こんにちは世界"
        jpn_line.start = 0
        jpn_line.end = 1000

        eng_line = MagicMock()
        eng_line.plaintext = "Hello world"
        eng_line.start = 0
        eng_line.end = 1000

        # Note: selected_streams processes eng before jpn
        mock_load.side_effect = [[eng_line], [jpn_line]]

        indexer.index_directory("/fake/path")

        # 1x probe + 1x multi-output (fails) + 2x per-track fallback = 4 calls
        assert mock_subrun.call_count == 4
        assert mock_load.call_count == 2

        sentences = test_db.execute("SELECT language, text FROM sentences ORDER BY language DESC").fetchall()
        assert len(sentences) == 2
        assert sentences[0]["language"] == "jpn"
        assert sentences[0]["text"] == "こんにちは世界"
        assert sentences[1]["language"] == "eng"
        assert sentences[1]["text"] == "Hello world"


def test_mkv_single_pass_fallback_timeout(test_db):
    """Ensure database is rolled back if a fallback per-track extraction times out."""
    with (
        patch("os.walk") as mock_walk,
        patch("hagi.indexer.get_db", return_value=test_db),
        patch("subprocess.run") as mock_subrun,
        patch("hagi.indexer.load_and_sanitize_subs") as mock_load,
    ):
        mock_walk.return_value = [("/fake/path", [], ["episode1.mkv"])]

        probe_output = {
            "streams": [
                {"index": 1, "codec_name": "subrip", "tags": {"language": "jpn"}},
                {"index": 2, "codec_name": "subrip", "tags": {"language": "eng"}},
            ]
        }
        probe_res = MagicMock(returncode=0, stdout=json.dumps(probe_output))
        multi_fail_res = MagicMock(returncode=1)
        mock_subrun.side_effect = [
            probe_res,
            multi_fail_res,
            subprocess.TimeoutExpired(cmd="ffmpeg", timeout=120),
        ]

        indexer.index_directory("/fake/path")

        assert mock_subrun.call_count == 3
        mock_load.assert_not_called()
        sentences = test_db.execute("SELECT language, text FROM sentences").fetchall()
        assert sentences == []


def test_mkv_handles_null_codec_name(test_db):
    """Ensure null codec_name in stream does not raise AttributeError and indexes successfully."""
    with (
        patch("os.walk") as mock_walk,
        patch("hagi.indexer.get_db", return_value=test_db),
        patch("subprocess.run") as mock_subrun,
        patch("hagi.indexer.load_and_sanitize_subs") as mock_load,
    ):
        mock_walk.return_value = [("/fake/path", [], ["episode1.mkv"])]

        probe_output = {
            "streams": [
                {"index": 1, "codec_name": None, "tags": {"language": "eng"}},
            ]
        }
        probe_res = MagicMock(returncode=0, stdout=json.dumps(probe_output))
        extract_res = MagicMock(returncode=0)
        mock_subrun.side_effect = [probe_res, extract_res]

        mock_line = MagicMock(plaintext="Hello", start=0, end=1000)
        mock_load.return_value = [mock_line]

        indexer.index_directory("/fake/path")

        assert mock_subrun.call_count == 2
        sentences = test_db.execute("SELECT language, text FROM sentences").fetchall()
        assert len(sentences) == 1
        assert sentences[0]["text"] == "Hello"


def test_mkv_fallback_remaining_budget_exhaustion(test_db):
    """Ensure fallback aborts and rolls back when the aggregate batch budget has elapsed."""
    with (
        patch("os.walk") as mock_walk,
        patch("hagi.indexer.get_db", return_value=test_db),
        patch("subprocess.run") as mock_subrun,
        patch("time.monotonic") as mock_time,
    ):
        mock_walk.return_value = [("/fake/path", [], ["episode1.mkv"])]

        probe_output = {
            "streams": [
                {"index": 1, "codec_name": "subrip", "tags": {"language": "jpn"}},
                {"index": 2, "codec_name": "subrip", "tags": {"language": "eng"}},
            ]
        }
        probe_res = MagicMock(returncode=0, stdout=json.dumps(probe_output))
        multi_fail_res = MagicMock(returncode=1)
        mock_subrun.side_effect = [probe_res, multi_fail_res]

        # Batch starts at 0.0, fallback checks elapsed time after 4000s (> 2 * 1800s = 3600s budget)
        mock_time.side_effect = [0.0, 4000.0]

        indexer.index_directory("/fake/path")

        # 1x probe + 1x multi-output (fails); fallback stops immediately due to budget exhaustion
        assert mock_subrun.call_count == 2
        sentences = test_db.execute("SELECT * FROM sentences").fetchall()
        assert len(sentences) == 0


def test_mkv_skips_empty_subtitles(test_db):
    """Ensure empty or whitespace-only subtitle tracks are not indexed as mkv_embedded."""
    with (
        patch("os.walk") as mock_walk,
        patch("hagi.indexer.get_db", return_value=test_db),
        patch("subprocess.run") as mock_subrun,
        patch("hagi.indexer.load_and_sanitize_subs") as mock_load,
    ):
        mock_walk.return_value = [("/fake/path", [], ["episode1.mkv"])]

        probe_output = {
            "streams": [
                {"index": 1, "codec_name": "subrip", "tags": {"language": "eng"}},
            ]
        }
        probe_res = MagicMock(returncode=0, stdout=json.dumps(probe_output))
        extract_res = MagicMock(returncode=0)
        mock_subrun.side_effect = [probe_res, extract_res]

        mock_line = MagicMock(plaintext="   \n  ", start=0, end=1000)
        mock_load.return_value = [mock_line]

        indexer.index_directory("/fake/path")

        assert mock_subrun.call_count == 2
        # No sentences indexed, media record added with mkv_embedded type
        sentences = test_db.execute("SELECT * FROM sentences").fetchall()
        assert len(sentences) == 0
        media = test_db.execute("SELECT type FROM media").fetchall()
        assert len(media) == 1
        assert media[0]["type"] == "mkv_embedded"


def test_mkv_both_batch_and_retry_fail(test_db):
    """Ensure that when batch and per-track retry both fail, partial output is rejected."""
    with (
        patch("os.walk") as mock_walk,
        patch("hagi.indexer.get_db", return_value=test_db),
        patch("subprocess.run") as mock_subrun,
        patch("hagi.indexer.load_and_sanitize_subs") as mock_load,
    ):
        mock_walk.return_value = [("/fake/path", [], ["episode1.mkv"])]

        probe_output = {
            "streams": [
                {"index": 1, "codec_name": "subrip", "tags": {"language": "eng"}},
            ]
        }
        probe_res = MagicMock(returncode=0, stdout=json.dumps(probe_output))
        batch_fail_res = MagicMock(returncode=1)
        retry_fail_res = MagicMock(returncode=1)
        mock_subrun.side_effect = [probe_res, batch_fail_res, retry_fail_res]

        indexer.index_directory("/fake/path")

        assert mock_subrun.call_count == 3
        mock_load.assert_not_called()
        sentences = test_db.execute("SELECT * FROM sentences").fetchall()
        assert len(sentences) == 0


def test_build_plex_cache_raises_plex_error(monkeypatch):
    """Ensure build_plex_cache raises PlexError and clears cache on Plex API error."""
    with patch("hagi.indexer._get_plex") as mock_get_plex, patch("os.path.exists", return_value=False):
        mock_plex = mock_get_plex.return_value
        mock_plex.library.sections.side_effect = Exception("(401) unauthorized")

        monkeypatch.setattr(indexer, "_plex_cache_built", False)
        monkeypatch.setattr(indexer, "plex_path_cache", {"pre_existing": ("Old Show", 1, 1, "Old Title")})

        with pytest.raises(indexer.PlexError) as exc_info:
            indexer.build_plex_cache()

        assert "Error building Plex cache" in str(exc_info.value)
        assert "(401) unauthorized" in str(exc_info.value)
        assert indexer._plex_cache_built is False
        assert len(indexer.plex_path_cache) == 0


def test_get_plex_connection_error_raises_plex_error(monkeypatch):
    """Ensure _get_plex raises PlexError when PlexServer connection fails."""
    monkeypatch.setenv("PLEX_URL", "http://localhost:32400")
    monkeypatch.setenv("PLEX_TOKEN", "fake_token")
    monkeypatch.setattr(indexer, "_plex_initialized", False)
    monkeypatch.setattr(indexer, "_plex_instance", None)

    with patch("plexapi.server.PlexServer", side_effect=Exception("Connection refused")):
        with pytest.raises(indexer.PlexError) as exc_info:
            indexer._get_plex()

        assert "Could not connect to Plex" in str(exc_info.value)
        assert "Connection refused" in str(exc_info.value)
        assert indexer._plex_initialized is False


def test_get_plex_partial_env_raises_plex_error(monkeypatch):
    """Ensure _get_plex raises PlexError when only one of PLEX_URL or PLEX_TOKEN is set."""
    monkeypatch.setenv("PLEX_URL", "http://localhost:32400")
    monkeypatch.delenv("PLEX_TOKEN", raising=False)
    monkeypatch.setattr(indexer, "_plex_initialized", False)
    monkeypatch.setattr(indexer, "_plex_instance", None)

    with pytest.raises(indexer.PlexError) as exc_info:
        indexer._get_plex()

    assert "Both PLEX_URL and PLEX_TOKEN must be set" in str(exc_info.value)


def test_index_directory_halts_on_plex_error(monkeypatch):
    """Ensure index_directory raises PlexError and stops before processing files."""
    def mock_build_cache():
        """Raise PlexError simulating a Plex communication failure."""
        raise indexer.PlexError("Plex connection failed")

    monkeypatch.setattr(indexer, "build_plex_cache", mock_build_cache)

    with pytest.raises(indexer.PlexError) as exc_info:
        indexer.index_directory("/fake/path")

    assert "Plex connection failed" in str(exc_info.value)


def test_get_plex_timeout_configuration(monkeypatch):
    """Ensure _get_plex forwards timeout from default, config.json, and PLEX_TIMEOUT env."""
    monkeypatch.setenv("PLEX_URL", "http://localhost:32400")
    monkeypatch.setenv("PLEX_TOKEN", "fake_token")
    monkeypatch.delenv("PLEX_TIMEOUT", raising=False)

    recorded_timeouts = []

    def mock_server(baseurl, token, timeout=None):
        """Record PlexServer initialization arguments."""
        recorded_timeouts.append(timeout)
        return MagicMock()

    # 1. Default timeout (120s) when no config or env
    monkeypatch.setattr(indexer, "_plex_initialized", False)
    monkeypatch.setattr(indexer, "_plex_instance", None)
    with (
        patch("plexapi.server.PlexServer", side_effect=mock_server),
        patch("hagi.indexer._load_config", return_value=None),
    ):
        indexer._get_plex()
    assert recorded_timeouts[-1] == 120

    # 2. Config timeout from config.json ("plex_timeout")
    monkeypatch.setattr(indexer, "_plex_initialized", False)
    monkeypatch.setattr(indexer, "_plex_instance", None)
    with (
        patch("plexapi.server.PlexServer", side_effect=mock_server),
        patch("hagi.indexer._load_config", return_value={"plex_timeout": 90}),
    ):
        indexer._get_plex()
    assert recorded_timeouts[-1] == 90

    # 3. Env timeout overrides config.json
    monkeypatch.setenv("PLEX_TIMEOUT", "180")
    monkeypatch.setattr(indexer, "_plex_initialized", False)
    monkeypatch.setattr(indexer, "_plex_instance", None)
    with (
        patch("plexapi.server.PlexServer", side_effect=mock_server),
        patch("hagi.indexer._load_config", return_value={"plex_timeout": 90}),
    ):
        indexer._get_plex()
    assert recorded_timeouts[-1] == 180

    # 4. Non-positive timeout values retain default
    monkeypatch.setenv("PLEX_TIMEOUT", "0")
    monkeypatch.setattr(indexer, "_plex_initialized", False)
    monkeypatch.setattr(indexer, "_plex_instance", None)
    with (
        patch("plexapi.server.PlexServer", side_effect=mock_server),
        patch("hagi.indexer._load_config", return_value={"plex_timeout": -5}),
    ):
        indexer._get_plex()
    assert recorded_timeouts[-1] == 120


