# DocStruct MCP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `mcp/docstruct/` (FastMCP, 127.0.0.1:8795, manifest-external): `doc_to_text` (PDF text layer → RapidOCR fallback), `doc_extract` (schema/template-driven field mapping via the local vLLM), `docstruct_health`, plus markitdown-style mount scaffolding.

**Architecture:** Small units — `config.py` (env-overridable config), `doctext.py` (pure extraction pipeline), `llm.py` (prompt build + vLLM call + parse/retry, fully mockable), `docstruct_mcp_server.py` (FastMCP wiring only). Scaffolding mirrors `mcp/markitdown/` (task `DocStruct-MCP`, register script).

**Tech Stack:** Python 3 (`mcp`, `anyio`, `pymupdf`, `rapidocr-onnxruntime`), pytest, PowerShell 5.1 scripts.

## Global Constraints

- Loopback only: `FastMCP("docstruct", host="127.0.0.1", port=8795)`; goose URI `http://127.0.0.1:8795/mcp`. Task name EXACTLY `DocStruct-MCP`, AtLogOn, RunLevel **Limited**, hidden launcher `-Name "docstruct"`.
- ZERO changes to suite files (manifest, setup, batch test, watchdog) and goose_web. Outside `mcp/docstruct/`, ONLY `mcp/README.md`'s manifest-external note may change (Task 5).
- Tests NEVER call the real vLLM (mock `llm.call_llm`) and never modify the live goose config. Deployment (install task, register script, live acceptance) is post-merge manual.
- Known runtime facts to honor: qwen-3.6-chat is a reasoning model — `finish_reason=="length"` with empty `content` must produce a clear error naming `max_tokens`; OCR text needs a 繁體正規化 instruction in the prompt.
- Branch `feature/docstruct-mcp`; commit there; do not push.
- Every commit body ends with the repo trailers:
  `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>` and `Claude-Session: https://claude.ai/code/session_013Wm8BeurMKFZK6TgvFhLjE`. (Omitted below — add them.)

---

## File Structure

- Create `mcp/docstruct/config.py`, `config.json`, `conftest.py`
- Create `mcp/docstruct/doctext.py` — text-layer + OCR pipeline (no LLM)
- Create `mcp/docstruct/llm.py` — templates, prompt, vLLM call, parse/retry (no file I/O)
- Create `mcp/docstruct/docstruct_mcp_server.py` — FastMCP tools only
- Create `mcp/docstruct/tests/` — `test_config.py`, `test_doctext.py`, `test_llm.py`, `test_server.py`
- Create `mcp/docstruct/requirements.txt`, `install_task.ps1`, `uninstall_task.ps1`, `register_goose_extension.ps1`, `tests/test_register.ps1`, `README.md`; Modify `mcp/README.md`

---

## Task 1: config.py + config.json

**Files:**
- Create: `mcp/docstruct/config.json`, `mcp/docstruct/config.py`, `mcp/docstruct/conftest.py`
- Test: `mcp/docstruct/tests/test_config.py`

**Interfaces:**
- Produces `config.load(path=None) -> dict` with keys: `llm_base_url` (str), `llm_model` (str), `max_tokens` (int, 6000), `ocr_dpi` (int, 150), `llm_timeout_seconds` (int, 300). Env overrides `DOCSTRUCT_MCP_<KEY>`.

- [ ] **Step 1: Write the failing test**

```python
# mcp/docstruct/conftest.py
import os, sys
sys.path.insert(0, os.path.dirname(__file__))
```

```python
# mcp/docstruct/tests/test_config.py
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
```

- [ ] **Step 2: Run to verify FAIL** — `cd mcp/docstruct && python -m pytest tests/test_config.py -v` → `ModuleNotFoundError: config`.

- [ ] **Step 3: Implement**

```json
{
  "llm_base_url": "http://100.88.242.174:8000",
  "llm_model": "qwen-3.6-chat",
  "max_tokens": 6000,
  "ocr_dpi": 150,
  "llm_timeout_seconds": 300
}
```

