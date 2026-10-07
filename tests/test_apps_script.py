"""Runs the Apps Script tests (Node, with Google's services mocked). Skipped only if Node is not installed."""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
def test_apps_script_logic():
    result = subprocess.run(["node", "--test", "apps_script/tests/code.test.js"], cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-1500:]
