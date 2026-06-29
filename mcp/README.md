# HarnessAgent `mcp/` — MCP servers + enable scripts

Launchers and enable scripts for the MCP servers wired into the Goose harness.
Each server is reached either as a **stdio** extension (Goose spawns it on demand)
or **streamable_http** (Goose connects to an mcp-proxy over HTTP).

> The live Goose config (`~/.config/goose/config.yaml`) is kept **read-only** (the
> self-strip guard — see `../docs/install_results.md`). The `enable_*` scripts here
> briefly unlock it, insert the extension, re-lock it, refresh the `.bak`, validate
> with `goose info -v`, and auto-restore on failure. The versioned template is
> `../config/goose_config.yaml`.

## PersonalKnowledge KB — `pk`
Stateless semantic retrieval over the `pk_*` ChromaDB collections.
Tools: `search_kb`, `get_document`, `list_sources` (serverInfo `personal-kb`).

| File | Purpose |
|---|---|
| `qb10_pk_mcp.sh` | stdio launcher (`venv/bin/python kb_query.py --mcp-mode`, cwd = PK root) |
| `enable_pk_mcp.sh` | add the `pk` extension to the live Goose config (idempotent, validated) |

```bash
./enable_pk_mcp.sh                                  # enable pk over stdio (default)
PK_MCP_URI=http://127.0.0.1:8766/mcp ./enable_pk_mcp.sh   # use streamable_http instead
```
PK is fast (first call = one embedding on vLLM `:8001`), so **stdio** needs no warm
proxy. For `streamable_http`, run PersonalKnowledge's `scripts/run_pk_mcp_proxy.sh`
(or install `pk-mcp-proxy.service`) on `:8766`, then enable with `PK_MCP_URI`.

## DTM Knowledge Agent — `dtm`
Telemetry/triage/plugin/hw-spec RAG. Warms a reranker + routing centroids, so a
warm HTTP proxy beats per-call stdio (~110s vs ~167s cold) — hence the default is
`streamable_http`, the opposite of `pk`.

| File | Purpose |
|---|---|
| `qb10_dtm_mcp.sh` | stdio launcher (`venv/bin/python -m dtm_agent mcp`, cwd = PK root) |
| `enable_dtm_mcp.sh` | add / re-point the `dtm` extension in the live Goose config (idempotent, validated) |

```bash
./enable_dtm_mcp.sh                                    # streamable_http -> :8765/mcp (default)
DTM_MCP_STDIO=1 ./enable_dtm_mcp.sh                    # self-contained stdio instead
DTM_MCP_REPLACE=1 DTM_MCP_STDIO=1 ./enable_dtm_mcp.sh  # switch an already-enabled dtm's transport
```
Default `streamable_http` (`:8765/mcp`) needs the always-on `dtm-mcp-proxy` system
service up (`PersonalKnowledge/dtm_agent/dtm-mcp-proxy.service`). See
`../docs/install_results.md`.

## Windows MCP servers — `windows_srum/`, `windows_eventlog/`
Windows-only servers (SRUM live-metrics, Event Log) with `start_*.ps1` launchers
(run elevated). Reached from Goose over `streamable_http` to their local ports.
