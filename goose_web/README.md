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

## Configuration
**The model / provider / backend addresses shown in the UI are read LIVE from
goose's own `config.yaml`** (`~/.config/goose/config.yaml`, or
`%APPDATA%\Block\goose\config\config.yaml` on Windows; override with
`GOOSE_CONFIG`) — the same file goose actually runs from. On every
`/api/health` poll (~20 s) the server re-reads its top-level
`GOOSE_PROVIDER` / `GOOSE_MODEL` / `OPENAI_HOST` / `OLLAMA_HOST` scalars, with
process env vars overriding the file (goose's own precedence). Flip
`GOOSE_PROVIDER` between `openai` and `ollama`, or change `GOOSE_MODEL`, and
the UI follows within one poll — no restart, and no way for the panel to show
a model goose isn't using.

Both servers also read **`config.json`** in this directory (override the path
with `GOOSE_WEB_CONFIG`) for the server knobs and the health-panel layout:

```json
{
  "host": "0.0.0.0", "port": 8799, "token": "", "workspace": "../workspace",
  "max_turns": 50, "timeout_seconds": 1800, "goose_bin": "",
  "max_upload_mb": 25, "uploads_subdir": "uploads",
  "model": "qwen-3.6-chat",
  "provider_labels": { "openai": "vLLM (OpenAI-compat)", "ollama": "Ollama" },
  "backends": [
    { "name": "vLLM chat",  "url": "http://100.88.242.174:8000",  "health_path": "/v1/models", "role": "chat"  },
    { "name": "CPU Qwen3 embed", "url": "http://127.0.0.1:8001", "health_path": "/health", "role": "embed" },
    { "name": "Ollama",     "url": "http://100.88.242.174:11434", "health_path": "/api/tags",  "role": "ollama" }
  ]
}
```
- `backends` defines the health-panel **rows** (name / `health_path` / `role`
  + fallback `url`). Rows with role `chat` / `ollama` get their URL overridden
  by the live `OPENAI_HOST` / `OLLAMA_HOST`, and the row matching the live
  `GOOSE_PROVIDER` is badged **in use**. Rows with other roles (e.g. `embed`,
  which goose's config doesn't describe) keep their `url` as configured.
- `model` is a last-resort fallback shown only if `config.yaml` is unreadable.
- `provider_labels` maps a goose provider id to the display label (the legacy
  single `provider_label` key is still honored as the `openai` label).
- Any `GOOSE_WEB_*` environment variable overrides the matching `config.json`
  value.

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
| `GOOSE_WEB_MODEL` | _(none)_ | overrides the live model name shown in the UI |
| `GOOSE_WEB_CONFIG` | `./config.json` | path to the web config file |
| `GOOSE_CONFIG` | OS default | goose's `config.yaml` used for the live model/provider display + MCP tool discovery |
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

## Enable/disable MCPs from the UI

Each **local Windows MCP** card (all 14 loopback `streamable_http` servers, ports
8777–8790, including `dtmsdk` and `obsidian`) has an on/off switch. Flipping it sets that
extension's `enabled:` flag in goose's live `config.yaml` and takes effect on your **next
message** — no restart. It is
a config-level switch (whether goose loads the extension); it does **not** start or stop
the backend MCP server process.

- Only loopback `streamable_http` MCPs are togglable. `developer`/`memory`/
  `computercontroller` (builtin) and `dtm` (remote) have no switch and are refused
  server-side.
- `POST /api/extensions/toggle` `{ "id": "<ext>", "enabled": <bool> }`, token-gated like
  `/api/chat`.
- The first edit backs up `config.yaml` to `config.yaml.bak-webtoggle` (once). Writes are
  atomic and honor a read-only durability guard on the config file.

## Schedules (`/api/schedules`)
The sidebar shows a live summary of the schedules registered with the `scheduler` MCP
(`mcp/scheduler/`, port 8793 — see [`../mcp/README.md`](../mcp/README.md)):
a count, and per-schedule rows (enabled dot, name, cadence label). Switching to the
in-app **Schedules** view swaps the transcript/composer for a table (next run, last
status, an enable/disable checkbox, and ▶ run-now / ✎ edit / 🗑 delete / ⧗ history
row actions) plus a create/edit drawer for name/kind (`cron` expr or `at` ISO
datetime)/session/prompt/mode.

- `GET /api/schedules` — proxies `sched_list`; returns `{ok, schedules}`.
- `POST /api/schedules` — `{action, ...}` where `action` is one of `create`, `update`,
  `delete`, `toggle`, `run-now`, `history`, each mapped to the matching `sched_*` MCP
  tool (`toggle` calls `sched_resume`/`sched_pause` depending on `enabled`).

Both endpoints reach the scheduler the same way every other local MCP is reached —
`tools/call` over `streamable_http` — not a database or file shared with `scheduler`.
The scheduler's mutating tools (`sched_create`/`sched_update`/`sched_delete`/
`sched_pause`/`sched_resume`/`sched_run_now`) are confirm-token gated to protect the
chat-agent path; `Invoke-SchedulerTool` in `server.ps1` auto-completes that
preview→confirm two-step (call once, and if the result carries a `confirm_token`,
immediately replay the same call with it attached) because the UI click that triggered
the request already **is** the human confirmation — the same pattern used for the
per-MCP toggle above. `sched_run_now` fires the schedule on a background thread and
returns immediately, so "Run now" doesn't wait for the (possibly minutes-long) goose
run to finish. As with the extension toggle, this is currently implemented in
`server.ps1` only (`server.py` does not yet expose `/api/schedules`).

