import json
import re
import subprocess
import tempfile
import shutil
from pathlib import Path
from urllib.parse import unquote

import pytest


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "config" / "mcp_servers.json"
REQUIRED = {"name", "directory", "port", "task", "run_level", "description", "health_tool"}
MARKDOWN_LINK = re.compile(
    r"!?\[[^\]]*\]\(\s*"
    r'(?:<(?P<angle>[^>\r\n]*)>|(?P<bare>[^\s)]*))'
    r'''(?:\s+(?:"[^"]*"|'[^']*'|\([^)]*\)))?\s*\)'''
)


def load_entries():
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def invalid_inventory_cases():
    cases = {}
    entries = load_entries()
    entries[1]["task"] = entries[0]["task"]
    cases["task"] = entries
    entries = load_entries()
    entries[0]["port"] = 9000
    cases["canonical ports"] = entries
    entries = load_entries()
    del entries[0]["health_tool"]
    cases["health_tool"] = entries
    entries = load_entries()
    entries[0]["run_level"] = "Admin"
    cases["run_level"] = entries
    return cases


def invalid_inventory_type_cases():
    cases = []

    def add_case(case_name, field, value, duplicate_value=None):
        entries = load_entries()
        entries[0][field] = value
        if duplicate_value is not None:
            entries[1][field] = duplicate_value
        cases.append((case_name, field, entries))

    add_case("string-port", "port", "8777")
    add_case("integral-decimal-port", "port", 8777.0)
    add_case("fractional-decimal-port", "port", 8777.5)
    for field in ("name", "directory", "task", "run_level", "description", "health_tool"):
        add_case(f"numeric-{field}", field, 123)
    add_case("coercible-duplicate-name", "name", 123, "123")
    add_case("coercible-duplicate-task", "task", 123, "123")
    add_case("coercible-duplicate-port", "port", "8778", 8778)
    return cases


def markdown_link_destinations(text):
    fence = None
    for line_number, line in enumerate(text.splitlines(), 1):
        fence_match = re.match(r"^\s*(`{3,}|~{3,})", line)
        if fence_match:
            marker = fence_match.group(1)[0]
            if fence is None:
                fence = marker
            elif fence == marker:
                fence = None
            continue
        if fence is not None:
            continue
        prose = re.sub(r"`[^`]*`", "", line)
        for match in MARKDOWN_LINK.finditer(prose):
            target = match.group("angle")
            if target is None:
                target = match.group("bare")
            target = target.strip()
            if (
                not target
                or target.startswith("#")
                or re.match(r"^(?:https?|mailto):", target, re.IGNORECASE)
                or re.fullmatch(r"\{[^{}]+\}", target)
            ):
                continue
            yield line_number, target


def find_missing_markdown_links(root):
    missing = []
    for document in sorted(root.rglob("*.md")):
        relative_document = document.relative_to(root)
        if ".git" in relative_document.parts or ".worktrees" in relative_document.parts:
            continue
        text = document.read_text(encoding="utf-8")
        for line_number, target in markdown_link_destinations(text):
            relative_path = unquote(target.split("#", 1)[0])
            if relative_path and not (document.parent / relative_path).resolve().exists():
                missing.append(f"{relative_document}:{line_number}: {target}")
    return missing


def test_manifest_has_all_local_servers():
    entries = load_entries()
    assert len(entries) == 18
    assert len({e["name"] for e in entries}) == 18
    assert len({e["task"] for e in entries}) == 18
    assert {e["port"] for e in entries} == (set(range(8777, 8794)) | {8796})


def test_manifest_entries_match_server_sources():
    for entry in load_entries():
        assert REQUIRED <= entry.keys()
        assert entry["run_level"] in {"Highest", "Limited"}
        directory = ROOT / "mcp" / entry["directory"]
        servers = list(directory.glob("*_mcp_server.py"))
        assert len(servers) == 1, entry["name"]
        source = servers[0].read_text(encoding="utf-8")
        assert re.search(rf"FastMCP\([^\n]+port={entry['port']}\)", source)
        assert re.search(
            rf"^(?:async )?def {re.escape(entry['health_tool'])}\(", source, re.MULTILINE
        )


def test_setup_script_loads_shared_manifest():
    source = (ROOT / "setup_mcp_servers.ps1").read_text(encoding="utf-8-sig")
    assert "config\\mcp_servers.json" in source
    assert "ConvertFrom-Json" in source
    assert "$MCPS = @(" not in source


def test_watchdog_inventory_matches_manifest_without_probing():
    completed = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(ROOT / "tools" / "mcp_watchdog" / "mcp_watchdog.ps1"),
            "-InventoryOnly",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    actual = json.loads(completed.stdout)
    expected = [
        {"name": entry["name"], "port": entry["port"], "task": entry["task"]}
        for entry in load_entries()
    ]
    assert actual == expected


def test_watchdog_rejects_truncated_manifest_without_probing():
    with tempfile.TemporaryDirectory(dir=ROOT / "tests") as directory:
        manifest = Path(directory) / "mcp_servers.json"
        manifest.write_text(json.dumps(load_entries()[:1]), encoding="utf-8")
        completed = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(ROOT / "tools" / "mcp_watchdog" / "mcp_watchdog.ps1"),
                "-InventoryOnly",
                "-ManifestPath",
                str(manifest),
            ],
            capture_output=True,
            text=True,
        )
    assert completed.returncode != 0
    assert "exactly 18 entries" in completed.stderr
    assert "8777-8793" in completed.stderr


