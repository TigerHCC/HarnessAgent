# HarnessAgent — Inventory & Debug Reference

> Purpose: a single place to answer "what is this, where does it live, and how do I debug it"
> for the Goose harness. Written from the **Windows client** perspective (this machine →
> GB10 models). For the GB10-native install details see `install_results.md` (Addendum).
>
> Last updated: **2026-06-29**. Boundary unchanged: **`PersonalKnowledge-GB10` is never modified.**

---

## 1. Topology — what runs where

```
THIS Windows machine                          GB10 remote (192.168.86.44)
┌───────────────────────────────┐            ┌─────────────────────────────────────┐
│ goose.exe  (~/.local/bin)      │── chat ──▶ │ vLLM  :8000  qwen-3.6-chat            │
│ live config:                   │            │   (parser qwen3_coder, reasoner)     │
│   %APPDATA%\Block\goose\config │── fallbk ▶ │ Ollama :11434  (8 models, qwen3.5:9b)│
│ extensions:                    │            │ vLLM  :8001  qwen-3-4b-embed         │
│   ├ developer  (builtin, local)│── dtm ───▶ │ mcp-proxy :8765  /mcp  /sse          │
│   ├ memory     (stdio, local)  │            │   └▶ dtm_agent (6 tools)             │
│   └ dtm        (streamable_http)│           │        └▶ ChromaDB + Ollama + DTMKnow │
└───────────────────────────────┘            │   systemd: dtm-mcp-proxy.service     │
                                              └─────────────────────────────────────┘
```

The harness = **goose.exe + live config** on the client; the "brains" (models + DTM
knowledge) are all **remote on GB10**.

---

## 2. File & component inventory

> **HarnessAgent is now a git repo.** `origin = ssh://nvidia@192.168.86.44:/home/nvidia/Downloads/HarnessAgent`
> (canonical copy on GB10; this folder is a clone, branch `master`). On 2026-06-29 ~08:13 this
> Windows folder was refreshed to the repo HEAD, which pulled in the GB10 work (`goose_web/`,
> `dtm_mcp.sh`, `RUN.md`, `config/gb10_config.yaml`, `docker-compose.yaml` moved into `config/`)
> and removed earlier stray files. **To propagate any change, commit & push** (pushing writes
> to GB10 over SSH — do that intentionally).

### 2a. Repo contents (tracked in git)
| Path | Role | Platform |
|---|---|---|
| `README.md` | entry doc | — |
| `RUN.md` | how to launch harness + DTM + web UI | — |
| `setup_goose.ps1` / `setup_goose.sh` | one-click installers for a new machine | Win / Linux+mac |
| `dtm_mcp.sh` | stdio launcher for the DTM agent (sets venv + cwd=PersonalKnowledge) | Linux/GB10 |
| `config/windows_config.yaml` | **Windows client template** — dtm `uri` = `192.168.86.44:8765`, Windows `goose.exe` path; has deploy-location header | Win |
| `config/goose_config.yaml` | template with dtm `uri` = `127.0.0.1:8765` (GB10-local); durability note | GB10/loopback |
| `config/gb10_config.yaml` | snapshot of GB10's own normalized live config (loopback dtm) | GB10 |
| `config/docker-compose.yaml` | GB10 vLLM deploy (chat `:8000` + embed `:8001`) | GB10 |
| `goose_web/` | remote web UI: `server.py` (`goose run` HTTP bridge), `index.html`, `serve_web.sh`, `README.md` | Linux/GB10 |
| `docs/install_goose_harness_plan.md` | original plan | — |
| `docs/install_results.md` | full install + smoke-test record (+ GB10 addendum) | — |
| `docs/inventory_and_debug.md` | **this file** (currently *untracked* — commit to persist/sync) | — |
| `install/download_cli.ps1`, `install/goose-…msvc.zip` | Windows installer + 73 MB cache | Win |

> ⚠️ `config/docker-compose.yaml` (current location) vs README's link to root `docker-compose.yaml`
> — the file moved under `config/`; the README link is slightly stale.

