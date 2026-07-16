import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "scripts" / "start_mcp_hidden.ps1"
HELPERS = ROOT / "scripts" / "mcp_task_helpers.ps1"
POWERSHELL = shutil.which("powershell") or shutil.which("pwsh")
MANIFEST = ROOT / "config" / "mcp_servers.json"
INSTALLER_HARNESS = ROOT / "tests" / "powershell" / "mcp_installer_harness.ps1"


def read_log(path):
    content = path.read_bytes()
    encoding = "utf-16" if content.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8"
    return content.decode(encoding)


def run_launcher(python, server, working, name, logs):
    return subprocess.run(
        [
            POWERSHELL,
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(LAUNCHER),
            "-PythonPath",
            str(python),
            "-ServerPath",
            str(server),
            "-WorkingDirectory",
            str(working),
            "-Name",
            name,
            "-LogDirectory",
            str(logs),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )


def run_helper_command(command):
    return subprocess.run(
        [
            POWERSHELL,
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            f"$ErrorActionPreference = 'Stop'; . '{HELPERS}'; {command}",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )


def load_manifest():
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def run_installer_harness(
    installer, mode, cwd, *, skip_tasks=False, skip_watchdog=False, no_start=False
):
    switches = []
    if skip_tasks:
        switches.append("-SkipTasks")
    if skip_watchdog:
        switches.append("-SkipWatchdog")
    if no_start:
        switches.append("-NoStart")
    completed = subprocess.run(
        [
            POWERSHELL,
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(INSTALLER_HARNESS),
            "-InstallerPath",
            str(installer),
            "-Mode",
            mode,
            "-PythonPath",
            sys.executable,
            "-PowerShellPath",
            POWERSHELL,
            *switches,
        ],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    marker = "HARNESS_JSON:"
    payload = next(
        (line[len(marker) :] for line in completed.stdout.splitlines() if line.startswith(marker)),
        None,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert payload is not None, completed.stdout
    return json.loads(payload)


def assert_hidden_action(action, entry):
    working = ROOT / "mcp" / entry["directory"]
    server = next(working.glob("*_mcp_server.py"))
    logs = ROOT / "logs" / "mcp"
    assert Path(action["Execute"]) == Path(POWERSHELL)
    assert Path(action["WorkingDirectory"]) == working
    assert f'-File "{LAUNCHER}"' in action["Argument"]
    assert f'-PythonPath "{sys.executable}"' in action["Argument"]
    assert f'-ServerPath "{server}"' in action["Argument"]
    assert f'-WorkingDirectory "{working}"' in action["Argument"]
    assert f'-Name "{entry["name"]}"' in action["Argument"]
    assert f'-LogDirectory "{logs}"' in action["Argument"]


@pytest.mark.skipif(not POWERSHELL, reason="PowerShell unavailable")
@pytest.mark.parametrize("name", ["srum", "obsidian"])
def test_standalone_installer_behavior_uses_hidden_action_and_manifest_principal(
    name, tmp_path
):
    entry = next(item for item in load_manifest() if item["name"] == name)
    installer = ROOT / "mcp" / entry["directory"] / "install_task.ps1"

    result = run_installer_harness(installer, "Standalone", tmp_path)

    assert Path(result["CallerWorkingDirectory"]) == tmp_path
    assert len(result["Registrations"]) == 1
    registration = result["Registrations"][0]
    assert registration["TaskName"] == entry["task"]
    assert registration["Trigger"]["AtLogOn"] is True
    assert registration["Principal"]["UserId"] == result["CurrentUser"]
    assert registration["Principal"]["LogonType"] == "Interactive"
    assert registration["Principal"]["RunLevel"] == entry["run_level"]
    assert registration["Settings"] == {
        "AllowStartIfOnBatteries": True,
        "DontStopIfGoingOnBatteries": True,
        "StartWhenAvailable": True,
    }
    assert registration["Force"] is True
    assert_hidden_action(registration["Action"], entry)
    assert result["Starts"] == []


@pytest.mark.skipif(not POWERSHELL, reason="PowerShell unavailable")
def test_suite_installer_behavior_registers_and_immediately_starts_hidden(tmp_path):
    result = run_installer_harness(ROOT / "setup_mcp_servers.ps1", "Suite", tmp_path)
    entries = load_manifest()

    assert Path(result["CallerWorkingDirectory"]) == tmp_path
    assert len(result["Registrations"]) == len(entries) + 1
    assert len(result["Starts"]) == len(entries)
    registrations = {item["TaskName"]: item for item in result["Registrations"]}
    starts = {item["Name"]: item for item in result["Starts"]}
    assert "MCP-Watchdog" in registrations
    for entry in entries:
        registration = registrations[entry["task"]]
        assert registration["Trigger"]["AtLogOn"] is True
        assert registration["Principal"]["UserId"] == result["CurrentUser"]
        assert registration["Principal"]["LogonType"] == "Interactive"
        assert registration["Principal"]["RunLevel"] == entry["run_level"]
        assert registration["Force"] is True
        assert_hidden_action(registration["Action"], entry)
        start = starts[entry["name"]]
        assert start["WindowStyle"] == "Hidden"
        assert start["PassThru"] is True
        assert_hidden_action(start, entry)


@pytest.mark.skipif(not POWERSHELL, reason="PowerShell unavailable")
def test_suite_skip_tasks_skips_watchdog_registration(tmp_path):
    result = run_installer_harness(
        ROOT / "setup_mcp_servers.ps1",
        "Suite",
        tmp_path,
        skip_tasks=True,
        no_start=True,
    )

    assert result["Registrations"] == []
    assert result["Starts"] == []


@pytest.mark.skipif(not POWERSHELL, reason="PowerShell unavailable")
def test_suite_explicit_skip_watchdog_preserves_mcp_task_registration(tmp_path):
    result = run_installer_harness(
        ROOT / "setup_mcp_servers.ps1",
        "Suite",
        tmp_path,
        skip_watchdog=True,
        no_start=True,
    )

    assert {item["TaskName"] for item in result["Registrations"]} == {
        entry["task"] for entry in load_manifest()
    }
    assert result["Starts"] == []


def test_standalone_installers_use_shared_hidden_launcher_action():
    for entry in load_manifest():
        installer = ROOT / "mcp" / entry["directory"] / "install_task.ps1"
        source = installer.read_text(encoding="utf-8-sig")

        assert "mcp_task_helpers.ps1" in source, entry["name"]
        assert "start_mcp_hidden.ps1" in source, entry["name"]
        assert "New-McpScheduledTaskAction" in source, entry["name"]
        assert f'-Name "{entry["name"]}"' in source, entry["name"]
        assert "New-ScheduledTaskAction -Execute $py" not in source, entry["name"]
        assert "New-ScheduledTaskTrigger -AtLogOn" in source, entry["name"]
        assert "-LogonType Interactive" in source, entry["name"]
        assert f'-TaskName "{entry["task"]}"' in source, entry["name"]


def test_suite_installer_uses_shared_helpers_for_tasks_and_immediate_start():
    source = (ROOT / "setup_mcp_servers.ps1").read_text(encoding="utf-8-sig")

    assert "mcp_task_helpers.ps1" in source
    assert "start_mcp_hidden.ps1" in source
    assert "New-McpScheduledTaskAction" in source
    assert "Start-McpHiddenServer" in source
    assert "-Name $m.name" in source
    assert "New-ScheduledTaskAction -Execute $py" not in source
    assert "Start-Process powershell" not in source
    assert "New-ScheduledTaskTrigger -AtLogOn" in source
    assert "-LogonType Interactive" in source


def test_obsidian_installer_retains_limited_run_level():
    source = (ROOT / "mcp" / "windows_obsidian" / "install_task.ps1").read_text(
        encoding="utf-8-sig"
    )
    assert "-RunLevel Limited" in source


@pytest.mark.skipif(not POWERSHELL, reason="PowerShell unavailable")
def test_helper_builds_exact_hidden_launcher_arguments_with_spaced_paths():
    completed = run_helper_command(
        "New-McpLauncherArguments "
        "-LauncherPath 'C:\\Program Files\\MCP Tools\\start hidden.ps1' "
        "-PythonPath 'C:\\Program Files\\Python\\python.exe' "
        "-ServerPath 'C:\\MCP Servers\\sample server.py' "
        "-WorkingDirectory 'C:\\MCP Servers\\working directory' "
        "-Name 'sample mcp' "
        "-LogDirectory 'C:\\MCP Logs\\server logs'"
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == (
        '-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden '
        '-File "C:\\Program Files\\MCP Tools\\start hidden.ps1" '
        '-PythonPath "C:\\Program Files\\Python\\python.exe" '
        '-ServerPath "C:\\MCP Servers\\sample server.py" '
        '-WorkingDirectory "C:\\MCP Servers\\working directory" '
        '-Name "sample mcp" '
        '-LogDirectory "C:\\MCP Logs\\server logs"'
    )


@pytest.mark.skipif(not POWERSHELL, reason="PowerShell unavailable")
def test_helper_rejects_values_containing_double_quotes():
    completed = run_helper_command(
        "New-McpLauncherArguments "
        "-LauncherPath 'C:\\MCP\\start.ps1' "
        "-PythonPath 'C:\\Python\\python.exe' "
        "-ServerPath 'C:\\MCP\\server.py' "
        "-WorkingDirectory 'C:\\MCP' "
        "-Name 'bad\"name' "
        "-LogDirectory 'C:\\MCP\\logs'"
    )

    assert completed.returncode != 0
    assert "double quote" in completed.stderr


@pytest.mark.skipif(not POWERSHELL, reason="PowerShell unavailable")
def test_helper_exposes_action_and_process_wrapper_contracts():
    completed = run_helper_command(
        "@(Get-Command New-McpScheduledTaskAction, Start-McpHiddenServer) | "
        "ForEach-Object { $_.Name + ':' + ($_.Parameters.Keys -join ',') }"
    )

    assert completed.returncode == 0, completed.stderr
    contracts = completed.stdout.splitlines()
    for function_name in ("New-McpScheduledTaskAction", "Start-McpHiddenServer"):
        contract = next(line for line in contracts if line.startswith(function_name + ":"))
        for parameter in (
            "PowerShellPath",
            "LauncherPath",
            "PythonPath",
            "ServerPath",
            "WorkingDirectory",
            "Name",
            "LogDirectory",
        ):
            assert parameter in contract.split(":", 1)[1].split(",")

    source = HELPERS.read_text(encoding="utf-8")
    assert "New-ScheduledTaskAction -Execute $PowerShellPath" in source
    assert "Start-Process -FilePath $PowerShellPath" in source
    assert "New-ScheduledTaskAction -Execute $PythonPath" not in source
    assert 'New-ScheduledTaskAction -Execute "python.exe"' not in source


@pytest.fixture
def launcher_paths(tmp_path):
    base = tmp_path / "launcher paths with spaces"
    working = base / "working directory"
    logs = base / "log directory"
    working.mkdir(parents=True)
    server = working / "test server.py"
    server.write_text(
        "import sys\n"
        "print('distinct stdout line')\n"
        "print('distinct stderr line', file=sys.stderr)\n",
        encoding="utf-8",
    )
    return server, working, logs


@pytest.mark.skipif(not POWERSHELL, reason="PowerShell unavailable")
def test_launcher_separates_stdout_and_stderr(launcher_paths):
    server, working, logs = launcher_paths

    completed = run_launcher(sys.executable, server, working, "sample-mcp", logs)

    assert completed.returncode == 0, completed.stderr
    assert read_log(logs / "sample-mcp.stdout.log").strip() == "distinct stdout line"
    assert read_log(logs / "sample-mcp.stderr.log").strip() == "distinct stderr line"


@pytest.mark.skipif(not POWERSHELL, reason="PowerShell unavailable")
def test_launcher_appends_to_existing_logs(launcher_paths):
    server, working, logs = launcher_paths

    first = run_launcher(sys.executable, server, working, "append.mcp", logs)
    second = run_launcher(sys.executable, server, working, "append.mcp", logs)

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert read_log(logs / "append.mcp.stdout.log").splitlines() == [
        "distinct stdout line",
        "distinct stdout line",
    ]
    assert read_log(logs / "append.mcp.stderr.log").splitlines() == [
        "distinct stderr line",
        "distinct stderr line",
    ]


@pytest.mark.skipif(not POWERSHELL, reason="PowerShell unavailable")
def test_launcher_rotates_logs_larger_than_ten_mib(launcher_paths):
    server, working, logs = launcher_paths
    logs.mkdir()
    stdout_log = logs / "rotate_mcp.stdout.log"
    stderr_log = logs / "rotate_mcp.stderr.log"
    oversized_stdout = b"o" * (10 * 1024 * 1024 + 1)
    oversized_stderr = b"e" * (10 * 1024 * 1024 + 1)
    stdout_log.write_bytes(oversized_stdout)
    stderr_log.write_bytes(oversized_stderr)
    (logs / "rotate_mcp.stdout.log.1").write_bytes(b"old stdout rotation")
    (logs / "rotate_mcp.stderr.log.1").write_bytes(b"old stderr rotation")

    completed = run_launcher(sys.executable, server, working, "rotate_mcp", logs)

    assert completed.returncode == 0, completed.stderr
    assert (logs / "rotate_mcp.stdout.log.1").read_bytes() == oversized_stdout
    assert (logs / "rotate_mcp.stderr.log.1").read_bytes() == oversized_stderr
    assert read_log(stdout_log).strip() == "distinct stdout line"
    assert read_log(stderr_log).strip() == "distinct stderr line"


@pytest.mark.skipif(not POWERSHELL, reason="PowerShell unavailable")
def test_launcher_does_not_rotate_log_at_exactly_ten_mib(launcher_paths):
    server, working, logs = launcher_paths
    logs.mkdir()
    stdout_log = logs / "exact.stdout.log"
    exact_size = b"x" * (10 * 1024 * 1024)
    stdout_log.write_bytes(exact_size)

    completed = run_launcher(sys.executable, server, working, "exact", logs)

    assert completed.returncode == 0, completed.stderr
    assert not (logs / "exact.stdout.log.1").exists()
    assert stdout_log.read_bytes().startswith(exact_size)


@pytest.mark.skipif(not POWERSHELL, reason="PowerShell unavailable")
def test_launcher_fails_for_missing_server(launcher_paths):
    _, working, logs = launcher_paths
    missing_server = working / "missing server.py"

    completed = run_launcher(
        sys.executable, missing_server, working, "missing-mcp", logs
    )

    assert completed.returncode != 0


@pytest.mark.skipif(not POWERSHELL, reason="PowerShell unavailable")
def test_launcher_preserves_percent_delimited_path_text(tmp_path):
    base = tmp_path / "%TEMP%" / "literal percent paths"
    working = base / "working directory"
    logs = base / "log directory"
    working.mkdir(parents=True)
    server = working / "percent server.py"
    server.write_text("print('percent path stayed literal')\n", encoding="utf-8")

    completed = run_launcher(sys.executable, server, working, "percent-mcp", logs)

    assert completed.returncode == 0, completed.stderr
    assert read_log(logs / "percent-mcp.stdout.log").strip() == (
        "percent path stayed literal"
    )


@pytest.mark.skipif(not POWERSHELL, reason="PowerShell unavailable")
def test_launcher_disables_delayed_expansion_for_bang_paths(tmp_path):
    source = LAUNCHER.read_text(encoding="utf-8")
    assert "& $env:ComSpec /d /v:off /s /c $Command" in source

    base = tmp_path / "!TEMP!" / "literal bang paths"
    working = base / "working directory"
    logs = base / "log directory"
    working.mkdir(parents=True)
    server = working / "bang server.py"
    server.write_text("print('bang path stayed literal')\n", encoding="utf-8")

    completed = run_launcher(sys.executable, server, working, "bang-mcp", logs)

    assert completed.returncode == 0, completed.stderr
    assert read_log(logs / "bang-mcp.stdout.log").strip() == (
        "bang path stayed literal"
    )


@pytest.mark.skipif(not POWERSHELL, reason="PowerShell unavailable")
def test_launcher_propagates_python_exit_code(launcher_paths):
    server, working, logs = launcher_paths
    server.write_text(
        "import sys\nprint('child failed', file=sys.stderr)\nraise SystemExit(23)\n",
        encoding="utf-8",
    )

    completed = run_launcher(sys.executable, server, working, "failure-mcp", logs)

    assert completed.returncode == 23
    assert read_log(logs / "failure-mcp.stderr.log").strip() == "child failed"


@pytest.mark.skipif(not POWERSHELL, reason="PowerShell unavailable")
def test_launcher_rejects_invalid_mcp_name(launcher_paths):
    server, working, logs = launcher_paths

    completed = run_launcher(sys.executable, server, working, "../invalid", logs)

    assert completed.returncode != 0
    assert "Invalid MCP name" in completed.stderr
    assert not logs.exists()
