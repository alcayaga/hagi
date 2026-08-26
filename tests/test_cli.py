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
    import os

    # Mock config
    def mock_exists(path):
        """Mock os.path.exists."""
        if path == "config.json":
            return True
        return os.path.exists(path)

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
        called_args.update({
            "sentence_id": sentence_id,
            "config": config,
            "target_note_id": target_note_id
        })
        return True, "Exported"

    monkeypatch.setattr(exporter, "export_ankiconnect", mock_export_ankiconnect)

    result = runner.invoke(app, ["anki", "123", "--note-id", "999"])
    assert result.exit_code == 0
    assert "Exporting sentence 123 via AnkiConnect" in result.stdout
    assert "Exported" in result.stdout

    assert called_args["sentence_id"] == 123
    assert called_args["config"] == {"ankiConnectUrl": "mock"}
    assert called_args["target_note_id"] == 999
