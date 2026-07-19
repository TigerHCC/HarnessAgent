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
