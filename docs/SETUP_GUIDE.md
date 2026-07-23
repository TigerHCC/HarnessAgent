# HarnessAgent — Authoritative Setup Guide

HarnessAgent wires the [Goose](https://github.com/aaif-goose/goose) CLI/agent to a local, self-hosted model stack and a set of MCP knowledge/telemetry servers. The **GB10** box (Linux/aarch64, `192.168.86.44`) is simultaneously the model server (vLLM + Ollama) and the host for the Linux MCP servers (DTM, PK). A suite of **twelve Windows-only diagnostic MCP servers** (`8777`–`8788`) reads live Windows data sources and has no Linux equivalent. Goose and its browser front end **goose_web** can run on either box and always point their model provider at the GB10. Telemetry is forced off everywhere — prompts and responses never leave the local provider.

> **In a hurry?** The repo [`README.md`](../README.md) has the short version: `setup_goose.ps1` then `setup_mcp_servers.ps1` and you're done. This guide is the long-form reference — it explains the GB10/Linux side (vLLM, Ollama, DTM, PK) that the one-click installers don't cover.

---

## Topology — what runs where

```
                              GB10  (Linux / aarch64, 192.168.86.44)
              ┌───────────────────────────────────────────────────────────────┐
              │  MODEL SERVERS                          MCP SERVERS (Linux)     │
              │  ┌──────────────────────────┐           ┌────────────────────┐ │
              │  │ vLLM chat   :8000  (Docker)│          │ DTM agent          │ │
              │  │  qwen-3.6-chat            │          │  stdio  ── or ──    │ │
              │  │ vLLM embed  :8001  (Docker)│          │  mcp-proxy :8765   │ │
              │  │  qwen-3-4b-embed          │◄──embed──┤ (/mcp, /sse)       │ │
              │  │ Ollama      :11434 (systemd)│          ├────────────────────┤ │
              │  │  qwen3.5:9b (fallback)    │◄──embed──┤ PK (personal-kb)   │ │
              │  └──────────────────────────┘          │  stdio  ── or ──    │ │
              │             ▲                           │  mcp-proxy :8766   │ │
              │             │ OpenAI-compat /v1         │ (/mcp, /sse)       │ │
              │             │                           └────────────────────┘ │
              └─────────────┼─────────────────────────────────────────────────┘
                            │ model provider (config.yaml)        ▲ dtm / pk extensions
                            │                                     │ (streamable_http or stdio)
        ┌───────────────────┴──────────────┐        ┌─────────────┴─────────────────────────┐
        │  GOOSE (CLI) — runs on EITHER box │        │  WINDOWS client (Windows-only MCP)     │
        │  ~/.config/goose/config.yaml (Lx) │        │  ┌──────────────────────────────────┐  │
        │  %APPDATA%\Block\goose\… (Win)    │        │  │ Diagnostic MCP suite — 12 servers │  │
        │  (runs UNELEVATED)                │        │  │ 127.0.0.1:8777–8788  /mcp         │  │
        │                                   │        │  │ srum eventlog crash exec drift    │  │
        │  goose_web  :8799  (HTTP bridge)  │        │  │ netconn perfmon disk procinspect  │  │
        │  GET / · /api/health · /api/chat  │        │  │ memstate filterstack winupdate    │  │
        └───────────────────────────────────┘        │  │ (started ELEVATED at logon)       │  │
                                                      │  └──────────────────────────────────┘  │
                                                      │  goose connects over loopback HTTP     │
                                                      └────────────────────────────────────────┘
```

| Component | Box | Bind / URI | Port |
|---|---|---|---|
| vLLM chat (`qwen-3.6-chat`) | GB10 / Linux | `http://192.168.86.44:8000` | **8000** |
| vLLM embed (`qwen-3-4b-embed`) | GB10 / Linux | `http://192.168.86.44:8001` (host 8001 → container 8000) | **8001** |
| Ollama API | GB10 / Linux | `http://192.168.86.44:11434` | **11434** |
| DTM mcp-proxy | GB10 / Linux | `http://127.0.0.1:8765/mcp` (`/sse` legacy) | **8765** |
| PK mcp-proxy | GB10 / Linux | `http://127.0.0.1:8766/mcp` (`/sse` legacy) | **8766** |
| Windows diagnostic MCP suite (12) | **Windows only** | `http://127.0.0.1:8777…8788/mcp` (loopback) | **8777–8788** |
| `dtmsdk` MCP (DTP Sample/SDK utils) | **Windows only** | `http://127.0.0.1:8789/mcp` (loopback) | **8789** |
| `obsidian` MCP (vault; task is Limited) | **Windows only** | `http://127.0.0.1:8790/mcp` (loopback) | **8790** |
| goose_web UI | Either | `http://0.0.0.0:8799` | **8799** |

The 12 diagnostic servers, in port order: `srum` 8777 · `eventlog` 8778 · `crash` 8779 · `exec` 8780 · `drift` 8781 · `netconn` 8782 · `perfmon` 8783 · `disk` 8784 · `procinspect` 8785 · `memstate` 8786 · `filterstack` 8787 · `winupdate` 8788. Plus `dtmsdk` 8789 (DTP utils — not read-only, confirmation-gated) and `obsidian` 8790 (vault read/write — gated, and the only server with a `RunLevel Limited` Scheduled Task). All 14 install via `setup_mcp_servers.ps1`.

> The diagnostic suite is **Windows-only** — it reads live Windows data (`SRUDB.dat`, the Event Log, WER dumps, Prefetch, the USN journal, kernel pool tags, the minifilter stack…) and has no Linux equivalent. Everything else is GB10/Linux, except the Goose CLI and goose_web which run on either box and connect to the GB10 model server.

---

## Prerequisites

**On GB10 (Linux / aarch64)**
- Docker with the NVIDIA runtime (`runtime: nvidia`, `NVIDIA_VISIBLE_DEVICES=all`).
- vLLM image `vllm/vllm-openai:latest` (pulled automatically by `docker compose`); vLLM ≥ 0.6.0 (required for `--enable-chunked-prefill`).
- `secrets.yaml` in `/home/nvidia/Downloads/PersonalKnowledge` containing `hf_token: "hf_..."`; Hugging Face licenses accepted for `Qwen/Qwen3.6-35B-A3B-FP8` and `Qwen/Qwen3-Embedding-4B`.
- HF cache at `/home/nvidia/.cache/huggingface`. Single-GPU budget: chat 0.65 + embed 0.20.
- Ollama binary at `/usr/local/bin/ollama` running as a systemd service on `:11434`.
- DTM/PK MCP trees on this GB10 box (the live `dtm-mcp-proxy`/`pk-mcp-proxy` systemd units and the `qb10_*_mcp.sh` stdio launchers point here): DTM agent at `/home/nvidia/Downloads/GB10-workspace/dtm-agent` (project `venv/`, `DTMKnowledge/` data dir, `config.yaml`) and PK at `/home/nvidia/Downloads/GB10-workspace/pk-mcp` (project `venv/`, `kb/` data dir, `kb_query.py`, `config.yaml`). Each `venv/` has chromadb, requests, pyyaml; Python 3.10+ (see each tree's `README.md`/`SETUP.md`). (`/home/nvidia/Downloads/PersonalKnowledge` is the older/divergent checkout — prefer the GB10-workspace trees for dtm/pk on this box.)

**On Windows (for the 14-server local MCP suite)**
- Windows machine, Python 3.13 in PATH as `python`, Administrator privileges available (needed to *install* the Scheduled Tasks — see Step 5).
- Goose 1.39 (uses `streamable_http` + `/mcp`; SSE dropped).
- Per-server `requirements.txt` deps — `setup_mcp_servers.ps1` installs the manifest-defined set for you.

**On any box that runs Goose / goose_web**
- Same LAN as the GB10 and able to reach `192.168.86.44` (`:8000`, `:11434`).
- Linux/macOS: `curl` + Python 3 (stdlib only). Windows: PowerShell 5+ (Goose Windows build is x86_64/AMD64 only); `server.ps1` needs no Python.

---

## Step 1 — Launch vLLM + Ollama

**On GB10 (Linux).** Brings up the model servers. This is pure infrastructure and can be started first.

```bash
cd /home/nvidia/Downloads/PersonalKnowledge
# Ensure secrets.yaml has hf_token set (start_vllm.sh exports HF_TOKEN and sets HF_HUB_DISABLE_SSL_VERIFY=1)
./start_vllm.sh up -d          # docker compose up -d with HF_TOKEN injected; brings up qwen-chat:8000 + qwen-embed:8001
```

The chat service is launched with `--enable-auto-tool-choice --tool-call-parser qwen3_coder` (required — see Troubleshooting). Watch startup and wait for the model to load (~4 minutes; do NOT assume failure during this window):

```bash
./start_vllm.sh logs -f qwen-chat
./start_vllm.sh logs -f qwen-embed
```

Ollama is already installed and running as a systemd service. (Re)start manually if needed, and ensure the Goose fallback model exists:

```bash
sudo systemctl start ollama       # or: ollama serve
ollama pull qwen3.5:9b            # documented Goose Ollama fallback
```

**Ports exposed:** `8000` (vLLM chat), `8001` (vLLM embed, host 8001 → container 8000), `11434` (Ollama).

**Verify:**
```bash
docker ps --format "{{.Names}} {{.Ports}}"   # personalknowledge-qwen-chat-1 0.0.0.0:8000->8000 ; ...qwen-embed-1 0.0.0.0:8001->8000
curl -s http://127.0.0.1:8000/v1/models       # id "qwen-3.6-chat", root Qwen/Qwen3.6-35B-A3B-FP8
curl -s http://127.0.0.1:8001/v1/models       # id "qwen-3-4b-embed", root Qwen/Qwen3-Embedding-4B
curl http://localhost:8000/health             # 200 once loaded
curl http://localhost:8001/health             # 200 once loaded
curl -s http://127.0.0.1:11434/api/tags        # lists qwen3.6:35b, qwen3.5:9b, qwen3-embedding:latest, ...
systemctl is-active ollama                     # active
```
Tool-call smoke test: a `POST http://localhost:8000/v1/chat/completions` with a tool schema must return `finish_reason: tool_calls` (NOT `stop` with raw `<function=...>` XML in content — that means the wrong parser).

**Stop:** `./start_vllm.sh down`. (If you edit the compose parser, re-create: `docker compose up -d --force-recreate qwen-chat`.)

---

## Step 2 — Enable DTM Agent MCP

The DTM Knowledge Agent is a RAG service on the GB10. The **server/proxy infrastructure below can be started now**, but the final "wire into Goose" sub-step (C) edits the Goose config and therefore **requires Step 4 (Install Goose) to be done first**. If you are following numeric order, run the model server/proxy now and run `./enable_dtm_mcp.sh` **after Step 4**.

**On GB10 (Linux) — one-time DTM setup + server (can run before Step 4):**
```bash
ollama pull qwen3.5:9b && ollama pull qwen3-embedding:4b
cd /home/nvidia/Downloads/GB10-workspace/dtm-agent
# ensure venv deps: venv/bin/pip install chromadb requests pyyaml
venv/bin/python -m dtm_agent health           # Ollama OK + DTMKnowledge files
venv/bin/python -m dtm_agent index --dry-run  # verify parsing
venv/bin/python -m dtm_agent index            # full build, 10-30 min (needs Ollama for embeddings)
venv/bin/python -m dtm_agent query "What telemetry covers battery health?"
```

Preferred transport is a warm always-on HTTP proxy on `:8765` (avoids per-call cold start):
```bash
venv/bin/pip install mcp-proxy
# B) ad-hoc proxy:
/home/nvidia/Downloads/GB10-workspace/dtm-agent/dtm_agent/run_mcp_proxy.sh   # binds 0.0.0.0:8765 -> /mcp (streamable), /sse
sudo ufw allow 8765/tcp                                                # only if remote LAN clients need it
# B2) always-on systemd service (needs sudo):
sudo cp /home/nvidia/Downloads/GB10-workspace/dtm-agent/dtm_agent/dtm-mcp-proxy.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now dtm-mcp-proxy
systemctl status dtm-mcp-proxy
```
(Self-contained stdio alternative — no proxy/port/sudo: `/home/nvidia/Downloads/GB10-workspace/dtm-agent/dtm_agent/run_mcp.sh`, or the Goose `dtm` stdio launcher `mcp/qb10_dtm_mcp.sh`.)

**On GB10 (Linux) — (C) wire into Goose — REQUIRES STEP 4 DONE FIRST:**
```bash
cd /home/nvidia/Downloads/HarnessAgent/mcp
./enable_dtm_mcp.sh                         # DEFAULT: streamable_http -> http://127.0.0.1:8765/mcp (needs proxy up)
# OR self-contained stdio (no proxy/port/sudo):
DTM_MCP_STDIO=1 ./enable_dtm_mcp.sh         # stdio via qb10_dtm_mcp.sh
# switch an already-enabled dtm (DTM_MCP_REPLACE=1):
DTM_MCP_REPLACE=1 DTM_MCP_STDIO=1 ./enable_dtm_mcp.sh   # http -> stdio
DTM_MCP_REPLACE=1 ./enable_dtm_mcp.sh                   # stdio -> http
# custom HTTP endpoint: DTM_MCP_URI=http://<host>:8765/mcp ./enable_dtm_mcp.sh
```
The script unlocks the read-only `~/.config/goose/config.yaml`, inserts the `dtm` extension, re-locks, refreshes `.bak`, validates with `goose info -v`, and auto-restores on failure (idempotent; no-op unless `DTM_MCP_REPLACE=1`). Restart any running goose session/web afterward.

**Ports exposed:** `8765/tcp` (mcp-proxy `/mcp` + `/sse`). Consumes `11434` (Ollama) and `8000/8001` (vLLM).

**Verify:**
```bash
curl http://localhost:11434/api/tags
cd /home/nvidia/Downloads/GB10-workspace/dtm-agent && venv/bin/python -m dtm_agent health
curl http://127.0.0.1:8765/mcp                # proxy reachable (streamable_http path)
systemctl status dtm-mcp-proxy
grep -cE '^[[:space:]]{2}dtm:[[:space:]]*$' ~/.config/goose/config.yaml   # expect 1 after enable
goose info -v                                  # config loads with dtm present
```

---

## Step 3 — Enable PK MCP

PK is a stateless retrieval MCP server (`search_kb` / `get_document` / `list_sources`) on the GB10. Because it is stateless and fast, **stdio is the default transport** (no proxy/port/sudo). As in Step 2, the **"wire into Goose" sub-step edits the Goose config and REQUIRES Step 4 done first** — if following numeric order, run `./enable_pk_mcp.sh` **after Step 4**.

**On GB10 (Linux) — stdio path (DEFAULT, recommended):**
```bash
# Prereqs: PK venv has chromadb, pk_* ChromaDB collections built, vLLM :8001 embedding endpoint up.
cd /home/nvidia/Downloads/GB10-workspace/pk-mcp && venv/bin/python kb_query.py --mcp-mode   # optional manual check; Ctrl-C to exit
# (or the wrapper, which sets cwd + venv for you:)
/home/nvidia/Downloads/HarnessAgent/mcp/qb10_pk_mcp.sh

# Wire into Goose over stdio  (REQUIRES STEP 4 DONE FIRST):
cd /home/nvidia/Downloads/HarnessAgent/mcp && ./enable_pk_mcp.sh
# restart any running goose session/web
```

**On GB10 (Linux) — streamable_http path (alternative, via pk-mcp-proxy on :8766):**
```bash
cd /home/nvidia/Downloads/GB10-workspace/pk-mcp && venv/bin/pip install mcp-proxy
/home/nvidia/Downloads/GB10-workspace/pk-mcp/scripts/run_pk_mcp_proxy.sh    # serves /mcp + /sse on 0.0.0.0:8766
sudo ufw allow 8766/tcp                                                 # only if LAN access needed
# always-on systemd:
sudo cp /home/nvidia/Downloads/GB10-workspace/pk-mcp/scripts/pk-mcp-proxy.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now pk-mcp-proxy
# wire into Goose over HTTP (REQUIRES STEP 4):
cd /home/nvidia/Downloads/HarnessAgent/mcp && PK_MCP_URI=http://127.0.0.1:8766/mcp ./enable_pk_mcp.sh
# switch transports: PK_MCP_REPLACE=1 [PK_MCP_URI=...] ./enable_pk_mcp.sh
```
(On GB10 the dev box IS the model server, so `127.0.0.1` is correct for `PK_MCP_URI`. A combined PK+DTM single-proxy on `:8765` also exists via `/home/nvidia/Downloads/PersonalKnowledge/HarnessAgent/mcp/start_mcp_servers.sh` (this combined script lives only in the older PersonalKnowledge checkout — it has no GB10-workspace equivalent), PK at `/servers/pk/mcp` — see that script.)

**Ports exposed:** stdio path uses **no** network port. HTTP path uses `8766/tcp` (`/mcp` + `/sse`). `search_kb` requires vLLM `:8001`.

**Verify:**
```bash
goose info -v                                              # config loads, pk extension present
grep -cE '^  pk:[[:space:]]*$' ~/.config/goose/config.yaml # expect 1
# in a Goose session: call list_sources (6 pk_* collections return chunk counts), then search_kb (needs :8001)
curl http://127.0.0.1:8766/mcp                            # only for the streamable_http path
```

---

## Step 4 — Install Goose

**This step must be done before the Goose-side wiring in Steps 2, 3, and 5** (the `enable_*_mcp.sh` scripts and `setup_mcp_servers.ps1` all edit a Goose config that must already exist). HarnessAgent ships idempotent one-click installers that install the Goose CLI, write a `config.yaml` pointed at the GB10 (`192.168.86.44`, vLLM `:8000`, model `qwen-3.6-chat`), force telemetry off, and run a headless tool-calling smoke test.

**On GB10 / any Linux box:**
```bash
chmod +x setup_goose.sh
./setup_goose.sh
# optional overrides:
BACKEND=ollama GB10_HOST=192.168.86.44 SKIP_SMOKE=1 ./setup_goose.sh
```
Installs Goose into `~/.local/bin/goose` and writes `~/.config/goose/config.yaml` (`GOOSE_PROVIDER: openai`, `GOOSE_MODEL: qwen-3.6-chat`, `OPENAI_HOST: http://192.168.86.44:8000`, `OPENAI_BASE_PATH: v1/chat/completions`, `OPENAI_API_KEY: sk-local`, Ollama fallback commented, `GOOSE_TELEMETRY_ENABLED: false`, `developer` + `memory` extensions). Ensure `~/.local/bin` is on PATH. **Recommended hardening** against the config self-strip risk:
```bash
cp ~/.config/goose/config.yaml ~/.config/goose/config.yaml.bak
chmod a-w ~/.config/goose/config.yaml      # goose + extensions run fine read-only
```

**On Windows:**
```powershell
powershell -ExecutionPolicy Bypass -File .\setup_goose.ps1
# optional: -Backend ollama  -Gb10Host 192.168.86.44  -GooseVersion stable  -SkipSmokeTest
```
Installs `goose.exe` into `%USERPROFILE%\.local\bin\` (restart terminals for PATH), and writes config to `%APPDATA%\Block\goose\config\config.yaml` with the same provider settings + `GOOSE_TELEMETRY_ENABLED: false`.

**Ports used:** connects to GB10 `:8000` (vLLM, default) / `:11434` (Ollama fallback). Deliberately blocks `us.i.posthog.com:443`.

**Verify:**
```bash
goose --version                                      # 1.39.0
GOOSE_MODE=auto goose run --no-session -t "Create ./ok.txt containing the word READY, then stop."   # ok.txt contains READY
```
Linux telemetry self-check (expect NO output):
```bash
strace -f -e trace=connect -o /tmp/gc.log -- bash -c 'goose run --no-session --max-turns 2 -t "hi" >/dev/null 2>&1'
grep 'connect(' /tmp/gc.log | grep -oE 'inet_addr\("[0-9.]+"\)' | grep -vE '127\.|192\.168\.86\.44'
```
Confirm config landed and contains `GOOSE_TELEMETRY_ENABLED: false` (Linux `~/.config/goose/config.yaml`; Windows `%APPDATA%\Block\goose\config\config.yaml`).

**Rollback (Linux):** `rm ~/.local/bin/goose && rm -rf ~/.config/goose`.

---

## Step 5 — Enable the local Windows MCP suite (14 servers)

**Windows only** (`linux_steps` empty by design). Fourteen loopback-only FastMCP servers on
`8777`–`8790`: twelve read-only diagnostic servers, confirmation-gated `dtmsdk`, and
confirmation-gated `obsidian` with a `RunLevel Limited` Scheduled Task. Goose runs **unprivileged** and
talks to them over loopback HTTP.
**Requires Step 4 (Install Goose) done first** — the installer registers extensions into a config
that must already exist. The canonical list is [`../config/mcp_servers.json`](../config/mcp_servers.json).

**On Windows — one command, in an ELEVATED PowerShell:**
```powershell
powershell -ExecutionPolicy Bypass -File .\setup_mcp_servers.ps1
```
Idempotent. It installs the manifest servers' Python requirements, registers + starts an **at-logon**
Scheduled Task per server (`Highest` except `obsidian`, which is `Limited`), appends all 14
`streamable_http` extension blocks to `%APPDATA%\Block\goose\config\config.yaml` (backup:
`config.yaml.bak-mcpsetup`), and prints a port/task status table.

Flags: `-SkipDeps` · `-SkipTasks` · `-NoStart` · `-SkipConfig` · `-ConfigPath <path>` · **`-Uninstall`** (stop the servers, drop the tasks, strip the 14 extension blocks; backup `config.yaml.bak-mcpuninstall`).

**Why elevated — and what that does *not* mean.** Admin is needed to *register* a `RunLevel Highest` Scheduled Task, and by several servers at *runtime* for specific data sources (SRUM's SYSTEM-locked `SRUDB.dat`, the Security event log, Prefetch/BAM/ShimCache, the USN journal's raw volume handle, `fltmc`). It is **not** needed by Goose, which stays unelevated — a TCP socket has no UAC boundary. Four of the twelve (`netconn`, `perfmon`, `drift`, `winupdate`) need no admin at all, and the rest gate only the affected tools and degrade gracefully. Full table: [`../mcp/README.md`](../mcp/README.md#privileges--what-actually-needs-administrator).

> The trigger is **at logon**, not at boot — a machine that boots but is never logged into starts none of them.

Each task runs as the **current user** with `LogonType Interactive` and starts
`scripts/start_mcp_hidden.ps1` through PowerShell with `-WindowStyle Hidden`; the launcher then runs the
Python MCP server. The installer's immediate starts use the same hidden path, so no MCP console window
is left open. Standard output and errors append separately to `logs/mcp/<name>.stdout.log` and
`logs/mcp/<name>.stderr.log`. At the next launch, a log larger than 10 MiB is moved to its `.log.1` file;
stdout and stderr rotate independently and only one rotated generation is retained.

> **Obsidian privilege nuance:** its Scheduled Task and logon launches use `RunLevel Limited` and are
> unelevated. The one-click setup runs elevated and starts servers directly unless `-NoStart` is used, so
> an immediate install-time Obsidian process inherits the elevated setup token. It remains elevated until
> Obsidian is restarted through its Scheduled Task or at the next logon; the task definition stays Limited.

**Per-server alternative** (any of the 12, e.g. `windows_srum`):
```powershell
cd mcp\windows_srum
python -m pip install -r requirements.txt
.\start_srum_mcp.ps1     # foreground, this session only
.\install_task.ps1       # OR persist: Scheduled Task 'mcp-srum' (elevated, at logon)
.\uninstall_task.ps1     # remove that one task
```

**Ports exposed:** `127.0.0.1:8777`–`8790` (loopback only; each port bind doubles as that server's single-instance lock).

**Test all local MCP servers** from a normal, **unelevated** PowerShell session:
```powershell
powershell -ExecutionPolicy Bypass -File .\test_mcp_servers.ps1
```
The client safely performs `initialize` → `notifications/initialized` → `tools/list` → each
manifest-declared health `tools/call`. It writes timestamped JSON and Markdown reports under
`reports/mcp/` by default and exits `0` when all pass, `1` for server transport/protocol/tool-call
failures, or `2` for invocation, manifest, or report errors. A degraded health payload is recorded
but differs from a transport or tool-call failure. See
[`MODULE_RELATIONSHIPS.md`](MODULE_RELATIONSHIPS.md#2-repository-module-relationships).

**Using them for real diagnosis** — [`DIAGNOSTIC_PLAYBOOK.md`](DIAGNOSTIC_PLAYBOOK.md) maps symptom → tool → exact call → ready-to-paste Goose prompt (written in Traditional Chinese).

---

## Step 6 — Launch goose_web

A stdlib-only web front end that bridges HTTP to `goose run` (one subprocess per message, cwd `../workspace`, `--max-turns 50`, `GOOSE_MODE` from the request). Runs on either box, binds `0.0.0.0:8799`. Its `config.json` backend URLs only drive the health panel / displayed provider — Goose's **real** model provider lives in Goose's own config. Telemetry is forced off on every subprocess.

**On GB10 / any Linux box:**
```bash
cd /home/nvidia/Downloads/HarnessAgent/goose_web
./serve_web.sh                               # bind 0.0.0.0:8799 (LAN), no token
# recommended on LAN — require a token:
GOOSE_WEB_TOKEN=secret ./serve_web.sh        # /api/chat then needs ?token= or X-Goose-Token header
# local-only:  GOOSE_WEB_HOST=127.0.0.1 ./serve_web.sh
# change port: GOOSE_WEB_PORT=9000 ./serve_web.sh
```

**Environment knobs** (read by both `server.py` and `server.ps1`; each overrides the matching `config.json` value):

| Env var | config.json | Default | Purpose |
|---|---|---|---|
| `GOOSE_WEB_HOST` | `host` | `0.0.0.0` | bind address |
| `GOOSE_WEB_PORT` | `port` | `8799` | listen port |
| `GOOSE_WEB_TOKEN` | `token` | _(empty)_ | shared secret; if set, `/api/chat` **and** `/api/upload` require `?token=` or `X-Goose-Token` |
| `GOOSE_WEB_WORKSPACE` | `workspace` | `../workspace` | agent working directory (cwd of each `goose run`) |
| `GOOSE_WEB_MAXTURNS` | `max_turns` | `50` | `--max-turns` per message |
| `GOOSE_WEB_TIMEOUT` | `timeout_seconds` | `1800` | hard wall-clock kill (seconds) |
| `GOOSE_WEB_MODEL` | `model` | `qwen-3.6-chat` | model name shown in the UI/health panel |
| `GOOSE_WEB_CONFIG` | _(n/a)_ | `./config.json` | path to the `config.json` itself |
| `GOOSE_WEB_MAX_UPLOAD_MB` | `max_upload_mb` | `25` | per-file upload cap (MB) |

**File attachments:** files attached in the chat UI are uploaded via `POST /api/upload` (raw request body; `?session=&name=` query params) into `workspace/<uploads_subdir>/<session>/` (default `workspace/uploads/<session>/`; `uploads_subdir` in `config.json`). Each file is capped at `GOOSE_WEB_MAX_UPLOAD_MB` (`config.json` `max_upload_mb`, default 25 MB); the save path is sandboxed to the workspace (filename sanitized, collisions auto-suffixed). The agent then reads the saved files with its own tools (developer `text_editor`/`shell`, computercontroller `pdf`/`docx`/`xlsx`) — the uploaded paths are appended to the chat message relative to the workspace.

**On Windows:**
```powershell
cd <repo>\goose_web
.\serve_web.ps1                              # bind 0.0.0.0:8799 (LAN), no token  (uses .NET HttpListener; no Python)
$env:GOOSE_WEB_TOKEN='secret'; .\serve_web.ps1
# local-only (no admin / urlacl needed): $env:GOOSE_WEB_HOST='127.0.0.1'; .\serve_web.ps1
# if unsigned scripts are blocked: powershell -ExecutionPolicy Bypass -File .\serve_web.ps1
# LAN bind (0.0.0.0) needs an elevated shell OR a one-time reservation (run once, elevated):
netsh http add urlacl url=http://+:8799/ user=%USERNAME%
```

**Port exposed:** `8799` (UI). Health panel polls GB10 `8000` / `8001` / `11434`.

**Verify:**
```bash
# Console banner: 'Goose Harness Web -> http://0.0.0.0:8799' + goose version, model, workspace, token status
curl http://192.168.86.44:8799/api/health    # JSON with model, provider, backends[].ok (expect 3/3 up)
# Open http://192.168.86.44:8799 in a LAN browser and send a chat message.
```

---

## Privacy / telemetry

`GOOSE_TELEMETRY_ENABLED=false` is enforced **everywhere**:
- written into the generated Goose `config.yaml` by both installers (`setup_goose.sh`, `setup_goose.ps1`),
- exported as an env var by every script that invokes goose (env overrides config), and
- set on every `goose run` subprocess spawned by goose_web (`server.py` / `server.ps1`) and exported by `serve_web.{sh,ps1}`.

With telemetry on, Goose would POST usage metadata (model, extension/session names, token/session counts, settings) to `us.i.posthog.com`. **Prompts and responses never leave the local provider regardless** — they go only to the GB10 vLLM/Ollama endpoints. The Linux self-check (`strace` connect trace in Step 4) should show zero external connections.

---

## End-to-end verification

1. **Model servers (GB10):** `curl -s http://127.0.0.1:8000/v1/models` → `qwen-3.6-chat`; `:8001/v1/models` → `qwen-3-4b-embed`; `curl :8000/health` and `:8001/health` → 200; `curl :11434/api/tags` lists `qwen3.5:9b`; `systemctl is-active ollama` → active.
2. **Tool-calling parser:** a `chat/completions` call with a tool schema returns `finish_reason: tool_calls` (not `stop` with raw `<function=...>` XML).
3. **Goose installed:** `goose --version` → 1.39.0; smoke test creates `ok.txt` containing `READY`; config contains `GOOSE_TELEMETRY_ENABLED: false`.
4. **DTM + PK in Goose (GB10):** `goose info -v` loads cleanly; `grep -cE '^[[:space:]]{2}dtm:' …config.yaml` → 1 and `grep -cE '^  pk:' …config.yaml` → 1; in a session, a DTM query (`What telemetry covers battery health?`) returns, and PK `list_sources` returns the 6 `pk_*` collection counts + `search_kb` retrieves (needs `:8001`). DTM proxy reachable: `curl http://127.0.0.1:8765/mcp`.
5. **Local Windows MCP suite:** run `powershell -ExecutionPolicy Bypass -File .\test_mcp_servers.ps1`
   unelevated; expect 14 passed entries plus timestamped JSON and Markdown report paths.
6. **goose_web reachable:** `curl http://192.168.86.44:8799/api/health` → 3/3 backends `ok`; the browser UI at `http://192.168.86.44:8799` answers a chat message.

---

## Troubleshooting

- **vLLM tool-call parser (CRITICAL):** the chat service MUST launch with **both** `--enable-auto-tool-choice` AND `--tool-call-parser qwen3_coder`. `hermes` parses only JSON `<tool_call>{...}` but `qwen-3.6-chat` emits Qwen XML `<function=...><parameter=...>`, so tool calls silently fail (`tool_calls=null`, `finish_reason: stop`). After any compose edit run `docker compose up -d --force-recreate qwen-chat`. (`qwen3_xml` also parses the XML.)
- **~4-minute model load:** after `start_vllm.sh up` or a model restart, `:8000`/`:8001` `/health` and `/v1/models` return errors during cold load (compose `start_period` 120s chat / 60s embed). This is expected — do not assume failure.
- **Embed port mapping:** host `8001` → container `8000` (both vLLM containers listen on 8000 internally); don't expect the embed container to expose 8001 internally.
- **`start_vllm.sh` requires `secrets.yaml`:** must run from the PersonalKnowledge dir; hard-exits if `secrets.yaml` is missing. An empty `hf_token` only warns (rate-limited downloads), not fatal.
- **Ollama 30B+ models stall on tools:** `qwen3.6:35b` (and other ≥30B) exceed 120s prompt-eval (`Ollama stream stalled`) on tool payloads — the Ollama fallback uses `qwen3.5:9b`. Config raises timeouts to ~900s.
- **DTM/PK `chromadb` shadowing:** both servers MUST run with `venv/bin/python` AND cwd = the tree's project root (GB10-workspace `dtm-agent` / `pk-mcp`), else the repo's local `./chromadb/` data dir shadows the installed `chromadb` package → `module 'chromadb' has no attribute 'PersistentClient'`. The `run_mcp.sh` / `qb10_*_mcp.sh` wrappers handle this.
- **`streamable_http` requires the proxy up:** the `dtm` (`:8765`) / `pk` (`:8766`) HTTP extensions exist after enabling but calls fail if the proxy isn't running. The enable scripts warn but do not start the proxy. (PK stdio path needs no proxy.)
- **SSE dropped in Goose 1.39+:** use `type: streamable_http` with the `/mcp` endpoint, NOT `type: sse` / `/sse`. (`/sse` remains only for legacy non-Goose clients like Claude Desktop.)
- **DTM cold start:** first query after a cold start takes 30–60s+ (per-call stdio ~167s vs warm proxy ~110s) — keep client `timeout: 600`; prefer the warm `:8765` proxy. The proxy holds one backend process, so concurrent requests serialize, and it adds no auth/TLS — only expose on a trusted LAN/VPN.
- **Read-only Goose config + `.bak` recovery (Linux):** Goose can silently rewrite `~/.config/goose/config.yaml`, dropping provider keys + stdio extensions → `error: No provider configured. Run goose configure first.` Clean runs are NOT a safety signal. Mitigation: keep `config.yaml.bak` and `chmod a-w` the live config. Recover: `cp ~/.config/goose/config.yaml.bak ~/.config/goose/config.yaml && chmod a-w ~/.config/goose/config.yaml`. Never hand-edit the live config — use the `enable_*.sh` scripts (unlock → edit → re-lock → refresh `.bak`). To edit: `chmod u+w`, edit, `chmod a-w`.
- **Windows goose_web LAN bind (urlacl):** HttpListener on `0.0.0.0` needs an elevated shell OR a one-time `netsh http add urlacl url=http://+:8799/ user=%USERNAME%` (run once, elevated). Binding `127.0.0.1` needs neither.
- **goose_web security:** with `GOOSE_MODE=auto` the agent auto-runs shell/file tools on the host; bound to `0.0.0.0` anyone reaching the port can run commands. On a shared LAN set `GOOSE_WEB_TOKEN` or bind `127.0.0.1` (a loud yellow warning prints on public bind without a token). Each web message spawns a fresh `goose run`, so every DTM query pays cold start — point `dtm` at the warm `:8765` proxy.
- **Windows MCP elevation:** the *installer* needs admin (it registers `RunLevel Highest` Scheduled Tasks). At *runtime* only some servers need it, and only for specific data sources — SRUM's `esentutl /vss` copy of the locked `SRUDB.dat`, the Security log (`user_activity`), Prefetch/BAM/ShimCache, the USN journal's raw volume handle, `fltmc`. `netconn`/`perfmon`/`drift`/`winupdate` need none. **Goose itself never needs admin** — loopback HTTP has no UAC boundary. Full table in [`../mcp/README.md`](../mcp/README.md#privileges--what-actually-needs-administrator).
- **Windows MCP gotchas:** a raw `GET /mcp` returning **HTTP 400 is normal** (the endpoint is up). The Scheduled Tasks trigger **at logon, not at boot**. SRUM is historical (flushed ~hourly) — use `live_snapshot` for "right now"; per-app energy is often 0 on desktops; live wattage is laptop-only. Port-in-use → that bind IS the single-instance lock. The servers have **no auth** and are reachable by any local process. Twelve diagnostic servers expose read-only data, while `dtmsdk` and `obsidian` have confirmation-gated write operations; treat all endpoints as a UAC-free window onto privileged capabilities and data.
- **Windows MCP task logs:** the current-user `AtLogOn` tasks use the hidden PowerShell launcher, so there is no server console to inspect. From the repository root, follow a server with `Get-Content .\logs\mcp\srum.stdout.log -Tail 50 -Wait` and `Get-Content .\logs\mcp\srum.stderr.log -Tail 50 -Wait`. Logs rotate independently to `.log.1` when larger than 10 MiB at the next launch.
- **Telemetry:** if `GOOSE_TELEMETRY_ENABLED` is ever set `true`, Goose POSTs metadata to `us.i.posthog.com`. Keep it `false` (configs + installers + every script enforce this); prompts/responses stay local regardless.
- **Misc:** `OPENAI_API_KEY: sk-local` is a dummy (vLLM ignores it, but Goose requires a value). `goose bench` does not exist in 1.39.0 — related commands are `recipe`, `skills`, `review`. `--enable-chunked-prefill` requires vLLM ≥ 0.6.0 (remove on older releases — see compose header comment).
