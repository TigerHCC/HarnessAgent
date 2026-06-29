import eventlog_mcp_server as s


def test_tools_registered():
    names = set(s.list_tool_names())
    assert {"list_channels", "query_events", "error_summary",
            "user_activity", "get_event", "eventlog_health"} <= names