### 2b. Live (untracked) state on THIS Windows machine
| Path | Role |
|---|---|
| `C:\Users\a9027\.local\bin\goose.exe` | the goose binary (248 MB); also self-invoked for `goose mcp memory` |
| `%APPDATA%\Block\goose\config\config.yaml` | **LIVE config — the only one goose reads** (synced from `config/goose_config.yaml`) |
| `%APPDATA%\Block\goose\data\sessions\sessions.db*` | session history |
| `%APPDATA%\Block\goose\data\logs\**` | cli logs + `llm_request.*.jsonl` (best log for debugging tool calls) |
| `%APPDATA%\Block\goose\data\projects.json`, `data\apps\*` | goose runtime data |
| User `PATH` += `…\.local\bin` | registry change (not a file) |

### 2c. Essential vs optional (to RUN a client)
**Essential (only two):** `goose.exe` + the **live** config
(`%APPDATA%\Block\goose\config\config.yaml` on Windows; `~/.config/goose/config.yaml` on Linux).
**To reproduce on a new machine:** `git clone` the repo then run `setup_goose.ps1`/`.sh`
(regenerates binary + live config); template = `config/goose_config.yaml`.
**Optional:** everything else — `docs/*`, `goose_web/` (only for the web UI), `install/*` (cache),
`dtm_mcp.sh` (only if using the **stdio** DTM path instead of the remote proxy), goose `data/`.

### 2d. Remote — used by the harness but NOT on this computer (GB10 `192.168.86.44`)
| Component | Endpoint | Role | Required for |
|---|---|---|---|
| vLLM chat | `:8000` `qwen-3.6-chat` | primary model backend | everything |
| Ollama | `:11434` (`qwen3.5:9b` etc.) | fallback model backend | when vLLM down |
| DTM MCP (mcp-proxy → dtm_agent) | `:8765/mcp` | the `dtm` extension's 6 tools | DTM queries only |
| vLLM embed | `:8001` `qwen-3-4b-embed` | embeddings (used by DTM agent internally) | DTM retrieval |
| **git origin** | `ssh://nvidia@…:/home/nvidia/Downloads/HarnessAgent` | source of truth for this repo | sync/clone |

Also remote on GB10 (not files here): the running docker-compose containers, HuggingFace model
weights cache, the DTM agent's ChromaDB (~21.8k chunks) + DTMKnowledge corpus, the
`dtm-mcp-proxy.service` (systemd), and the GB10 live config + its `config.yaml.bak`.

---

## 3. DTM MCP — debug runbook

**Architecture:** Goose (`dtm` extension, `type: streamable_http`, `uri: http://192.168.86.44:8765/mcp`)
→ **mcp-proxy** on GB10 `:8765` (`dtm_agent/run_mcp_proxy.sh`, exposes `/mcp` + `/sse`)
→ **dtm_agent** (`python -m dtm_agent mcp`, 6 tools) → ChromaDB + Ollama + vLLM `:8000/:8001`.
Persisted by systemd **`dtm-mcp-proxy.service`** (`Restart=on-failure`, `WantedBy=multi-user.target`).

### Check it from THIS (client) machine
```powershell
# 1) Is the proxy listening?
Test-NetConnection 192.168.86.44 -Port 8765        # TcpTestSucceeded = True is good
# 2) End-to-end (the real test) — drive goose to call dtm_health:
$env:GOOSE_MODE='auto'; goose run --no-session --max-turns 4 -t "Call the dtm_health tool and report its raw result."
```
**Interpreting raw HTTP probes:**
- `GET http://…:8765/mcp` → **HTTP 400 is NORMAL/healthy** (streamable HTTP needs a proper
  MCP POST + `Accept: application/json, text/event-stream`). 400/406 ≠ broken.
- **Do NOT** `Invoke-WebRequest …:8765/sse` — `/sse` is an event-stream and will **hang**
  your shell until timeout. Use the goose `dtm_health` test instead.

### Fix it on GB10 (when `:8765` is actually down)
```bash
systemctl status dtm-mcp-proxy           # is it running?
sudo systemctl restart dtm-mcp-proxy     # restart
journalctl -u dtm-mcp-proxy -f           # tail logs for crash cause
```
Cannot be fixed from the Windows client — the proxy/agent live on GB10.

### Status history
- **2026-06-28** wired in & verified end-to-end (6 tools, KB-grounded answers).
- **2026-06-29 ~08:1x** observed **DOWN** (`:8765` TCP closed, 3/3) — transient.
- **2026-06-29 ~08:24** back **UP**, `dtm_health` green (Ollama OK; collections 301 /
  9472 / 3935 / 8100 chunks). Likely recovered by `Restart=on-failure`.

