"""Frontend test integration module."""

from pathlib import Path
import shutil
import subprocess

import pytest


def test_node_frontend_suite():
    """Execute Node.js built-in test runner for frontend unit tests."""
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.fail("Node.js executable not found in PATH; required for frontend tests.")

    test_file = Path(__file__).resolve().parent / "test_main.mjs"
    assert test_file.exists(), f"Frontend test file missing: {test_file}"

    try:
        result = subprocess.run(
            [node_bin, "--test", str(test_file)],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except subprocess.TimeoutExpired as exc:
        pytest.fail(f"Node frontend tests timed out after 30 seconds:\n{exc}")

    assert result.returncode == 0, (
        f"Node frontend tests failed (exit code {result.returncode}):\n"
        f"STDOUT:\n{result.stdout}\n"
        f"STDERR:\n{result.stderr}"
    )


def test_node_frontend_suite_missing_node(monkeypatch):
    """Verify that test_node_frontend_suite fails when Node.js is missing from PATH."""
    monkeypatch.setattr(shutil, "which", lambda cmd: None)
    with pytest.raises(pytest.fail.Exception, match="Node.js executable not found in PATH"):
        test_node_frontend_suite()


def test_node_frontend_suite_missing_file(monkeypatch):
    """Verify that test_node_frontend_suite fails when test_main.mjs does not exist."""
    monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/node")
    monkeypatch.setattr(
        Path,
        "resolve",
        lambda self: Path("/nonexistent/path/test_frontend.py"),
    )
    with pytest.raises(AssertionError, match="Frontend test file missing"):
        test_node_frontend_suite()


def test_node_frontend_suite_failure(monkeypatch):
    """Verify that test_node_frontend_suite fails when Node test runner returns non-zero."""
    monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/node")
    fake_completed = subprocess.CompletedProcess(
        args=["node", "--test", "fake.mjs"],
        returncode=1,
        stdout="FAIL: mock test failure",
        stderr="mock error trace",
    )
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: fake_completed)
    with pytest.raises(AssertionError, match="Node frontend tests failed"):
        test_node_frontend_suite()


def test_node_frontend_suite_timeout(monkeypatch):
    """Verify that test_node_frontend_suite fails when the Node test runner times out."""
    monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/node")

    def mock_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=30)

    monkeypatch.setattr(subprocess, "run", mock_run)
    with pytest.raises(pytest.fail.Exception, match="timed out after 30 seconds"):
        test_node_frontend_suite()

