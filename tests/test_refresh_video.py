"""Tests for video file upgrades, MKV refresh, and permalink preservation."""

import json
import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from hagi import db
from hagi import exporter
from hagi import indexer
from hagi.cli import app


@pytest.fixture
def test_db(monkeypatch):
    """Create an in-memory database fixture for tests."""
    monkeypatch.setattr(db, "DB_PATH", ":memory:")
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


def test_refresh_media_mkv_multi_language(test_db, tmp_path):
    """Test refreshing an MKV file preserves sentence IDs across multiple language tracks."""
    old_path = "/fake/conan_old.mkv"
    new_file = tmp_path / "conan_new.mkv"
    new_file.write_text("dummy")
    new_path = str(new_file)

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

    with patch("hagi.indexer.extract_mkv_subtitles", return_value=(mock_subs, False, True)):
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
        patch("hagi.indexer.extract_mkv_subtitles", return_value=(mock_subs, False, True)),
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
        base = os.path.basename(p)
        if "_tmp_" in base or ".tmp." in base:
            return True
        if base.startswith(("hagi_", ".hagi_")):
            return False
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


def test_find_matching_media_rejects_mismatched_show(test_db):
    """Test that candidate matching does not match across different shows with identical season/episode."""
    naruto_path = "/nonexistent/Naruto/Season 1/Naruto - S01E01.mkv"
    db.add_media(test_db, naruto_path, "mkv_embedded", show_title="Naruto", season=1, episode=1)

    one_piece_path = "/nonexistent/One Piece/Season 1/One Piece - S01E01.mkv"
    matched_id = indexer.find_matching_media(test_db, one_piece_path, media_type="mkv_embedded")
    assert matched_id is None


def test_refresh_media_validates_file_and_probe_failure(test_db, tmp_path):
    """Test that refresh_media rejects non-files and aborts when ffprobe fails."""
    mid = db.add_media(test_db, "/old/video.mkv", "mkv_embedded", season=1, episode=1)

    # 1. Non-existent file
    assert indexer.refresh_media(test_db, mid, "/nonexistent/video.mkv") is False

    # 2. Existing file but ffprobe fails
    real_file = tmp_path / "corrupt.mkv"
    real_file.write_text("corrupt content")

    with patch("hagi.indexer.extract_mkv_subtitles", return_value=({}, False, False)):
        assert indexer.refresh_media(test_db, mid, str(real_file)) is False


def test_extract_mkv_subtitles_handles_probe_timeout(tmp_path):
    """Test that extract_mkv_subtitles gracefully handles probe subprocess timeout."""
    import subprocess

    dummy_mkv = tmp_path / "test.mkv"
    dummy_mkv.write_text("dummy")

    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="ffprobe", timeout=5)):
        subs, had_timeout, probe_ok = indexer.extract_mkv_subtitles(str(dummy_mkv), probe_timeout=5)
        assert subs == {}
        assert had_timeout is True
        assert probe_ok is False


def test_find_matching_media_rejects_flat_folder_cross_shows(test_db):
    """Test that candidate matching in flat non-season folders does not match across different shows."""
    naruto_path = "/nonexistent/Anime/Naruto - 01.mkv"
    db.add_media(test_db, naruto_path, "mkv_embedded", show_title="Naruto", episode=1)

    one_piece_path = "/nonexistent/Anime/One Piece - 01.mkv"
    matched_id = indexer.find_matching_media(test_db, one_piece_path, media_type="mkv_embedded")
    assert matched_id is None


