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

## Run it
```bash
./serve_web.sh                              # 0.0.0.0:8799  (LAN)
GOOSE_WEB_TOKEN=secret ./serve_web.sh       # require ?token / X-Goose-Token  (recommended on LAN)
GOOSE_WEB_HOST=127.0.0.1 ./serve_web.sh     # local-only
GOOSE_WEB_PORT=9000 ./serve_web.sh
```
Then open `http://<gb10-ip>:8799` (this box is `192.168.86.44`).

| Env | Default | Purpose |
|---|---|---|
| `GOOSE_WEB_HOST` | `0.0.0.0` | bind address |
| `GOOSE_WEB_PORT` | `8799` | port (8765 is used by another service on this box) |
| `GOOSE_WEB_TOKEN` | _(none)_ | if set, `/api/chat` requires the token |
| `GOOSE_WEB_WORKSPACE` | `../workspace` | agent working dir (where `developer` writes files) |
| `GOOSE_WEB_MAXTURNS` | `50` | `--max-turns` per turn |
| `GOOSE_BIN` | auto | path to the goose binary |

## Endpoints
- `GET /` — the chat page
- `GET /api/health` — model + backend status + tool list (version cached at startup)
- `POST /api/chat` — `{session, message, mode}` → streamed NDJSON events

## ⚠ Security
With `GOOSE_MODE=auto`, the agent auto-runs shell/file tools on this box. Bound to
`0.0.0.0` that means **anyone who can reach the port can run commands here**. On a
shared network set `GOOSE_WEB_TOKEN`, or bind to `127.0.0.1`. The server prints a
loud warning when it binds publicly without a token.

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
