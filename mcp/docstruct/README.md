# DocStruct MCP (`docstruct`)

A local, **UNELEVATED** MCP server that extracts structured text and fields from PDF documents via text-layer or OCR with schema/template-driven mapping. Binds **`127.0.0.1:8795`**, transport `streamable-http`, endpoint `/mcp`.

This is a **CUSTOM implementation** providing OCR (RapidOCR) and structured extraction via a local vLLM,
mounted **manifest-external** -- the 19th MCP service in this repo, outside the canonical 17-server
manifest (ports 8777-8793) -- see "Why manifest-external" below for the full reason (no health tool
in the canonical probe -> no watchdog/batch coverage -> restarts only at logon, or manually).

## Tools

| Tool | What it does |
|---|---|
| `doc_to_text(path)` | Extracts raw text from a PDF. Uses the text layer first, falls back to RapidOCR for scanned/image-only PDFs. Returns `{source: 'text-layer'\|'ocr', pages, text, page_errors}` with per-page error tracking. PDF only -- use markitdown's `convert_to_markdown` for other formats. |
| `doc_extract(path, schema="", template="")` | Extracts structured field→value JSON from a PDF. Takes EITHER a JSON schema skeleton (e.g., `'{"發票號碼":"","總金額":0}'`) OR a built-in template name (e.g., `'cht_bill'` for 中華電信繳費通知單). Pipeline: text layer → RapidOCR fallback → local vLLM field mapping. Numbers are normalized; Chinese is normalized to Traditional. **Verify totals for OCR sources.** |
| `docstruct_health()` | Server health: OCR/render availability, configured LLM endpoint + reachability, templates list, max_tokens config. |

## Pipeline

```
PDF file → doc_to_text → {text layer | RapidOCR} → per-page text + errors
                                                      ↓
                                                 doc_extract (with schema or template)
                                                      ↓
                                                local vLLM (field mapping) → {fields, model}
```

The OCR path (RapidOCR, 150 DPI default) is used automatically for PDFs without a text layer.
For template extraction, the LLM follows a prompt-driven field mapping (see docstruct_health to list
available templates).

## Configuration

Config is loaded from `config.json` in this directory, with environment overrides via `DOCSTRUCT_MCP_<KEY>`:

| Key | Type | Default | Purpose |
|---|---|---|---|
| `llm_base_url` | string | (from config.json) | **Must track goose's `OPENAI_HOST`**. e.g., `"http://100.88.242.174:8000"`. This is the vLLM endpoint used for field extraction. |
| `llm_model` | string | (from config.json) | Model name passed to the vLLM endpoint (e.g., `"qwen-3.6-chat"`). |
| `max_tokens` | int | 6000 | Maximum token count for vLLM extraction responses. See note below. |
| `ocr_dpi` | int | 150 | DPI for RapidOCR rendering. Higher = slower + larger memory, better OCR on low-res scans. |
| `llm_timeout_seconds` | int | 300 | Timeout for vLLM requests (seconds). Long documents + reasoning models may need more. |

**Note on `max_tokens` with reasoning models**: If using a reasoning model (e.g., qwen-3.6-chat) for extraction,
watch for truncation in the LLM response. The error message names `max_tokens` explicitly. Increase
`DOCSTRUCT_MCP_MAX_TOKENS` if truncation is observed.

## OCR Caveats

- **簡繁 noise**: RapidOCR's Traditional/Simplified Chinese detection can flip within a single document.
  The pipeline normalizes all output to Traditional Chinese (used by `cht_bill` template), but OCR itself
  may have detected Simplified glyphs. **Verify field totals and critical numbers** when using OCR
  sources (use `doc_to_text` to check the raw text first).
- **1-digit misreads**: OCR on low-res or poor-quality scans may misread a single digit. This was
  observed in testing on real 繳費通知單 scans. Normalization helps but does not eliminate this risk.
  Always verify extracted numbers against the source document.
- **Text-layer vs OCR**: `doc_to_text` reports which path was taken (`source` field). If you see
  corrupted or garbled text, inspect the PDF in a viewer to confirm whether the original has a
  proper text layer or is image-only; if image-only, OCR misreads are expected on poor-quality scans.

## Why manifest-external

- No `health_tool` in the canonical probe set → cannot be automatically probed by `tools/mcp_watchdog`
  or the repo's MCP batch-test harness, which both require health tools for watchdog/batch coverage.
- Consequence: it is **not** auto-restarted by the watchdog. It only comes back up at the next logon
  (its own `AtLogOn` Scheduled Task trigger) or if you start it manually.
