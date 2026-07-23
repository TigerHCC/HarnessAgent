import config

def test_defaults():
    c = config.load()
    assert c["trace_seconds_default"] == 0
    assert c["subprocess_timeout"] == 30 and c["trace_max_seconds"] == 30

def test_env_override(monkeypatch):
    monkeypatch.setenv("WINAUDIO_MCP_SUBPROCESS_TIMEOUT", "5")
    assert config.load()["subprocess_timeout"] == 5
