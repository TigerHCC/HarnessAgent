# HarnessAgent — Goose on Windows/▪ → GB10 models

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
  RAG over ChromaDB, via `dtm_mcp.sh`)
- A tool-calling smoke test that proves it end-to-end
- See [`RUN.md`](RUN.md) for how to launch the harness with DTM support

## Backends (on GB10)
| Backend | Endpoint | Model | Notes |
|---|---|---|---|
| **vLLM** (default) | `:8000` (OpenAI-compat) | `qwen-3.6-chat` | Fast (tool task ~8s); needs tool-parser flag (below) |
| Ollama (fallback) | `:11434` | `qwen3.5:9b` | No GB10 flags; only 9b is fast enough for tools |

GB10 vLLM is deployed via [`docker-compose.yaml`](docker-compose.yaml). **Critical flag** for
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
| `setup_goose.ps1` / `setup_goose.sh` | one-click installers for a new machine |
| `dtm_mcp.sh` | launcher that exposes the PersonalKnowledge DTM agent as an MCP stdio server (venv + cwd handling) |
| `RUN.md` | how to launch the harness and use the DTM tools |
| `config/goose_config.yaml` | reference copy of the working config (incl. `dtm` extension) |
| `docker-compose.yaml` | GB10 vLLM deployment (chat + embed) |
| `docs/install_goose_harness_plan.md` | original plan |
| `docs/install_results.md` | install + smoke-test results, findings, rollback |

## Rollback
Delete `~/.local/bin/goose` and the goose config dir
(`%APPDATA%\Block\goose` on Windows, `~/.config/goose` on Linux/macOS). Nothing else is touched.

## Known notes
- `goose bench` does **not** exist in 1.39.0 (the plan's §7 eval/ratchet idea needs a redesign).
- The Goose install script is gated by Claude Code's permission classifier when run by the
  agent; these scripts are meant to be run by **you** directly (no such gate).