def test_find_matching_media_auto_samples_subtitles(test_db, tmp_path):
    """Test that find_matching_media automatically extracts sample sentences from subtitle files."""
    old_path = "/nonexistent/old_subs.srt"
    mid = db.add_media(test_db, old_path, "subtitle")
    db.add_sentences(
        test_db,
        mid,
        [
            ("jpn", 1.0, 2.0, "This is a distinctive sentence alpha"),
            ("jpn", 3.0, 4.0, "This is a distinctive sentence beta"),
        ],
    )
    test_db.commit()

    new_sub = tmp_path / "completely_different_name.srt"
    new_sub.write_text(
        "1\n00:00:01,000 --> 00:00:02,000\nThis is a distinctive sentence alpha\n\n"
        "2\n00:00:03,000 --> 00:00:04,000\nThis is a distinctive sentence beta\n\n"
    )

    matched_id = indexer.find_matching_media(test_db, str(new_sub), media_type="subtitle")
    assert matched_id == mid


def test_refresh_media_rejects_language_mismatch(test_db, tmp_path):
    """Test that refresh_media rejects a replacement subtitle whose language mismatches stored media."""
    mid = db.add_media(test_db, "/old/subs.ja.srt", "subtitle")
    db.add_sentences(test_db, mid, [("jpn", 1.0, 3.0, "これは日本語のテキストです。")])
    test_db.commit()

    en_sub = tmp_path / "subs.en.srt"
    en_sub.write_text("1\n00:00:01,000 --> 00:00:03,000\nThis is purely English text.\n\n")

    assert indexer.refresh_media(test_db, mid, str(en_sub)) is False


def test_find_matching_media_rejects_tied_fingerprints(test_db, tmp_path):
    """Test that candidate matching returns None when two candidates have equal top fingerprint matches."""
    mid1 = db.add_media(test_db, "/nonexistent/cand1.srt", "subtitle")
    mid2 = db.add_media(test_db, "/nonexistent/cand2.srt", "subtitle")

    shared = [
        ("eng", 1.0, 2.0, "Shared sentence one that is distinctive"),
        ("eng", 3.0, 4.0, "Shared sentence two that is distinctive"),
    ]
    db.add_sentences(test_db, mid1, shared)
    db.add_sentences(test_db, mid2, shared)
    test_db.commit()

    new_sub = tmp_path / "test_tied.srt"
    new_sub.write_text(
        "1\n00:00:01,000 --> 00:00:02,000\nShared sentence one that is distinctive\n\n"
        "2\n00:00:03,000 --> 00:00:04,000\nShared sentence two that is distinctive\n\n"
    )

    matched = indexer.find_matching_media(test_db, str(new_sub), media_type="subtitle")
    assert matched is None


def test_find_matching_media_rejects_cross_language_subtitles(test_db):
    """Test that candidate matching does not match a .en.srt file to a missing .ja.srt row."""
    ja_path = "/nonexistent/Season 34/Conan - S34E21.ja.srt"
    db.add_media(test_db, ja_path, "subtitle", show_title="Detective Conan", season=34, episode=21)

    en_path = "/nonexistent/Season 34/Conan - S34E21.en.srt"
    matched_id = indexer.find_matching_media(test_db, en_path, media_type="subtitle")
    assert matched_id is None


def test_refresh_media_rejects_type_mismatch(test_db, tmp_path):
    """Test that refresh_media rejects files whose extension conflicts with media.type."""
    mid_sub = db.add_media(test_db, "/old/sub.srt", "subtitle")
    mid_mkv = db.add_media(test_db, "/old/video.mkv", "mkv_embedded")

    dummy_mkv = tmp_path / "new_video.mkv"
    dummy_mkv.write_text("dummy mkv")

    dummy_srt = tmp_path / "new_sub.srt"
    dummy_srt.write_text("1\n00:00:01,000 --> 00:00:02,000\nHello\n\n")

    # Trying to refresh a subtitle row with an MKV file should fail
    assert indexer.refresh_media(test_db, mid_sub, str(dummy_mkv)) is False

    # Trying to refresh an MKV row with a subtitle file should fail
    assert indexer.refresh_media(test_db, mid_mkv, str(dummy_srt)) is False


