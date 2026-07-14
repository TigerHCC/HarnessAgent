import json
import re
from pathlib import Path
from urllib.parse import unquote


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "config" / "mcp_servers.json"
REQUIRED = {"name", "directory", "port", "task", "run_level", "description", "health_tool"}


def load_entries():
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


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


def test_local_markdown_links_resolve():
    link_pattern = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
    missing = []

    for document in sorted(ROOT.rglob("*.md")):
        relative_document = document.relative_to(ROOT)
        if ".git" in relative_document.parts or ".worktrees" in relative_document.parts:
            continue
        in_fence = False
        for line_number, line in enumerate(document.read_text(encoding="utf-8").splitlines(), 1):
            if re.match(r"^\s*(```|~~~)", line):
                in_fence = not in_fence
                continue
            if in_fence:
                continue
            prose = re.sub(r"`[^`]*`", "", line)
            for match in link_pattern.finditer(prose):
                raw_target = match.group(1).strip()
                target = raw_target.split(maxsplit=1)[0].strip("<>")
                if (
                    not target
                    or target.startswith("#")
                    or re.match(r"^(?:https?|mailto):", target, re.IGNORECASE)
                    or any(marker in target for marker in ("<", ">", "{", "}"))
                ):
                    continue
                relative_path = unquote(target.split("#", 1)[0])
                if relative_path and not (document.parent / relative_path).resolve().exists():
                    missing.append(
                        f"{relative_document}:{line_number}: {raw_target}"
                    )

    assert not missing, "Missing local Markdown link targets:\n" + "\n".join(missing)
