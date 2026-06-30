# Running the Goose Harness Agent (with DTM Knowledge Agent MCP)

The DTM agent is wired into Goose as the `dtm` extension. By default it is
`type: streamable_http` pointing at `http://127.0.0.1:8765/mcp`, so it needs the
**`dtm-mcp-proxy` systemd unit** (binds `:8765`) to be up — it is *not* a Goose
subprocess. The canonical stdio launcher is `mcp/qb10_dtm_mcp.sh` (runs
`python -m dtm_agent mcp` from the GB10-workspace/dtm-agent venv); use it as a
self-contained, proxy-free alternative via `DTM_MCP_STDIO=1 mcp/enable_dtm_mcp.sh`.

## 1. Preflight (optional) — confirm the backends are up
```bash
curl -sf http://192.168.86.44:8000/v1/models >/dev/null && echo "vLLM chat  :8000 OK"   # generation (qwen-3.6-chat)
curl -sf http://localhost:8001/v1/models     >/dev/null && echo "vLLM embed :8001 OK"   # DTM retrieval (qwen-3-4b-embed)
```
If a backend is down:
```bash
cd ~/Downloads/PersonalKnowledge && docker compose up -d   # starts qwen-chat (:8000) + qwen-embed (:8001)
```

## 2. Launch the harness
Interactive REPL (run in your own terminal):
```bash
goose session
```
Headless / scripted (auto-approves tool calls):
```bash
GOOSE_MODE=auto goose run --no-session -t "your task here"
```
> If `goose` is not found: `export PATH="$HOME/.local/bin:$PATH"` (add to `~/.bashrc` to persist).

## 3. Use the DTM tools
Available tools: `dtm_query` · `dtm_telemetry_lookup` · `dtm_triage` ·
`dtm_data_feature` · `dtm_hw_spec` · `dtm_health`

```bash
# auto-routing front door
GOOSE_MODE=auto goose run --no-session -t "Use dtm_query: customer laptop runs hot and battery drains fast"

# specific specialists
GOOSE_MODE=auto goose run --no-session -t "Use dtm_telemetry_lookup to find datatypes for SSD wear and NVMe SMART health"
GOOSE_MODE=auto goose run --no-session -t "Use dtm_triage for: BSOD WHEA_UNCORRECTABLE_ERROR after dock hotplug"
GOOSE_MODE=auto goose run --no-session -t "Use dtm_hw_spec to look up SMBIOS Type 17 memory fields"

# inside an interactive `goose session`, just type:
#   use dtm_health to check the DTM agent
```

