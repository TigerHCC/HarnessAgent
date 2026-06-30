# HarnessAgent — Authoritative Setup Guide

HarnessAgent wires the [Goose](https://github.com/aaif-goose/goose) CLI/agent to a local, self-hosted model stack and a set of MCP knowledge/telemetry servers. The **GB10** box (Linux/aarch64, `192.168.86.44`) is simultaneously the model server (vLLM + Ollama) and the host for the Linux MCP servers (DTM, PK). Two MCP servers (**SRUM**, **Event Log**) are **Windows-only** because they read live Windows data sources. Goose and its browser front end **goose_web** can run on either box and always point their model provider at the GB10. Telemetry is forced off everywhere — prompts and responses never leave the local provider.

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
        │  %APPDATA%\Block\goose\… (Win)    │        │  │ SRUM MCP   127.0.0.1:8777/mcp    │  │
        │                                   │        │  │  (ELEVATED — reads SRUDB.dat)    │  │
        │  goose_web  :8799  (HTTP bridge)  │        │  │ EventLog MCP 127.0.0.1:8778/mcp  │  │
        │  GET / · /api/health · /api/chat  │        │  │  (ELEVATED — reads Security log) │  │
        └───────────────────────────────────┘        │  └──────────────────────────────────┘  │
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
| SRUM MCP | **Windows only** | `http://127.0.0.1:8777/mcp` (loopback) | **8777** |
| Event Log MCP | **Windows only** | `http://127.0.0.1:8778/mcp` (loopback) | **8778** |
| goose_web UI | Either | `http://0.0.0.0:8799` | **8799** |

> SRUM and Event Log are **Windows-only** — they read live Windows data (`SRUDB.dat`, the Windows Event Log) and have no Linux equivalent (their `linux_steps` are empty by design). Everything else is GB10/Linux, except the Goose CLI and goose_web which run on either box and connect to the GB10 model server.

---

## Prerequisites

**On GB10 (Linux / aarch64)**
- Docker with the NVIDIA runtime (`runtime: nvidia`, `NVIDIA_VISIBLE_DEVICES=all`).
- vLLM image `vllm/vllm-openai:latest` (pulled automatically by `docker compose`); vLLM ≥ 0.6.0 (required for `--enable-chunked-prefill`).
- `secrets.yaml` in `/home/nvidia/Downloads/PersonalKnowledge` containing `hf_token: "hf_..."`; Hugging Face licenses accepted for `Qwen/Qwen3.6-35B-A3B-FP8` and `Qwen/Qwen3-Embedding-4B`.
- HF cache at `/home/nvidia/.cache/huggingface`. Single-GPU budget: chat 0.65 + embed 0.20.
- Ollama binary at `/usr/local/bin/ollama` running as a systemd service on `:11434`.
- DTM/PK MCP trees on this GB10 box (the live `dtm-mcp-proxy`/`pk-mcp-proxy` systemd units and the `qb10_*_mcp.sh` stdio launchers point here): DTM agent at `/home/nvidia/Downloads/GB10-workspace/dtm-agent` (project `venv/`, `DTMKnowledge/` data dir, `config.yaml`) and PK at `/home/nvidia/Downloads/GB10-workspace/pk-mcp` (project `venv/`, `kb/` data dir, `kb_query.py`, `config.yaml`). Each `venv/` has chromadb, requests, pyyaml; Python 3.10+ (see each tree's `README.md`/`SETUP.md`). (`/home/nvidia/Downloads/PersonalKnowledge` is the older/divergent checkout — prefer the GB10-workspace trees for dtm/pk on this box.)

**On Windows (for SRUM + Event Log only)**
- Windows machine, Python 3.13 in PATH as `python`, Administrator privileges available.
- Goose 1.39 (uses `streamable_http` + `/mcp`; SSE dropped).
- Per-server `requirements.txt` deps (installed in their steps).

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

**This step must be done before the Goose-side wiring in Steps 2, 3, 5, and 6** (the `enable_*_mcp.sh` scripts and the Windows config deploy all edit a Goose config that must already exist). HarnessAgent ships idempotent one-click installers that install the Goose CLI, write a `config.yaml` pointed at the GB10 (`192.168.86.44`, vLLM `:8000`, model `qwen-3.6-chat`), force telemetry off, and run a headless tool-calling smoke test.

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

## Step 5 — Enable SRUM MCP (Windows)

**Windows only** (`linux_steps` empty by design). An elevated, loopback-only FastMCP server exposing live metrics (`live_snapshot`, `top_processes`) and historical SRUM per-app tools (`srum_app_usage`, `srum_network_usage`, `srum_energy_usage`, `srum_health`) parsed from `C:\Windows\System32\sru\SRUDB.dat`. Goose runs unprivileged and talks to the elevated server over loopback HTTP. **The Goose config deploy in step 6 below requires Step 4 (Install Goose) done first.**

**On Windows:**
```powershell
cd HarnessAgent\mcp\windows_srum
python -m pip install -r requirements.txt          # mcp>=1.2, psutil>=5.9, dissect.esedb>=3.0, wmi>=1.5, pytest>=8.0
# Open PowerShell AS ADMINISTRATOR (required: reads locked SRUDB.dat via esentutl /vss), then:
.\start_srum_mcp.ps1                                # prints: [*] Starting SRUM MCP on http://127.0.0.1:8777/mcp
# OR persistent/always-on (run once as Administrator):
.\install_task.ps1                                  # registers Scheduled Task 'SRUM-MCP' (RunLevel Highest, AtLogOn)
Start-ScheduledTask -TaskName SRUM-MCP
# remove later: .\uninstall_task.ps1
```
Wire into Goose — the `srum` extension is predefined in `config\windows_config.yaml`. **Deploy it to the live config Goose reads (requires Step 4):**
```powershell
Copy-Item ..\..\config\windows_config.yaml "$env:APPDATA\Block\goose\config\config.yaml" -Force
```
Resulting extension (`streamable_http`):
```yaml
  srum:
    type: streamable_http
    bundled: false
    name: srum
    enabled: true
    uri: http://127.0.0.1:8777/mcp
    headers: {}
    env_keys: []
    timeout: 120
    description: Windows SRUM + live system resource usage (CPU/mem/net/power)
```

**Port exposed:** `127.0.0.1:8777` (loopback only; the port bind is the single-instance lock).

**Verify:**
```powershell
# Server console should print: [*] Starting SRUM MCP on http://127.0.0.1:8777/mcp
# A raw GET http://127.0.0.1:8777/mcp returning HTTP 400 is NORMAL (endpoint is up).
$env:GOOSE_MODE="auto"; goose run --no-session -t "Call srum_health, then live_snapshot. Report admin status, SRUM tables, and current CPU%/memory%/battery."
goose run --no-session -t "Use srum_network_usage for the last 48 hours and list the top 5 apps by bytes."
Get-ScheduledTask -TaskName SRUM-MCP        # if using the scheduled task
```

---

## Step 6 — Enable Event Log MCP (Windows)

**Windows only.** An elevated, loopback-only FastMCP server giving 6 read-only Event Log tools (`list_channels`, `query_events`, `error_summary`, `user_activity`, `get_event`, `eventlog_health`) over the modern pywin32 Evt API, serving `http://127.0.0.1:8778/mcp`. Runs elevated so the Security log (`user_activity`) is readable. **The config deploy requires Step 4 done first.**

**On Windows:**
```powershell
cd HarnessAgent\mcp\windows_eventlog
python -m pip install -r requirements.txt          # mcp>=1.2, pywin32>=306, pytest>=8.0
# Open PowerShell AS ADMINISTRATOR (required for Security log), then:
.\start_eventlog_mcp.ps1                            # sets PYTHONIOENCODING=utf-8; serves http://127.0.0.1:8778/mcp
# OR persistent/always-on (run once as Administrator):
.\install_task.ps1                                  # registers Scheduled Task 'EventLog-MCP' (RunLevel Highest, at logon)
Start-ScheduledTask -TaskName EventLog-MCP
# remove later: .\uninstall_task.ps1
```
Wire into Goose — the `eventlog` extension is predefined in `config/windows_config.yaml`. **Deploy to the live config (requires Step 4):**
```powershell
Copy-Item ..\..\config\windows_config.yaml "$env:APPDATA\Block\goose\config\config.yaml" -Force
```
Resulting extension (`streamable_http`):
```yaml
  eventlog:
    type: streamable_http
    bundled: false
    name: eventlog
    enabled: true
    uri: http://127.0.0.1:8778/mcp
    headers: {}
    env_keys: []
    timeout: 120
    description: Windows Event Log via local elevated MCP server (127.0.0.1:8778)
```

**Port exposed:** `8778` (bound to `127.0.0.1` only).

**Verify:**
```powershell
# Server console should print: [*] Starting Event Log MCP on http://127.0.0.1:8778/mcp
$env:GOOSE_MODE="auto"; goose run --no-session -t "Call eventlog_health, then error_summary for the last 72 hours (top 5 system errors)."
goose run --no-session -t "Use user_activity for the last 24 hours and summarize logons."
Get-ScheduledTask -TaskName EventLog-MCP    # if using the scheduled task
```
(`level` uses Windows numerics: 1=Critical, 2=Error, 3=Warning, 4=Information.)

---

## Step 7 — Launch goose_web

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
5. **Windows MCP:** `$env:GOOSE_MODE="auto"; goose run --no-session -t "Call srum_health, then live_snapshot…"` reports admin status + CPU/mem/battery; `goose run --no-session -t "Call eventlog_health, then error_summary for the last 72 hours…"` returns top errors. Both servers print their `[*] Starting … on http://127.0.0.1:877x/mcp` banner.
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
- **Windows MCP elevation:** SRUM (`8777`) and Event Log (`8778`) servers MUST run elevated — SRUM reads the locked `SRUDB.dat` via `esentutl /vss`; Event Log needs the Security log for `user_activity`. The `start_*.ps1` / `install_task.ps1` scripts self-check admin and refuse otherwise. A raw `GET /mcp` returning **HTTP 400 is normal**. SRUM is historical (flushed ~hourly) — use `live_snapshot` for "right now"; per-app energy is often 0 on desktops; live wattage is laptop-only. Port-in-use → the bind is the single-instance lock. Remember the `srum`/`eventlog` extensions must be both in `config/windows_config.yaml` AND copied to `%APPDATA%\Block\goose\config\config.yaml`.
- **Telemetry:** if `GOOSE_TELEMETRY_ENABLED` is ever set `true`, Goose POSTs metadata to `us.i.posthog.com`. Keep it `false` (configs + installers + every script enforce this); prompts/responses stay local regardless.
- **Misc:** `OPENAI_API_KEY: sk-local` is a dummy (vLLM ignores it, but Goose requires a value). `goose bench` does not exist in 1.39.0 — related commands are `recipe`, `skills`, `review`. `--enable-chunked-prefill` requires vLLM ≥ 0.6.0 (remove on older releases — see compose header comment).