```python
# mcp/docstruct/config.py
"""Config for the docstruct MCP: config.json defaults with DOCSTRUCT_MCP_<KEY> env overrides.
llm_base_url should track goose's live OPENAI_HOST (see README)."""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))


def env_key(name):
    return "DOCSTRUCT_MCP_" + name.upper()


def load(path=None):
    path = path or os.environ.get("DOCSTRUCT_MCP_CONFIG") or os.path.join(HERE, "config.json")
    with open(path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    for k in ("llm_base_url", "llm_model"):
        cfg[k] = os.environ.get(env_key(k), cfg.get(k, ""))
    for k, default in (("max_tokens", 6000), ("ocr_dpi", 150), ("llm_timeout_seconds", 300)):
        cfg[k] = int(os.environ.get(env_key(k), cfg.get(k, default)))
    return cfg
```

- [ ] **Step 4: Run to verify PASS** (2 tests).
- [ ] **Step 5: Commit** — `git add mcp/docstruct/config.py mcp/docstruct/config.json mcp/docstruct/conftest.py mcp/docstruct/tests/test_config.py && git commit -m "feat(docstruct): config loading with env overrides"`

---

## Task 2: doctext.py — text layer + OCR fallback

**Files:**
- Create: `mcp/docstruct/doctext.py`
- Test: `mcp/docstruct/tests/test_doctext.py`

**Interfaces:**
- Consumes: nothing internal (fitz + rapidocr lazily).
- Produces: `doc_to_text(path: str, dpi: int = 150) -> dict` → `{"source": "text-layer"|"ocr", "pages": N, "text": str, "page_errors": [str]}` or `{"error": str}` for non-PDF/missing files.

- [ ] **Step 1: Write the failing test** (tests generate their own PDFs with PyMuPDF — no fixtures needed)

```python
# mcp/docstruct/tests/test_doctext.py
import fitz
import doctext


def make_text_pdf(path):
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 100), "Invoice Number INV-001")
    page.insert_text((72, 130), "Total 698")
    doc.save(path)


def make_image_pdf(path):
    src = fitz.open()
    p = src.new_page(width=200, height=100)
    p.insert_text((20, 50), "IMG")
    pix = p.get_pixmap(dpi=100)
    out = fitz.open()
    page = out.new_page(width=200, height=100)
    page.insert_image(page.rect, stream=pix.tobytes("png"))
    out.save(path)


def test_text_layer_pdf(tmp_path):
    p = tmp_path / "t.pdf"
    make_text_pdf(str(p))
    r = doctext.doc_to_text(str(p))
    assert r["source"] == "text-layer"
    assert "INV-001" in r["text"] and r["pages"] == 1 and r["page_errors"] == []


def test_image_only_pdf_falls_back_to_ocr(tmp_path, monkeypatch):
    p = tmp_path / "img.pdf"
    make_image_pdf(str(p))
    monkeypatch.setattr(doctext, "_ocr_image", lambda png_path: "OCRED LINE")  # avoid real OCR in unit test
    r = doctext.doc_to_text(str(p))
    assert r["source"] == "ocr"
    assert "OCRED LINE" in r["text"]


def test_ocr_page_error_recorded_not_fatal(tmp_path, monkeypatch):
    p = tmp_path / "img.pdf"
    make_image_pdf(str(p))
    monkeypatch.setattr(doctext, "_ocr_image", lambda png_path: (_ for _ in ()).throw(RuntimeError("boom")))
    r = doctext.doc_to_text(str(p))
    assert r["source"] == "ocr" and r["text"] == ""
    assert len(r["page_errors"]) == 1 and "boom" in r["page_errors"][0]


def test_non_pdf_and_missing_return_error(tmp_path):
    f = tmp_path / "x.docx"
    f.write_bytes(b"zz")
    assert "error" in doctext.doc_to_text(str(f))
    assert "error" in doctext.doc_to_text(str(tmp_path / "nope.pdf"))
```

- [ ] **Step 2: Run to verify FAIL** — `ModuleNotFoundError: doctext`.

