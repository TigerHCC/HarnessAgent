import importlib
import pytest


@pytest.fixture()
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("FILTERSTACK_BASELINES", str(tmp_path / "bl.json"))
    import parsers
    importlib.reload(parsers)
    import filterstack_mcp_server
    importlib.reload(filterstack_mcp_server)
    return filterstack_mcp_server


def test_all_tools_registered(server):
    names = set(server.list_tool_names())
    expected = {"minifilters", "filter_instances", "network_filters", "altitude_lookup",
                "baseline_save", "baseline_diff", "filterstack_health"}
    assert expected <= names, names


def test_altitude_lookup(server):
    r = server.altitude_lookup("323850.5")
    assert r["class"] == "FSFilter Anti-Virus"
    assert server.altitude_lookup("999999")["class"] is None


def test_baseline_roundtrip(server):
    saved = server.baseline_save(name="b")
    if "error" in saved:  # not elevated
        return
    assert saved["filter_count"] >= 1
    d = server.baseline_diff(name="b")
    assert "summary" in d and d["summary"]["added"] == 0
    assert "error" in server.baseline_diff(name="missing")


def test_health(server):
    h = server.filterstack_health()
    assert "is_admin" in h and "fltmc_ok" in h
