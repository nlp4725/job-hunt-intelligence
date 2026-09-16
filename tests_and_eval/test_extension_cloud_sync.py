"""Runs the extension's cloud-sync JavaScript tests under pytest (skipped when
Node isn't installed). See tests_and_eval/extension_cloud_sync_test.js."""

import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent / "extension_cloud_sync_test.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_extension_cloud_sync():
    result = subprocess.run(["node", str(SCRIPT)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
