import sqlite3
import pytest
from db import init_db, search_sentences, add_media, add_sentences, DB_PATH
import os
import db

@pytest.fixture
def test_db():
    # Override the database path to use an in-memory database for testing
    db.DB_PATH = ":memory:"
    conn = db.init_db()
    
    # Add dummy media
    media_id = add_media(conn, "fake_anime_episode_1.srt", "subtitle")
    
    # Add test sentences
    sentences = [
        ("jp", 10.0, 12.0, "おそらく、見られても平気な格好をしてたんでしょう。"),
        ("jp", 15.0, 17.0, "私 平気"),
        ("jp", 20.0, 22.0, "ごめん 憂 私なら平気"),
        ("jp", 25.0, 27.0, "平気 平気 もう何もないからッ"),
        ("en", 30.0, 32.0, "I am a normal high school girl"),
        ("en", 35.0, 37.0, "Let's go to school!"),
    ]
    add_sentences(conn, media_id, sentences)
    
    yield conn
    conn.close()

def test_basic_search(test_db):
    results = search_sentences(test_db, "平気")
    # Should find all 4 sentences containing "平気"
    assert len(results) == 4
    texts = [r['text'] for r in results]
    assert "おそらく、見られても平気な格好をしてたんでしょう。" in texts
    assert "私 平気" in texts

def test_exclusive_search(test_db):
    # Find "平気" but exclude "私"
    results = search_sentences(test_db, "平気 -私")
    
    # Should only find 2 sentences (excluding "私 平気" and "ごめん 憂 私なら平気")
    assert len(results) == 2
    texts = [r['text'] for r in results]
    assert "おそらく、見られても平気な格好をしてたんでしょう。" in texts
    assert "平気 平気 もう何もないからッ" in texts
    assert "私 平気" not in texts

def test_multiple_terms_search(test_db):
    results = search_sentences(test_db, "憂 ごめん")
    assert len(results) == 1
    assert results[0]['text'] == "ごめん 憂 私なら平気"

def test_exact_phrase_search(test_db):
    results = search_sentences(test_db, '"high school"')
    assert len(results) == 1
    assert results[0]['text'] == "I am a normal high school girl"

def test_no_results(test_db):
    results = search_sentences(test_db, "宇宙人")
    assert len(results) == 0
