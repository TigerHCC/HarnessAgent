import windows_audio_mcp_server as srv

def test_seven_tools():
    import inspect, re
    assert len(re.findall(r"@mcp\.tool\(\)", inspect.getsource(srv))) == 7

def test_defaults_impl_flags(monkeypatch):
    monkeypatch.setattr(srv.coreaudio, "default_for_roles", lambda: {"available": True,
        "render": {"multimedia": {"name": "S", "state": "Active", "volume": 0.2, "mute": True},
                   "communications": {"error": "no default"}}, "capture": {}})
    r = srv._defaults_impl()
    assert r["flags"]["no_multimedia_output"] is True
    assert r["flags"]["no_communications_output"] is True

def test_bluetooth_impl_classifies(monkeypatch):
    monkeypatch.setattr(srv.winaudio, "pnp_audio", lambda timeout=30: [
        {"status": "OK", "name": "FIIO BTR15 Hands-Free", "class": "MEDIA"},
        {"status": "OK", "name": "Pixel 6 Pro A2DP SNK", "class": "MEDIA"},
        {"status": "OK", "name": "Realtek(R) Audio", "class": "MEDIA"},
        {"status": "OK", "name": "MX Master 3 Mouse", "class": "Bluetooth"}])
    r = srv._bluetooth_impl()
    profs = {d["name"]: d["profile"] for d in r["bluetooth"]}
    assert profs["FIIO BTR15 Hands-Free"] == "hfp" and profs["Pixel 6 Pro A2DP SNK"] == "a2dp"
    assert "MX Master 3 Mouse" not in profs  # class=Bluetooth but no audio marker -> not an audio device

def test_health_impl_degrades(monkeypatch):
    monkeypatch.setattr(srv.coreaudio, "default_for_roles", lambda: {"available": False, "error": "no pycaw"})
    monkeypatch.setattr(srv.winaudio, "services", lambda timeout=30: {"Audiosrv": {"status": "Running"}})
    r = srv._health_impl()
    assert r["ok"] is True and r["coreaudio_available"] is False

def test_microphone_impl(monkeypatch):
    monkeypatch.setattr(srv.winaudio, "mic_privacy", lambda: {"global": "Deny", "denied_apps": ["X"], "app_count": 1})
    monkeypatch.setattr(srv.coreaudio, "default_for_roles", lambda: {"available": True, "render": {},
        "capture": {"communications": {"error": "no default"}, "multimedia": {"error": "no default"}}})
    r = srv._microphone_impl()
    assert r["privacy"]["global"] == "Deny"
    assert r["no_default_capture"] is True
