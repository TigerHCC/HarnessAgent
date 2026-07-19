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
    return {"ok": ocr_ok and llm_ok, "ocr_available": ocr_ok, "ocr_error": ocr_err,
            "llm_base_url": _CFG["llm_base_url"], "llm_model": _CFG["llm_model"],
            "llm_reachable": llm_ok, "templates": sorted(llm.TEMPLATES),
            "max_tokens": _CFG["max_tokens"]}


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
