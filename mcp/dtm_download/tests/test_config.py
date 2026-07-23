import json

import config


def _write(tmp_path, obj):
    p = tmp_path / "config.json"
    p.write_text(json.dumps(obj), encoding="utf-8")
    return str(p)


def test_var_expansion(tmp_path):
    p = _write(tmp_path, {"download_path": "${repo_root}/downloads/dtm",
                          "artifactory_base_url": "https://example", "repo": "r"})
    cfg = config.load(p)
    assert cfg["download_path"].endswith("/downloads/dtm")


def test_env_override(tmp_path, monkeypatch):
    p = _write(tmp_path, {"download_path": "./downloads/dtm",
                          "artifactory_base_url": "https://example", "repo": "r"})
    monkeypatch.setenv("DTM_DOWNLOAD_MCP_DOWNLOAD_PATH", "OVERRIDE")
    cfg = config.load(p)
    assert cfg["download_path"] == "OVERRIDE"


def test_token_never_read_from_config_json(tmp_path, monkeypatch):
    p = _write(tmp_path, {"download_path": "./d", "artifactory_base_url": "https://example",
                          "repo": "r", "token": "should-be-ignored"})
    monkeypatch.delenv(config.TOKEN_ENV_VAR, raising=False)
    cfg = config.load(p)
    assert config.get_token() == ""
    assert cfg["_resolved"]["token_present"]["resolved"] is False


def test_token_from_env_only(monkeypatch):
    monkeypatch.setenv(config.TOKEN_ENV_VAR, "secret-token")
    assert config.get_token() == "secret-token"


def test_defaults_applied(tmp_path):
    p = _write(tmp_path, {"download_path": "./d", "artifactory_base_url": "https://example", "repo": "r"})
    cfg = config.load(p)
    assert cfg["default_channel"] == "Daily"
    assert cfg["default_build_type"] == "Release"
    assert cfg["zip_components"]
    assert cfg["download_timeout_seconds"] == 600


def test_env_key():
    assert config.env_key("download_path") == "DTM_DOWNLOAD_MCP_DOWNLOAD_PATH"