## Agent Profiles (`/api/profiles`)

The sidebar shows a **Profile** dropdown (role switcher) listing 6 presets from
`config/profiles.json` — each profile scopes which MCPs goose **sees** when that
profile is active (e.g. `perf` shows only performance-related MCPs; `sec` shows
security-forensic MCPs; `diag` shows all). Profiles are the source of truth for agent
focus; servers and watchdog are untouched.

- `GET /api/profiles` — returns `{ok, profiles, active}`:
  - `profiles[]` — array of `{name, label, description, enable[]}` — 6 presets from
    `config/profiles.json`.
  - `active` — current profile name (string), or `"custom"` if the live
    `config.yaml` has been hand-edited or settings don't match any preset.
- `POST /api/profiles` — `{action: "apply", name}` applies a profile: validates
  against `config/profiles.json`, backs up `config.yaml` to
  `config.yaml.bak-profile`, writes the new extension set, refreshes the live goose
  state, and returns `{ok, name, changed: [ids], warnings: []}` (`changed` lists the
  extensions actually flipped; `warnings` collects non-fatal skips, e.g. an
  extension absent from this config or a `.goosehints` write failure). Token-gated
  like `/api/chat`.
  - **Builtin-flip policy:** The profile POST endpoint is the **only path** where the
    `builtin` (`developer` + `memory`) MCPs can toggle; the per-MCP toggle switch
    (`POST /api/extensions/toggle`) still rejects builtin with `403` if you try to
    flip it directly.
  - Errors: an unknown profile name or a malformed request returns HTTP `400` with
    `{error}` — validated **before any write**, so `config.yaml` is untouched on
    failure. An unreadable `config/profiles.json` returns `500`.

**Sidebar switcher:**
The **Profile** card is a `<select>` dropdown listing the active and available profile
labels. A badge shows the active profile's MCP count. Below the dropdown, the active
profile's full description is displayed. When active is `custom` (live config doesn't
match any preset), an extra "自訂" option appears. Selecting a profile triggers the
apply endpoint and refreshes the UI and health status.

**`.goosehints` generation:**
After a profile is successfully applied, the server regenerates
`workspace/.goosehints` with a **header line** `# profile: <name>` (e.g.
`# profile: perf`), then the full recipe Markdown (e.g. from
`config/recipes/perf.md`). The file carries a do-not-edit comment warning; reapply the
profile (click the dropdown, select again) to update `.goosehints` if the recipe
changes.

