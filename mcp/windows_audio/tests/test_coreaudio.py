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

def test_list_sessions_degrades_bad_session(monkeypatch):
    class _Proc:
        def __init__(self, n): self._n = n
        def name(self): return self._n
    class _GoodSession:
        Process = _Proc("good.exe")
        State = 1
    class _BadState:
        def __int__(self): raise OSError("session vanished mid-enumeration")
    class _BadSession:
        Process = _Proc("bad.exe")
        State = _BadState()
    class _FakeAudioUtilities:
        @staticmethod
        def GetAllSessions(): return [_GoodSession(), _BadSession()]
    monkeypatch.setattr(ca, "_pycaw", lambda: {"AudioUtilities": _FakeAudioUtilities})
    r = ca.list_sessions()
    assert r["available"] is True
    assert r["sessions"] == [{"process": "good.exe", "state": 1}]  # bad one skipped, no exception
