"""Test module for CLI commands."""

from typer.testing import CliRunner
from cli import app

runner = CliRunner()


def test_index_non_existent_directory():
    """Test that indexing a non-existent directory fails with a clear error."""
    # Pass a path that definitely doesn't exist
    result = runner.invoke(app, ["index", "/path/that/does/not/exist/12345"])

    # Typer should return a non-zero exit code because of our raise typer.Exit(code=1)
    assert result.exit_code == 1

    # The output should contain our custom error message
    assert "does not exist" in result.stdout


def test_index_existing_directory(tmp_path, monkeypatch):
    """Test that indexing an existing directory works without error."""
    # Mock indexer so it doesn't actually try to scan the empty dir and do DB stuff
    import indexer

    monkeypatch.setattr(indexer, "index_directory", lambda x: None)

    # Mock db.init_db to avoid creating local dbs during tests
    import db

    monkeypatch.setattr(db, "init_db", lambda: None)

    # Create a temporary directory that exists
    d = tmp_path / "fake_anime_dir"
    d.mkdir()

    # Invoke the CLI with the real path
    result = runner.invoke(app, ["index", str(d)])

    # It should succeed
    assert result.exit_code == 0
    assert "Indexing complete!" in result.stdout


def test_anki_command(monkeypatch):
    """Test the anki CLI command."""
    import json

    # Mock config
    def mock_exists(path):
        """Mock os.path.exists."""
        if path == "config.json":
            return True
        return False

    def mock_open(path, mode="r", *args, **kwargs):
        """Mock open()."""
        if path == "config.json":
            from io import StringIO

            return StringIO(json.dumps({"ankiConnectUrl": "mock"}))
        return open(path, mode, *args, **kwargs)

    monkeypatch.setattr("os.path.exists", mock_exists)
    monkeypatch.setattr("builtins.open", mock_open)

    # Mock exporter
    import exporter

    called_args = {}

    def mock_export_ankiconnect(sentence_id, config, out_dir, pad_start, pad_end, target_note_id):
        """Mock export_ankiconnect."""
        called_args.update({"sentence_id": sentence_id, "config": config, "target_note_id": target_note_id})
        return True, "Exported", False

    monkeypatch.setattr(exporter, "export_ankiconnect", mock_export_ankiconnect)

    result = runner.invoke(app, ["anki", "123", "--note-id", "999"])
    assert result.exit_code == 0
    assert "Exporting sentence 123 via AnkiConnect" in result.stdout
    assert "Exported" in result.stdout

    assert called_args["sentence_id"] == 123
    assert called_args["config"] == {"ankiConnectUrl": "mock"}
    assert called_args["target_note_id"] == 999


def test_anki_command_invalid_config(monkeypatch):
    """Test anki command fails if config is invalid (e.g., array)."""
    import json

    def mock_exists(path):
        """Mock os.path.exists."""
        if path == "config.json":
            return True
        return False

    def mock_open(path, mode="r", *args, **kwargs):
        """Mock open()."""
        if path == "config.json":
            from io import StringIO

            # Return an array instead of a dictionary
            return StringIO(json.dumps([]))
        return open(path, mode, *args, **kwargs)

    monkeypatch.setattr("os.path.exists", mock_exists)
    monkeypatch.setattr("builtins.open", mock_open)

    # We should let export_ankiconnect run natively to trigger the validation check
    # But we mock extract_media to avoid db calls
    import exporter

    def mock_extract(sentence_id, out_dir, pad_start, pad_end):
        """Mock extract_media."""
        return True, "Extracted", "a.mp3", "b.jpg", "text", False

    monkeypatch.setattr(exporter, "extract_media", mock_extract)

    result = runner.invoke(app, ["anki", "123"])
    assert result.exit_code == 1
    assert "Invalid configuration format" in result.stdout


def test_cli_search_with_filters(monkeypatch):
    """Verify the search command parses filters and passes them to db.search_sentences."""
    import db
    called_args = {}

    def mock_search_sentences(conn, query, show_title=None, season=None, episode=None):
        called_args.update({
            "query": query,
            "show_title": show_title,
            "season": season,
            "episode": episode
        })
        return []

    monkeypatch.setattr(db, "search_sentences", mock_search_sentences)
    monkeypatch.setattr(db, "get_db", lambda: "mock_db_conn")

    result = runner.invoke(app, ["search", "hello", "--show", "Conan", "--season", "1", "--episode", "2"])

    assert result.exit_code == 0
    assert called_args["query"] == "hello"
    assert called_args["show_title"] == "Conan"
    assert called_args["season"] == 1
    assert called_args["episode"] == 2


def test_cli_anki_search_success(monkeypatch):
    """Verify anki-search correctly loads config.json and calls exporter.search_anki_notes."""
    import json
    import exporter

    def mock_exists(path):
        if path == "config.json":
            return True
        return False

    def mock_open(path, mode="r", *args, **kwargs):
        from io import StringIO
        return StringIO(json.dumps({"wordField": "Expression"}))

    monkeypatch.setattr("os.path.exists", mock_exists)
    monkeypatch.setattr("builtins.open", mock_open)

    called_args = {}

    def mock_search_anki_notes(config, query, limit=20, exact=False):
        called_args.update({"query": query, "limit": limit, "exact": exact})
        notes = [{"noteId": 12345, "fields": {"Expression": {"value": "Hello"}}}]
        return True, "Success", notes

    monkeypatch.setattr(exporter, "search_anki_notes", mock_search_anki_notes)

    result = runner.invoke(app, ["anki-search", "hello", "--limit", "10"])

    assert result.exit_code == 0
    assert "Searching Anki for 'hello'..." in result.stdout
    assert "12345" in result.stdout
    assert "Expression" in result.stdout
    assert "Hello" in result.stdout
    assert called_args["query"] == "hello"
    assert called_args["limit"] == 10
    assert called_args["exact"] is False


