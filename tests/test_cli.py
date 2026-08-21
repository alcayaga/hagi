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
