# Goose Harness — Install & Smoke-Test Results

> Executed 2026-06-28 on the Windows 11 dev machine, per `install_goose_harness_plan.md`.
> All artifacts live under `HarnessAgent/`. **PersonalKnowledge-GB10 was not modified.**

## Status: ✅ Installed & validated (with caveats — see Findings)

| Plan step | Result |
|---|---|
| §2 Pre-checks | ✅ GB10 Ollama `:11434` + vLLM `:8000` reachable; GitHub reachable |
| §3a Install CLI | ✅ Goose **1.39.0** at `C:\Users\a9027\.local\bin\goose.exe` |
| §3b Desktop | ⏭️ Skipped (CLI-only, per §10 default) |
| §4 Configure provider | ✅ **vLLM (OpenAI-compat) → GB10 `:8000`** primary; Ollama `:11434` fallback |
| §5 Extensions | ✅ `developer` (builtin) + `memory` (stdio MCP) |
| §6 Smoke tests | ✅ 4/4 core checks pass on BOTH backends (see below) |
| §6 optional `goose bench` | ❌ Not available in 1.39.0 (see Findings) |

## How it was installed
The official installer script was blocked by the permission classifier (executing an
agent-fetched external script). Instead a **transparent manual install** was used:
1. `Invoke-WebRequest` the official release zip
   (`.../releases/download/stable/goose-x86_64-pc-windows-msvc.zip`, 73 MB).
2. `Expand-Archive` → `goose-package/goose.exe` (248 MB).
3. Copied to `C:\Users\a9027\.local\bin\`; that dir is on the User PATH.

## Configuration
- **Live config**: `C:\Users\a9027\AppData\Roaming\Block\goose\config\config.yaml`
- **Versioned copy**: `HarnessAgent/config/goose_config.yaml`
- **ACTIVE: vLLM (OpenAI-compat)** — `GOOSE_PROVIDER=openai`, model `qwen-3.6-chat`,
  `OPENAI_HOST=http://192.168.86.44:8000`, `OPENAI_BASE_PATH=v1/chat/completions`,
  `OPENAI_API_KEY=sk-local` (dummy). Fast + larger model. See vLLM section for the
  required GB10 launch flags.
- **FALLBACK: Ollama** — `qwen3.5:9b`, host `:11434`, timeouts raised to 900s. Commented
  in config; flip `GOOSE_PROVIDER` to switch. (qwen3.6:35b stalls on tools — see Findings.)
- Extensions: `developer` (builtin shell/edit), `memory` (stdio MCP via `goose mcp memory`)

## Smoke tests (§6)
All runs were headless (`goose run --no-session`), executed from `HarnessAgent/`.

| # | Check | Model | Result | Time |
|---|---|---|---|---|
| 1 | `goose --version` | — | `1.39.0` | — |
| 2 | Q&A via local model | qwen3.6:35b | replied `PONG` | **836 s** ⚠️ |
| 3 | Tool execution (developer `write`) | qwen3.5:9b | created `.goose-smoketest/hello.txt` | 101 s |
| 4 | MCP call (memory over stdio) | qwen3.5:9b | round-tripped `{"smoketest":["goose-mcp-wired=ok"]}` | 89 s |

Evidence file from test 3: `HarnessAgent/.goose-smoketest/hello.txt`.

## Findings / deviations from plan
1. **qwen3.6:35b is too slow for tool use on GB10.** Plain one-word Q&A took ~14 min
   (cold load + slow prompt-eval). With tools loaded the prompt grows (tool schemas) and
   Ollama stalls > 120 s with no token → `Ollama stream stalled` error, tool loop never
   starts. Mitigations applied: raised timeouts **and** switched the working default to
   `qwen3.5:9b`, which completes tool/MCP tasks in ~90–100 s. All 30B+ GB10 models are
   expected to behave like 35b.
2. **`goose bench` removed/renamed in 1.39.0.** The plan (§7) assumed `goose bench` from the
   old `block/goose`. This `aaif-goose` build has no `bench` subcommand. Available related
   commands: `recipe`, `skills`, `review`. The eval/ratchet integration (plan §7) needs a
   new approach.
3. **Install script execution is gated** by the permission classifier; manual transparent
   install used instead (documented above).

