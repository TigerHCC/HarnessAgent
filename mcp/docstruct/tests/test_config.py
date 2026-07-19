import config

def test_defaults():
    c = config.load()
    assert c["llm_base_url"].startswith("http")
    assert c["llm_model"] == "qwen-3.6-chat"
    assert c["max_tokens"] == 6000 and c["ocr_dpi"] == 150 and c["llm_timeout_seconds"] == 300

def test_env_override_wins(monkeypatch):
    monkeypatch.setenv("DOCSTRUCT_MCP_MAX_TOKENS", "1234")
    monkeypatch.setenv("DOCSTRUCT_MCP_LLM_BASE_URL", "http://127.0.0.1:9999")
    c = config.load()
    assert c["max_tokens"] == 1234
    assert c["llm_base_url"] == "http://127.0.0.1:9999"
