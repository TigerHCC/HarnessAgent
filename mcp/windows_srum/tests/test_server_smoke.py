import srum_mcp_server as s


def test_tools_registered():
    names = set(s.list_tool_names())
    assert {"live_snapshot", "top_processes", "srum_app_usage",
            "srum_network_usage", "srum_energy_usage", "srum_health"} <= names
