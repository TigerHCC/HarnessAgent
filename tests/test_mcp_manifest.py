import json
import re
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import unquote


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
    assert len(entries) == 14
    assert len({e["name"] for e in entries}) == 14
    assert {e["port"] for e in entries} == set(range(8777, 8791))


def test_manifest_entries_match_server_sources():
    for entry in load_entries():
        assert REQUIRED <= entry.keys()
        assert entry["run_level"] in {"Highest", "Limited"}
        directory = ROOT / "mcp" / entry["directory"]
        servers = list(directory.glob("*_mcp_server.py"))
        assert len(servers) == 1, entry["name"]
        source = servers[0].read_text(encoding="utf-8")
        assert re.search(rf"FastMCP\([^\n]+port={entry['port']}\)", source)
        assert re.search(rf"^def {re.escape(entry['health_tool'])}\(", source, re.MULTILINE)


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
