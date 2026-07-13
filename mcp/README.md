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

### DTM Sample/SDK Util MCP (`dtm_sdk/`, 127.0.0.1:8789) — NOT read-only

`dtmsdk` wraps the five DTP sample utilities (65 commands) plus the datatype tables and HowTo. **Unlike
the twelve diagnostic MCPs above, it is not read-only** — some commands transmit telemetry to Dell or
change DTP configuration, so every command outside a per-util safe allowlist requires a per-command
confirmation token. Requires Administrator and a running Dell TechHub service. Paths live in
`dtm_sdk/config.json` (one-line redeploy via `samples_root`). See [`dtm_sdk/README.md`](dtm_sdk/README.md).

**One-click setup on a new machine** (elevated, idempotent — installs Python deps, registers + starts a
logon Scheduled Task per server, and registers each extension into goose's `config.yaml`):
```powershell
powershell -ExecutionPolicy Bypass -File .\..\setup_goose.ps1          # 1. goose itself + base config
powershell -ExecutionPolicy Bypass -File .\..\setup_mcp_servers.ps1    # 2. all 12 diagnostic MCPs (Administrator)
```
`setup_mcp_servers.ps1` flags: `-SkipDeps` (no `pip install`) · `-SkipTasks` (don't register Scheduled
Tasks) · `-NoStart` (register but don't launch) · `-SkipConfig` (leave `config.yaml` alone) ·
`-SkipSysmon` (don't install/refresh Sysmon) · `-ConfigPath <path>` (non-default goose config) ·
**`-Uninstall`** (stop the servers, unregister the Scheduled Tasks, and strip the extension blocks from
`config.yaml` — backed up to `config.yaml.bak-mcpuninstall` first; pip packages and Sysmon are left alone).

By default the setup also installs **Sysmon** (Microsoft kernel driver + audit config from the committed
`../tools/sysmon/Sysmon.zip`) so the `eventlog` MCP can query `Microsoft-Windows-Sysmon/Operational`. This
is a security-relevant change that **accepts the Sysinternals EULA** — it runs only when you run the setup
script, and `-SkipSysmon` opts out. Idempotent: an already-installed Sysmon has its config refreshed, not
reinstalled. See [`../tools/sysmon/README.md`](../tools/sysmon/README.md).

Each server can also be started standalone (`windows_<name>\start_<name>_mcp.ps1`) or persisted/removed
individually via its own `install_task.ps1` / `uninstall_task.ps1`. The full extension block set is in
[`../config/windows_config.yaml`](../config/windows_config.yaml); the candidate roadmap + build status is
in [`../docs/windows-diagnostic-mcp-candidates.md`](../docs/windows-diagnostic-mcp-candidates.md).

### Privileges — what actually needs Administrator

Three separate things, often conflated:

1. **The installer needs admin.** `setup_mcp_servers.ps1` (and each `install_task.ps1`) registers a
   `RunLevel Highest` Scheduled Task, which requires an elevated shell. This says nothing about runtime.
2. **The servers are *started* elevated** — the task's trigger is **`-AtLogOn`** (not at boot) running as
   **your own account** with `RunLevel Highest`, so they come up silently (no UAC prompt) when you log in.
   A machine that boots but is never logged into runs none of them.
3. **Goose never needs admin.** It runs unelevated and reaches every server over loopback HTTP
   (`streamable_http`). A TCP socket has no UAC/UIPI boundary, so an unprivileged client talking to an
   elevated server is fine — that separation is the whole point of the design.

Not every server actually requires elevation. Each one gates only the *specific* tools whose data source
demands it, and returns a structured `{"error": ..., "is_admin": false}` (or a partial result) rather than
failing outright:

| MCP | Needs admin? | Why / what still works unelevated |
|---|---|---|
| `srum` | for SRUM history | `SRUDB.dat` is SYSTEM-locked (copied via `esentutl /vss`). `live_snapshot` / `top_processes` work as a normal user. |
| `eventlog` | for the Security log | `user_activity` reads Security. System/Application queries (`query_events`, `error_summary`) work as a normal user. |
| `crash` | for kernel dumps | `C:\Windows\Minidump` / `MEMORY.DMP` need admin. WER app-crash tools work as a normal user. |
| `exec` | for Prefetch/BAM/ShimCache | Those live in `C:\Windows\Prefetch` and the SYSTEM hive. `userassist_list` (HKCU) works as a normal user. |
| `disk` | for the USN journal | Needs a raw volume handle (`\\.\C:`). `disk_health` (SMART) and `volume_state` work as a normal user. |
| `filterstack` | for minifilters | `fltmc.exe` requires admin. `network_filters` (NDIS/Winsock) works as a normal user. |
| `memstate` | mostly no | Pool-tag enumeration works unelevated; only memory-list composition wants `SeProfileSingleProcessPrivilege`. |
| `procinspect` | no | psutil / Restart Manager / wait-chain all work unelevated; unqueryable threads are reported, not fatal. |
| `netconn` · `perfmon` · `drift` · `winupdate` | **no** | No admin gate anywhere in their code. |

#### FAQ

**Q: Goose runs unelevated but the MCP servers run elevated. Does that break Goose's ability to call them?**
No. The servers are separate processes reached over loopback HTTP (`streamable_http`), not in-process
libraries. Goose just sends an HTTP request to `127.0.0.1:87xx` — and a **TCP socket has no UAC/UIPI
boundary** (UIPI restricts window messages, not sockets). An unprivileged client connecting to a port
opened by an elevated process is completely normal. Keeping the elevation on the server side, so the agent
never needs it, is the *point* of this architecture.

**Q: The Scheduled Tasks launch the servers with admin rights at system boot, right?**
Half right. They run **elevated** (`RunLevel Highest`, and Scheduled Tasks elevate *silently* — no UAC
prompt), as **your own user account** (not `SYSTEM`), so your account must be in Administrators. But the
trigger is **`-AtLogOn`, not `-AtStartup`**: they start **when you log in**, not when the machine boots. A
box that powers on and sits at the login screen is running none of them.

This is deliberate. Switching to `-AtStartup` + `SYSTEM` would change what the servers *see*: `exec`'s
UserAssist and `drift`'s HKCU autoruns read the **current user's** registry hive, so under `SYSTEM` they
would report on SYSTEM's hive instead of yours.

> **Security note:** the servers bind `127.0.0.1` with **no authentication**. Any process on this box —
> including unprivileged ones — can call them and read data that would normally require admin. Everything
> is read-only, so this is information disclosure, not privilege escalation, but it is effectively a
> UAC-free read-only window onto admin-level data. Fine for a single-user diagnostic box; do not run this
> suite on a machine with untrusted local users.

**Turning servers on and off at runtime:** goose_web's sidebar has a per-MCP toggle for exactly these 12
(it flips `enabled:` in `config.yaml`, which the next `goose run` re-reads — no restart). See
[`../goose_web/README.md`](../goose_web/README.md).

**How to actually use them** — [`../docs/DIAGNOSTIC_PLAYBOOK.md`](../docs/DIAGNOSTIC_PLAYBOOK.md) maps common
symptoms → which tool → the exact call → a ready-to-paste Goose prompt, plus the baseline/trend method
(現象 vs 故障) and cross-tool correlation workflows for root-causing.

**Sysmon** (separate, in [`../tools/sysmon/`](../tools/sysmon/)) enriches the `eventlog` MCP with process/
network/driver telemetry — install it manually (kernel driver + EULA); see its README.
