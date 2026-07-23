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

## Agent profiles — role-scoped MCP access

`config/profiles.json` defines 6 profiles (e.g. `perf`, `sec`, `dtm`, `docs`, `ops`,
`diag`), each specifying which MCPs are visible to goose for that profile. Recipes in
`config/recipes/*.md` provide the system prompt per profile. Profiles **scope what goose
SEES**, not what the servers do — servers and watchdog are untouched. Apply a profile
via goose_web's **Profile** dropdown (sidebar); on apply, `config.yaml` extensions are
rewritten and `.goosehints` is regenerated with a `# profile: <name>` header + the
recipe. See [`../goose_web/README.md`](../goose_web/README.md) § Agent Profiles.

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

### DTM Download MCP (`dtm_download/`, 127.0.0.1:8791) — UNELEVATED

`dtm_download` downloads DTP build artifacts (installer MSI, sample zips, datatype CSVs, doc HTML)
from Artifactory. Pure-Python (`requests` + stdlib `zipfile`/`hashlib`; no `.ps1` dependency),
reimplementing the download half of `ccp/tools/DTMTransmissionAutoTest-/Install-DTP.ps1`. It only
writes into its own configured `download_path`, so it runs **UNELEVATED** (`RunLevel Limited`, like
`obsidian`). The Artifactory bearer token is env-only (`DTM_DOWNLOAD_ARTIFACTORY_TOKEN`) -- never a
config.json value or a tool argument. Tools: `dtm_download_build`, `dtm_list_builds`,
`dtm_download_health`. See [`dtm_download/README.md`](dtm_download/README.md).

### DTM Deploy MCP (`dtm_deploy/`, 127.0.0.1:8792) — NOT read-only

`dtm_deploy` wraps the elevated half of `Run-DTPSetup.ps1`: uninstall, enable-user-consent,
insert-test-plugin, install, enable-transmission, plus verify-collection/verify-heartbeat. Pure-Python
(COM `WindowsInstaller.Installer` via `win32com` for MSI property reads + `msiexec.exe` via
`subprocess` for the actual install/uninstall action, `winreg` for the registry writes, `DTMUtil.exe`
via `subprocess` for transmission). Takes the `msi_path` produced by `dtm_download`'s
`dtm_download_build` as a plain tool argument -- the two servers share no state. Every
system-mutating tool (`dtm_uninstall`, `dtm_enable_user_consent`, `dtm_insert_test_plugin`,
`dtm_install`, `dtm_enable_transmission`, `dtm_run_pipeline`) is confirm-token gated exactly like
`dtmsdk`; `dtm_verify_collection`/`dtm_verify_heartbeat`/`dtm_deploy_health` are safe and run
directly. Requires Administrator (`RunLevel Highest`). See
[`dtm_deploy/README.md`](dtm_deploy/README.md).

### Scheduler MCP (`scheduler/`, 127.0.0.1:8793) — UNELEVATED

`scheduler` fires headless `goose run` agent tasks on cron/one-shot (`at`) schedules, independently of
goose_web — a background Ticker thread checks for due jobs and spawns them on a daemon thread so a
long-running agent turn never blocks the tick loop or a tool call. Pure-Python (stdlib + `mcp`; no extra
deps beyond the other 16 modules). Mutating tools (`sched_create`, `sched_update`, `sched_delete`,
`sched_pause`, `sched_resume`, `sched_run_now`) are confirm-token gated exactly like `dtmsdk`/`dtm_deploy`;
`sched_list`, `sched_get`, `sched_history`, and `scheduler_health` are direct read tools. It only writes
its own schedule store and run logs, so it runs **UNELEVATED** (`RunLevel Limited`, like `obsidian` and
`dtm_download`). See [`scheduler/README.md`](scheduler/README.md).

### Windows Audio MCP (`windows_audio/`, 127.0.0.1:8796) — 18th canonical entry

`audio` is the 18th and final entry in `config/mcp_servers.json` (registered after `scheduler`), which
makes the canonical port set **non-contiguous**: `{8777-8793} ∪ {8796}` — ports 8794/8795 fall in that
gap but belong to the manifest-external `markitdown`/`docstruct` servers below, not the canonical suite.

### MarkItDown MCP (`markitdown/`, 127.0.0.1:8794) — manifest-external, UNELEVATED

`markitdown` wraps the official Microsoft `markitdown-mcp` package to convert documents (PDF, Office,
images, audio, HTML, CSV, ZIP, YouTube, EPub) to Markdown. It is **manifest-external** — not included in
`setup_mcp_servers.ps1`'s suite build — so one-time registration is via `mcp/markitdown/register_goose_extension.ps1`.
Runs **UNELEVATED** (`RunLevel Limited`), reads the requested resource (any user-readable file via
`file://`, or a fetched URL), and writes Markdown to the MCP response. Not covered by the suite
watchdog/batch test. See [`markitdown/README.md`](markitdown/README.md).

