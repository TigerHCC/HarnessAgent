# HarnessAgent — Goose on Windows/Linux → GB10 models

A self-contained setup for the [Goose](https://github.com/aaif-goose/goose) agent harness
that runs on a client machine and uses the **GB10 model server** (`192.168.86.44`) as its
LLM backend. Goose is the execution/orchestration layer; models live on GB10.

> **Boundary:** everything here is independent of `PersonalKnowledge-GB10`, which is **never
> modified**. References to it are read-only.

Validated on the Windows 11 dev box on 2026-06-28, and re-validated the same day
**natively on the GB10 box itself** (aarch64 Linux) — where the model backend is
**local**, not remote. See [`docs/install_results.md`](docs/install_results.md).

---

## What you get
- Goose CLI installed locally (`~/.local/bin/goose`)
- `config.yaml` pointed at GB10, with **vLLM primary + Ollama fallback**
- Extensions: `developer` (shell/file tools), `memory` (example stdio MCP server),
  and `dtm` (PersonalKnowledge DTM Knowledge Agent — telemetry/triage/plugin/hw-spec
  RAG over ChromaDB). `dtm` connects over **streamable HTTP** (`http://127.0.0.1:8765/mcp`)
  to the mcp-proxy, kept alive by the enabled `dtm-mcp-proxy` **system service** (survives
  reboot); stdio `mcp/qb10_dtm_mcp.sh` is the no-dependency fallback. The DTM agent is also reachable
  **remotely** over the same proxy (streamable HTTP / SSE) — see [`RUN.md`](RUN.md) §5, and
  `pk` (PersonalKnowledge KB MCP — `search_kb` / `get_document` / `list_sources`) over
  **streamable HTTP** via the `pk-mcp-proxy` on `:8766`
- A tool-calling smoke test that proves it end-to-end
- A **remote web UI** ([`goose_web/`](goose_web/), served on `:8799`) — chat with the harness
  + DTM tools from any browser on the LAN, with streaming and rendered tool-call cards
- A **local MCP suite** (14 servers, `mcp/windows_*/` + `mcp/dtm_sdk/`, ports 8777–8790), one-click
  install (and `-Uninstall`) via [`setup_mcp_servers.ps1`](setup_mcp_servers.ps1):
  - **12 read-only Windows diagnostic servers** (8777–8788) — a full "what's wrong with this box"
    toolkit: SRUM, Event Log, crash/WER, execution evidence, config drift, live network, PDH perf,
    disk/USN, process inspection, memory attribution, filter stack, and Windows-Update history.
  - **`dtmsdk`** (8789) — wraps the DTP Sample/SDK utilities; **not read-only** (per-command confirmation-gated).
  - **`obsidian`** (8790) — file-level access to an Obsidian vault (read/search/link-graph + gated
    create/update); the only server that runs **unelevated**.

  See [`mcp/README.md`](mcp/README.md) — including [which of them actually need Administrator](mcp/README.md#privileges--what-actually-needs-administrator)
  — and the roadmap in [`docs/windows-diagnostic-mcp-candidates.md`](docs/windows-diagnostic-mcp-candidates.md).
  goose_web's sidebar can enable/disable each one at runtime, no restart.
- See [`RUN.md`](RUN.md) for how to launch the harness with DTM support

## Where to read next
| Doc | For |
|---|---|
| **this README** (below) | the short path: two commands, from clone to working |
| [`mcp/README.md`](mcp/README.md) | all 14 local MCPs — ports, privileges, install and batch-test instructions |
| [`docs/DIAGNOSTIC_PLAYBOOK.md`](docs/DIAGNOSTIC_PLAYBOOK.md) | how to actually *use* them: symptom → tool → prompt (Chinese) |
| [`docs/SETUP_GUIDE.md`](docs/SETUP_GUIDE.md) | long-form reference — the GB10/Linux side (vLLM, Ollama, DTM, PK) the one-click installers don't cover |
| [`RUN.md`](RUN.md) | launching the harness + goose_web with DTM/PK |

## Backends (on GB10)
| Backend | Endpoint | Model | Notes |
|---|---|---|---|
| **vLLM** (default) | `:8000` (OpenAI-compat) | `qwen-3.6-chat` | Fast (tool task ~8s); needs tool-parser flag (below) |
| vLLM embed | `:8001` (OpenAI-compat) | (embeddings) | Used by the `dtm`/`pk` RAG for embeddings |
| Ollama (fallback) | `:11434` | `qwen3.5:9b` | No GB10 flags; only 9b is fast enough for tools |

GB10 vLLM is deployed via [`config/docker-compose.yaml`](config/docker-compose.yaml). **Critical flag** for
Goose tool-calling (the `qwen-3.6-chat` model emits Qwen XML `<function=...>` format):

```
--enable-auto-tool-choice --tool-call-parser qwen3_coder
```
`hermes` (JSON format) does **not** work for this model. After model restart, `:8000` takes
~4 min to answer while the model loads.

---

## Set up on a NEW machine (one command)

### Windows
```powershell
# from the HarnessAgent folder (copy it to the new machine, or just the script)
powershell -ExecutionPolicy Bypass -File .\setup_goose.ps1
# options:
#   -Backend ollama            # use Ollama instead of vLLM
#   -Gb10Host 192.168.86.44    # different server IP
#   -SkipSmokeTest
```
The `-ExecutionPolicy Bypass` is only needed to run an unsigned local script; the script
itself makes no permanent policy change.

### Linux / macOS
```bash
chmod +x setup_goose.sh
./setup_goose.sh
# options via env vars:
#   BACKEND=ollama GB10_HOST=192.168.86.44 SKIP_SMOKE=1 ./setup_goose.sh
```

The script will: check GB10 connectivity → install Goose → write `config.yaml` →
run a headless tool-calling smoke test → print how to use it.

**Prerequisite:** the new machine must be on the same LAN as GB10 (reach `192.168.86.44`).

### Local MCP suite (optional, second step)
After `setup_goose.ps1`, install the 14 local MCP servers (elevated, idempotent — installs Python deps,
registers + starts a logon Scheduled Task per server, and adds each extension to `config.yaml`):
```powershell
powershell -ExecutionPolicy Bypass -File .\setup_mcp_servers.ps1
```
This also installs **Sysmon** (kernel driver + audit config, enriching the `eventlog` MCP) — a
security-relevant change that accepts the Sysinternals EULA; `-SkipSysmon` opts out. See
[`tools/sysmon/README.md`](tools/sysmon/README.md). Flags and per-server details: [`mcp/README.md`](mcp/README.md).

### Test all local MCP servers
Run the protocol-level test client from a normal, **unelevated** PowerShell session after the
servers are started:
```powershell
powershell -ExecutionPolicy Bypass -File .\test_mcp_servers.ps1
```
For every entry in [`config/mcp_servers.json`](config/mcp_servers.json), the client safely performs
`initialize` → `notifications/initialized` → `tools/list` → the declared health `tools/call`.
It does not invoke diagnostic or write-capable tools. Timestamped JSON and Markdown reports go to
`reports/mcp/` by default. Exit code `0` means all servers passed, `1` means one or more transport,
protocol, or tool-call checks failed, and `2` means the test could not run or write its reports.
A health payload may report a degraded data source while the MCP transport and health tool call
still pass; transport/protocol/tool-call failures are reported as failed stages. See the
[`mcp/` instructions](mcp/README.md#test-all-local-mcp-servers) and
[`module relationships`](docs/MODULE_RELATIONSHIPS.md#2-repository-module-relationships).

---

## Using Goose
```bash
# headless / scripted (auto-approves tool calls)
GOOSE_MODE=auto goose run --no-session -t "your task here"

# interactive REPL (run in your own terminal, not a background shell)
goose session

# switch model for one run
goose run --no-session --model qwen3.5:9b -t "..."
```

Config lives at:
- Windows: `%APPDATA%\Block\goose\config\config.yaml`
- Linux/macOS: `~/.config/goose/config.yaml`
- Versioned reference copy: [`config/goose_config.yaml`](config/goose_config.yaml)

## Switch backend
Edit `config.yaml`: set `GOOSE_PROVIDER` to `openai` (vLLM) or `ollama`, and the matching
`GOOSE_MODEL`. Both blocks are present (one commented).

## Files
| Path | Purpose |
|---|---|
| `setup_goose.ps1` / `setup_goose.sh` | one-click Goose installer for a new machine |
| `setup_mcp_servers.ps1` | one-click installer for the 14 local MCP servers + Sysmon (deps + tasks + config) |
| `mcp/` | MCP launchers + enable scripts: `qb10_dtm_mcp.sh`/`qb10_pk_mcp.sh` (stdio → GB10-workspace), `enable_dtm_mcp.sh`/`enable_pk_mcp.sh`, and the **14 local MCPs** — 12 `windows_*/` diagnostic (8777–8788), `dtm_sdk/` (8789), `windows_obsidian/` (8790) — see `mcp/README.md` |
| `tools/sysmon/` | Sysmon (committed `Sysmon.zip` + starter config); installed by `setup_mcp_servers.ps1` (enriches the `eventlog` MCP) |
| `docs/windows-diagnostic-mcp-candidates.md` | diagnostic-MCP roadmap + build status |
| `goose_web/` | remote web UI — `server.py` / `server.ps1` (stdlib HTTP→`goose run` bridge), `index.html` (chat page), `serve_web.sh`/`.ps1` (launchers) |
| `workspace/` | working dir for files the agent creates when driven from the web UI |
| `RUN.md` | how to launch the harness and use the DTM tools |
| `config/goose_config.yaml` | reference copy of the working config (incl. `dtm` extension) |
| `config/docker-compose.yaml` | GB10 vLLM deployment (chat + embed) |
| `docs/install_goose_harness_plan.md` | original plan |
| `docs/install_results.md` | install + smoke-test results, findings, rollback |

## Rollback
Delete `~/.local/bin/goose` and the goose config dir
(`%APPDATA%\Block\goose` on Windows, `~/.config/goose` on Linux/macOS). Nothing else is touched.

## Privacy / telemetry
**Telemetry is OFF by policy.** By default goose POSTs usage metadata (model, your extension
names, session names, token/session counts, settings) to a hosted PostHog endpoint
(`us.i.posthog.com`). This repo disables it everywhere: `GOOSE_TELEMETRY_ENABLED: false` in the
configs + templates + installers, **and** every script that runs goose exports
`GOOSE_TELEMETRY_ENABLED=false` (env overrides config). Your prompts/responses always stay on
your configured provider (the **local** vLLM/Ollama). Verified with `strace` that telemetry-off
makes **zero** external connections. Details + a self-check command in
[`docs/install_results.md`](docs/install_results.md) ("Telemetry / privacy").

## Known notes
- `goose bench` does **not** exist in 1.39.0 (the plan's §7 eval/ratchet idea needs a redesign).
- The Goose install script is gated by Claude Code's permission classifier when run by the
  agent; these scripts are meant to be run by **you** directly (no such gate).
- **Goose can silently rewrite `~/.config/goose/config.yaml` and drop the provider + stdio
  extensions** → `error: No provider configured`. Mitigation (applied): a known-good copy is
  kept at `~/.config/goose/config.yaml.bak`, and the live config is made **read-only**
  (`chmod a-w`) — goose + all extensions run fine read-only, so the rewrite can't happen.
  To recover: `cp ~/.config/goose/config.yaml.bak ~/.config/goose/config.yaml`. To edit
  config: `chmod u+w`, edit, `chmod a-w`. Details in [`docs/install_results.md`](docs/install_results.md).