- [ ] **Step 3: Implement**

```python
# mcp/docstruct/doctext.py
"""PDF -> text: text layer first (PyMuPDF get_text), OCR fallback (render page -> RapidOCR) for
scanned/image-only PDFs. Pure pipeline, no LLM. RapidOCR is imported lazily (heavy: onnxruntime)."""
import os
import tempfile

import fitz

_OCR = None


def _ocr_image(png_path):
    """OCR one rendered page image -> joined text lines. Isolated for test monkeypatching."""
    global _OCR
    if _OCR is None:
        from rapidocr_onnxruntime import RapidOCR
        _OCR = RapidOCR()
    result, _ = _OCR(png_path)
    return "\n".join(item[1] for item in (result or []))


def doc_to_text(path, dpi=150):
    if not os.path.isfile(path):
        return {"error": "file not found: %s" % path}
    if not path.lower().endswith(".pdf"):
        return {"error": "only PDF is supported here; use markitdown's convert_to_markdown for %s"
                         % os.path.splitext(path)[1]}
    try:
        doc = fitz.open(path)
    except Exception as e:
        return {"error": "cannot open PDF: %s" % e}

    layer = [page.get_text().strip() for page in doc]
    if any(layer):
        return {"source": "text-layer", "pages": len(layer),
                "text": "\n\n".join(layer), "page_errors": []}

    # image-only PDF -> render + OCR each page, best-effort per page
    texts, errors = [], []
    with tempfile.TemporaryDirectory() as tmp:
        for i, page in enumerate(doc):
            try:
                png = os.path.join(tmp, "p%d.png" % i)
                page.get_pixmap(dpi=dpi).save(png)
                texts.append(_ocr_image(png))
            except Exception as e:
                errors.append("page %d: %s" % (i + 1, e))
    return {"source": "ocr", "pages": len(doc),
            "text": "\n\n".join(t for t in texts if t), "page_errors": errors}
```

