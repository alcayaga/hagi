"""Tests for the smart refresh feature in indexer.py."""

import os
import tempfile

import pytest

from hagi import db
from hagi.db import add_media, add_sentences
from hagi.indexer import refresh_file


@pytest.fixture
def test_db(tmp_path):
    """Create a temporary database for testing."""
    old_db_path = db.DB_PATH
    temp_db = tmp_path / "test_hagi.db"
    db.DB_PATH = str(temp_db)
    db.init_db()
    conn = db.get_db()
    yield conn
    conn.close()
    db.DB_PATH = old_db_path


def create_srt(path, lines):
    """Create a temporary SRT file for testing."""
    with open(path, "w", encoding="utf-8") as f:
        for i, line in enumerate(lines, 1):
            f.write(f"{i}\n")
            f.write(f"{line['start']} --> {line['end']}\n")
            f.write(f"{line['text']}\n\n")


def test_refresh_perfect_1_to_1(test_db):
    """Test replacing an SRT with exact same timestamps but fixed text."""
    conn = test_db
    with tempfile.NamedTemporaryFile(suffix=".srt", delete=False) as tf:
        srt_path = tf.name

    try:
        # Initial fake media & sentences
        media_id = add_media(conn, srt_path, "subtitle")
        initial_sentences = [
            ("ja", 1.0, 2.0, "Typo text 1"),
            ("ja", 3.0, 4.0, "Typo text 2"),
        ]
        add_sentences(conn, media_id, initial_sentences)
        conn.commit()

        # Verify initial state
        rows = conn.execute("SELECT id, text FROM sentences ORDER BY id").fetchall()
        id1, id2 = rows[0]["id"], rows[1]["id"]

        # Now create new SRT with fixed text
        new_lines = [
            {"start": "00:00:01,000", "end": "00:00:02,000", "text": "Fixed text 1"},
            {"start": "00:00:03,000", "end": "00:00:04,000", "text": "Fixed text 2"},
        ]
        create_srt(srt_path, new_lines)

        # Run refresh
        refresh_file(srt_path)

        # Verify ids are preserved
        rows = conn.execute("SELECT id, text FROM sentences ORDER BY start_time").fetchall()
        assert len(rows) == 2
        assert rows[0]["id"] == id1
        assert rows[0]["text"] == "Fixed text 1"
        assert rows[1]["id"] == id2
        assert rows[1]["text"] == "Fixed text 2"
    finally:
        if os.path.exists(srt_path):
            os.remove(srt_path)


def test_refresh_retiming(test_db):
    """Test replacing an SRT with shifted timestamps within threshold."""
    conn = test_db
    with tempfile.NamedTemporaryFile(suffix=".srt", delete=False) as tf:
        srt_path = tf.name

    try:
        media_id = add_media(conn, srt_path, "subtitle")
        initial_sentences = [
            ("ja", 1.0, 2.0, "Line 1"),
        ]
        add_sentences(conn, media_id, initial_sentences)
        conn.commit()
        id1 = conn.execute("SELECT id FROM sentences").fetchone()["id"]

        # Shift by +1.5s (within 2.0s threshold)
        new_lines = [
            {"start": "00:00:02,500", "end": "00:00:03,500", "text": "Line 1 retimed"},
        ]
        create_srt(srt_path, new_lines)

        refresh_file(srt_path)

        rows = conn.execute("SELECT id, start_time, text FROM sentences").fetchall()
        assert len(rows) == 1
        assert rows[0]["id"] == id1
        assert rows[0]["start_time"] == 2.5
        assert rows[0]["text"] == "Line 1 retimed"
    finally:
        if os.path.exists(srt_path):
            os.remove(srt_path)


def test_refresh_line_splits(test_db):
    """Test when one old line splits into two new lines."""
    conn = test_db
    with tempfile.NamedTemporaryFile(suffix=".srt", delete=False) as tf:
        srt_path = tf.name

    try:
        media_id = add_media(conn, srt_path, "subtitle")
        initial_sentences = [
            ("ja", 1.0, 5.0, "Long line to split"),
        ]
        add_sentences(conn, media_id, initial_sentences)
        conn.commit()
        id_old = conn.execute("SELECT id FROM sentences").fetchone()["id"]

        new_lines = [
            {"start": "00:00:01,000", "end": "00:00:02,500", "text": "Split 1"},
            {"start": "00:00:03,000", "end": "00:00:05,000", "text": "Split 2"},
        ]
        create_srt(srt_path, new_lines)

        refresh_file(srt_path)

        rows = conn.execute("SELECT id, text FROM sentences ORDER BY start_time").fetchall()
        assert len(rows) == 2
        # The first split should inherit the old ID because it matches the start time
        ids = [r["id"] for r in rows]
        assert ids[0] == id_old
        assert ids[1] != id_old
    finally:
        if os.path.exists(srt_path):
            os.remove(srt_path)


def test_refresh_line_merges(test_db):
    """Test when two old lines merge into one new line."""
    conn = test_db
    with tempfile.NamedTemporaryFile(suffix=".srt", delete=False) as tf:
        srt_path = tf.name

    try:
        media_id = add_media(conn, srt_path, "subtitle")
        initial_sentences = [
            ("ja", 1.0, 2.0, "Merge 1"),
            ("ja", 2.0, 3.0, "Merge 2"),
        ]
        add_sentences(conn, media_id, initial_sentences)
        conn.commit()
        rows = conn.execute("SELECT id FROM sentences ORDER BY start_time").fetchall()
        id1 = rows[0]["id"]

        new_lines = [
            {"start": "00:00:01,000", "end": "00:00:03,000", "text": "Merged text"},
        ]
        create_srt(srt_path, new_lines)

        refresh_file(srt_path)

        rows = conn.execute("SELECT id, text FROM sentences").fetchall()
        assert len(rows) == 1
        # The new line should specifically match the first old ID because of start time alignment
        assert rows[0]["id"] == id1
        assert rows[0]["text"] == "Merged text"

        # Verify the other was deleted (by checking total count is 1, already done)
    finally:
        if os.path.exists(srt_path):
            os.remove(srt_path)