## Endpoints
- `GET /` — the chat page
- `GET /api/health` — **live model/provider from goose's `config.yaml`** (`model`, `provider`, `provider_name`) + backend status (`backends[].active` marks goose's current provider) + **live-discovered MCP extensions + tool list** (snapshot cache, version cached at startup)
- `POST /api/chat` — `{session, message, mode, attachments?}` → streamed NDJSON events
- `POST /api/upload?session=&name=` — raw file bytes in the body → saved to `workspace/uploads/<session>/`; returns `{ok, name, size}`
- `POST /api/extensions/toggle` — `{id, enabled}` → flips one MCP's `enabled:` in `config.yaml` (builtin MCPs refused with `403`)
- `GET /api/profiles` — list 6 presets + current active profile name
- `POST /api/profiles` — `{name}` applies a profile: validates, backs up, writes, refreshes (token-gated; builtin MCPs allowed here)
- `GET /api/schedules` — list schedules from the `scheduler` MCP
- `POST /api/schedules` — `{action, ...}` → create/update/delete/toggle/run-now/history against the `scheduler` MCP

## Text encoding (non-ASCII / CJK input)
Everything on the wire is **UTF-8**, and `server.ps1` decodes it as UTF-8 **explicitly** — see
[`http_encoding.ps1`](http_encoding.ps1).

This is not incidental. .NET's `HttpListenerRequest.ContentEncoding` falls back to
`Encoding.Default` — the machine's **ANSI codepage** (Big5, GBK, Shift-JIS, 1252…) — whenever the
request's `Content-Type` carries no `charset`, which is exactly what the browser sends for
`application/json`. On a Chinese/Japanese Windows, a CJK chat message would be decoded as Big5/GBK
and reach `goose` as mojibake. `HttpListenerRequest.QueryString` has the same flaw (it %-decodes
using `ContentEncoding`), which mangled non-ASCII **upload filenames** too. Both are fixed by never
consulting `ContentEncoding`: JSON is UTF-8 by definition (RFC 8259 §8.1) and URI percent-escapes
are UTF-8 by definition (RFC 3986 §2.5).

`server.py` is correct by construction — `json.loads()` on `bytes` auto-detects UTF-8 and `parse_qs`
defaults to it — so this only ever affected the PowerShell backend. Regression tests:
`tests/test_encoding_ps.py`.

> Related trap when editing the PowerShell: **PowerShell 5.1 reads a BOM-less `.ps1` as ANSI**, so a
> literal CJK string in the source is corrupted at *parse* time. That's why `server.ps1` builds its
> Chinese from code points (`-join (@(0x9644,0x52A0,…) | %{[char]$_})`) instead of writing it inline.

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

## Running as a Scheduled Task (auto-start, easy restart)

Instead of launching `serve_web.ps1` by hand, register goose_web as a Windows Scheduled Task so it
starts at logon and is managed like the MCP servers:

    # elevated PowerShell (binding all interfaces on :8799 needs admin)
    .\install_web_task.ps1                # registers 'GooseWeb' (RunLevel Highest, AtLogOn); stops any manual instance
    Start-ScheduledTask -TaskName GooseWeb

Restart / stop / remove:

    Stop-ScheduledTask -TaskName GooseWeb; Start-ScheduledTask -TaskName GooseWeb   # restart
    Stop-ScheduledTask -TaskName GooseWeb                                           # stop
    .\uninstall_web_task.ps1                                                        # remove the task

Output (banner, request logs, errors) is redirected to `logs/goose_web.log`. The task binds `:8799`
on all interfaces (LAN / Meshnet), so keep it behind a token (`config.json`) or a scoped firewall
rule — `GOOSE_MODE=auto` lets a connected client run shell commands via the agent.
