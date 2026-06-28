# Running the Goose Harness Agent (with DTM Knowledge Agent MCP)

The DTM MCP server is wired into Goose as the `dtm` extension and **starts
automatically** when Goose launches — there is no separate server to start.
Goose spawns `HarnessAgent/dtm_mcp.sh` as a subprocess on startup, which runs
`python -m dtm_agent mcp` from the PersonalKnowledge venv.

## 1. Preflight (optional) — confirm the backends are up
```bash
curl -sf http://192.168.86.44:8000/v1/models >/dev/null && echo "vLLM chat  :8000 OK"   # generation (qwen-3.6-chat)
curl -sf http://localhost:8001/v1/models     >/dev/null && echo "vLLM embed :8001 OK"   # DTM retrieval (qwen-3-4b-embed)
```
If a backend is down:
```bash
cd ~/Downloads/PersonalKnowledge && docker compose up -d   # starts qwen-chat (:8000) + qwen-embed (:8001)
```

## 2. Launch the harness
Interactive REPL (run in your own terminal):
```bash
goose session
```
Headless / scripted (auto-approves tool calls):
```bash
GOOSE_MODE=auto goose run --no-session -t "your task here"
```
> If `goose` is not found: `export PATH="$HOME/.local/bin:$PATH"` (add to `~/.bashrc` to persist).

## 3. Use the DTM tools
Available tools: `dtm_query` · `dtm_telemetry_lookup` · `dtm_triage` ·
`dtm_data_feature` · `dtm_hw_spec` · `dtm_health`

```bash
# auto-routing front door
GOOSE_MODE=auto goose run --no-session -t "Use dtm_query: customer laptop runs hot and battery drains fast"

# specific specialists
GOOSE_MODE=auto goose run --no-session -t "Use dtm_telemetry_lookup to find datatypes for SSD wear and NVMe SMART health"
GOOSE_MODE=auto goose run --no-session -t "Use dtm_triage for: BSOD WHEA_UNCORRECTABLE_ERROR after dock hotplug"
GOOSE_MODE=auto goose run --no-session -t "Use dtm_hw_spec to look up SMBIOS Type 17 memory fields"

# inside an interactive `goose session`, just type:
#   use dtm_health to check the DTM agent
```

## Notes
- A successful DTM call shows a `▸ dtm_telemetry_lookup dtm` block in the output —
  that confirms it routed through the DTM RAG, not the base `qwen-3.6-chat` model.
- The **first** DTM query after a fresh start is slow (~20 s routing-centroid warmup
  + embedding + specialist LLM). The `dtm` extension timeout is set to 600 s to absorb this.
- To run **without** DTM: set `enabled: false` under the `dtm:` extension in
  `~/.config/goose/config.yaml` (or `goose configure` → Toggle Extensions).
- Switch model backend: edit `~/.config/goose/config.yaml` — `GOOSE_PROVIDER: openai`
  (vLLM, default) or `ollama` (fallback, `qwen3.5:9b`).

## What this depends on (see README "Files needed")
- Goose binary `~/.local/bin/goose` + config `~/.config/goose/config.yaml`
- `HarnessAgent/dtm_mcp.sh` (the DTM launcher)
- PersonalKnowledge: `venv/`, `dtm_agent/`, `config.yaml` (dtm_agent section),
  `chromadb/` (built index), `DTMKnowledge/` (source KB)
- Model services: vLLM `:8000` + `:8001` (Ollama `:11434` fallback)
