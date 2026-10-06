"""Tests for video file upgrades, MKV refresh, and permalink preservation."""

import json
import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from hagi import db
from hagi import indexer
from hagi.cli import app


@pytest.fixture
def test_db(monkeypatch):
    """Create an in-memory database fixture for tests."""
    db.DB_PATH = ":memory:"
    conn = db.init_db()
    monkeypatch.setattr(indexer, "_plex_cache_built", True)
    yield conn
    conn.close()


def test_parse_media_identifiers():
    """Test extracting season, episode, absolute episode, and show hint from various paths."""
    p1 = (
        "/mnt/NAS/Anime/Detective Conan (1996) {tvdb-72454}/Season 34/"
        "Detective Conan (1996) - S34E21 - 1207 - The J League Opening Whistle [CR][WEBDL-1080p]-Erai-raws.mkv"
    )
    ids1 = indexer.parse_media_identifiers(p1)
    assert ids1["season"] == 34
    assert ids1["episode"] == 21
    assert ids1["abs_episode"] == 1207
    assert ids1["show_hint"] == "Detective Conan"

    p2 = "/mnt/NAS/Anime/[Erai-raws] Detective Conan - 1207 [1080p].mkv"
    ids2 = indexer.parse_media_identifiers(p2)
    assert ids2["abs_episode"] == 1207
    assert ids2["show_hint"] == "Detective Conan"

    p3 = "/mnt/NAS/Anime/Conan/Season 2/Conan 2x05.mkv"
    ids3 = indexer.parse_media_identifiers(p3)
    assert ids3["season"] == 2
    assert ids3["episode"] == 5


def test_estimate_timestamp_offset():
    """Test detecting uniform timestamp offsets between releases."""
    existing = [
        {"start_time": 10.0, "text": "This is a distinctive sentence one."},
        {"start_time": 20.0, "text": "This is a distinctive sentence two."},
        {"start_time": 30.0, "text": "This is a distinctive sentence three."},
    ]
    # Shifted by +2.5 seconds
    new_s = [
        {"start_time": 12.5, "text": "This is a distinctive sentence one."},
        {"start_time": 22.5, "text": "This is a distinctive sentence two."},
        {"start_time": 32.5, "text": "This is a distinctive sentence three."},
    ]
    offset = indexer.estimate_timestamp_offset(existing, new_s)
    assert offset == 2.5

    # Test negligible offset (0.1s) returns 0.0
    new_negligible = [
        {"start_time": 10.1, "text": "This is a distinctive sentence one."},
        {"start_time": 20.1, "text": "This is a distinctive sentence two."},
        {"start_time": 30.1, "text": "This is a distinctive sentence three."},
    ]
    assert indexer.estimate_timestamp_offset(existing, new_negligible) == 0.0


def test_align_and_update_sentences_preserves_ids_with_offset(test_db):
    """Test that align_and_update_sentences preserves IDs when sentences have a global time shift."""
    mid = db.add_media(test_db, "/fake/conan.mkv", "mkv_embedded")
    initial_sentences = [
        ("jpn", 10.0, 12.0, "真実はいつもひとつ"),
        ("jpn", 20.0, 22.0, "見た目は子供頭脳は大人"),
    ]
    db.add_sentences(test_db, mid, initial_sentences)
    test_db.commit()

    rows = test_db.execute("SELECT id, start_time, text FROM sentences ORDER BY id").fetchall()
    id1, id2 = rows[0]["id"], rows[1]["id"]

    # New sentences shifted by +5.0s
    new_sentences = [
        {"start_time": 15.0, "end_time": 17.0, "text": "真実はいつもひとつ"},
        {"start_time": 25.0, "end_time": 27.0, "text": "見た目は子供頭脳は大人"},
    ]

    offset = indexer.estimate_timestamp_offset([dict(r) for r in rows], new_sentences)
    assert offset == 5.0

    updates, inserts, deletes = indexer.align_and_update_sentences(
        test_db, mid, "jpn", new_sentences, global_offset=offset
    )
    test_db.commit()

    assert updates == 2
    assert inserts == 0
    assert deletes == 0

    after_rows = test_db.execute("SELECT id, start_time, end_time, text FROM sentences ORDER BY id").fetchall()
    assert after_rows[0]["id"] == id1
    assert after_rows[0]["start_time"] == 15.0
    assert after_rows[1]["id"] == id2
    assert after_rows[1]["start_time"] == 25.0


