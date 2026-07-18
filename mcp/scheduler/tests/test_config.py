import os
import config

def test_defaults_resolve_to_absolute_paths():
    c = config.load()
    assert os.path.isabs(c["workspace"])
    assert os.path.isabs(c["schedules_path"]) and c["schedules_path"].endswith("schedules.json")
    assert c["tick_seconds"] == 30 and c["default_max_turns"] == 50

def test_env_override_wins(monkeypatch):
    monkeypatch.setenv("SCHEDULER_MCP_TICK_SECONDS", "5")
    assert config.load()["tick_seconds"] == 5