def test_refresh_many_deletions(test_db):
    """Test that a large number of leading deletions does not break the DP alignment window."""
    conn = test_db
    with tempfile.NamedTemporaryFile(suffix=".srt", delete=False) as tf:
        srt_path = tf.name

    try:
        media_id = add_media(conn, srt_path, "subtitle")

        # Add 60 dummy lines
        initial_sentences = []
        for i in range(60):
            initial_sentences.append(("ja", float(i), float(i) + 0.5, f"Dummy {i}"))

        # Add a final line that we expect to keep
        initial_sentences.append(("ja", 100.0, 101.0, "Keep this"))

        add_sentences(conn, media_id, initial_sentences)
        conn.commit()

        rows = conn.execute("SELECT id FROM sentences WHERE start_time = 100.0").fetchall()
        id_keep = rows[0]["id"]

        # New SRT only contains the final line (60 leading deletions)
        new_lines = [
            {"start": "00:01:40,000", "end": "00:01:41,000", "text": "Keep this"},
        ]
        create_srt(srt_path, new_lines)

        refresh_file(srt_path)

        # Ensure only 1 line remains and it has the correct ID
        rows = conn.execute("SELECT id, text FROM sentences").fetchall()
        assert len(rows) == 1
        assert rows[0]["id"] == id_keep
        assert rows[0]["text"] == "Keep this"
    finally:
        if os.path.exists(srt_path):
            os.remove(srt_path)


def test_refresh_many_overlapping(test_db):
    """Test that >200 highly dense overlapping sentences are properly matched."""
    conn = test_db
    with tempfile.NamedTemporaryFile(suffix=".srt", delete=False) as tf:
        srt_path = tf.name

    try:
        media_id = add_media(conn, srt_path, "subtitle")

        # Add 250 dummy lines all starting at 5.0 seconds
        initial_sentences = []
        for i in range(250):
            initial_sentences.append(("ja", 5.0, 6.0, f"Dense {i}"))

        add_sentences(conn, media_id, initial_sentences)
        conn.commit()

        rows = conn.execute("SELECT id FROM sentences ORDER BY id").fetchall()
        id_first = rows[0]["id"]

        # New SRT contains the same 250 lines
        new_lines = []
        for i in range(250):
            new_lines.append({"start": "00:00:05,000", "end": "00:00:06,000", "text": f"Dense {i}"})

        create_srt(srt_path, new_lines)

        refresh_file(srt_path)

        rows = conn.execute("SELECT id, text FROM sentences ORDER BY id").fetchall()
        assert len(rows) == 250
        assert rows[0]["id"] == id_first
    finally:
        if os.path.exists(srt_path):
            os.remove(srt_path)


def test_refresh_empty_file(test_db):
    """Test that refreshing with an empty subtitle file aborts and does not delete existing lines."""
    conn = test_db
    with tempfile.NamedTemporaryFile(suffix=".srt", delete=False) as tf:
        srt_path = tf.name

    try:
        media_id = add_media(conn, srt_path, "subtitle")
        add_sentences(conn, media_id, [("ja", 1.0, 2.0, "Test Line")])
        conn.commit()

        # The new SRT file is completely empty (no valid subtitles)
        create_srt(srt_path, [])

        # It should return False because it aborted
        assert refresh_file(srt_path) is False

        # The existing sentence must still exist
        rows = conn.execute("SELECT text FROM sentences").fetchall()
        assert len(rows) == 1
        assert rows[0]["text"] == "Test Line"
    finally:
        if os.path.exists(srt_path):
            os.remove(srt_path)


def test_refresh_tie_breaker(test_db):
    """Test that text similarity correctly tie-breaks overlapping sentences."""
    conn = test_db
    with tempfile.NamedTemporaryFile(suffix=".srt", delete=False) as tf:
        srt_path = tf.name

    try:
        media_id = add_media(conn, srt_path, "subtitle")
        # Add two exact overlapping sentences with different text
        add_sentences(conn, media_id, [("ja", 1.0, 2.0, "This is sentence A"), ("ja", 1.0, 2.0, "This is sentence B")])
        conn.commit()

        rows = conn.execute("SELECT id, text FROM sentences ORDER BY id").fetchall()
        id_a = rows[0]["id"]
        id_b = rows[1]["id"]

        # New SRT inserts a sentence between them, but the timestamps overlap perfectly
        create_srt(
            srt_path,
            [
                {"start": "00:00:01,000", "end": "00:00:02,000", "text": "This is sentence A"},
                {"start": "00:00:01,000", "end": "00:00:02,000", "text": "Inserted Sentence!"},
                {"start": "00:00:01,000", "end": "00:00:02,000", "text": "This is sentence B"},
            ],
        )

        refresh_file(srt_path)

        # The tie-breaker should match A to A and B to B
        final_rows = conn.execute("SELECT id, text FROM sentences ORDER BY id").fetchall()
        assert len(final_rows) == 3

        # Verify IDs mapped correctly
        for row in final_rows:
            if row["text"] == "This is sentence A":
                assert row["id"] == id_a
            elif row["text"] == "This is sentence B":
                assert row["id"] == id_b
    finally:
        if os.path.exists(srt_path):
            os.remove(srt_path)
