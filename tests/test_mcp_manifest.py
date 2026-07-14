import json
import re
from pathlib import Path


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