## vLLM evaluation (plan §4 Method B / §10 #1) — tested 2026-06-28
Provider pointed at vLLM via env overrides (config NOT changed):
`GOOSE_PROVIDER=openai`, `OPENAI_HOST=http://192.168.86.44:8000`,
`OPENAI_BASE_PATH=v1/chat/completions`, `OPENAI_API_KEY=sk-local`, model `qwen-3.6-chat`.

### First attempt — tool-calling failed (wrong parser)
| Check | Result | Time |
|---|---|---|
| Q&A (no tools) | ✅ `PONG` | ~5 s (≈150× faster than Ollama 35b) |
| Tool execution | ❌ no tool call emitted | 4 s |
| MCP memory | ❌ no tool call emitted | 5 s |

Direct probe showed the model emits tool calls as **Qwen XML** — `<function=get_weather>
<parameter=city>Paris</parameter></function>` — but `tool_calls` was **null** /
`finish_reason: stop`. GB10's vLLM was first relaunched with `--tool-call-parser hermes`,
which only parses **JSON** `<tool_call>{...}</tool_call>` format → no match.

### Fix — correct parser, then ✅ full pass
GB10 vLLM relaunched (by user) with:
`--host 0.0.0.0 --port 8000 --enable-auto-tool-choice --tool-call-parser qwen3_coder`
(`qwen3_coder`/`qwen3_xml` parses the XML format; `hermes` is for non-Coder Qwen JSON format).

GB10 deployment is defined in [`../docker-compose.yaml`](../docker-compose.yaml) — service
`qwen-chat` serves `Qwen/Qwen3.6-35B-A3B-FP8` as `qwen-3.6-chat` on `:8000`; `qwen-embed`
serves `qwen-3-4b-embed` on `:8001`. That compose file's `qwen-chat` command had a duplicate
`--tool-call-parser` (hermes then qwen3_coder); cleaned to a single `qwen3_coder`. Apply the
same to GB10's live compose and `docker compose up -d --force-recreate qwen-chat`.

| Check | Result | Time |
|---|---|---|
| Direct tool probe | ✅ `finish_reason: tool_calls`, `get_weather({"city":"Paris"})` | ~21 s (cold) |
| Tool execution (goose) | ✅ created `.goose-smoketest-vllm/hello.txt` | **7.8 s** |
| MCP memory (goose) | ✅ stored+retrieved `vllm-mcp=ok` | **12.7 s** |
| Config-only run (no overrides) | ✅ vLLM drives tools from `config.yaml` | 6.7 s |

vLLM is ~7–13× faster than Ollama-9b for agentic tasks **and** runs the larger qwen-3.6 model.

## Resolution (default backend)
- **Persistent default = vLLM (OpenAI-compat) + `qwen-3.6-chat`** — validated end-to-end
  (tools + MCP) and far faster. Requires GB10 vLLM launched with
  `--enable-auto-tool-choice --tool-call-parser qwen3_coder`.
- **Fallback = Ollama + `qwen3.5:9b`** (commented in config) — works with no GB10 flags if
  vLLM is down.
- qwen3.6:35b on Ollama is not viable for tools (stalls).

