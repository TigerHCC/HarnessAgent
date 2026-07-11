import exec_mcp_server as server


def test_all_tools_registered():
    names = set(server.list_tool_names())
    expected = {"prefetch_list", "prefetch_detail", "bam_list", "userassist_list",
                "shimcache_list", "exec_timeline", "exec_health"}
    assert expected <= names, names


def test_exec_health_runs():
    h = server.exec_health()
    assert "is_admin" in h
    assert "registry" in h


def test_exec_timeline_runs():
    t = server.exec_timeline(hours=168, max=10)
    assert "timeline" in t
    assert "window_hours" in t