def test_watchdog_rejects_invalid_inventory_contract_without_probing():
    for error_text, entries in invalid_inventory_cases().items():
        with tempfile.TemporaryDirectory(dir=ROOT / "tests") as directory:
            manifest = Path(directory) / "mcp_servers.json"
            manifest.write_text(json.dumps(entries), encoding="utf-8")
            completed = subprocess.run(
                [
                    "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                    str(ROOT / "tools" / "mcp_watchdog" / "mcp_watchdog.ps1"),
                    "-InventoryOnly", "-ManifestPath", str(manifest),
                ],
                capture_output=True,
                text=True,
            )
        assert completed.returncode != 0, error_text
        assert error_text in completed.stderr, completed.stderr


def test_setup_rejects_invalid_inventory_contract_before_installation():
    for error_text, entries in invalid_inventory_cases().items():
        with tempfile.TemporaryDirectory(dir=ROOT / "tests") as directory:
            temporary_root = Path(directory)
            (temporary_root / "config").mkdir()
            shutil.copy2(ROOT / "setup_mcp_servers.ps1", temporary_root)
            (temporary_root / "config" / "mcp_servers.json").write_text(
                json.dumps(entries), encoding="utf-8"
            )
            completed = subprocess.run(
                [
                    "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                    str(temporary_root / "setup_mcp_servers.ps1"), "-SkipTasks", "-SkipSysmon",
                    "-SkipWatchdog",
                ],
                capture_output=True,
                text=True,
            )
        assert completed.returncode != 0, error_text
        assert error_text.lower() in (completed.stdout + completed.stderr).lower()


@pytest.mark.parametrize(
    "case_name,field,entries",
    invalid_inventory_type_cases(),
    ids=lambda value: value if isinstance(value, str) else None,
)
def test_watchdog_rejects_raw_json_type_coercions_before_inventory_normalization(
    case_name, field, entries
):
    with tempfile.TemporaryDirectory(dir=ROOT / "tests") as directory:
        manifest = Path(directory) / "mcp_servers.json"
        manifest.write_text(json.dumps(entries), encoding="utf-8")
        completed = subprocess.run(
            [
                "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                str(ROOT / "tools" / "mcp_watchdog" / "mcp_watchdog.ps1"),
                "-InventoryOnly", "-ManifestPath", str(manifest),
            ],
            capture_output=True,
            text=True,
        )
    assert completed.returncode != 0, case_name
    assert f"invalid '{field}'" in completed.stderr.lower(), completed.stderr


@pytest.mark.parametrize(
    "case_name,field,entries",
    invalid_inventory_type_cases(),
    ids=lambda value: value if isinstance(value, str) else None,
)
def test_setup_rejects_raw_json_type_coercions_before_installation(
    case_name, field, entries
):
    with tempfile.TemporaryDirectory(dir=ROOT / "tests") as directory:
        temporary_root = Path(directory)
        (temporary_root / "config").mkdir()
        shutil.copy2(ROOT / "setup_mcp_servers.ps1", temporary_root)
        (temporary_root / "config" / "mcp_servers.json").write_text(
            json.dumps(entries), encoding="utf-8"
        )
        completed = subprocess.run(
            [
                "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                str(temporary_root / "setup_mcp_servers.ps1"), "-SkipTasks", "-SkipSysmon",
                "-SkipWatchdog",
            ],
            capture_output=True,
            text=True,
        )
    assert completed.returncode != 0, case_name
    output = (completed.stdout + completed.stderr).lower()
    assert f"invalid '{field}'" in output, output


def test_markdown_destination_parser_supports_standard_syntax():
    text = "\n".join(
        [
            '[angle](<folder/file name.md> "Title")',
            "[encoded](folder/file%20name.md#section)",
            "[braces](missing{draft}.md)",
            "[angles](missing<draft>.md)",
        ]
    )
    assert list(markdown_link_destinations(text)) == [
        (1, "folder/file name.md"),
        (2, "folder/file%20name.md#section"),
        (3, "missing{draft}.md"),
        (4, "missing<draft>.md"),
    ]


def test_markdown_destination_parser_ignores_code_and_external_targets():
    text = "\n".join(
        [
            "`[inline](missing.md)`",
            "```markdown",
            "[fenced](missing.md)",
            "```",
            "[web](https://example.com/a)",
            "[mail](mailto:user@example.com)",
            "[anchor](#local)",
            "[placeholder]({path})",
            "[local](present.md)",
        ]
    )
    assert list(markdown_link_destinations(text)) == [(9, "present.md")]


def test_missing_markdown_links_reports_brace_and_angle_paths():
    with tempfile.TemporaryDirectory(dir=ROOT / "tests") as directory:
        temporary_root = Path(directory)
        (temporary_root / "README.md").write_text(
            "[brace](missing{draft}.md)\n[angle](missing<draft>.md)\n",
            encoding="utf-8",
        )
        assert find_missing_markdown_links(temporary_root) == [
            "README.md:1: missing{draft}.md",
            "README.md:2: missing<draft>.md",
        ]


def test_local_markdown_links_resolve():
    missing = find_missing_markdown_links(ROOT)
    assert not missing, "Missing local Markdown link targets:\n" + "\n".join(missing)
