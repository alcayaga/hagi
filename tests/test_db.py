"""Tests for db.py concurrency fixes."""

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
