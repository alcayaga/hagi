"""Unit tests targeting the individual domain submodules in hagi.indexer."""

import json
from unittest.mock import MagicMock

import pytest

from hagi import db
from hagi.indexer.alignment import (
    align_and_update_sentences,
    estimate_timestamp_offset,
)
from hagi.indexer.config import (
    DEFAULT_EXTRACT_TIMEOUT,
    DEFAULT_PROBE_TIMEOUT,
    _load_config,
    _resolve_timeouts,
)
from hagi.indexer.extractor import (
    BITMAP_SUBTITLE_CODECS,
    select_best_stream,
)
from hagi.indexer.matching import (
    parse_media_identifiers,
    titles_match,
)
from hagi.indexer.plex import (
    PlexError,
    get_plex_metadata,
)
from hagi.indexer.subtitles import (
    _extract_file_lang,
    _normalize_lang_code,
    detect_language,
    detect_text_language,
)


@pytest.fixture
def test_db(monkeypatch):
    """Create an in-memory SQLite database connection for testing."""
    monkeypatch.setattr(db, "DB_PATH", ":memory:")
    conn = db.init_db()
    yield conn
    conn.close()


def test_config_submodule_load(tmp_path, monkeypatch):
    """Verify config loading and validation from JSON file."""
    monkeypatch.chdir(tmp_path)

    # Missing file returns None
    assert _load_config() is None

    # Valid config
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps({"test_key": "test_val"}))
    assert _load_config() == {"test_key": "test_val"}

    # Invalid non-dict JSON raises ValueError
    cfg_file.write_text(json.dumps(["not", "a", "dict"]))
    try:
        _load_config()
        assert False, "Expected ValueError"
    except ValueError:
        pass


def test_config_submodule_resolve_timeouts(tmp_path, monkeypatch):
    """Verify timeout resolution with explicit, configured, and default values."""
    monkeypatch.chdir(tmp_path)

    # Defaults
    ext, probe = _resolve_timeouts(None, None)
    assert ext == DEFAULT_EXTRACT_TIMEOUT
    assert probe == DEFAULT_PROBE_TIMEOUT

    # Explicit values
    ext, probe = _resolve_timeouts(60, 30)
    assert ext == 60
    assert probe == 30

    # Non-positive numbers become None (unlimited)
    ext, probe = _resolve_timeouts(0, -10)
    assert ext is None
    assert probe is None


def test_subtitles_submodule_lang_detection():
    """Verify heuristic language detection across multiple scripts and alphabets."""
    assert detect_text_language("これは日本語のテストです。") == "jpn"
    assert detect_text_language("¿Dónde está la biblioteca pública?") == "spa"
    assert detect_text_language("Este é um teste em português com acentuação.") == "por"
    assert detect_text_language("This is a simple English sentence.") == "eng"
    assert detect_text_language("") == "unknown"


def test_subtitles_submodule_detect_language_object():
    """Verify subtitle object language detection with sample lines."""
    mock_subs = MagicMock()
    mock_line1 = MagicMock()
    mock_line1.plaintext = "ありがとう"
    mock_line2 = MagicMock()
    mock_line2.plaintext = "さようなら"
    mock_subs.__iter__.return_value = [mock_line1, mock_line2]

    assert detect_language(mock_subs) == "jpn"

    empty_subs = MagicMock()
    empty_subs.__iter__.return_value = []
    assert detect_language(empty_subs) == "unknown"


def test_subtitles_submodule_code_normalization():
    """Verify language code mapping and filename extraction."""
    assert _normalize_lang_code("ja") == "jpn"
    assert _normalize_lang_code("spa") == "spa"
    assert _normalize_lang_code("unknown_code") == "unknown_code"
    assert _normalize_lang_code(None) == "unknown"

    assert _extract_file_lang("/path/show.ja.srt") == "jpn"
    assert _extract_file_lang("/path/show.en.ass") == "eng"
    assert _extract_file_lang("/path/show.es.vtt") == "spa"
    assert _extract_file_lang("/path/show.mp4") is None


def test_plex_submodule_metadata_and_error():
    """Verify Plex metadata resolution and custom exception class."""
    assert issubclass(PlexError, Exception)

    # Unmapped path returns tuple of None
    assert get_plex_metadata("/nonexistent/file.mkv") == (None, None, None, None)


def test_matching_submodule_identifiers():
    """Verify media filename identifier extraction logic."""
    ids = parse_media_identifiers("[Subs] Show Name - S02E05 [1080p].mkv")
    assert ids["season"] == 2
    assert ids["episode"] == 5

    anime_ids = parse_media_identifiers("[Group] Show Title - 105 [720p].mkv")
    assert anime_ids["abs_episode"] == 105


def test_matching_submodule_titles():
    """Verify title comparison with prefixes and suffix restrictions."""
    assert titles_match("Detective Conan", "Conan") is True
    assert titles_match("The Melancholy of Haruhi Suzumiya", "Melancholy of Haruhi Suzumiya") is True
    assert titles_match("Attack on Titan", "Attack on Titan: The Final Season") is False
    assert titles_match(None, "Show") is False


def test_alignment_submodule_offset_estimation():
    """Verify timestamp offset estimation logic."""
    old_sentences = [
        {"text": "A distinct subtitle line here", "start_time": 10.0},
        {"text": "Another identifiable line here", "start_time": 20.0},
        {"text": "A third recognizable line here", "start_time": 30.0},
    ]
    new_sentences = [
        {"text": "A distinct subtitle line here", "start_time": 12.5},
        {"text": "Another identifiable line here", "start_time": 22.5},
        {"text": "A third recognizable line here", "start_time": 32.5},
    ]
    offset = estimate_timestamp_offset(old_sentences, new_sentences)
    assert offset == 2.5

    assert estimate_timestamp_offset([], new_sentences) == 0.0


def test_alignment_submodule_empty_inputs(test_db):
    """Verify alignment handling for empty input lists."""
    mid = db.add_media(test_db, "/fake/media.mkv", "mkv_embedded")

    # When existing is empty, all new sentences are inserted
    up, ins, d = align_and_update_sentences(
        test_db, mid, "eng", [{"start_time": 1.0, "end_time": 2.0, "text": "Hello"}]
    )
    assert (up, ins, d) == (0, 1, 0)

    # When new is empty, all existing sentences are deleted
    up, ins, d = align_and_update_sentences(test_db, mid, "eng", [])
    assert (up, ins, d) == (0, 0, 1)


def test_extractor_submodule_stream_selection():
    """Verify stream prioritization and Spanish dialect handling."""
    assert "hdmv_pgs_subtitle" in BITMAP_SUBTITLE_CODECS

    streams = [
        {"index": 1, "tags": {"title": "Signs & Songs"}},
        {"index": 2, "tags": {"title": "Full Dialogue"}},
    ]
    selected = select_best_stream(streams)
    assert selected["index"] == 2

    empty_selected = select_best_stream([])
    assert empty_selected is None

    spanish_streams = [
        {"index": 1, "tags": {"title": "Castilian"}},
        {"index": 2, "tags": {"title": "Latin American"}},
    ]
    spa_selected = select_best_stream(spanish_streams, is_spanish=True)
    assert spa_selected["index"] == 2


def test_package_facade_all_exports():
    """Ensure all symbols defined in __all__ are properly exported by the package facade."""
    import hagi.indexer as indexer

    for symbol in indexer.__all__:
        assert hasattr(indexer, symbol), f"Symbol {symbol} missing from indexer"
