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
        # One of them should inherit the old ID, the other gets a new one
        ids = [r["id"] for r in rows]
        assert id_old in ids
        assert ids[0] != ids[1]
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
        id1, id2 = rows[0]["id"], rows[1]["id"]

        new_lines = [
            {"start": "00:00:01,000", "end": "00:00:03,000", "text": "Merged text"},
        ]
        create_srt(srt_path, new_lines)

        refresh_file(srt_path)

        rows = conn.execute("SELECT id, text FROM sentences").fetchall()
        assert len(rows) == 1
        # The new line should have matched one of the old IDs
        assert rows[0]["id"] in (id1, id2)
        assert rows[0]["text"] == "Merged text"

        # Verify the other was deleted (by checking total count is 1, already done)
    finally:
        if os.path.exists(srt_path):
            os.remove(srt_path)
