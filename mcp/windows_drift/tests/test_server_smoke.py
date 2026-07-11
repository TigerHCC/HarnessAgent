import drift_mcp_server as server


def test_all_tools_registered():
    names = set(server.list_tool_names())
    expected = {"snapshot_now", "list_snapshots", "current", "diff",
                "what_changed_since", "drift_health"}
    assert expected <= names, names


def test_drift_health_runs():
    h = server.drift_health()
    assert "is_admin" in h
    assert "live_counts" in h
