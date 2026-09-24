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

    test_files = sorted(Path(__file__).resolve().parent.glob("test_*.mjs"))
    assert len(test_files) > 0, "Frontend test files missing"

    for test_file in test_files:
        try:
            result = subprocess.run(
                [node_bin, "--test", str(test_file)],
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
        except subprocess.TimeoutExpired as exc:
            pytest.fail(f"Node frontend tests ({test_file.name}) timed out after 30 seconds:\n{exc}")

        assert result.returncode == 0, (
            f"Node frontend tests failed ({test_file.name}) (exit code {result.returncode}):\n"
            f"STDOUT:\n{result.stdout}\n"
            f"STDERR:\n{result.stderr}"
        )


def test_node_frontend_suite_missing_node(monkeypatch):
    """Verify that test_node_frontend_suite fails when Node.js is missing from PATH."""
    monkeypatch.setattr(shutil, "which", lambda cmd: None)
    with pytest.raises(pytest.fail.Exception, match="Node.js executable not found in PATH"):
        test_node_frontend_suite()


def test_node_frontend_suite_missing_file(monkeypatch):
    """Verify that test_node_frontend_suite fails when frontend test files do not exist."""
    monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/node")
    monkeypatch.setattr(
        Path,
        "glob",
        lambda self, pattern: [],
    )
    with pytest.raises(AssertionError, match="Frontend test files missing"):
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
        """Simulate a subprocess timeout after asserting the timeout duration."""
        assert kwargs.get("timeout") == 30
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=30)

    monkeypatch.setattr(subprocess, "run", mock_run)
    with pytest.raises(pytest.fail.Exception, match="timed out after 30 seconds"):
        test_node_frontend_suite()