### Common DTM failure modes
| Symptom | Likely cause | Action |
|---|---|---|
| `:8765` TCP closed | proxy service stopped/crashed | `systemctl restart dtm-mcp-proxy` on GB10 |
| Goose: `SSE is unsupported, migrate to streamable_http` | extension set to `type: sse` | use `type: streamable_http`, `uri: …/mcp` (Goose 1.39 dropped SSE) |
| dtm tools missing in goose | edited the versioned copy, not live config | sync to `%APPDATA%\…\config.yaml` (see §4) |
| `dtm_health` errors on ChromaDB | DTM agent venv/cwd wrong on GB10 | run via `dtm_mcp.sh` (venv + cwd=PersonalKnowledge) |

---

## 4. Debug cheat-sheet (gotchas that have actually bitten us)

1. **Goose only reads the LIVE config**, not the `HarnessAgent/config/` copy. After editing the
   reference copy:
   ```powershell
   Copy-Item .\config\goose_config.yaml $env:APPDATA\Block\goose\config\config.yaml -Force
   ```
2. **Config self-strip risk (latent).** Goose can rewrite `config.yaml` on a run and once
   dropped the provider keys + stdio extensions → `No provider configured`. Mitigation: keep a
   `config.yaml.bak` and make the live config read-only (`chmod a-w` / on Windows set the
   Read-only attribute). **Status: applied on GB10; on THIS Windows machine a `.bak` was just
   created but the live config is NOT yet read-only** — recommend setting it read-only (note:
   that will make a future `setup_goose.ps1` re-run unable to overwrite it until you clear it).
3. **vLLM tool-calling needs `--tool-call-parser qwen3_coder`** (NOT `hermes`). `qwen-3.6-chat`
   emits Qwen XML (`<function=…><parameter=…>`); hermes only parses JSON → `tool_calls: null`,
   tools silently never fire.
4. **Model speed:** qwen3.6:35b on **Ollama** stalls on tool payloads (>120 s, `stream stalled`).
   Use vLLM `:8000`, or Ollama `qwen3.5:9b` as the only fast Ollama option.
5. **`goose bench` does not exist** in 1.39.0 (aaif-goose build).
6. **⚠️ DTM `uri` differs by host — use the right config.** All three matter:
   - `config/goose_config.yaml` and `config/gb10_config.yaml` have `dtm` with
     `uri: http://127.0.0.1:8765/mcp` (**loopback — only works ON GB10**).
   - **`config/windows_config.yaml`** (the Windows client template) has
     `uri: http://192.168.86.44:8765/mcp` (remote) + the Windows `goose.exe` `memory.cmd`.
   - `setup_goose.ps1`/`.sh` add **no** dtm at all → a fresh install via the scripts won't have
     DTM until you deploy `windows_config.yaml` (or paste the dtm block) over the live config.
   On a Windows client, deploy `config/windows_config.yaml` → `%APPDATA%\Block\goose\config\config.yaml`
   (NOT the 127.0.0.1 templates).
7. **Best debug log:** `%APPDATA%\Block\goose\data\logs\llm_request.*.jsonl` shows the exact
   model requests/responses incl. tool calls — use it when a tool "didn't fire".

---

## 5. One-shot health check (copy-paste)
```powershell
$h="192.168.86.44"
"vLLM  :8000  " + (try{((Invoke-RestMethod "http://${h}:8000/v1/models" -TimeoutSec 6).data.id) -join ','}catch{"DOWN"})
"embed :8001  " + (try{((Invoke-RestMethod "http://${h}:8001/v1/models" -TimeoutSec 6).data.id) -join ','}catch{"DOWN"})
"Ollama:11434 " + (try{(Invoke-RestMethod "http://${h}:11434/api/tags" -TimeoutSec 6).models.Count}catch{"DOWN"}) + " models"
"DTM   :8765  TCP=" + (Test-NetConnection $h -Port 8765 -WarningAction SilentlyContinue).TcpTestSucceeded
# then end-to-end: $env:GOOSE_MODE='auto'; goose run --no-session --max-turns 4 -t "Call dtm_health and report raw result."
```