def test_refresh_media_rejects_already_owned_path(test_db, tmp_path):
    """Test that refresh_media refuses to retarget to a path already owned by a different media record."""
    owned_file = tmp_path / "ep1.srt"
    owned_file.write_text("1\n00:00:01,000 --> 00:00:02,000\nHello\n\n")

    db.add_media(test_db, str(owned_file), "subtitle")
    mid2 = db.add_media(test_db, "/old/ep2.srt", "subtitle")

    # Attempting to refresh mid2 to owned_file should fail because mid1 already owns it
    assert indexer.refresh_media(test_db, mid2, str(owned_file)) is False


def test_find_matching_media_fingerprint_rejects_conflicting_episode(test_db, tmp_path):
    """Test that subtitle fingerprinting does not match candidates with conflicting episode numbers."""
    mid_ep1 = db.add_media(test_db, "/nonexistent/Conan - S01E01.srt", "subtitle", season=1, episode=1)
    shared = [
        ("eng", 1.0, 2.0, "Universal anime disclaimer sentence"),
        ("eng", 3.0, 4.0, "Another generic sentence in disclaimer"),
    ]
    db.add_sentences(test_db, mid_ep1, shared)
    test_db.commit()

    # Episode 2 with the same recap lines should NOT match Episode 1 candidate
    new_sub = tmp_path / "Conan - S01E02.srt"
    new_sub.write_text(
        "1\n00:00:01,000 --> 00:00:02,000\nUniversal anime disclaimer sentence\n\n"
        "2\n00:00:03,000 --> 00:00:04,000\nAnother generic sentence in disclaimer\n\n"
    )

    matched = indexer.find_matching_media(test_db, str(new_sub), media_type="subtitle")
    assert matched is None


def test_refresh_file_specified_old_not_found(test_db, tmp_path):
    """Test that refresh_file returns False when an explicit old path does not exist in DB."""
    new_file = tmp_path / "new_video.mkv"
    new_file.write_text("video content")

    with patch("hagi.indexer.get_db", return_value=test_db):
        res = indexer.refresh_file(str(new_file), old_path="/nonexistent/old_video.mkv")
        assert res is False


def test_refresh_media_alignment_failure_rolls_back(test_db, tmp_path):
    """Test that refresh_media rolls back changes if alignment reports a failure."""
    old_sub = "/old/sub.srt"
    mid = db.add_media(test_db, old_sub, "subtitle", show_title="Show", season=1, episode=1)
    db.add_sentences(test_db, mid, [("eng", 1.0, 2.0, "Original sentence")])
    test_db.commit()

    new_sub = tmp_path / "sub.srt"
    new_sub.write_text("1\n00:00:01,000 --> 00:00:02,000\nOriginal sentence\n\n")

    def mock_align(conn, media_id, lang, new_sentences, global_offset=0.0):
        """Mutate a sentence in DB before failing to verify rollback undoes changes."""
        conn.execute("UPDATE sentences SET text = 'Mutated before failure' WHERE media_id = ?", (media_id,))
        return (-1, -1, -1)

    with patch("hagi.indexer.align_and_update_sentences", side_effect=mock_align):
        res = indexer.refresh_media(test_db, mid, str(new_sub))
        assert res is False

    # Path and sentences should be uncommitted / rolled back
    row = test_db.execute("SELECT path FROM media WHERE id = ?", (mid,)).fetchone()
    assert row["path"] == old_sub
    sentences = test_db.execute("SELECT text FROM sentences WHERE media_id = ?", (mid,)).fetchall()
    assert len(sentences) == 1
    assert sentences[0]["text"] == "Original sentence"


