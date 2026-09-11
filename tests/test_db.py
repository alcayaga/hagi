"""Tests for db.py concurrency fixes."""

import sqlite3

import pytest

from hagi import db


@pytest.fixture
def test_db_path(tmp_path):
    """Fixture to provide a temporary database path."""
    old_db_path = db.DB_PATH
    temp_db = tmp_path / "test_hagi.db"
    db.DB_PATH = str(temp_db)
    yield str(temp_db)
    db.DB_PATH = old_db_path


def test_get_db_wal_mode_initialization(test_db_path):
    """Test that get_db() initializes the database in WAL mode correctly."""
    conn = db.get_db()
    cursor = conn.execute("PRAGMA journal_mode;")
    mode = cursor.fetchone()[0]
    assert mode.lower() == "wal"
    conn.close()


def test_get_db_wal_mode_existing(test_db_path):
    """Test that get_db() correctly handles an existing WAL mode database without locking."""
    # First connection to set WAL
    conn1 = db.get_db()

    # Second connection to verify it doesn't fail and reads WAL
    conn2 = db.get_db()
    cursor = conn2.execute("PRAGMA journal_mode;")
    mode = cursor.fetchone()[0]
    assert mode.lower() == "wal"

    conn1.close()
    conn2.close()


def test_init_db_creates_sentence_lookup_index_after_column_migration(test_db_path):
    """Ensure legacy sentence tables receive migrated columns before their lookup index."""
    conn = sqlite3.connect(test_db_path)
    conn.executescript(
        """
        CREATE TABLE media (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            path TEXT UNIQUE,
            type TEXT
        );
        CREATE TABLE sentences (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            media_id INTEGER,
            language TEXT,
            text TEXT
        );
        """
    )
    conn.close()

    migrated_conn = db.init_db()
    index_columns = [row[2] for row in migrated_conn.execute("PRAGMA index_info(idx_sentences_lookup)")]

    assert index_columns == ["media_id", "language", "start_time"]
    migrated_conn.close()
