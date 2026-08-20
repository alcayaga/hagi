import sqlite3
import os

DB_PATH = os.path.expanduser("~/.local_nadeshiko.db")

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    with conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS media (
                id INTEGER PRIMARY KEY,
                path TEXT UNIQUE,
                type TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sentences (
                id INTEGER PRIMARY KEY,
                media_id INTEGER,
                language TEXT,
                start_time REAL,
                end_time REAL,
                text TEXT,
                FOREIGN KEY(media_id) REFERENCES media(id)
            )
        """)
        conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS sentences_fts USING fts5(
                text,
                content='sentences',
                content_rowid='id'
            )
        """)
        # Triggers to keep FTS updated
        conn.executescript("""
            CREATE TRIGGER IF NOT EXISTS sentences_ai AFTER INSERT ON sentences BEGIN
                INSERT INTO sentences_fts(rowid, text) VALUES (new.id, new.text);
            END;
            CREATE TRIGGER IF NOT EXISTS sentences_ad AFTER DELETE ON sentences BEGIN
                INSERT INTO sentences_fts(sentences_fts, rowid, text) VALUES ('delete', old.id, old.text);
            END;
            CREATE TRIGGER IF NOT EXISTS sentences_au AFTER UPDATE ON sentences BEGIN
                INSERT INTO sentences_fts(sentences_fts, rowid, text) VALUES ('delete', old.id, old.text);
                INSERT INTO sentences_fts(rowid, text) VALUES (new.id, new.text);
            END;
        """)
    return conn

def add_media(conn, path, media_type):
    cursor = conn.execute("INSERT OR IGNORE INTO media (path, type) VALUES (?, ?)", (path, media_type))
    if cursor.lastrowid == 0:
        cursor = conn.execute("SELECT id FROM media WHERE path = ?", (path,))
        return cursor.fetchone()[0]
    return cursor.lastrowid

def add_sentences(conn, media_id, sentences):
    """sentences: list of (language, start_time, end_time, text)"""
    conn.executemany("""
        INSERT INTO sentences (media_id, language, start_time, end_time, text)
        VALUES (?, ?, ?, ?, ?)
    """, [(media_id, s[0], s[1], s[2], s[3]) for s in sentences])

def search_sentences(conn, query):
    return conn.execute("""
        SELECT s.id, s.text, s.language, s.start_time, m.path
        FROM sentences_fts f
        JOIN sentences s ON f.rowid = s.id
        JOIN media m ON s.media_id = m.id
        WHERE sentences_fts MATCH ?
        ORDER BY rank
        LIMIT 50
    """, (query,)).fetchall()