def test_exporter_video_fallback_single_candidate_show_mismatch(test_db, tmp_path):
    """Test that single candidate fallback does not match when show_hint conflicts with meta."""
    from hagi import exporter

    dir_path = tmp_path / "Season 34"
    dir_path.mkdir()

    # Video file from a completely different show with same season/episode
    naruto_file = dir_path / "Naruto Shippuden - S34E21.mkv"
    naruto_file.write_text("dummy video")

    sub_file = dir_path / "Detective Conan - S34E21.srt"
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

    with patch("hagi.exporter.db.get_db", return_value=test_db):
        # Should reject Naruto and fall back to non-existent Detective Conan .mkv path
        success, msg, _, _, _, _ = exporter.extract_media(sid, "/tmp/media")
        assert success is False
        assert "Video file not found" in msg
        assert "Detective Conan - S34E21.mkv" in msg
        assert "Naruto" not in msg

    # Now add matching Detective Conan upgraded video file and remove Naruto file
    naruto_file.unlink()
    conan_file = dir_path / "Detective Conan - S34E21 - 1207 - Erai-raws.mkv"
    conan_file.write_text("conan video")

    real_exists = os.path.exists

    def mock_exists(p: str) -> bool:
        """Mock file existence so temporary media targets succeed while caches miss."""
        base = os.path.basename(p)
        if "_tmp_" in base or ".tmp." in base:
            return True
        if base.startswith(("hagi_", ".hagi_")):
            return False
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

        success, msg, _, _, _, _ = exporter.extract_media(sid, "/tmp/media")
        assert success is True
        ffprobe_args = mock_subrun.call_args_list[0][0][0]
        assert str(conan_file) in ffprobe_args


def test_extract_file_lang_locale_validation():
    """Test that _extract_file_lang validates known locales and ignores non-locale tokens."""
    # Valid locales (case-insensitive and aliases)
    assert indexer._extract_file_lang("/path/show.ja.srt") == "jpn"
    assert indexer._extract_file_lang("/path/show.JA.srt") == "jpn"
    assert indexer._extract_file_lang("/path/show.jpn.ass") == "jpn"
    assert indexer._extract_file_lang("/path/show.en.srt") == "eng"
    assert indexer._extract_file_lang("/path/show.sp.srt") == "spa"
    assert indexer._extract_file_lang("/path/show.spa.vtt") == "spa"

    # Non-locale tokens should return None
    assert indexer._extract_file_lang("/path/show.v2.srt") is None
    assert indexer._extract_file_lang("/path/show.web.srt") is None
    assert indexer._extract_file_lang("/path/show.crc.srt") is None
    assert indexer._extract_file_lang("/path/show.srt") is None


def test_resolve_timeouts(monkeypatch):
    """Test that _resolve_timeouts resolves explicit, config, and default timeouts."""
    # Explicit positive values
    assert indexer._resolve_timeouts(100, 50) == (100, 50)
    # Explicit non-positive (unlimited)
    assert indexer._resolve_timeouts(0, -1) == (None, None)

    # Defaults when config is empty
    monkeypatch.setattr(indexer, "_load_config", lambda: {})
    assert indexer._resolve_timeouts(None, None) == (
        indexer.DEFAULT_EXTRACT_TIMEOUT,
        indexer.DEFAULT_PROBE_TIMEOUT,
    )

    # Custom values from config
    monkeypatch.setattr(
        indexer,
        "_load_config",
        lambda: {"extractTimeout": 600, "probeTimeout": 120},
    )
    assert indexer._resolve_timeouts(None, None) == (600, 120)


def test_extract_mkv_subtitles_allow_partial(tmp_path):
    """Test that extract_mkv_subtitles rejects partial output when allow_partial=False."""
    dummy_mkv = tmp_path / "test.mkv"
    dummy_mkv.write_text("fake mkv")

    probe_json = json.dumps({
        "streams": [
            {"index": 0, "codec_name": "subrip", "tags": {"language": "eng"}},
        ]
    })

    def mock_subrun(*args, **kwargs):
        """Mock subprocess.run to simulate probe success but extraction failure."""
        from subprocess import CompletedProcess

        cmd = args[0]
        if "ffprobe" in cmd:
            return CompletedProcess(cmd, 0, stdout=probe_json, stderr="")
        # First batch ffmpeg fails, retry also fails
        return CompletedProcess(cmd, 1, stdout="", stderr="ffmpeg error")

    with patch("subprocess.run", side_effect=mock_subrun):
        # With allow_partial=False (as in refresh), failure reports empty dict and failure status
        subs, had_timeout, probe_ok = indexer.extract_mkv_subtitles(
            str(dummy_mkv), allow_partial=False
        )
        assert subs is None
        assert probe_ok is True


