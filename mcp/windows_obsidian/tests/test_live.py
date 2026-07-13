# mcp/windows_obsidian/tests/test_live.py
"""Read-only smoke test against the REAL configured vault. Gated: runs only when
OBSIDIAN_MCP_LIVE_TESTS=1 and the vault exists. NEVER writes."""
import os
import unittest

import obsidian_mcp_server as srv

_LIVE = os.environ.get("OBSIDIAN_MCP_LIVE_TESTS") == "1"


def _reason():
    if not _LIVE:
        return "OBSIDIAN_MCP_LIVE_TESTS != 1"
    if not srv.obsidian_health().get("exists"):
        return "configured vault does not exist"
    return None


@unittest.skipUnless(_reason() is None, _reason() or "prereqs unmet")
class Live(unittest.TestCase):
    def test_health(self):
        h = srv.obsidian_health()
        self.assertTrue(h["exists"])
        self.assertGreater(h["note_count"], 0)

    def test_search_returns_something(self):
        res = srv.obsidian_search("the", in_name=False, max=5)
        self.assertIn("results", res)