def test_cli_anki_search_exact(monkeypatch):
    """Verify anki-search --exact correctly passes the exact=True parameter downstream."""
    import json
    import exporter

    def mock_exists(path):
        if path == "config.json":
            return True
        return False

    def mock_open(path, mode="r", *args, **kwargs):
        from io import StringIO
        return StringIO(json.dumps({"wordField": "Expression"}))

    monkeypatch.setattr("os.path.exists", mock_exists)
    monkeypatch.setattr("builtins.open", mock_open)

    called_args = {}

    def mock_search_anki_notes(config, query, limit=20, exact=False):
        called_args.update({"exact": exact})
        return True, "Success", []

    monkeypatch.setattr(exporter, "search_anki_notes", mock_search_anki_notes)

    result = runner.invoke(app, ["anki-search", "hello", "--exact"])

    assert result.exit_code == 0
    assert called_args["exact"] is True


def test_cli_anki_search_exact_missing_word_field(monkeypatch):
    """Verify that using --exact when wordField is missing raises a proper error."""
    import json

    def mock_exists(path):
        if path == "config.json":
            return True
        return False

    def mock_open(path, mode="r", *args, **kwargs):
        from io import StringIO
        return StringIO(json.dumps({})) # Missing wordField

    monkeypatch.setattr("os.path.exists", mock_exists)
    monkeypatch.setattr("builtins.open", mock_open)

    result = runner.invoke(app, ["anki-search", "hello", "--exact"])

    assert result.exit_code == 1
    assert "Error: Cannot use --exact because 'wordField' is not set" in result.stdout


def test_cli_anki_search_html_truncation(monkeypatch):
    """Verify that HTML is stripped before truncation to prevent broken tags."""
    import json
    import exporter

    def mock_exists(path):
        if path == "config.json":
            return True
        return False

    def mock_open(path, mode="r", *args, **kwargs):
        from io import StringIO
        return StringIO(json.dumps({"sentenceField": "Sentence"}))

    monkeypatch.setattr("os.path.exists", mock_exists)
    monkeypatch.setattr("builtins.open", mock_open)

    def mock_search_anki_notes(config, query, limit=20, exact=False):
        # A very long HTML string where the visible text is exactly 50 chars, but raw HTML is longer.
        # It shouldn't be truncated if HTML is stripped first.
        # Visible text length: "This is a sentence. " (20) + "A" * 30 = 50 chars.
        html_val = f"<div style='color: red; font-size: 20px; font-weight: bold;'>This is a sentence. {'A' * 30}</div>"
        notes = [{"noteId": 999, "fields": {"Sentence": {"value": html_val}}}]
        return True, "Success", notes

    monkeypatch.setattr(exporter, "search_anki_notes", mock_search_anki_notes)

    result = runner.invoke(app, ["anki-search", "hello"])

    assert result.exit_code == 0
    assert "999" in result.stdout
    # Should not be truncated
    assert f"This is a sentence. {'A' * 30}" in result.stdout
    assert f"[{'A' * 30}..." not in result.stdout
    assert "<div" not in result.stdout

    # Test truncation of visible text > 50 chars
    def mock_search_anki_notes_long(config, query, limit=20, exact=False):
        # Visible text length: 60 chars.
        html_val = f"<span class='some-class'>{'B' * 60}</span>"
        notes = [{"noteId": 888, "fields": {"Sentence": {"value": html_val}}}]
        return True, "Success", notes

    monkeypatch.setattr(exporter, "search_anki_notes", mock_search_anki_notes_long)

    result_long = runner.invoke(app, ["anki-search", "hello"])

    assert result_long.exit_code == 0
    assert "888" in result_long.stdout
    assert f"{'B' * 47}..." in result_long.stdout
    assert "<span" not in result_long.stdout


def test_cli_anki_search_json(monkeypatch):
    """Verify that --json outputs a full JSON array of notes."""
    import json
    import exporter

    def mock_exists(path):
        if path == "config.json":
            return True
        return False

    def mock_open(path, mode="r", *args, **kwargs):
        from io import StringIO
        return StringIO(json.dumps({"wordField": "Expression"}))

    monkeypatch.setattr("os.path.exists", mock_exists)
    monkeypatch.setattr("builtins.open", mock_open)

    def mock_search_anki_notes(config, query, limit=20, exact=False):
        notes = [{"noteId": 55555, "fields": {}}, {"noteId": 66666, "fields": {}}]
        return True, "Success", notes

    monkeypatch.setattr(exporter, "search_anki_notes", mock_search_anki_notes)

    result = runner.invoke(app, ["anki-search", "hello", "--json"])

    assert result.exit_code == 0
    assert "Searching Anki for 'hello'..." not in result.stdout
    assert "Preview" not in result.stdout

    # Verify the output is valid JSON and matches the mocked notes
    output_data = json.loads(result.stdout)
    assert len(output_data) == 2
    assert output_data[0]["noteId"] == 55555
    assert output_data[1]["noteId"] == 66666