def test_parse_media_identifiers_excludes_release_year():
    """Test that parse_media_identifiers excludes release years matched by the fallback regex."""
    # Fallback regex matches 4-digit number that is a release year (1900..2099)
    p = "/mnt/NAS/Anime/Detective Conan (1996)/Detective Conan 2024 episode [1080p].mkv"
    ids = indexer.parse_media_identifiers(p)
    assert ids["abs_episode"] is None

    # Prefix match with hyphen is a legitimate absolute episode even if > 1000
    p2 = "/mnt/NAS/Anime/Conan - 1207 [1080p].mkv"
    ids2 = indexer.parse_media_identifiers(p2)
    assert ids2["abs_episode"] == 1207


def test_find_matching_media_rejects_conflicting_season_episode_in_abs(test_db):
    """Test that find_matching_media rejects absolute episode candidates with conflicting season/episode."""
    # Existing missing media is S01E05 with abs 100
    p1 = "/nonexistent/Conan - S01E05 - 100.mkv"
    mid1 = db.add_media(test_db, p1, "mkv_embedded", show_title="Conan", season=1, episode=5)

    # New file is S02E05 with abs 100 (conflicting season)
    p2 = "/nonexistent/Conan - S02E05 - 100.mkv"
    matched = indexer.find_matching_media(test_db, p2, media_type="mkv_embedded")
    assert matched is None

    # New file with matching season/episode and same abs should match
    p3 = "/nonexistent/Conan - S01E05 - 100 [Upgraded].mkv"
    matched_ok = indexer.find_matching_media(test_db, p3, media_type="mkv_embedded")
    assert matched_ok == mid1


def test_prune_database_uses_resolved_timeouts(monkeypatch, test_db):
    """Test that prune_database resolves timeouts via _resolve_timeouts and passes them to refresh_media."""
    called_timeouts = {}

    def mock_refresh_media(conn, mid, path, extract_timeout=None, probe_timeout=None):
        """Mock refresh_media recording passed timeouts."""
        called_timeouts["extract"] = extract_timeout
        called_timeouts["probe"] = probe_timeout
        return True

    monkeypatch.setattr(indexer, "refresh_media", mock_refresh_media)
    monkeypatch.setattr(indexer, "get_db", lambda: test_db)
    monkeypatch.setattr(indexer, "_resolve_timeouts", lambda: (777, 888))

    # Add a missing media row that has an upgraded replacement
    import tempfile
    with tempfile.TemporaryDirectory() as tmp_dir:
        old_path = os.path.join(tmp_dir, "Show - S01E01 [Old].mkv")
        new_path = os.path.join(tmp_dir, "Show - S01E01 [New].mkv")
        with open(new_path, "w") as f:
            f.write("content")

        db.add_media(test_db, old_path, "mkv_embedded", show_title="Show", season=1, episode=1)
        test_db.commit()

        indexer.prune_database()
        assert called_timeouts.get("extract") == 777
        assert called_timeouts.get("probe") == 888


def test_refresh_file_directory_rejects_options(test_db, tmp_path):
    """Test that refresh_file rejects calls on a directory when old_path or media_id is passed."""
    dummy_dir = tmp_path / "Season 1"
    dummy_dir.mkdir()

    with patch("hagi.indexer.get_db", return_value=test_db):
        # Reject when old_path is provided
        assert indexer.refresh_file(str(dummy_dir), old_path="/some/old/path") is False

        # Reject when media_id is provided
        assert indexer.refresh_file(str(dummy_dir), media_id=42) is False


