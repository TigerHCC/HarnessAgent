# Goose Harness Web — remote browser UI

A tiny, **stdlib-only** (no pip) web front end for the local Goose harness agent.
Open it from any machine on the LAN and chat with the agent — including the
`developer`, `memory`, and `dtm` (DTM Knowledge Agent) tools — with live streaming
and rendered tool-call cards, in the spirit of Claude Code / Codex / Gemini CLI.

```
 __( o)>
 \____)     Goose Harness — qwen-3.6-chat on GB10
```

## How it works
`server.py` is a thin HTTP bridge. For each message it runs:

```
goose run -n <session> [-r] --max-turns 50 -t "<message>"
```

with `cwd = ../workspace` and `GOOSE_MODE` from the request (`auto` runs tools,
`chat` is model-only). It streams goose's stdout, strips ANSI, and parses it into
NDJSON events (`text`, `tool_start`, `tool_args`, `done`) that `index.html` renders.
Session context carries across turns via goose's own `-n`/`-r` session store; the
first turn of a session omits `-r` (goose errors if you resume a session that
doesn't exist yet), later turns add it.

There are two interchangeable implementations with an identical HTTP contract:
- **`server.py`** — Linux/macOS/Windows, needs Python 3 (stdlib only).
- **`server.ps1`** — Windows-native, needs no Python (uses .NET `HttpListener`).
  The PowerShell port feeds the message to goose on **stdin** (`goose run … -i -`)
  so arbitrary message text never has to be quoted for the Windows command line.

## Configuration (`config.json`)
Both servers read **`config.json`** in this directory (override the path with
`GOOSE_WEB_CONFIG`). It sets the backend addresses shown in the status panel and
the rest of the knobs:

```json
{
  "host": "0.0.0.0", "port": 8799, "token": "", "workspace": "../workspace",
  "max_turns": 50, "timeout_seconds": 1800, "goose_bin": "",
  "model": "qwen-3.6-chat", "provider_label": "vLLM (OpenAI-compat)",
  "backends": [
    { "name": "vLLM chat",  "url": "http://192.168.86.44:8000",  "health_path": "/v1/models", "role": "chat"  },
    { "name": "vLLM embed", "url": "http://192.168.86.44:8001",  "health_path": "/v1/models", "role": "embed" },
    { "name": "Ollama",     "url": "http://192.168.86.44:11434", "health_path": "/api/tags",  "role": "ollama" }
  ]
}
```
Edit the `backends` URLs to point the health panel at your vLLM chat / vLLM embed /
Ollama servers; the `role:"chat"` backend supplies the provider host shown in the UI.
**Note:** these addresses only drive the web UI's health panel + displayed provider —
goose's *actual* model provider is configured in goose's own config
(`~/.config/goose/config.yaml`, or `%APPDATA%\Block\goose\config\config.yaml` on Windows).
Any `GOOSE_WEB_*` environment variable overrides the matching `config.json` value.

## Run it
**Linux / macOS:**
```bash
./serve_web.sh                              # 0.0.0.0:8799  (LAN)
GOOSE_WEB_TOKEN=secret ./serve_web.sh       # require ?token / X-Goose-Token  (recommended on LAN)
GOOSE_WEB_HOST=127.0.0.1 ./serve_web.sh     # local-only
GOOSE_WEB_PORT=9000 ./serve_web.sh
```

**Windows (PowerShell):**
```powershell
.\serve_web.ps1                                   # 0.0.0.0:8799 (LAN)
$env:GOOSE_WEB_TOKEN='secret'; .\serve_web.ps1    # require a token (recommended on LAN)
$env:GOOSE_WEB_HOST='127.0.0.1'; .\serve_web.ps1  # local-only (no admin needed)
# if unsigned scripts are blocked:
powershell -ExecutionPolicy Bypass -File .\serve_web.ps1
```
> **Windows LAN bind:** `HttpListener` on `0.0.0.0` needs an elevated shell **or** a
> one-time URL reservation (run once, elevated):
> `netsh http add urlacl url=http://+:8799/ user=%USERNAME%`.
> Binding `127.0.0.1` needs neither. The script prints this hint if the bind fails.

Then open `http://<gb10-ip>:8799` (this box is `192.168.86.44`).

| Env (overrides `config.json`) | Default | Purpose |
|---|---|---|
| `GOOSE_WEB_HOST` | `0.0.0.0` | bind address |
| `GOOSE_WEB_PORT` | `8799` | port (8765 is used by another service on this box) |
| `GOOSE_WEB_TOKEN` | _(none)_ | if set, `/api/chat` requires the token |
| `GOOSE_WEB_WORKSPACE` | `../workspace` | agent working dir (where `developer` writes files) |
| `GOOSE_WEB_MAXTURNS` | `50` | `--max-turns` per turn |
| `GOOSE_WEB_TIMEOUT` | `1800` | hard wall-clock kill (seconds) per turn |
| `GOOSE_WEB_MAX_UPLOAD_MB` | `25` | max size per attached file (`POST /api/upload`) |
| `GOOSE_WEB_MODEL` | from config | model name shown in the UI |
| `GOOSE_WEB_CONFIG` | `./config.json` | path to the web config file |
| `GOOSE_CONFIG` | OS default | goose's `config.yaml` used for live MCP tool discovery |
| `GOOSE_BIN` | auto | path to the goose binary |

## Live MCP tool discovery
The sidebar **Tools** list is discovered **live** from goose's own
`config.yaml` — it is never hardcoded. On startup the server parses the
`extensions:` block, then handshakes each enabled extension over MCP for its real
tool set and caches the result (refreshed every ~90s; `/api/health` only ever
reads the cache, so a slow/offline server never blocks it):

- **builtin** — `developer` is in-process and not handshakeable (`goose mcp developer`
  is invalid), so a curated static entry (`shell`, `text_editor`) is shown; other
  bundled builtins (e.g. `memory`, `computercontroller`) are introspected via
  `goose mcp <id>`.
- **stdio** — spawned `cmd` + `args`, newline-delimited JSON-RPC
  `initialize`→`tools/list`.
- **streamable_http** (e.g. `dtm`, `srum`, `eventlog`) — POST `initialize`
  (echoing the `Mcp-Session-Id`) → `tools/list`; both `application/json` and SSE
  `text/event-stream` replies are handled.

Each `/api/health` response now includes an `extensions[]` array
(`id, name, transport, status, count, detail`) alongside the flat `tools[]`; the
sidebar shows a per-extension status dot + tool count, and marks unreachable
servers `offline`. Point discovery at a non-default config with `GOOSE_CONFIG`.

## Endpoints
- `GET /` — the chat page
- `GET /api/health` — model + backend status + **live-discovered MCP extensions + tool list** (snapshot cache, version cached at startup)
- `POST /api/chat` — `{session, message, mode, attachments?}` → streamed NDJSON events
- `POST /api/upload?session=&name=` — raw file bytes in the body → saved to `workspace/uploads/<session>/`; returns `{ok, name, size}`

## Attaching files
The composer has a 📎 button (plus drag-drop and paste). On send, each staged file is
uploaded via `POST /api/upload` into `workspace/uploads/<session>/`, then `/api/chat` is
called with `attachments: ["<name>", …]`. The server appends the files' workspace-relative
paths to the message, so the agent reads them with its own tools (`developer`
`text_editor`/`shell`; `computercontroller` `pdf_tool`/`docx_tool`/`xlsx_tool`).
Filenames are sanitized and confined to `workspace/uploads/`; each file is capped at
`max_upload_mb` (default 25, env `GOOSE_WEB_MAX_UPLOAD_MB`). Uploads inherit the same
token gate as `/api/chat`.

## ⚠ Security
With `GOOSE_MODE=auto`, the agent auto-runs shell/file tools on this box. Bound to
`0.0.0.0` that means **anyone who can reach the port can run commands here**. On a
shared network set `GOOSE_WEB_TOKEN`, or bind to `127.0.0.1`. The server prints a
loud warning when it binds publicly without a token.

**Privacy:** `server.py` / `server.ps1` set `GOOSE_TELEMETRY_ENABLED=false` on every goose
subprocess, and `serve_web.{sh,ps1}` export it — so goose uploads **no** usage telemetry
(it otherwise POSTs metadata to PostHog). See the repo `README.md` → "Privacy / telemetry".

## DTM speed note (optional)
Each web message spawns a fresh `goose run`, so every DTM query pays the DTM cold-start
(reranker + routing-centroid warmup) — measured ~167 s vs ~110 s against an always-on
warm backend. If DTM latency matters, point this box's `dtm` extension at the warm
mcp-proxy instead of stdio (in `~/.config/goose/config.yaml`):
```yaml
  dtm:
    type: streamable_http
    name: dtm
    uri: http://localhost:8765/mcp
    enabled: true
    timeout: 600
```
…and keep the proxy running persistently (`sudo systemctl enable --now dtm-mcp-proxy`).
Trade-off: the harness then depends on the proxy being up. Default here is stdio
(self-contained). See `../RUN.md` §5.

## Verified (2026-06-28, through the web API)
`/api/health` 3/3 backends up · streaming Q&A · multi-turn resume (`meta.resume=true`)
· DTM tool call (`tool_start dtm_telemetry_lookup`, KB-grounded answer) · `developer`
write landing in `workspace/`.
