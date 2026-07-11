import netconn_mcp_server as server


def test_all_tools_registered():
    names = set(server.list_tool_names())
    expected = {"connections", "listeners", "connection_stats", "by_remote",
                "baseline_save", "baseline_diff", "netconn_health"}
    assert expected <= names, names


def test_health_runs():
    h = server.netconn_health()
    assert "is_admin" in h and "psutil_ok" in h


def test_connections_real_smoke():
    r = server.connections(max=5)
    assert "error" not in r
    assert "connections" in r
    for c in r["connections"]:
        assert "proto" in c and "state" in c


def test_connection_stats_real():
    s = server.connection_stats()
    assert "error" not in s
    assert "total" in s and "by_state" in s and "ephemeral" in s
