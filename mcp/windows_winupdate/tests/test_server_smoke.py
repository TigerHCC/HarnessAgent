import winupdate_mcp_server as server


def test_all_tools_registered():
    names = set(server.list_tool_names())
    expected = {"update_history", "installed_updates", "pending_state",
                "hresult_decode", "winupdate_health"}
    assert expected <= names, names


def test_hresult_decode_tool():
    r = server.hresult_decode("0x80073712")
    assert r["name"] == "ERROR_SXS_COMPONENT_STORE_CORRUPT"
    unknown = server.hresult_decode("0x11112222")
    assert unknown["name"] is None and "unknown" in unknown["meaning"].lower()


def test_health_runs():
    h = server.winupdate_health()
    assert "is_admin" in h and "wua_ok" in h
