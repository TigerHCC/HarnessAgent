import coreaudio as ca

def test_flow_of_id():
    assert ca.flow_of_id("{0.0.0.00000000}.{abc}") == "render"
    assert ca.flow_of_id("{0.0.1.00000000}.{abc}") == "capture"
    assert ca.flow_of_id("weird") == "unknown"

def test_flag_defaults_muted_multimedia():
    render = {"multimedia": {"name": "Spk", "state": "Active", "volume": 0.25, "mute": True},
              "communications": {"name": "USB", "state": "Active", "volume": 0.5, "mute": False}}
    f = ca.flag_defaults(render)
    assert f["no_multimedia_output"] is True and any("mut" in r.lower() for r in f["reasons"])
    assert f["no_communications_output"] is False

def test_flag_defaults_missing_comms():
    render = {"multimedia": {"name": "Spk", "state": "Active", "volume": 0.5, "mute": False},
              "communications": {"error": "no default"}}
    f = ca.flag_defaults(render)
    assert f["no_communications_output"] is True
    assert f["no_multimedia_output"] is False

def test_flag_defaults_vol_zero_and_unplugged():
    render = {"multimedia": {"name": "S", "state": "Unplugged", "volume": 0.0, "mute": False},
              "communications": {"name": "S", "state": "Active", "volume": 0.0, "mute": False}}
    f = ca.flag_defaults(render)
    assert f["no_multimedia_output"] is True   # unplugged
    assert f["no_communications_output"] is True  # volume 0

def test_list_endpoints_degrades_without_pycaw(monkeypatch):
    monkeypatch.setattr(ca, "_pycaw", lambda: (_ for _ in ()).throw(ca.AudioUnavailable("no pycaw")))
    r = ca.list_endpoints()
    assert r["available"] is False and "pycaw" in r["error"]