def test_refresh_file_single_handles_exception_and_rolls_back(test_db, tmp_path):
    """Test that refresh_file catches exceptions in refresh_media, rolls back, and returns False."""
    sub_file = tmp_path / "ep1.srt"
    sub_file.write_text("1\n00:00:01,000 --> 00:00:02,000\nHello\n\n")

    mid = db.add_media(test_db, str(sub_file), "subtitle", show_title="Show", season=1, episode=1)
    test_db.commit()

    with (
        patch("hagi.indexer.get_db", return_value=test_db),
        patch("hagi.indexer.refresh_media", side_effect=RuntimeError("disk read error")),
    ):
        res = indexer.refresh_file(str(sub_file), media_id=mid)
        assert res is False


def test_titles_match():
    """Test show title matching with prefix preservation and trailing token rejection."""
    # Exact match
    assert indexer.titles_match("Naruto", "Naruto") is True
    assert indexer.titles_match("Detective Conan (1996)", "Detective Conan") is True

    # Leading prefix match (Conan vs Detective Conan)
    assert indexer.titles_match("Detective Conan", "Conan") is True
    assert indexer.titles_match("Conan", "Detective Conan") is True
    assert indexer.titles_match("The Melancholy of Haruhi", "Melancholy of Haruhi") is True

    # Trailing addition should NOT match (Naruto vs Naruto Shippuden)
    assert indexer.titles_match("Naruto", "Naruto Shippuden") is False
    assert indexer.titles_match("Naruto Shippuden", "Naruto") is False
    assert indexer.titles_match("Bleach", "Bleach: Thousand-Year Blood War") is False

    # Arbitrary single-word prefixes should NOT match
    assert indexer.titles_match("Black Clover", "Clover") is False
    assert indexer.titles_match("Shin Evangelion", "Evangelion") is False
    assert indexer.titles_match("Re:Zero", "Zero") is False

    # Empty or None titles
    assert indexer.titles_match(None, "Naruto") is False
    assert indexer.titles_match("Naruto", "") is False


def test_find_matching_media_rejects_subseries_extension(test_db):
    """Test that Naruto does not match Naruto Shippuden when finding matching media."""
    naruto_path = "/nonexistent/Naruto/Season 1/Naruto - S01E01.mkv"
    db.add_media(test_db, naruto_path, "mkv_embedded", show_title="Naruto", season=1, episode=1)

    # Replacement candidate has same season/episode but is "Naruto Shippuden"
    shippuden_path = "/nonexistent/Naruto Shippuden/Season 1/Naruto Shippuden - S01E01.mkv"
    matched_id = indexer.find_matching_media(test_db, shippuden_path, media_type="mkv_embedded")
    assert matched_id is None


def test_prune_database_retains_media_on_failed_refresh(test_db, tmp_path):
    """Test that prune_database retains missing media if a matched upgrade refresh fails."""
    old_path = str(tmp_path / "Show - S01E01 [Old].mkv")
    new_path = str(tmp_path / "Show - S01E01 [New].mkv")
    with open(new_path, "w") as f:
        f.write("content")

    mid = db.add_media(test_db, old_path, "mkv_embedded", show_title="Show", season=1, episode=1)
    db.add_sentences(test_db, mid, [("eng", 1.0, 2.0, "Dialogue")])
    test_db.commit()

    with (
        patch("hagi.indexer.get_db", return_value=test_db),
        patch("hagi.indexer.refresh_media", return_value=False),
    ):
        indexer.prune_database()

    # The missing media record should NOT have been pruned
    row = test_db.execute("SELECT id, path FROM media WHERE id = ?", (mid,)).fetchone()
    assert row is not None
    assert row["path"] == old_path


