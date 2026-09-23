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

    result = subprocess.run(
        [node_bin, "--test", str(test_file)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"Node frontend tests failed (exit code {result.returncode}):\n"
        f"STDOUT:\n{result.stdout}\n"
        f"STDERR:\n{result.stderr}"
    )
