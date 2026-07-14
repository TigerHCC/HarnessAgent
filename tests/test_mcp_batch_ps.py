import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "test_mcp_servers.ps1"
POWERSHELL = shutil.which("powershell") or shutil.which("pwsh")


def test_wrapper_is_location_independent_and_does_not_require_administrator():
    source = WRAPPER.read_text(encoding="utf-8")

    assert "[CmdletBinding()]" in source
    assert "$PSScriptRoot" in source
    assert "#Requires -RunAsAdministrator" not in source
    assert "-Verb RunAs" not in source


@pytest.mark.skipif(not POWERSHELL, reason="PowerShell unavailable")
def test_wrapper_passes_arguments_and_preserves_python_failure(tmp_path):
    missing_manifest = tmp_path / "missing servers.json"
    output_dir = tmp_path / "reports with spaces"

    completed = subprocess.run(
        [
            POWERSHELL,
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(WRAPPER),
            "-ManifestPath",
            str(missing_manifest),
            "-OutputDir",
            str(output_dir),
            "-TimeoutSeconds",
            "1",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )

    output = completed.stdout + completed.stderr
    assert completed.returncode == 2
    assert "manifest" in output.lower()
    assert str(missing_manifest) in output
    assert not output_dir.exists()
