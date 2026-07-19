import docstruct_mcp_server as srv


def test_exactly_three_tools_registered():
    import inspect, re
    src = inspect.getsource(srv)
    assert len(re.findall(r"@mcp\.tool\(\)", src)) == 3


def test_extract_impl_requires_exactly_one_of_schema_template(monkeypatch):
    r = srv._extract_impl("x.pdf", "", "")
    assert "error" in r and "schema" in r["error"]
    r2 = srv._extract_impl("x.pdf", '{"a":""}', "cht_bill")
    assert "error" in r2


def test_extract_impl_unknown_template():
    r = srv._extract_impl("x.pdf", "", "no_such")
    assert "error" in r and "template" in r["error"]


def test_extract_impl_pipeline(monkeypatch, tmp_path):
    monkeypatch.setattr(srv.doctext, "doc_to_text",
                        lambda path, dpi=150: {"source": "ocr", "pages": 1, "text": "T", "page_errors": []})
    monkeypatch.setattr(srv.llm, "extract", lambda cfg, text, schema: {"fields": {"欄": "值"}})
    r = srv._extract_impl(str(tmp_path / "a.pdf"), "", "cht_bill")
    assert r["fields"] == {"欄": "值"} and r["source"] == "ocr"


def test_extract_impl_propagates_doc_error(monkeypatch):
    monkeypatch.setattr(srv.doctext, "doc_to_text", lambda path, dpi=150: {"error": "file not found: x"})
    r = srv._extract_impl("x.pdf", "", "cht_bill")
    assert "error" in r


def test_health_ok_requires_both_ocr_and_llm(monkeypatch):
    import urllib.request
    class FakeResp:
        status = 200
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=4: FakeResp())
    h = srv.docstruct_health()
    assert h["llm_reachable"] is True and h["ok"] is True

    def boom(req, timeout=4):
        raise OSError("down")
    monkeypatch.setattr(urllib.request, "urlopen", boom)
    h2 = srv.docstruct_health()
    assert h2["llm_reachable"] is False and h2["ok"] is False
