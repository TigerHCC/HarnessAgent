import importlib
import os

import pytest


@pytest.fixture()
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMSTATE_BASELINES", str(tmp_path / "bl.json"))
    import memstate_mcp_server
    importlib.reload(memstate_mcp_server)
    return memstate_mcp_server


def test_all_tools_registered(server):
    names = set(server.list_tool_names())
    expected = {"pool_tags", "memory_composition", "memory_overview", "tag_driver",
                "baseline_save", "baseline_diff", "memstate_health"}
    assert expected <= names, names


def test_pool_tags_real(server):
    r = server.pool_tags(top_n=5)
    assert "error" not in r, r
    assert "tags" in r and r["total_nonpaged_mb"] > 0
    assert r["total_tag_count"] >= r["matched_tag_count"] >= r["count"]
    for t in r["tags"]:
        assert "tag" in t and "nonpaged_mb" in t


def test_baseline_diff_corrupt_value_does_not_raise(server):
    # a hand-edited baseline with a non-numeric per-tag value must not raise
    import json
    import os
    os.makedirs(os.path.dirname(server.BASELINE_PATH), exist_ok=True)
    with open(server.BASELINE_PATH, "w", encoding="utf-8") as fh:
        json.dump({"c": {"ts": "t", "tags": {"EtwB": "oops", "MmSt": 1000}}}, fh)
    r = server.baseline_diff(name="c")
    assert "error" not in r and "top_growth" in r


def test_memory_overview_real(server):
    o = server.memory_overview()
    assert "error" not in o
    assert o["physical_total_gb"] > 0 and o["handles"] > 0


def test_baseline_roundtrip(server):
    saved = server.baseline_save(name="b")
    assert saved["tag_count"] > 10
    d = server.baseline_diff(name="b")
    assert "top_growth" in d
    assert "error" in server.baseline_diff(name="missing")


def test_health(server):
    h = server.memstate_health()
    assert "is_admin" in h and "ntdll_ok" in h