def test_index_directory_retains_media_and_skips_duplicate_on_failed_upgrade(test_db, tmp_path):
    """Test that index_directory does not index a replacement as duplicate when upgrade refresh fails."""
    season_dir = tmp_path / "Season 1"
    season_dir.mkdir()

    old_file_path = str(season_dir / "Conan - S01E01 [Old].mkv")
    new_file_path = str(season_dir / "Conan - S01E01 [New].mkv")

    mid = db.add_media(test_db, old_file_path, "mkv_embedded", show_title="Conan", season=1, episode=1)
    db.add_sentences(test_db, mid, [("eng", 1.0, 2.0, "Dialogue")])
    test_db.commit()

    with open(new_file_path, "w") as f:
        f.write("new content")

    with (
        patch("hagi.indexer.get_db", return_value=test_db),
        patch("hagi.indexer.refresh_media", return_value=False),
    ):
        indexer.index_directory(str(season_dir))

    # Should retain the original media and NOT create a new duplicate media record
    media_rows = test_db.execute("SELECT id, path FROM media").fetchall()
    assert len(media_rows) == 1
    assert media_rows[0]["id"] == mid
    assert media_rows[0]["path"] == old_file_path


def test_prune_database_retains_media_on_scan_exception(test_db, tmp_path):
    """Test that prune_database retains missing media if directory scan raises an exception."""
    parent_dir = tmp_path / "Season 1"
    parent_dir.mkdir()
    old_path = str(parent_dir / "Show - S01E01 [Old].mkv")

    mid = db.add_media(test_db, old_path, "mkv_embedded", show_title="Show", season=1, episode=1)
    db.add_sentences(test_db, mid, [("eng", 1.0, 2.0, "Dialogue")])
    test_db.commit()

    with (
        patch("hagi.indexer.get_db", return_value=test_db),
        patch("os.scandir", side_effect=OSError("Disk read error")),
    ):
        indexer.prune_database()

    row = test_db.execute("SELECT id, path FROM media WHERE id = ?", (mid,)).fetchone()
    assert row is not None
    assert row["path"] == old_path


def test_parse_media_identifiers_extracts_file_title():
    """Test that parse_media_identifiers extracts file_title before season/episode numbers."""
    ids = indexer.parse_media_identifiers("Detective Conan S34E21.mkv")
    assert ids["file_title"] == "Detective Conan"
    assert ids["show_hint"] == "Detective Conan"
    assert ids["season"] == 34
    assert ids["episode"] == 21

    ids2 = indexer.parse_media_identifiers("[Erai-raws] Detective Conan - 1207 [1080p].mkv")
    assert ids2["file_title"] == "Detective Conan"
    assert ids2["abs_episode"] == 1207


def test_extract_media_sole_candidate_with_generic_dir(test_db, tmp_path):
    """Test that extract_media accepts sole episode candidate when directory is generic."""
    generic_dir = tmp_path / "Downloads"
    generic_dir.mkdir()
    old_file = str(generic_dir / "Conan - S01E01 [Old].mkv")
    new_file = str(generic_dir / "S01E01.mkv")
    with open(new_file, "w") as f:
        f.write("content")

    mid = db.add_media(test_db, old_file, "mkv_embedded", show_title="Detective Conan", season=1, episode=1)
    db.add_sentences(test_db, mid, [("eng", 1.0, 2.0, "Hello world")])
    test_db.commit()

    with (
        patch("hagi.db.get_db", return_value=test_db),
        patch("hagi.exporter.get_media_stream_info", return_value=(1, False, None)),
        patch("subprocess.run") as mock_sub,
    ):
        mock_sub.return_value.returncode = 0
        with (
            patch(
                "os.path.exists",
                side_effect=lambda p: p == new_file or os.path.basename(p).startswith((".hagi_", "hagi_")),
            ),
            patch("os.path.getsize", return_value=100),
            patch("os.replace"),
        ):
            success, msg, audio_out, img_out, text, cached = exporter.extract_media(
                1, str(tmp_path / "out"), pad_start=0.0, pad_end=0.0
            )
            assert success is True
            assert cached is False
            cmd_calls = [call[0][0] for call in mock_sub.call_args_list]
            assert any(new_file in cmd for cmd in cmd_calls)