### DocStruct MCP (`docstruct/`, 127.0.0.1:8795) — manifest-external, UNELEVATED

`docstruct` provides OCR (RapidOCR) and structured document field extraction via a local vLLM. Tools:
`doc_to_text` (PDF text layer → RapidOCR fallback), `doc_extract` (schema or template-driven field
mapping), `docstruct_health` (LLM + OCR probe). It is **manifest-external** — not included in
`setup_mcp_servers.ps1`'s suite build — so one-time registration is via `mcp/docstruct/register_goose_extension.ps1`.
Runs **UNELEVATED** (`RunLevel Limited`), reads PDFs from the user's filesystem, and calls the local
vLLM endpoint for extraction. Not covered by the suite watchdog/batch test. See [`docstruct/README.md`](docstruct/README.md).

### Obsidian vault MCP (`windows_obsidian/`, 127.0.0.1:8790) — one of three Limited tasks

`obsidian` gives the harness file-level access to an Obsidian vault (read/search/list, wikilink &
backlink graph, tag & frontmatter queries) plus **confirmation-gated** create/update of markdown notes.
Filesystem-based (no Obsidian app/plugin needed); complementary to the `dtm` RAG (semantic) — this is
exact structured access. Its **scheduled/logon launches run unelevated** (`RunLevel Limited`) because it
only reads/writes user files. An immediate start by elevated suite setup inherits the setup process token
until Obsidian is restarted through its Scheduled Task or at the next logon. Every path is confined to the
vault's `.md` files (no traversal/symlink escape); there is no delete and no silent overwrite. Vault path lives in `windows_obsidian/config.json` (one-line redeploy). See
[`windows_obsidian/README.md`](windows_obsidian/README.md).