## 4. Remote web UI (use it from a browser)
For remote use, run the bundled web front end and open it from any machine on the LAN:
```bash
cd ~/Downloads/HarnessAgent/goose_web
./serve_web.sh                            # binds 0.0.0.0:8799
# recommended on a shared network — require a token:
GOOSE_WEB_TOKEN=pick-a-secret ./serve_web.sh
```
Then browse to **`http://192.168.86.44:8799`** (this box's LAN IP). The page streams
responses and renders tool-call cards; the same `developer` / `memory` / `dtm` tools are
available, and files the agent creates land in `HarnessAgent/workspace/`. The UI also
supports **attaching files** (`POST /api/upload`), saved under
`workspace/uploads/<session>/` and capped at `GOOSE_WEB_MAX_UPLOAD_MB` (default 25)
per file; the agent then reads them with its own tools.

> ⚠ With `GOOSE_MODE=auto` the agent runs shell/file commands on this box. Bound to
> `0.0.0.0`, anyone who can reach the port can do so — set `GOOSE_WEB_TOKEN` or bind
> `GOOSE_WEB_HOST=127.0.0.1`. See [`goose_web/README.md`](goose_web/README.md).

## 5. Remote DTM access from another machine (SSE / streamable HTTP)
The DTM agent's stdio MCP server can be exposed over the network with
[`mcp-proxy`](https://github.com/sparfenyuk/mcp-proxy) (already installed in the
PersonalKnowledge venv). It serves **both** transports on `:8765`:

| Transport | URL (from a remote host) |
|---|---|
| Streamable HTTP | `http://192.168.86.44:8765/mcp` |
| SSE | `http://192.168.86.44:8765/sse` |

**Start the proxy** (on this GB10 box):
```bash
cd ~/Downloads/PersonalKnowledge
./dtm_agent/run_mcp_proxy.sh                 # binds 0.0.0.0:8765
```
For an always-on service (survives reboot/logout) install the bundled unit — **this
needs sudo, so it's a step only you can run**:
```bash
sudo cp dtm_agent/dtm-mcp-proxy.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now dtm-mcp-proxy
```

**Connect a remote Goose** — in that machine's `~/.config/goose/config.yaml`:
```yaml
extensions:
  dtm:
    type: streamable_http        # NOT sse — goose 1.39 rejects sse ("migrate to streamable_http")
    name: dtm
    uri: http://192.168.86.44:8765/mcp
    enabled: true
    timeout: 600
```
> Verified 2026-06-28: a remote-style `streamable_http` connection drives the real DTM
> tools (`▸ dtm_telemetry_lookup dtm`, KB-grounded). The `/sse` endpoint also works for
> non-Goose MCP clients (Claude Desktop, custom). The local harness on this box now
> **defaults to `streamable_http` against the local `:8765` proxy** (a warm proxy beats
> per-call stdio) — so keep `dtm-mcp-proxy` up. Stdio remains available via
> `DTM_MCP_STDIO=1 mcp/enable_dtm_mcp.sh` (no proxy/port/sudo).

> ⚠ The proxy binds `0.0.0.0` with **no authentication** (same as the web UI on :8799).
> Only expose it on a trusted LAN/VPN, or front it with a reverse proxy that adds auth/TLS.

## 6. PersonalKnowledge (PK) KB MCP
A second knowledge extension, `pk`, exposes the PersonalKnowledge KB (semantic search
over the indexed Outlook/Jira/Confluence/OneNote/markdown sources). Tools:
`search_kb` · `get_document` · `list_sources`. Enable it with:
```bash
cd ~/Downloads/HarnessAgent/mcp
./enable_pk_mcp.sh                                   # stdio default (mcp/qb10_pk_mcp.sh; no proxy)
PK_MCP_URI=http://127.0.0.1:8766/mcp ./enable_pk_mcp.sh   # streamable_http (needs pk-mcp-proxy :8766)
```
PK is stateless retrieval (one embedding call, no rerank/LLM), so stdio is a good fit;
the `pk-mcp-proxy` systemd unit serves the streamable_http transport on `:8766`.

## Notes
- A successful DTM call shows a `▸ dtm_telemetry_lookup dtm` block in the output —
  that confirms it routed through the DTM RAG, not the base `qwen-3.6-chat` model.
- The **first** DTM query after a fresh start is slow (~20 s routing-centroid warmup
  + embedding + specialist LLM). The `dtm` extension timeout is set to 600 s to absorb this.
- To run **without** DTM: set `enabled: false` under the `dtm:` extension in
  `~/.config/goose/config.yaml` (or `goose configure` → Toggle Extensions).
- Switch model backend: edit `~/.config/goose/config.yaml` — `GOOSE_PROVIDER: openai`
  (vLLM, default) or `ollama` (fallback, `qwen3.5:9b`).

## What this depends on (see README "Files needed")
- Goose binary `~/.local/bin/goose` + config `~/.config/goose/config.yaml`
- `mcp/qb10_dtm_mcp.sh` (canonical DTM stdio launcher) + `mcp/enable_dtm_mcp.sh`;
  for streamable_http, the `dtm-mcp-proxy` systemd unit on `:8765`
- `mcp/qb10_pk_mcp.sh` + `mcp/enable_pk_mcp.sh` (PK KB); `pk-mcp-proxy` unit on `:8766`
- GB10-workspace/dtm-agent: `venv/`, `dtm_agent/`, `config.yaml` (dtm_agent section),
  `chromadb/` (built index), `DTMKnowledge/` (source KB)
- Model services: vLLM `:8000` + `:8001` (Ollama `:11434` fallback)
