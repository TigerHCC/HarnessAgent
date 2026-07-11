import perfmon_mcp_server as server


def test_all_tools_registered():
    names = set(server.list_tool_names())
    expected = {"snapshot", "bottleneck", "counters", "baseline_save", "baseline_diff", "perfmon_health"}
    assert expected <= names, names


def test_health_real():
    h = server.perfmon_health()
    assert "is_admin" in h and "pdh_ok" in h


def test_snapshot_real():
    s = server.snapshot(delay_ms=100)
    assert "error" not in s, s
    assert "counters" in s
    assert "cpu" in s["counters"] and "disk" in s["counters"] and "memory" in s["counters"]


def test_bottleneck_real():
    b = server.bottleneck(delay_ms=100)
    assert "error" not in b
    assert "verdict" in b and "findings" in b


def test_counters_validation():
    assert "error" in server.counters(paths=[])
    assert "error" in server.counters(paths="notalist")