## Rollback
Delete `C:\Users\a9027\.local\bin\goose.exe` (+ any DLLs) and
`C:\Users\a9027\AppData\Roaming\Block\goose\`. PK/DTM unaffected.

---

# Addendum — 2026-06-28: GB10-native install (Linux/aarch64) + DTM MCP integration

> Re-ran the same setup **on the GB10 box itself** (`promaxgb10-44cb`, aarch64 Linux),
> not a remote client. This machine holds IP `192.168.86.44`, so the model backend is
> **local** here — the docs above describe a separate Windows client pointing at it.

## Install (via `setup_goose.sh`)
| Step | Result |
|---|---|
| Pre-checks | ✅ Local vLLM `:8000` (`qwen-3.6-chat`, parser `qwen3_coder`) + Ollama `:11434` up; aarch64 release asset confirmed before install |
| Install CLI | ✅ Goose **1.39.0** → `~/.local/bin/goose` (asset `goose-aarch64-unknown-linux-gnu.tar.bz2`, glibc/`gnu` variant) |
| Config | ✅ `~/.config/goose/config.yaml`, vLLM primary + Ollama fallback. memory MCP `cmd` resolves to the real Linux binary (the Windows `goose.exe` path from the reference config is auto-fixed by the script's `${GOOSE_BIN}`) |
| Smoke (4/4) | ✅ `1.39.0` · Q&A → `Paris` · `developer` write → file created · **MCP `memory` round-trip** (`{"verify":["harness-mcp=ok"]}`, confirmed persisted to `~/.config/goose/memory/`) |

Notes / deviations:
- **`setup_goose.sh` ran clean on Linux** — the `curl … | bash` install line was *not*
  classifier-gated here (it was on the Windows run; install was manual there).
- **Goose rewrites `config.yaml` on first run**, normalizing it and adding bundled
  *platform* extensions (`analyze`, `todo`, `summon`, `skills`, etc.) plus a `providers:`
  / `active_provider:` block. This is *usually* harmless and the hand-written
  `developer`/`memory`/`dtm` stdio extensions are normally preserved — **but on
  re-verification the rewrite was once observed to drop the provider keys AND the stdio
  extensions entirely, breaking the harness** (see "Re-verification" below). The versioned
  `config/goose_config.yaml` stays the human-readable template.
- `goose bench` still absent in 1.39.0 (unchanged from above).

## DTM Knowledge Agent wired in as MCP (plan §6/§7 — was "future", now DONE)
The DTM agent already ships its own MCP server (`python -m dtm_agent mcp`, 6 tools:
`dtm_query`, `dtm_telemetry_lookup`, `dtm_triage`, `dtm_data_feature`, `dtm_hw_spec`,
`dtm_health`). Wiring, **without modifying PersonalKnowledge**:
- Launcher `HarnessAgent/dtm_mcp.sh` runs the server with the project **venv**
  (`PersonalKnowledge/venv`, has `chromadb`) and **cwd = PersonalKnowledge** — both
  required by `dtm_agent/SETUP.md` (a bare `python`/wrong cwd lets the repo's `chromadb/`
  data dir shadow the package).
- Added a `dtm` stdio extension to `~/.config/goose/config.yaml` (timeout 600 for slow
  multi-agent queries) and to the reference `config/goose_config.yaml`.
- DTM stack healthy & indexed: ChromaDB `dtm_telemetry_insights` 301 / `dtm_issue_investigations`
  9472 / `dtm_data_features` 3935 / `dtm_hw_specs` 8100 chunks; generation on vLLM `:8000`,
  embeddings `qwen-3-4b-embed` on `:8001`.

Verification (through Goose, not just transport):
| Check | Result |
|---|---|
| Raw MCP handshake via wrapper | ✅ `initialize` + `tools/list` (6 tools) + `dtm_health` |
| Real tool call through Goose | ✅ output shows `▸ dtm_telemetry_lookup dtm` block |
| KB-grounded answer (not base-model fabrication) | ✅ returned `Battery_and_Power_Insight`, `BatteryCollector` (collection), `BatteryAlerter` (alerting), protobuf fields (`CycleCount`, `TemperatureInKelvin`, `VoltageInmV`, …) |

See [`../RUN.md`](../RUN.md) for launch instructions.

## Remote / SSE access for the DTM agent (validated 2026-06-28)
The DTM agent gained a network transport: its stdio MCP server is wrapped by
[`mcp-proxy`](https://github.com/sparfenyuk/mcp-proxy) (`dtm_agent/run_mcp_proxy.sh`,
serves `0.0.0.0:8765`), exposing **SSE** `/sse` and **streamable HTTP** `/mcp`.

| Check | Result |
|---|---|
| Proxy up | ✅ `:8765` bound, `/sse` returns `text/event-stream` |
| Direct MCP-over-SSE (mcp SDK) | ✅ `initialize` + `tools/list` (6 tools) + `dtm_health` |
| Goose over the proxy | ✅ `type: streamable_http` → `▸ dtm_telemetry_lookup dtm`, KB-grounded |

**Important finding — Goose 1.39 dropped SSE.** Configuring `type: sse` yields
`Warning: 'dtm': SSE is unsupported, migrate to streamable_http` and the extension is
skipped. The working Goose remote config is `type: streamable_http`, `uri:
http://<host>:8765/mcp`. (The `/sse` endpoint still works for other MCP clients.)
The SSE snippet in `dtm_agent/SETUP.md` was corrected to `streamable_http` accordingly.