- **Single copy, not duplicated**: Unlike the 17 canonical servers (which are baked into the manifest),
  this is a one-time registration in `goose/config.yaml`. If the config is copied or migrated, the
  extension block must be re-registered manually.
- Everything else about it follows the same pattern as the canonical servers: unelevated
  (`RunLevel Limited`), loopback-only, launched hidden via `scripts/start_mcp_hidden.ps1`.

## Security notes

- Binds `127.0.0.1` only (loopback) and the Scheduled Task runs **unelevated** (`RunLevel Limited`),
  same as every other server in this repo.
- `doc_to_text(path)` reads **any PDF file the logged-on user can read** -- there is no path allowlist
  or sandbox. Input is limited to local filesystem paths; no HTTP/HTTPS URIs.
- Egress is **only to the configured local vLLM endpoint** (typically `127.0.0.1:8000` or similar),
  not arbitrary endpoints.
- This is **not** a new capability grant: any developer machine already running the Goose desktop
  extension has an unrestricted local shell, which can already read any user-readable file and invoke
  arbitrary Python. `docstruct-mcp` grants nothing beyond what the shell already permits -- which is
  why, unlike some tools in this repo, it has **no confirm-token gating** on `doc_to_text` or
  `doc_extract`.

## Running

```powershell
# one-off, foreground (for testing)
python docstruct_mcp_server.py

# persist across logons (elevated shell needed to REGISTER the task; the server itself runs unelevated)
.\install_task.ps1
.\uninstall_task.ps1   # remove
```

The server (`docstruct_mcp_server.py`) is a FastMCP instance that binds `127.0.0.1:8795` and
exports the three tools above. It loads config from `config.json` with environment overrides, then
uses RapidOCR and the local vLLM for extraction.

## Registering with goose

Register this server as a goose extension with `register_goose_extension.ps1` (from the task).
It points goose's `config.yaml` at `http://127.0.0.1:8795/mcp` under the extension name `docstruct`,
the same way the canonical 17 servers are registered.

This is a **one-time registration**: the config block is added to `config.yaml` only once. If you
re-run the script, it detects the block and exits cleanly (idempotent).

## Troubleshooting

- **Server won't respond / check logs first**: `logs\mcp\docstruct.stderr.log` and
  `logs\mcp\docstruct.stdout.log` (written by `scripts/start_mcp_hidden.ps1`). Most startup failures
  (missing dependency, port conflict, bad argv) show up there immediately.
- **`docstruct_health` first**: Before trying `doc_to_text` or `doc_extract`, call `docstruct_health`
  to confirm OCR is available, the vLLM endpoint is reachable, and templates are loaded.
- **Port 8795 already in use**: find and stop whatever's bound to it --
  `Get-CimInstance Win32_Process -Filter "Name='python.exe'"` and filter `CommandLine` for
  `docstruct_mcp_server`, then `Stop-Process -Id <pid>`. Only one instance should ever be running.
- **`ModuleNotFoundError: rapidocr_onnxruntime`**: `pip install -r requirements.txt` in this directory.
- **`ModuleNotFoundError: pymupdf`**: `pip install -r requirements.txt` in this directory.
- **LLM endpoint unreachable**: Check that the vLLM server is running and that `llm_base_url` in
  `config.json` (or `DOCSTRUCT_MCP_LLM_BASE_URL` env var) points to it. Use `docstruct_health` to test.
- **Bare `GET /mcp` returns 406/400/405**: this is expected -- FastMCP-style servers reject a plain GET
  without the correct `Accept`/session headers. It confirms the server is alive; it is not an error.

## Tests

```powershell
pip install -r requirements.txt
python -m pytest tests -v
```

Tests cover:
- `config.py`: loading from JSON and environment overrides.
- `doctext.py`: text extraction (text layer + OCR fallback), per-page error tracking.
- `llm.py`: field extraction via mock LLM, schema/template routing, number normalization, Chinese normalization.
- `docstruct_mcp_server.py`: the server's tool exports and health probe.

No live vLLM calls or real document extraction in the unit suite (the LLM is mocked). Tests run
with RapidOCR if available, but gracefully degrade if it's missing.

## Post-merge deployment

1. `pip install -r mcp/docstruct/requirements.txt` (pymupdf + rapidocr already installed from the experiments; this formalizes them).
2. Elevated: `mcp\docstruct\install_task.ps1` → then `Start-ScheduledTask mcp-docstruct`.
3. `mcp\docstruct\register_goose_extension.ps1`.
4. Acceptance: 8795 answers 406 (confirms FastMCP is alive); goose_web card appears; `doc_extract` on a test PDF (e.g., `reports/attachments/2024.09.pdf`) with template `cht_bill` extracts fields (e.g., `發票` number, `總金額`).
