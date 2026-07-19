# mcp/docstruct/tests/test_llm.py
import json
import llm

CFG = {"llm_base_url": "http://x", "llm_model": "m", "max_tokens": 100, "llm_timeout_seconds": 5}


def test_template_registry_has_cht_bill():
    skeleton = json.loads(llm.TEMPLATES["cht_bill"])
    assert "繳費總金額" in skeleton and "費用項目" in skeleton


def test_strip_fences():
    assert llm.strip_fences('```json\n{"a":1}\n```') == '{"a":1}'
    assert llm.strip_fences('{"a":1}') == '{"a":1}'


def test_build_prompt_contains_schema_text_and_rules():
    p = llm.build_prompt("OCRTEXT", '{"欄":""}')
    assert "OCRTEXT" in p and '{"欄":""}' in p
    assert "繁體" in p and "JSON" in p


def test_extract_success(monkeypatch):
    monkeypatch.setattr(llm, "call_llm", lambda cfg, prompt: ('{"欄":"值"}', "stop"))
    r = llm.extract(CFG, "text", '{"欄":""}')
    assert r["fields"] == {"欄": "值"}


def test_extract_length_exhaustion_names_max_tokens(monkeypatch):
    monkeypatch.setattr(llm, "call_llm", lambda cfg, prompt: (None, "length"))
    r = llm.extract(CFG, "text", '{"欄":""}')
    assert "max_tokens" in r["error"]


def test_extract_retries_once_on_bad_json_then_reports_raw(monkeypatch):
    calls = []
    def fake(cfg, prompt):
        calls.append(prompt)
        return ("not json at all", "stop")
    monkeypatch.setattr(llm, "call_llm", fake)
    r = llm.extract(CFG, "text", '{"欄":""}')
    assert len(calls) == 2                       # one retry
    assert "error" in r and r["raw"] == "not json at all"
    assert "not json" in calls[1] or "Expecting" in calls[1]   # parse error fed back


def test_extract_retry_succeeds(monkeypatch):
    answers = [("oops", "stop"), ('{"欄":"ok"}', "stop")]
    monkeypatch.setattr(llm, "call_llm", lambda cfg, prompt: answers.pop(0))
    r = llm.extract(CFG, "text", '{"欄":""}')
    assert r["fields"] == {"欄": "ok"}
