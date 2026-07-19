# DocStruct MCP Design (structured document extraction)

## Goal

Turn document conversion output from "a pile of strings" into field→value structured data: OCR for
scanned PDFs that markitdown/pdfminer cannot read, and schema-driven extraction that maps document
fields to JSON — proven end-to-end on a real scanned 中華電信 bill (all header fields correct,
line-items captured).

## Decision Record (validated by experiments, 2026-07-20)

- `reports/attachments/2024.09.pdf` has NO text layer (0 fonts, image-only) → pdfminer AND markitdown
  (pdfminer-based) return empty. OCR is required for this class of input.
- GB10 vision models are NOT currently viable: vLLM occupies the box, only ~11 GB free; every vision
  candidate needs 21-80 GB (attempting a 9 GB load destabilized the box and briefly took vLLM down —
  it self-recovered via docker restart in ~4 min).
- Local CPU OCR IS viable: RapidOCR (pip `rapidocr-onnxruntime`) read the bill page in ~7 s with good
  CJK accuracy. PyMuPDF (pip `pymupdf`) renders PDF pages without system deps (poppler not needed).
- Structured extraction IS viable on the existing text LLM: OCR text + a JSON-format prompt to vLLM
  `qwen-3.6-chat` (temperature 0) returned a correct field-mapped JSON. Two caveats encoded below:
  qwen-3.6-chat is a REASONING model (needs large max_tokens or `content` comes back None with
  finish_reason=length), and OCR simplified/traditional noise must be normalized by prompt.
- Placement: manifest-external on **8795** (same lightweight mount as markitdown on 8794), keeping the
  canonical 17-server contiguous-port validators untouched. Accepted cost: no watchdog/batch-test
  coverage; restarts at next logon.

## Scope

New module `mcp/docstruct/` (FastMCP, streamable-http, 127.0.0.1:8795, UNELEVATED). New files only;
zero changes to suite files and goose_web. Scaffolding mirrors `mcp/markitdown/`: install/uninstall
task scripts (task `DocStruct-MCP`, AtLogOn, Limited, hidden launcher `-Name "docstruct"`),
`register_goose_extension.ps1` (idempotent config.yaml add, backup + YAML validation), README.

### Tools

- `doc_to_text(path: str) -> dict` — text extraction with OCR fallback:
  1. Try the PDF text layer (pdfminer via markitdown's stack is NOT imported; use `pymupdf`'s
     `page.get_text()` which needs no extra dep). Non-PDF files are out of scope for OCR fallback and
     return an error pointing at markitdown.
  2. If the text layer is empty/whitespace → render each page (PyMuPDF, 150 dpi) → RapidOCR → join
     lines per page.
  Returns `{source: "text-layer"|"ocr", pages: N, text: "..."}`.
- `doc_extract(path: str, schema: str = "", template: str = "") -> dict` — the full pipeline:
  `doc_to_text` → build prompt (schema JSON skeleton + 繁體正規化 + "output JSON only") → vLLM
  chat/completions (temperature 0, configurable max_tokens default 6000) → strip code fences →
  `json.loads` → return `{fields: {...}, source, model}`. Exactly one of `schema` (a JSON skeleton
  string supplied by the caller) or `template` (a built-in name) must be given.
  Built-in template: `"cht_bill"` — the 中華電信繳費通知單 skeleton proven in the experiment
  (公司/期別/繳費總金額/繳費方式/發票號碼/隨機碼/營運處代號/用戶號碼/用戶帳號/計費期間/費用項目[]).
- `docstruct_health() -> dict` — OCR/render import status, configured LLM endpoint + model, reachable
  (bounded probe), templates list.

### Config (`config.py` + `config.json`, mirroring dtm_download's pattern)

- `llm_base_url` (default `http://100.88.242.174:8000`, matching goose's live OPENAI_HOST),
  `llm_model` (default `qwen-3.6-chat`), `max_tokens` (default 6000), `ocr_dpi` (default 150),
  `llm_timeout_seconds` (default 300). Env overrides `DOCSTRUCT_MCP_<KEY>`. The README notes
  `llm_base_url` should track goose's `OPENAI_HOST` if the backend moves.

## Error Handling

- LLM returns `finish_reason=length` with empty content (reasoning model ran out of budget) → clear
  error naming `max_tokens` and its config key, not a crash.
- LLM output that fails `json.loads` after fence-stripping → ONE retry appending the parse error to
  the prompt; then return `{error, raw}` so the caller sees what came back.
- OCR/render failure on a page → record the page error, continue remaining pages, report per-page
  status in the result.
- vLLM unreachable → bounded timeout, clear error naming the endpoint (`docstruct_health` first).

## Security

- Loopback bind, UNELEVATED, read-only on input files; egress ONLY to the configured local vLLM
  endpoint. No confirm-gating: grants nothing beyond the developer shell (same stance as markitdown,
  documented in README).
- Extracted content may contain PII (the proven example is a personal bill) — results go back to the
  caller only; nothing is persisted by the module.

## Tests

- Pure units (LLM mocked): fence stripping, prompt build (schema + template + 繁體 instruction),
  retry-on-parse-error path, length/finish_reason error path, template registry, config env overrides.
- OCR smoke: a tiny generated image with known text through RapidOCR (skippable if runtime missing).
- `doc_to_text` on a generated text-layer PDF (PyMuPDF can create one in-test) → source="text-layer";
  on a generated image-only PDF → source="ocr".
- Manual acceptance: `doc_extract` on `reports/attachments/2024.09.pdf` with template `cht_bill`
  reproduces the experiment's field values; goose_web sidebar shows the docstruct card.

## Documentation

- `mcp/docstruct/README.md`: tools, pipeline diagram, config, the reasoning-model max_tokens note,
  OCR accuracy caveats (simplified/traditional noise, small-amount misreads — recommend verifying
  totals), security stance, deployment steps.
- `mcp/README.md`: extend the manifest-external note to list docstruct (8795) beside markitdown
  (8794), canonical claims untouched.