def test_find_matching_media_by_metadata(test_db):
    """Test matching an upgraded file using show, season, and episode metadata."""
    old_path = "/nonexistent/path/Season 34/Conan - S34E21 [Kitsune].mkv"
    mid = db.add_media(test_db, old_path, "mkv_embedded", show_title="Detective Conan", season=34, episode=21)

    new_path = "/nonexistent/new/Season 34/Conan - S34E21 [Erai-raws].mkv"
    matched_id = indexer.find_matching_media(test_db, new_path, media_type="mkv_embedded")
    assert matched_id == mid


def test_find_matching_media_by_directory_and_episode(test_db):
    """Test matching an upgraded file in the same directory using parsed episode numbers."""
    dir_path = tempfile.mkdtemp()
    try:
        old_path = os.path.join(dir_path, "Detective Conan - S34E21 - 1207 [Kitsune].mkv")
        mid = db.add_media(test_db, old_path, "mkv_embedded", show_title="Detective Conan")

        new_path = os.path.join(dir_path, "Detective Conan - S34E21 - 1207 [Erai-raws].mkv")
        matched_id = indexer.find_matching_media(test_db, new_path, media_type="mkv_embedded")
        assert matched_id == mid
    finally:
        os.rmdir(dir_path)


def test_find_matching_media_by_content_fingerprint(test_db):
    """Test matching an upgraded file using subtitle sentence fingerprinting."""
    old_path = "/nonexistent/old_file.mkv"
    mid = db.add_media(test_db, old_path, "mkv_embedded")
    db.add_sentences(
        test_db,
        mid,
        [
            ("jpn", 1.0, 2.0, "Distinctive sentence alpha"),
            ("jpn", 3.0, 4.0, "Distinctive sentence beta"),
        ],
    )
    test_db.commit()

    new_path = "/nonexistent/completely_different_name.mkv"
    sample_texts = ["Distinctive sentence alpha", "Distinctive sentence beta", "Something else"]
    matched_id = indexer.find_matching_media(
        test_db, new_path, media_type="mkv_embedded", sample_sentences=sample_texts
    )
    assert matched_id == mid


def test_refresh_media_mkv_multi_language(test_db):
    """Test refreshing an MKV file preserves sentence IDs across multiple language tracks."""
    old_path = "/fake/conan_old.mkv"
    new_path = "/fake/conan_new.mkv"

    mid = db.add_media(test_db, old_path, "mkv_embedded", show_title="Detective Conan", season=34, episode=21)
    db.add_sentences(
        test_db,
        mid,
        [
            ("jpn", 1.0, 2.0, "Japanese line 1"),
            ("jpn", 3.0, 4.0, "Japanese line 2"),
            ("eng", 1.0, 2.0, "English line 1"),
        ],
    )
    test_db.commit()

    orig_jpn_ids = [r["id"] for r in test_db.execute("SELECT id FROM sentences WHERE language = 'jpn' ORDER BY id")]
    orig_eng_id = test_db.execute("SELECT id FROM sentences WHERE language = 'eng'").fetchone()["id"]

    mock_subs = {
        "jpn": [
            {"start_time": 1.2, "end_time": 2.2, "text": "Japanese line 1 retimed"},
            {"start_time": 3.2, "end_time": 4.2, "text": "Japanese line 2 retimed"},
        ],
        "eng": [
            {"start_time": 1.2, "end_time": 2.2, "text": "English line 1 retimed"},
        ],
    }

    with patch("hagi.indexer.extract_mkv_subtitles", return_value=(mock_subs, False)):
        success = indexer.refresh_media(test_db, mid, new_path)
        assert success is True

    # Verify media path was updated
    media_row = test_db.execute("SELECT path FROM media WHERE id = ?", (mid,)).fetchone()
    assert media_row["path"] == os.path.abspath(new_path)

    # Verify sentence IDs were preserved
    new_jpn_ids = [r["id"] for r in test_db.execute("SELECT id FROM sentences WHERE language = 'jpn' ORDER BY id")]
    new_eng_id = test_db.execute("SELECT id FROM sentences WHERE language = 'eng'").fetchone()["id"]
    assert new_jpn_ids == orig_jpn_ids
    assert new_eng_id == orig_eng_id