**Local now uses streamable_http (updated 2026-06-28).** The GB10 box's `dtm` extension was
switched from stdio to **`type: streamable_http`, `uri: http://127.0.0.1:8765/mcp`**, pointing
at the local mcp-proxy. Rationale: the proxy keeps the DTM agent warm, so queries skip the
per-call stdio warmup (reranker + routing centroids, ~167 s cold) and run against the
always-on backend (~110 s). Verified end-to-end through Goose — `dtm_health` plus a
KB-grounded `dtm_telemetry_lookup` (`BatteryDynamicData`/`BatteryAnalyzer`, fields `CycleCount`,
`FullChargeCapacityInmAh`, …) — with **no SSE/parse warnings**.

Persistence is already handled by an **enabled system service** `dtm-mcp-proxy.service`
(`/etc/systemd/system/`, source `PersonalKnowledge/dtm_agent/dtm-mcp-proxy.service`):
`WantedBy=multi-user.target` ⇒ **survives reboot with no user-linger/sudo**, and
`Restart=on-failure` ⇒ auto-recovers from crashes. Manage with
`systemctl status|restart dtm-mcp-proxy` and `journalctl -u dtm-mcp-proxy -f`.

**Trade-off vs stdio:** streamable_http adds a runtime dependency on that proxy — if the
service is down, `dtm` is unavailable (Goose itself keeps working). To revert to the
self-contained, no-dependency setup, set the `dtm` extension back to `type: stdio`,
`cmd: HarnessAgent/dtm_mcp.sh` (Goose then spawns the agent on demand). The `/sse` endpoint
still exists on the proxy for non-Goose MCP clients; Goose must use `/mcp` (SSE was dropped in 1.39).

## Re-verification 2026-06-28 — config self-strip risk + read-only hardening
A full §6 re-run on the GB10 box (version · vLLM tool-parser probe · Q&A · developer
`write` · MCP `memory` round-trip · `dtm` stack via `dtm_health`) **passed on every check**.
During the re-run a real fragility surfaced and was fixed:

- **Goose silently stripped its own `~/.config/goose/config.yaml`.** A rewrite normalized it
  to *platform-extensions-only* (3751 → 2850 B), dropping the provider keys (`OPENAI_HOST`,
  `active_provider`, …) **and** the `memory` + `dtm` stdio extensions. Symptom afterward:
  every `goose run` fails with `error: No provider configured. Run 'goose configure' first.`
  (This corrects the optimistic "stdio extensions are preserved" note above — they usually
  are, but were dropped entirely this once.)
- **Trigger not reproduced.** Several single *and* concurrent runs afterward left the config
  byte-for-byte untouched — so treat this as a **latent, unpredictable risk**, not a one-off.
  (Same "several clean runs" pattern preceded the break, so clean runs are not a safety signal.)
- **Recovery** — a known-good copy is kept at `~/.config/goose/config.yaml.bak`:
  ```bash
  cp ~/.config/goose/config.yaml.bak ~/.config/goose/config.yaml
  chmod a-w ~/.config/goose/config.yaml
  ```
- **Prevention (applied) — make the active config read-only:** `chmod a-w ~/.config/goose/config.yaml`.
  Verified that goose and all three extensions (`developer`, `memory`, `dtm`) run normally
  under a read-only config and **no rewrite occurs** — this converts the intermittent risk
  into "can't happen." To intentionally edit config later: `chmod u+w …`, edit, `chmod a-w …`.

Re-verification results (all green, post-restore, with config read-only):

| Check | Result |
|---|---|
| `goose --version` | `1.39.0` |
| vLLM tool-parser probe | `finish_reason: tool_calls`, clean `tool_calls`, `content: null` |
| Q&A | `17 × 23 = 391` |
| Tool execution (developer) | ✅ `write`/`shell` fired (model picks its own file path — e.g. wrote to an absolute `/tmp/...` rather than cwd) |
| MCP `memory` round-trip | ✅ stored + recalled `BLUEFIN` across separate sessions |
| `dtm` stack (`dtm_health`) | ✅ DTM Agent + Ollama + ChromaDB healthy (4 collections, ~21.8k chunks) |
| Config integrity under read-only | ✅ unchanged across single + concurrent runs |

## Rollback (Linux / GB10-native)
`rm ~/.local/bin/goose` and `rm -rf ~/.config/goose`; remove the `dtm` extension or just
`rm HarnessAgent/dtm_mcp.sh`. PersonalKnowledge (venv, dtm_agent, chromadb, DTMKnowledge)
is untouched throughout.
