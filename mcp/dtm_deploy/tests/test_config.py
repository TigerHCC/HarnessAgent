import json

import config


def _write(tmp_path, obj):
    p = tmp_path / "config.json"
    p.write_text(json.dumps(obj), encoding="utf-8")
    return str(p)


def test_var_expansion(tmp_path):
    p = _write(tmp_path, {"download_path": "${repo_root}/downloads/dtm"})
    cfg = config.load(p)
    assert cfg["download_path"].endswith("/downloads/dtm")


def test_env_override(tmp_path, monkeypatch):
    p = _write(tmp_path, {"download_path": "./d"})
    monkeypatch.setenv("DTM_DEPLOY_MCP_DOWNLOAD_PATH", "OVERRIDE")
    cfg = config.load(p)
    assert cfg["download_path"] == "OVERRIDE"


def test_defaults_applied(tmp_path):
    p = _write(tmp_path, {"download_path": "./d"})
    cfg = config.load(p)
    assert cfg["consent_value_name"] == "ConsentOverride"
    assert cfg["consent_value_data"] == 1
    assert cfg["dtp_service_name"] == "DellTechHub"
    assert cfg["verify_poll_timeout_seconds"] == 3300


def test_env_key():
    assert config.env_key("download_path") == "DTM_DEPLOY_MCP_DOWNLOAD_PATH"
