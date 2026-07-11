import disk_mcp_server as server


def test_all_tools_registered():
    names = set(server.list_tool_names())
    expected = {"recent_file_changes", "directory_churn", "disk_health",
                "health_baseline_save", "health_baseline_diff", "volume_state", "disk_status"}
    assert expected <= names, names


def test_disk_status_runs():
    s = server.disk_status()
    assert "is_admin" in s and "usn" in s
