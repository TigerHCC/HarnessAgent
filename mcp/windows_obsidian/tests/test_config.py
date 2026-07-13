# mcp/windows_obsidian/tests/test_config.py
import json
import config


def _write(tmp_path, obj):
    p = tmp_path / "config.json"
    p.write_text(json.dumps(obj), encoding="utf-8")
    return str(p)


def _base(**kw):
    d = {"vault_path": "V", "max_search_results": 50, "max_file_bytes": 1048576, "confirm_ttl_seconds": 120}
    d.update(kw)
    return d


def test_defaults_passthrough(tmp_path):
    cfg = config.load(_write(tmp_path, _base()))
    assert cfg["vault_path"] == "V"
    assert cfg["max_search_results"] == 50
    assert cfg["confirm_ttl_seconds"] == 120


def test_var_expansion(tmp_path):
    cfg = config.load(_write(tmp_path, _base(vault_path="${repo_root}/vault")))
    assert cfg["vault_path"].endswith("/vault")
    assert "${" not in cfg["vault_path"]


def test_vault_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("OBSIDIAN_MCP_VAULT_PATH", "OVERRIDE")
    assert config.load(_write(tmp_path, _base()))["vault_path"] == "OVERRIDE"


def test_vault_alias_override(tmp_path, monkeypatch):
    monkeypatch.setenv("OBSIDIAN_VAULT", "ALIASED")
    assert config.load(_write(tmp_path, _base()))["vault_path"] == "ALIASED"


def test_int_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("OBSIDIAN_MCP_MAX_FILE_BYTES", "42")
    assert config.load(_write(tmp_path, _base()))["max_file_bytes"] == 42


def test_resolved_reports_existence(tmp_path):
    cfg = config.load(_write(tmp_path, _base(vault_path=str(tmp_path))))
    assert cfg["_resolved"]["vault_path"]["exists"] is True
    cfg2 = config.load(_write(tmp_path, _base(vault_path=str(tmp_path / "nope"))))
    assert cfg2["_resolved"]["vault_path"]["exists"] is False


def test_env_key():
    assert config.env_key("vault_path") == "OBSIDIAN_MCP_VAULT_PATH"
    assert config.env_key("max_file_bytes") == "OBSIDIAN_MCP_MAX_FILE_BYTES"