- [ ] **Step 4: Run to verify PASS** (4 tests; the real-OCR path is exercised in Task 4's smoke).
- [ ] **Step 5: Commit** — `git add mcp/docstruct/doctext.py mcp/docstruct/tests/test_doctext.py && git commit -m "feat(docstruct): PDF text-layer extraction with OCR fallback"`

---

## Task 3: llm.py — templates, prompt, call, parse/retry

**Files:**
- Create: `mcp/docstruct/llm.py`
- Test: `mcp/docstruct/tests/test_llm.py`

**Interfaces:**
- Produces:
  - `TEMPLATES: dict[str, str]` — `"cht_bill"` → the proven skeleton JSON string.
  - `strip_fences(s: str) -> str`
  - `build_prompt(text: str, schema: str) -> str` — includes 繁體正規化 + "只輸出 JSON".
  - `call_llm(cfg, prompt) -> tuple[str|None, str]` — `(content, finish_reason)`; the ONLY network touchpoint (tests monkeypatch it).
  - `extract(cfg, text, schema) -> dict` — `{"fields": {...}}` on success; `{"error": ..., ...}` on length-exhaustion or double parse failure (includes `raw`).

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run to verify FAIL** — `ModuleNotFoundError: llm`.

- [ ] **Step 3: Implement**

```python
# mcp/docstruct/llm.py
"""Schema-driven extraction over the local vLLM chat endpoint. qwen-3.6-chat is a REASONING model:
it spends tokens thinking before answering, so max_tokens must be generous or content comes back
empty with finish_reason=length -- extract() turns that into a clear error instead of a crash."""
import json
import urllib.request

TEMPLATES = {
    "cht_bill": ('{"公司":"","期別":"","繳費總金額":0,"繳費方式":"","發票號碼":"","隨機碼":"",'
                 '"營運處代號":"","用戶號碼":"","用戶帳號":"","計費期間":"",'
                 '"費用項目":[{"項目":"","金額":0}]}'),
}


def strip_fences(s):
    s = (s or "").strip()
    if s.startswith("```"):
        parts = s.split("```")
        if len(parts) >= 2:
            s = parts[1]
            if s.lower().startswith("json"):
                s = s[4:]
    return s.strip()


def build_prompt(text, schema):
    return (
        "以下是從文件抽取（可能經 OCR，含簡繁混字或雜訊）的全文。"
        "請依照給定格式抽取欄位，只輸出 JSON（不要任何其他文字或說明），"
        "所有中文一律正規化為繁體中文，數字使用半形：\n"
        "格式：\n%s\n\n全文：\n%s" % (schema, text)
    )


def call_llm(cfg, prompt):
    """POST /v1/chat/completions -> (content|None, finish_reason). The only network touchpoint."""
    body = {"model": cfg["llm_model"], "temperature": 0, "max_tokens": cfg["max_tokens"],
            "messages": [{"role": "user", "content": prompt}]}
    req = urllib.request.Request(cfg["llm_base_url"].rstrip("/") + "/v1/chat/completions",
                                 data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    resp = urllib.request.urlopen(req, timeout=cfg["llm_timeout_seconds"])
    out = json.loads(resp.read())
    choice = out["choices"][0]
    return choice["message"].get("content"), choice.get("finish_reason", "")


def extract(cfg, text, schema):
    prompt = build_prompt(text, schema)
    content, finish = call_llm(cfg, prompt)
    if not content:
        if finish == "length":
            return {"error": "LLM ran out of tokens while reasoning (finish_reason=length); "
                             "raise max_tokens (config key max_tokens / DOCSTRUCT_MCP_MAX_TOKENS)"}
        return {"error": "LLM returned empty content (finish_reason=%s)" % finish}
    raw = strip_fences(content)
    try:
        return {"fields": json.loads(raw)}
    except ValueError as e:
        retry_prompt = prompt + ("\n\n注意：你上一次的輸出不是合法 JSON（%s）。"
                                 "請重新只輸出合法 JSON。" % e)
        content2, _ = call_llm(cfg, retry_prompt)
        raw2 = strip_fences(content2 or "")
        try:
            return {"fields": json.loads(raw2)}
        except ValueError:
            return {"error": "LLM output is not valid JSON after one retry", "raw": raw2 or raw}
```

- [ ] **Step 4: Run to verify PASS** (7 tests).
- [ ] **Step 5: Commit** — `git add mcp/docstruct/llm.py mcp/docstruct/tests/test_llm.py && git commit -m "feat(docstruct): schema extraction over local vLLM with retry"`

---

## Task 4: docstruct_mcp_server.py — FastMCP wiring + health

**Files:**
- Create: `mcp/docstruct/docstruct_mcp_server.py`
- Test: `mcp/docstruct/tests/test_server.py`

**Interfaces:**
- Consumes: `config.load`, `doctext.doc_to_text`, `llm.TEMPLATES/extract`.
- Produces MCP tools: `doc_to_text(path)`, `doc_extract(path, schema="", template="")`, `docstruct_health()`; module-level `_extract_impl(path, schema, template)` for direct testing.

- [ ] **Step 1: Write the failing test**

```python
# mcp/docstruct/tests/test_server.py
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
```

- [ ] **Step 2: Run to verify FAIL** — `ModuleNotFoundError: docstruct_mcp_server`.

- [ ] **Step 3: Implement**

```python
# mcp/docstruct/docstruct_mcp_server.py
"""DocStruct MCP (FastMCP, streamable HTTP, 127.0.0.1:8795).

Structured document extraction: PDF text layer -> RapidOCR fallback -> schema/template-driven
field mapping via the local vLLM. Manifest-external (like markitdown on 8794): own scheduled task,
no watchdog/batch-test coverage. Read-only on input files; egress only to the configured local LLM
endpoint; runs UNELEVATED. Goose connects via streamable_http, uri http://127.0.0.1:8795/mcp.
"""
import anyio
from mcp.server.fastmcp import FastMCP

import config
import doctext
import llm

mcp = FastMCP("docstruct", host="127.0.0.1", port=8795)

_CFG = config.load()
_LIMITER = anyio.CapacityLimiter(2)   # OCR + LLM are heavy; cap concurrency


def _extract_impl(path, schema, template):
    if bool(schema) == bool(template):
        return {"error": "provide exactly one of schema (JSON skeleton) or template (name)"}
    if template:
        if template not in llm.TEMPLATES:
            return {"error": "unknown template %r; available: %s" % (template, sorted(llm.TEMPLATES))}
        schema = llm.TEMPLATES[template]
    doc = doctext.doc_to_text(path, dpi=_CFG["ocr_dpi"])
    if "error" in doc:
        return doc
    result = llm.extract(_CFG, doc["text"], schema)
    if "error" in result:
        return {**result, "source": doc["source"], "page_errors": doc["page_errors"]}
    return {"fields": result["fields"], "source": doc["source"], "pages": doc["pages"],
            "page_errors": doc["page_errors"], "model": _CFG["llm_model"]}


@mcp.tool()
async def doc_to_text(path: str) -> dict:
    """Extract text from a PDF: text layer first, OCR fallback (RapidOCR) for scanned/image-only
    PDFs. Returns {source: 'text-layer'|'ocr', pages, text, page_errors}. PDF only -- use
    markitdown's convert_to_markdown for other formats."""
    return await anyio.to_thread.run_sync(doctext.doc_to_text, path, _CFG["ocr_dpi"],
                                          limiter=_LIMITER)


@mcp.tool()
async def doc_extract(path: str, schema: str = "", template: str = "") -> dict:
    """Extract structured field->value JSON from a PDF document. Give EITHER schema (a JSON skeleton
    string, e.g. '{"發票號碼":"","總金額":0}') OR template (built-in name, e.g. 'cht_bill' for
    中華電信繳費通知單). Pipeline: text layer -> OCR fallback -> local vLLM mapping. Numbers are
    normalized; Chinese is normalized to Traditional. Verify totals for OCR sources."""
    return await anyio.to_thread.run_sync(_extract_impl, path, schema, template, limiter=_LIMITER)


@mcp.tool()
def docstruct_health() -> dict:
    """Server health: OCR/render availability, configured LLM endpoint + reachability, templates."""
    ocr_ok, ocr_err = True, ""
    try:
        import rapidocr_onnxruntime  # noqa: F401
    except Exception as e:            # pragma: no cover
        ocr_ok, ocr_err = False, str(e)
    llm_ok = False
    try:
        import urllib.request
        req = urllib.request.Request(_CFG["llm_base_url"].rstrip("/") + "/v1/models")
        llm_ok = urllib.request.urlopen(req, timeout=4).status == 200
    except Exception:
        pass
    return {"ok": ocr_ok, "ocr_available": ocr_ok, "ocr_error": ocr_err,
            "llm_base_url": _CFG["llm_base_url"], "llm_model": _CFG["llm_model"],
            "llm_reachable": llm_ok, "templates": sorted(llm.TEMPLATES),
            "max_tokens": _CFG["max_tokens"]}


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
```

- [ ] **Step 4: Run to verify PASS**, then full module suite `python -m pytest -q` (Tasks 1-4, ~18 tests).
- [ ] **Step 5: Live smoke** — start the server hidden, expect 406/400/405 on `http://127.0.0.1:8795/mcp`, then STOP the python process (find via CommandLine match on `docstruct_mcp_server`). Do not leave it running.
- [ ] **Step 6: Commit** — `git add mcp/docstruct/docstruct_mcp_server.py mcp/docstruct/tests/test_server.py && git commit -m "feat(docstruct): FastMCP tools + health"`

---

## Task 5: Scaffolding (requirements, task scripts, register script, READMEs)

**Files:**
- Create: `mcp/docstruct/requirements.txt`, `install_task.ps1`, `uninstall_task.ps1`, `register_goose_extension.ps1`, `tests/test_register.ps1`, `README.md`
- Modify: `mcp/README.md` (extend the manifest-external note)

**Interfaces:** none new. All four PS scripts are the `mcp/markitdown/` ones with these substitutions: name `markitdown`→`docstruct`, task `MarkItDown-MCP`→`DocStruct-MCP`, port `8794`→`8795`, server file →`docstruct_mcp_server.py`, backup suffix `.bak-markitdown`→`.bak-docstruct`, extension block description → `'Structured document extraction: PDF text-layer/OCR (RapidOCR) to text, and schema/template-driven field->value JSON via the local vLLM (docstruct MCP, manifest-external, 127.0.0.1:8795).'`

- [ ] **Step 1: `requirements.txt`**

```
mcp>=1.2
anyio>=4.5
pymupdf>=1.24
rapidocr-onnxruntime>=1.3
pytest>=8.0
```

- [ ] **Step 2: PS scripts.** Read each `mcp/markitdown/` counterpart (`install_task.ps1`, `uninstall_task.ps1`, `register_goose_extension.ps1`, `tests/test_register.ps1`) and apply the substitution table above — no other changes. The register test must keep its TEMP-sandbox pattern (never the live config) with the `docstruct` block name.
- [ ] **Step 3: RED/GREEN for the register script** — run `powershell -NoProfile -File mcp/docstruct/tests/test_register.ps1` before writing the script (FAIL: not found), then after (expect `[OK] register script tests pass`).
- [ ] **Step 4: `README.md`** — mirror `mcp/markitdown/README.md` structure: tools + pipeline diagram, config keys (`llm_base_url` must track goose's OPENAI_HOST), the reasoning-model `max_tokens` note, OCR caveats (簡繁 noise handled by prompt; verify totals on OCR sources — a 1-digit misread was observed), security stance (read-only, egress only to local vLLM, no gating — developer shell already grants more), manifest-external rationale (single copy), troubleshooting (`logs/mcp/docstruct.stderr.log`, `docstruct_health` first), deployment steps.
- [ ] **Step 5: `mcp/README.md`** — in the existing manifest-external note (added for markitdown), list docstruct (8795) beside markitdown (8794). Do not touch canonical counts.
- [ ] **Step 6: Parse checks** on all four PS scripts (`[ScriptBlock]::Create`), full pytest suite one more time.
- [ ] **Step 7: Commit** — `git add mcp/docstruct/requirements.txt mcp/docstruct/install_task.ps1 mcp/docstruct/uninstall_task.ps1 mcp/docstruct/register_goose_extension.ps1 mcp/docstruct/tests/test_register.ps1 mcp/docstruct/README.md mcp/README.md && git commit -m "feat(docstruct): mount scaffolding + docs"`

---

## Post-merge deployment (manual)

1. `pip install -r mcp/docstruct/requirements.txt` (pymupdf + rapidocr already installed from the experiments; this formalizes them).
2. Elevated: `mcp\docstruct\install_task.ps1` → `Start-ScheduledTask DocStruct-MCP`.
3. `mcp\docstruct\register_goose_extension.ps1`.
4. Acceptance: 8795 answers 406; goose_web card appears; `doc_extract` on `reports/attachments/2024.09.pdf` with template `cht_bill` reproduces the experiment (總金額 698, 發票 EF14063066, …).

## Self-Review Notes

- Spec coverage: doc_to_text with source marking + per-page errors (Task 2), doc_extract with schema XOR template + cht_bill (Tasks 3-4), health with LLM probe (Task 4), reasoning-model length error naming max_tokens (Task 3), 繁體 normalization in prompt (Task 3), config with env overrides + llm_base_url note (Tasks 1, 5), mount scaffolding + register script (Task 5), README caveats + mcp/README note (Task 5), tests never hit real vLLM (all llm tests monkeypatch call_llm; server tests monkeypatch llm.extract).
- Type consistency: `doc_to_text` dict shape identical between doctext.py, server passthrough, and tests; `llm.extract(cfg, text, schema)` signature matches server call; `_extract_impl(path, schema, template)` matches its tests.
- The `doc_to_text` tool name intentionally shadows `doctext.doc_to_text` at module scope in the server — the tests reference `srv.doctext.doc_to_text` (module attr), unaffected.