def test_extract_media_invalidates_cache_on_source_change(test_db, tmp_path):
    """Test that extract_media does not reuse cache if source video changed."""
    media_dir = tmp_path / "Season 1"
    media_dir.mkdir()
    old_file = str(media_dir / "Show - S01E01 [Old].mkv")
    new_file = str(media_dir / "Show - S01E01 [New].mkv")
    with open(new_file, "w") as f:
        f.write("video content")

    mid = db.add_media(test_db, old_file, "mkv_embedded", show_title="Show", season=1, episode=1)
    db.add_sentences(test_db, mid, [("eng", 1.0, 2.0, "Hello")])
    test_db.commit()

    out_dir = str(tmp_path / "out")
    os.makedirs(out_dir, exist_ok=True)
    audio_file = os.path.join(out_dir, "hagi_audio_1_0.000_0.000.mp3")
    img_file = os.path.join(out_dir, "hagi_img_1_0.000_0.000.jpg")
    src_tag = os.path.join(out_dir, ".hagi_cache_1_0.000_0.000.src")

    with open(audio_file, "w") as f:
        f.write("old audio")
    with open(img_file, "w") as f:
        f.write("old image")
    with open(src_tag, "w") as f:
        f.write(old_file)

    def mock_run(cmd, *args, **kwargs):
        """Mock subprocess.run by creating the expected output file."""
        out_path = cmd[-1]
        with open(out_path, "wb") as f:
            f.write(b"dummy data")
        res = MagicMock()
        res.returncode = 0
        res.stderr = ""
        return res

    with (
        patch("hagi.db.get_db", return_value=test_db),
        patch("hagi.exporter.get_media_stream_info", return_value=(1, False, None)),
        patch("subprocess.run", side_effect=mock_run),
    ):
        success, msg, a_out, i_out, text, cached = exporter.extract_media(
            1, out_dir, pad_start=0.0, pad_end=0.0
        )
        assert success is True
        # Because src_tag contained old_file and new_file was selected as fallback, cache is invalidated
        assert cached is False
        with open(src_tag, "r") as f:
            assert f.read().strip() == new_file


def test_refresh_media_reconciles_relabeled_tracks(test_db, tmp_path):
    """Test that refresh_media reconciles subtitle tracks relabeled to another language code."""
    file_path = str(tmp_path / "Show - S01E01.mkv")
    with open(file_path, "w") as f:
        f.write("video content")

    mid = db.add_media(test_db, file_path, "mkv_embedded", show_title="Show", season=1, episode=1)
    # Stored initially under jpn
    stored_subs = [
        ("jpn", 1.0, 2.0, "Konnichiwa"),
        ("jpn", 3.0, 4.0, "Arigatou"),
        ("jpn", 5.0, 6.0, "Sayounara"),
        ("jpn", 7.0, 8.0, "Hai"),
        ("jpn", 9.0, 10.0, "Iie"),
    ]
    db.add_sentences(test_db, mid, stored_subs)
    test_db.commit()

    initial_sids = [
        r["id"] for r in test_db.execute("SELECT id FROM sentences WHERE media_id = ? ORDER BY id", (mid,)).fetchall()
    ]

    # Replacement MKV mislabeled the track as eng
    new_subs = {
        "eng": [
            {"start_time": 1.2, "end_time": 2.2, "text": "Konnichiwa"},
            {"start_time": 3.2, "end_time": 4.2, "text": "Arigatou"},
            {"start_time": 5.2, "end_time": 6.2, "text": "Sayounara"},
            {"start_time": 7.2, "end_time": 8.2, "text": "Hai"},
            {"start_time": 9.2, "end_time": 10.2, "text": "Iie"},
        ]
    }

    with patch("hagi.indexer.extract_mkv_subtitles", return_value=(new_subs, False, True)):
        success = indexer.refresh_media(test_db, mid, file_path)
        assert success is True

    # Language in DB was reconciled to eng and existing sentence IDs were preserved
    rows = test_db.execute("SELECT id, language, start_time FROM sentences WHERE media_id = ? ORDER BY id", (mid,)).fetchall()
    assert len(rows) == 5
    assert [r["id"] for r in rows] == initial_sids
    assert all(r["language"] == "eng" for r in rows)
    assert round(rows[0]["start_time"], 1) == 1.2
