"""Database management module for Nadeshiko Local."""

import os
import shlex
import sqlite3

DB_PATH = os.path.abspath("nadeshiko.db")


def get_db():
    """Get a database connection.

    Returns:
        sqlite3.Connection: Database connection object.
    """
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
    except sqlite3.OperationalError:
        pass  # If DB is heavily locked, WAL might fail to set, but timeout will still help
    return conn


def init_db():
    """Initialize the database schema.

    Returns:
        sqlite3.Connection: Database connection object.
    """
    conn = get_db()
    with conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS media (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                path TEXT UNIQUE,
                type TEXT,
                show_title TEXT,
                season INTEGER,
                episode INTEGER
            );
            
            CREATE TABLE IF NOT EXISTS sentences (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                media_id INTEGER,
                language TEXT,
                start_time REAL,
                end_time REAL,
                text TEXT,
                FOREIGN KEY(media_id) REFERENCES media(id)
            );
            """
        )

        # Add columns to existing DB
        try:
            conn.execute("ALTER TABLE media ADD COLUMN show_title TEXT")
            conn.execute("ALTER TABLE media ADD COLUMN season INTEGER")
            conn.execute("ALTER TABLE media ADD COLUMN episode INTEGER")
        except sqlite3.OperationalError:
            pass

        conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS sentences_fts USING fts5(
                text,
                content='sentences',
                content_rowid='id',
                tokenize='trigram'
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


def add_media(conn, path, media_type, show_title=None, season=None, episode=None):
    """Add a media file to the database.

    Args:
        conn (sqlite3.Connection): Database connection.
        path (str): File path to the media.
        media_type (str): Type of the media.
        show_title (str, optional): The name of the show from Plex.
        season (int, optional): The season number.
        episode (int, optional): The episode number.

    Returns:
        int: The ID of the inserted or existing media.
    """
    cursor = conn.execute("SELECT id, show_title FROM media WHERE path = ?", (path,))
    row = cursor.fetchone()
    if row:
        if row["show_title"] is None and show_title is not None:
            conn.execute(
                "UPDATE media SET show_title=?, season=?, episode=? WHERE id=?",
                (show_title, season, episode, row["id"]),
            )
        return row["id"]

    cursor = conn.execute(
        "INSERT INTO media (path, type, show_title, season, episode) VALUES (?, ?, ?, ?, ?)",
        (path, media_type, show_title, season, episode),
    )
    return cursor.lastrowid


def add_sentences(conn, media_id, sentences):
    """Add sentences to the database.

    Args:
        conn (sqlite3.Connection): Database connection.
        media_id (int): The ID of the media file.
        sentences (list): List of tuples containing (language, start_time, end_time, text).
    """
    conn.executemany(
        """
        INSERT INTO sentences (media_id, language, start_time, end_time, text)
        VALUES (?, ?, ?, ?, ?)
    """,
        [(media_id, s[0], s[1], s[2], s[3]) for s in sentences],
    )


def search_sentences(conn, query, show_title=None, episode=None):
    """Search for sentences using a query string.

    Args:
        conn (sqlite3.Connection): Database connection.
        query (str): The search query.
        show_title (str, optional): Exact show title to filter by.
        episode (int, optional): Exact episode number to filter by.

    Returns:
        list: List of matching sentence rows.
    """
    try:
        # Respect quoted exact phrases (e.g. "exact match")
        tokens = shlex.split(query)
    except ValueError:
        tokens = query.split()

    conditions = []
    params = []

    for token in tokens:
        if token.startswith("-"):
            term = token[1:]
            if term:
                conditions.append("s.text NOT LIKE ?")
                params.append(f"%{term}%")
        else:
            conditions.append("s.text LIKE ?")
            params.append(f"%{token}%")

    if show_title:
        conditions.append("m.show_title = ?")
        params.append(show_title)

    if episode is not None:
        conditions.append("m.episode = ?")
        params.append(episode)

    if not conditions:
        return []

    where_clause = " AND ".join(conditions)

    sql = f"""
        SELECT 
            s.id, s.text, s.language, s.start_time, 
            m.path, m.show_title, m.season, m.episode,
            (
                SELECT s2.text 
                FROM sentences s2 
                JOIN media m2 ON s2.media_id = m2.id 
                WHERE m2.show_title = m.show_title 
                  AND m2.season = m.season 
                  AND m2.episode = m.episode 
                  AND s2.language != s.language 
                ORDER BY ABS(s2.start_time - s.start_time) ASC 
                LIMIT 1
            ) AS translation
        FROM sentences s
        JOIN media m ON s.media_id = m.id
        WHERE {where_clause}
        LIMIT 1000
    """
    return conn.execute(sql, params).fetchall()