def test_index_directory_auto_upgrades_media(test_db, tmp_path):
    """Test that index_directory automatically upgrades replaced files and preserves sentence IDs."""
    season_dir = tmp_path / "Season 34"
    season_dir.mkdir()

    old_file_path = str(season_dir / "Conan - S34E21 [Kitsune].mkv")
    new_file_path = str(season_dir / "Conan - S34E21 [Erai-raws].mkv")

    # Seed database with old file entry (which does not exist on disk)
    mid = db.add_media(test_db, old_file_path, "mkv_embedded", show_title="Detective Conan", season=34, episode=21)
    db.add_sentences(test_db, mid, [("eng", 10.0, 15.0, "Old english dialogue")])
    test_db.commit()
    orig_sentence_id = test_db.execute("SELECT id FROM sentences").fetchone()["id"]

    # Create new file on disk
    with open(new_file_path, "w") as f:
        f.write("fake mkv content")

    mock_subs = {
        "eng": [
            {"start_time": 10.5, "end_time": 15.5, "text": "Old english dialogue"},
        ],
    }

    with (
        patch("hagi.indexer.get_db", return_value=test_db),
        patch("hagi.indexer.extract_mkv_subtitles", return_value=(mock_subs, False)),
    ):
        indexer.index_directory(str(season_dir))

    # Media row should be updated in-place (ID preserved, path changed)
    media_rows = test_db.execute("SELECT id, path FROM media").fetchall()
    assert len(media_rows) == 1
    assert media_rows[0]["id"] == mid
    assert media_rows[0]["path"] == new_file_path

    # Sentence row ID preserved
    sentences = test_db.execute("SELECT id, start_time, text FROM sentences").fetchall()
    assert len(sentences) == 1
    assert sentences[0]["id"] == orig_sentence_id
    assert sentences[0]["start_time"] == 10.5


def test_cli_refresh_command(tmp_path, monkeypatch):
    """Test that hagi refresh CLI command invokes indexer.refresh_file with correct parameters."""
    runner = CliRunner()

    called = {}

    def mock_refresh_file(path, old_path=None, media_id=None, **kwargs):
        """Mock refresh_file recording invocations."""
        called["path"] = path
        called["old_path"] = old_path
        called["media_id"] = media_id
        return True

    monkeypatch.setattr(indexer, "refresh_file", mock_refresh_file)
    monkeypatch.setattr(db, "init_db", lambda: None)

    fake_file = tmp_path / "episode1.mkv"
    fake_file.write_text("content")

    result = runner.invoke(app, ["refresh", str(fake_file), "--old", "/old/ep1.mkv", "--media-id", "42"])
    assert result.exit_code == 0
    assert called["path"] == str(fake_file)
    assert called["old_path"] == "/old/ep1.mkv"
    assert called["media_id"] == 42


def test_exporter_video_fallback_by_episode(test_db, tmp_path):
    """Test that exporter.extract_media falls back to finding a video by season/episode in the directory."""
    from hagi import exporter

    # Create dummy directory with upgraded video file
    dir_path = tmp_path / "Season 34"
    dir_path.mkdir()

    video_file = dir_path / "Detective Conan - S34E21 - 1207 - Erai-raws.mkv"
    video_file.write_text("dummy video")

    # Subtitle file points to an old name that doesn't match the new video basename
    sub_file = dir_path / "Detective Conan - S34E21 - 1207 - OldRelease.srt"

    mid = db.add_media(
        test_db,
        str(sub_file),
        "subtitle",
        show_title="Detective Conan",
        season=34,
        episode=21,
    )
    db.add_sentences(test_db, mid, [("jpn", 1.0, 3.0, "テスト台詞")])
    test_db.commit()

    sid = test_db.execute("SELECT id FROM sentences").fetchone()["id"]

    exporter.clear_stream_info_cache()
    real_exists = os.path.exists

    def mock_exists(p: str) -> bool:
        """Mock file existence so temporary media targets succeed while caches miss."""
        if ("hagi_audio" in p or "hagi_img" in p) and "tmp" not in p:
            return False
        if "tmp" in p:
            return True
        return real_exists(p)

    with (
        patch("hagi.exporter.db.get_db", return_value=test_db),
        patch("os.makedirs"),
        patch("hagi.exporter.os.path.exists", side_effect=mock_exists),
        patch("subprocess.run") as mock_subrun,
        patch("os.replace"),
        patch("hagi.exporter.os.path.getsize", return_value=1024),
    ):
        mock_res = MagicMock()
        mock_res.returncode = 0
        mock_res.stdout = json.dumps(
            {"streams": [{"codec_type": "video", "codec_name": "h264"}, {"codec_type": "audio", "codec_name": "aac"}]}
        )
        mock_subrun.return_value = mock_res

        success, msg, audio_out, image_out, text, is_cached = exporter.extract_media(sid, "/tmp/media")
        assert success is True
        # Verify ffprobe was called with the discovered video path
        ffprobe_args = mock_subrun.call_args_list[0][0][0]
        assert str(video_file) in ffprobe_args
