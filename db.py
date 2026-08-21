import sqlite3
import os

DB_PATH = os.path.abspath("nadeshiko.db")

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

def add_media(conn, path, media_type):
    cursor = conn.execute("SELECT id FROM media WHERE path = ?", (path,))
    row = cursor.fetchone()
    if row:
        return row[0]
        
    cursor = conn.execute("INSERT INTO media (path, type) VALUES (?, ?)", (path, media_type))
    return cursor.lastrowid

def add_sentences(conn, media_id, sentences):
    """sentences: list of (language, start_time, end_time, text)"""
    conn.executemany("""
        INSERT INTO sentences (media_id, language, start_time, end_time, text)
        VALUES (?, ?, ?, ?, ?)
    """, [(media_id, s[0], s[1], s[2], s[3]) for s in sentences])

import shlex

def search_sentences(conn, query):
    try:
        # Respect quoted exact phrases (e.g. "exact match")
        tokens = shlex.split(query)
    except ValueError:
        tokens = query.split()
        
    conditions = []
    params = []
    
    for token in tokens:
        if token.startswith('-'):
            term = token[1:]
            if term:
                conditions.append("s.text NOT LIKE ?")
                params.append(f"%{term}%")
        else:
            conditions.append("s.text LIKE ?")
            params.append(f"%{token}%")
            
    if not conditions:
        return []
        
    where_clause = " AND ".join(conditions)
    
    sql = f"""
        SELECT s.id, s.text, s.language, s.start_time, m.path
        FROM sentences s
        JOIN media m ON s.media_id = m.id
        WHERE {where_clause}
        LIMIT 50
    """
    return conn.execute(sql, params).fetchall()
