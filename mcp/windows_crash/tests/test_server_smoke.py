import crash_mcp_server as server


def test_all_tools_registered():
    names = set(server.list_tool_names())
    expected = {"crash_summary", "list_crashes", "get_crash",
                "list_dumps", "analyze_dump", "crash_health"}
    assert expected <= names, names


def test_crash_health_runs():
    h = server.crash_health()
    assert "is_admin" in h
    assert "stores" in h
    assert "dumps" in h