**One-click setup on a new machine** (elevated, idempotent — installs Python deps, registers + starts a
logon Scheduled Task per server, and registers each extension into goose's `config.yaml`):
```powershell
powershell -ExecutionPolicy Bypass -File .\..\setup_goose.ps1          # 1. goose itself + base config
powershell -ExecutionPolicy Bypass -File .\..\setup_mcp_servers.ps1    # 2. all 18 local MCPs (Administrator)
```
`setup_mcp_servers.ps1` flags: `-SkipDeps` (no `pip install`) · `-SkipTasks` (don't register Scheduled
Tasks) · `-NoStart` (register but don't launch) · `-SkipConfig` (leave `config.yaml` alone) ·
`-SkipSysmon` (don't install/refresh Sysmon) · `-ConfigPath <path>` (non-default goose config) ·
**`-Uninstall`** (stop the servers, unregister the Scheduled Tasks, and strip the extension blocks from
`config.yaml` — backed up to `config.yaml.bak-mcpuninstall` first; pip packages and Sysmon are left alone).

The suite installer and every standalone `install_task.ps1` create the same launch chain: an `AtLogOn`
Scheduled Task for the current user with `LogonType Interactive` starts
`../scripts/start_mcp_hidden.ps1` through PowerShell with `-WindowStyle Hidden`; the launcher then runs
the server's Python entry point. The setup script's immediate start uses that hidden launcher too. The
task principal and each existing run level are unchanged (`Highest`, except `obsidian`, `dtm_download`,
and `scheduler` are `Limited`).

The task run level applies when Task Scheduler launches the server. Because suite setup itself runs
elevated, its direct immediate start inherits the setup token; this means an immediately started Obsidian
process is elevated until it is restarted through its `RunLevel Limited` task or at the next logon.

Scheduled launches append separate streams to the repository's `logs/mcp/` directory:

- `logs/mcp/<name>.stdout.log`
- `logs/mcp/<name>.stderr.log`

If either active log is larger than 10 MiB when the launcher next starts, that file is moved to `.1`
(for example, `eventlog.stdout.log.1`) before output resumes. Rotation is independent for stdout and
stderr, and only one rotated generation is retained. There is no visible MCP console to inspect, so
tail the logs from the repository root when a task exits or a port does not come up:

```powershell
Get-Content .\logs\mcp\eventlog.stdout.log -Tail 50 -Wait
Get-Content .\logs\mcp\eventlog.stderr.log -Tail 50 -Wait
```

### Test all local MCP servers

Once the servers are running, test all 18 from a normal, **unelevated** PowerShell session:
```powershell
powershell -ExecutionPolicy Bypass -File .\..\test_mcp_servers.ps1
```
The test reads the shared [`../config/mcp_servers.json`](../config/mcp_servers.json) manifest and
uses the safe sequence `initialize` → `notifications/initialized` → `tools/list` → the manifest's
health `tools/call`; it never calls the diagnostic or confirmation-gated tools. Timestamped `.json`
and `.md` reports default to `../reports/mcp/`. Exit codes are `0` (all pass), `1` (one or more
transport, protocol, or tool-call failures), and `2` (invalid invocation/manifest or report error).
A successful health call can contain degraded data-source status; that payload is preserved and is
not the same as a failed transport or tool call. See the
[`module relationships`](../docs/MODULE_RELATIONSHIPS.md#2-repository-module-relationships).

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
   Scheduled Task: 15 use `RunLevel Highest`, while `obsidian`, `dtm_download`, and `scheduler` use
   `RunLevel Limited`. Registration requires an elevated shell; this says nothing about runtime.
2. **Fifteen servers are *started* elevated.** Their tasks trigger at **`-AtLogOn`** (not at boot) as
   **your own account** with `LogonType Interactive` and `RunLevel Highest`, so they come up silently (no
   UAC prompt or visible console) through the hidden PowerShell launcher when you log in. `obsidian`,
   `dtm_download`, and `scheduler` are the scheduled-task exceptions: they retain `RunLevel Limited` and
   start unelevated as the current user at logon. An immediate start by elevated suite setup inherits the
   elevated setup token until they are restarted through their task or at the next logon. A machine that
   boots but is never logged into runs none of them.
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
| `dtmsdk` (8789) | at runtime, yes | The DTP utils require Administrator. (The installer registers its task RunLevel Highest.) |
| `obsidian` (8790) | **never required** | Only reads/writes user files in the vault. Scheduled/logon launches use **RunLevel Limited** and are unelevated. An immediate start by elevated suite setup inherits the setup token until a task/logon restart. |
| `dtm_download` (8791) | **never required** | Only writes into its own download_path from Artifactory. Scheduled/logon launches use **RunLevel Limited** and are unelevated, same as `obsidian`. |
| `dtm_deploy` (8792) | at runtime, yes | msiexec/HKLM writes/service control all require Administrator. (The installer registers its task RunLevel Highest.) |
| `scheduler` (8793) | **never required** | Only writes its own schedule store and run logs; `goose run` for a schedule launches as the current user. Scheduled/logon launches use **RunLevel Limited** and are unelevated, same as `obsidian`/`dtm_download`. |

#### FAQ

**Q: Goose runs unelevated while 15 MCP tasks are Highest and Obsidian's/dtm_download's/scheduler's tasks are Limited. Can Goose call all of them?**
Yes. All 18 servers are separate processes reached over loopback HTTP (`streamable_http`), not in-process
libraries. Goose just sends an HTTP request to `127.0.0.1:87xx`. For the 15 elevated servers, a **TCP
socket has no UAC/UIPI boundary** (UIPI restricts window messages, not sockets), so an unelevated client
can connect normally. Obsidian's, dtm_download's, and scheduler's `RunLevel Limited` tasks also launch them unelevated as the current user.
The exception is the suite installer's direct immediate start: when setup is elevated, that child inherits
the elevated setup token until restarted through the Limited task or at the next logon. Keeping scheduled
elevation confined to the 15 `RunLevel Highest` tasks is the point of this architecture.

**Q: The Scheduled Tasks launch the servers with admin rights at system boot, right?**
Mostly wrong. Fifteen tasks run **elevated** (`RunLevel Highest`, and Scheduled Tasks elevate *silently*
— no UAC prompt); `obsidian`'s, `dtm_download`'s, and `scheduler`'s tasks remain `RunLevel Limited` and launch them unelevated. All run as
**your own user account** (not `SYSTEM`), and the trigger is **`-AtLogOn`, not `-AtStartup`**: they start
**when you log in**, not when the machine boots. A box that powers on and sits at the login screen is
running none of them.

This is deliberate. Switching to `-AtStartup` + `SYSTEM` would change what the servers *see*: `exec`'s
UserAssist and `drift`'s HKCU autoruns read the **current user's** registry hive, so under `SYSTEM` they
would report on SYSTEM's hive instead of yours.

> **Security note:** the servers bind `127.0.0.1` with **no authentication**. Any process on this box —
> including unprivileged ones — can call them. The twelve diagnostic servers are read-only, making their
> elevated data an information-disclosure concern. `dtmsdk`, `obsidian`, and `dtm_deploy` also expose
> write-capable tools behind per-command confirmation gates; those gates are part of their safety boundary, not a reason to
> treat the full suite as read-only. This loopback API is therefore appropriate only on a trusted,
> single-user diagnostic box; do not run it on a machine with untrusted local users.

**Turning servers on and off at runtime:** goose_web's sidebar has a per-MCP toggle for all 18 local MCPs
(it flips `enabled:` in `config.yaml`, which the next `goose run` re-reads — no restart). See
[`../goose_web/README.md`](../goose_web/README.md).

**How to actually use them** — [`../docs/DIAGNOSTIC_PLAYBOOK.md`](../docs/DIAGNOSTIC_PLAYBOOK.md) maps common
symptoms → which tool → the exact call → a ready-to-paste Goose prompt, plus the baseline/trend method
(現象 vs 故障) and cross-tool correlation workflows for root-causing.

**Sysmon** (separate, in [`../tools/sysmon/`](../tools/sysmon/)) enriches the `eventlog` MCP with process/
network/driver telemetry — install it manually (kernel driver + EULA); see its README.
