# HarnessAgent `mcp/` — MCP servers + enable scripts

Launchers and enable scripts for the MCP servers wired into the Goose harness.
Each server is reached either as a **stdio** extension (Goose spawns it on demand)
or **streamable_http** (Goose connects to an mcp-proxy over HTTP).

> The live Goose config (`~/.config/goose/config.yaml`) is kept **read-only** (the
> self-strip guard — see `../docs/install_results.md`). The `enable_*` scripts here
> briefly unlock it, insert the extension, re-lock it, refresh the `.bak`, validate
> with `goose info -v`, and auto-restore on failure. The versioned template is
> `../config/goose_config.yaml`.
>
> **Privacy:** the `enable_*` scripts export `GOOSE_TELEMETRY_ENABLED=false` before invoking
> goose, and the configs set it false — goose uploads no usage telemetry. See the repo
> `README.md` → "Privacy / telemetry".

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
PK_MCP_REPLACE=1 PK_MCP_URI=http://127.0.0.1:8766/mcp ./enable_pk_mcp.sh   # re-point an already-enabled pk (stdio<->http)
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
DTM_MCP_URI=http://127.0.0.1:8765/mcp ./enable_dtm_mcp.sh  # override the streamable_http URI
DTM_MCP_STDIO=1 ./enable_dtm_mcp.sh                    # self-contained stdio instead
DTM_MCP_REPLACE=1 DTM_MCP_STDIO=1 ./enable_dtm_mcp.sh  # switch an already-enabled dtm's transport
```
Default `streamable_http` (`:8765/mcp`) needs the always-on `dtm-mcp-proxy` system
service up (`PersonalKnowledge/dtm_agent/dtm-mcp-proxy.service`). See
`../docs/install_results.md`.

## Windows diagnostic MCP suite (`windows_*/`, 127.0.0.1:8777–8788)
Twelve local, **read-only** Windows diagnostic MCP servers — a full "what's wrong with this box"
toolkit. Each is self-contained (`<name>_mcp_server.py` + reader modules, `DESIGN.md`, `README.md`,
`tests/`, `start_<name>_mcp.ps1`, `install_task.ps1`/`uninstall_task.ps1`), pure-stdlib/ctypes (no pip
beyond `mcp`/`psutil`/`pywin32`/`dissect.esedb`), and reached from Goose over `streamable_http`.
Most run **elevated** (they read SYSTEM-hive / kernel data); the launchers self-check admin.

| MCP | Port | What it diagnoses |
|---|---|---|
| [`windows_srum`](windows_srum/) | 8777 | SRUM historical per-app CPU/net/energy + live resource snapshot |
| [`windows_eventlog`](windows_eventlog/) | 8778 | Event Log: system errors + user activity (feeds Sysmon telemetry) |
| [`windows_crash`](windows_crash/) | 8779 | WER app crashes/hangs + BSOD dump bugcheck decode |
| [`windows_exec`](windows_exec/) | 8780 | Execution evidence: Prefetch/BAM/UserAssist/ShimCache + timeline |
| [`windows_drift`](windows_drift/) | 8781 | Config drift: autoruns/services/programs/tasks snapshots + diff |
| [`windows_netconn`](windows_netconn/) | 8782 | Live connections + owning process/service + baseline diff |
| [`windows_perfmon`](windows_perfmon/) | 8783 | Real-time PDH counters (disk latency, pool, paging) + baselines |
| [`windows_disk`](windows_disk/) | 8784 | USN file-change journal + SMART health + volume state |
| [`windows_procinspect`](windows_procinspect/) | 8785 | Who-locks-a-file, hang/deadlock wait chains, handle-leak view |
| [`windows_memstate`](windows_memstate/) | 8786 | Pool-tag memory attribution (poolmon) + tag→driver + RamMap |
| [`windows_filterstack`](windows_filterstack/) | 8787 | Filesystem minifilters (AV/VPN) + NDIS/Winsock network filters |
| [`windows_winupdate`](windows_winupdate/) | 8788 | Windows Update history + failure HRESULTs + pending-reboot state |

**One-click setup on a new machine** (elevated, idempotent — installs Python deps, registers + starts a
logon Scheduled Task per server, and registers each extension into goose's `config.yaml`):
```powershell
powershell -ExecutionPolicy Bypass -File .\..\setup_goose.ps1          # 1. goose itself + base config
powershell -ExecutionPolicy Bypass -File .\..\setup_mcp_servers.ps1    # 2. all 12 diagnostic MCPs (Administrator)
```
Each server can also be started standalone (`windows_<name>\start_<name>_mcp.ps1`, elevated) or persisted
via its `install_task.ps1`. The full extension block set is in [`../config/windows_config.yaml`](../config/windows_config.yaml);
the candidate roadmap + build status is in [`../docs/windows-diagnostic-mcp-candidates.md`](../docs/windows-diagnostic-mcp-candidates.md).

**Sysmon** (separate, in [`../tools/sysmon/`](../tools/sysmon/)) enriches the `eventlog` MCP with process/
network/driver telemetry — install it manually (kernel driver + EULA); see its README.
