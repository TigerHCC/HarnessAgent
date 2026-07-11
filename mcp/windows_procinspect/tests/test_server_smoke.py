import procinspect_mcp_server as server


def test_all_tools_registered():
    names = set(server.list_tool_names())
    expected = {"who_locks", "wait_chain", "process_detail", "loaded_modules",
                "top_handle_users", "find_process", "procinspect_health"}
    assert expected <= names, names


def test_health_runs():
    h = server.procinspect_health()
    assert "is_admin" in h and "psutil_ok" in h
