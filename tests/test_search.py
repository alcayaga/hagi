"""Test module."""

import pytest

import db
from db import add_media, add_sentences, search_sentences


@pytest.fixture
def test_db():
    # Override the database path to use an in-memory database for testing
    """Test function."""
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
    """Test function."""
    results = search_sentences(test_db, "平気")
    # Should find all 4 sentences containing "平気"
    assert len(results) == 4
    texts = [r["text"] for r in results]
    assert "おそらく、見られても平気な格好をしてたんでしょう。" in texts
    assert "私 平気" in texts

    # Verify end_time is correctly populated
    r_first = next(r for r in results if r["text"] == "私 平気")
    assert r_first["end_time"] == 17.0


def test_exclusive_search(test_db):
    # Find "平気" but exclude "私"
    """Test function."""
    results = search_sentences(test_db, "平気 -私")

    # Should only find 2 sentences (excluding "私 平気" and "ごめん 憂 私なら平気")
    assert len(results) == 2
    texts = [r["text"] for r in results]
    assert "おそらく、見られても平気な格好をしてたんでしょう。" in texts
    assert "平気 平気 もう何もないからッ" in texts
    assert "私 平気" not in texts


def test_multiple_terms_search(test_db):
    """Test function."""
    results = search_sentences(test_db, "憂 ごめん")
    assert len(results) == 1
    assert results[0]["text"] == "ごめん 憂 私なら平気"


def test_exact_phrase_search(test_db):
    """Test function."""
    results = search_sentences(test_db, '"high school"')
    assert len(results) == 1
    assert results[0]["text"] == "I am a normal high school girl"


def test_no_results(test_db):
    """Test that searching for a non-existent word returns empty."""
    results = search_sentences(test_db, "宇宙人")
    assert len(results) == 0


def test_plex_metadata_filters(test_db):
    """Test searching with show_title and episode filters."""
    # Add a mock show to filter by
    media_id = db.add_media(
        test_db, "/mock/Shaman.mkv", "mkv_embedded", show_title="Shaman King", season=1, episode=5
    )
    db.add_sentences(test_db, media_id, [("jpn", 0, 1, "ハオ様")])

    # Search without filter should find it
    assert len(search_sentences(test_db, "ハオ様")) == 1

    # Search with correct show filter should find it
    assert len(search_sentences(test_db, "ハオ様", show_title="Shaman King")) == 1

    # Search with incorrect show filter should return empty
    assert len(search_sentences(test_db, "ハオ様", show_title="One Piece")) == 0

    # Search with correct episode filter should find it
    assert len(search_sentences(test_db, "ハオ様", show_title="Shaman King", episode=5)) == 1

    # Search with incorrect episode filter should return empty
    assert len(search_sentences(test_db, "ハオ様", show_title="Shaman King", episode=6)) == 0

    # Search with correct season filter should find it
    assert len(search_sentences(test_db, "ハオ様", show_title="Shaman King", season=1)) == 1

    # Search with incorrect season filter should return empty
    assert len(search_sentences(test_db, "ハオ様", show_title="Shaman King", season=2)) == 0


def test_translation_matching(test_db):
    """Test that search results automatically fetch closest translation."""
    # Add Japanese media
    jp_id = db.add_media(
        test_db, "/mock/Anime.ass", "ass", show_title="Dual Language Anime", season=1, episode=1
    )
    db.add_sentences(test_db, jp_id, [("jpn", 10.0, 15.0, "これはテストです")])

    # Add English media for the exact same episode
    en_id = db.add_media(
        test_db, "/mock/Anime.mkv", "mkv", show_title="Dual Language Anime", season=1, episode=1
    )
    db.add_sentences(test_db, en_id, [("eng", 10.1, 14.9, "This is a test")])

    results = search_sentences(test_db, "テスト")
    assert len(results) == 1

    # Assert that the native text is the Japanese sentence
    assert results[0]["text"] == "これはテストです"

    # Assert that the translation field was successfully populated with the English match
    assert results[0]["eng_translation"] == "This is a test"
    assert results[0]["spa_translation"] is None


def test_translation_language_priority(test_db):
    """Test that search results prioritize Spanish > English > Portuguese."""
    jp_id = db.add_media(
        test_db, "/mock/Anime_jp.mkv", "mkv", show_title="Priority Anime", season=1, episode=1
    )
    db.add_sentences(test_db, jp_id, [("jpn", 10.0, 15.0, "日本のテキスト")])

    por_id = db.add_media(
        test_db, "/mock/Anime_por.ass", "ass", show_title="Priority Anime", season=1, episode=1
    )
    # Portuguese is very close to the Japanese timestamp (diff = 0.1)
    db.add_sentences(test_db, por_id, [("por", 10.1, 15.1, "Portuguese text")])

    spa_id = db.add_media(
        test_db, "/mock/Anime_spa.ass", "ass", show_title="Priority Anime", season=1, episode=1
    )
    # Spanish is further away (diff = 2.0) but should take priority
    db.add_sentences(test_db, spa_id, [("spa", 12.0, 17.0, "Spanish text")])

    results = search_sentences(test_db, "日本のテキスト")
    assert len(results) == 1

    # Spanish should be chosen because it is prioritized over Portuguese
    assert results[0]["spa_translation"] == "Spanish text"


def test_search_foreign_returns_japanese_primary(test_db):
    """Test that searching for a foreign language word prioritizes the Japanese text as the primary result."""
    jp_id = db.add_media(
        test_db, "/mock/Anime_jp.mkv", "mkv", show_title="Priority Anime", season=1, episode=2
    )
    db.add_sentences(test_db, jp_id, [("jpn", 10.0, 15.0, "日本のテキスト")])

    spa_id = db.add_media(
        test_db, "/mock/Anime_spa.ass", "ass", show_title="Priority Anime", season=1, episode=2
    )
    db.add_sentences(test_db, spa_id, [("spa", 10.1, 15.1, "Palabra en español")])

    # Search for the Spanish word
    results = search_sentences(test_db, "español")
    assert len(results) == 1

    # Primary text should be Japanese, despite the search query matching Spanish
    assert results[0]["text"] == "日本のテキスト"
    assert results[0]["language"] == "jpn"

    # Secondary text should be the Spanish match
    assert results[0]["spa_translation"] == "Palabra en español"
