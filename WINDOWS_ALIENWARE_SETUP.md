# Windows Alienware deployment

This is the master runbook for the four-repository deployment on the Alienware
16 with an NVIDIA RTX 4090 Laptop GPU and 16 GB VRAM.

## Repository layout

Keep the four repositories as siblings:

```text
HarnessAgent\
  HarnessAgent-LLM-main\
  HarnessAgent-main\
  HarnessAgent-MCP-PersonalKb-main\
  HarnessAgent-MCP-TelemetryKb-main\
```

Each repository has its own `WINDOWS_ALIENWARE_SETUP.md`. Follow them in this
order:

1. `HarnessAgent-LLM-main`: install WSL Docker, chat model, and embedding model.
2. `HarnessAgent-main`: install Goose, Windows MCP servers, and Goose Web.
3. `HarnessAgent-MCP-PersonalKb-main`: install the Personal KB proxy.
4. `HarnessAgent-MCP-TelemetryKb-main`: install the Telemetry KB proxy.

## Verified topology

| Port | Service | Runtime |
|---:|---|---|
| 8000 | Qwen3.6-27B Q3_K_S, 163,840 context, Q4 KV | CUDA llama.cpp in WSL Docker |
| 8001 | Qwen3 Embedding 4B, 2560 dimensions | CPU llama.cpp in WSL Docker |
| 8765 | Telemetry KB MCP (`dtm`) | Windows Scheduled Task |
| 8766 | Personal KB MCP (`pk`) | Windows Scheduled Task |
| 8777-8793, 8796 | HarnessAgent MCP services | Windows Scheduled Tasks |
| 8794-8795 | MarkItDown and DocStruct MCP | Windows Scheduled Tasks |
| 8799 | Goose Web | Windows Scheduled Task |

The embedding service must remain 2560-dimensional. The existing ChromaDB
collections were built with that dimension; do not rebuild them merely to
change the inference runtime.

## Prerequisites

- Windows 11 with current NVIDIA driver
- WSL 2 with an Ubuntu distribution
- Docker Engine inside Ubuntu WSL
- Python 3.10 or newer on Windows
- PowerShell 5.1
- Git
- Approximately 20 GB free for model files, plus space for the two KB datasets

## Fresh installation

### 1. Install local models

From a normal Windows PowerShell:

```powershell
cd C:\path\to\HarnessAgent-LLM-main
powershell -NoProfile -ExecutionPolicy Bypass `
  -File .\install_llamacpp_windows.ps1 -Distro Ubuntu
```

Verify:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
Invoke-RestMethod http://127.0.0.1:8001/health
```

### 2. Install Goose and core MCP services

Install Goose as the current user:

```powershell
cd C:\path\to\HarnessAgent-main
powershell -NoProfile -ExecutionPolicy Bypass -File .\setup_goose.ps1
```

Open PowerShell as Administrator, then install the MCP services, watchdog,
extras, and Goose Web:

```powershell
cd C:\path\to\HarnessAgent-main
powershell -NoProfile -ExecutionPolicy Bypass `
  -File .\setup_mcp_servers.ps1

powershell -NoProfile -ExecutionPolicy Bypass `
  -File .\goose_web\install_web_task.WINDOWS_ALIENWARE.ps1
```

Use `-SkipSysmon` only when Sysmon installation or its EULA acceptance is not
wanted.

The generic `goose_web/config.json` belongs to the shared/Linux-oriented
configuration. Alienware startup uses
`goose_web/config.WINDOWS_ALIENWARE.json`; the second command replaces the
generic GooseWeb task with an Alienware-specific task. Do not replace the
generic file.

### 3. Install Personal KB without rebuilding the index

From normal PowerShell:

```powershell
cd C:\path\to\HarnessAgent-MCP-PersonalKb-main
Copy-Item .\config.WINDOWS_ALIENWARE.yaml .\config.yaml -Force
powershell -NoProfile -ExecutionPolicy Bypass `
  -File .\setup_win11.ps1 -ModelHost 127.0.0.1
```

The repository must already contain the compatible `chromadb`, `kb`, and data
directories. Do not pass `-BuildIndex`.

### 4. Install Telemetry KB without rebuilding the index

```powershell
cd C:\path\to\HarnessAgent-MCP-TelemetryKb-main
Copy-Item .\config.WINDOWS_ALIENWARE.yaml .\config.yaml -Force
powershell -NoProfile -ExecutionPolicy Bypass `
  -File .\setup_win11.ps1 -ModelHost 127.0.0.1
```

The repository must already contain `DTMKnowledge` and the compatible
`chromadb` directory. Do not pass `-BuildIndex`.

The two copy commands create the runtime `config.yaml` required by the current
KB applications. The tracked generic/Linux `config.yaml` is not changed by
this deployment commit; only the local working copy is replaced at install
time.

## Start after reboot

The MCP proxies, diagnostic MCPs, and Goose Web are logon Scheduled Tasks.
Docker runs inside WSL, so start the model stack first:

```powershell
cd C:\path\to\HarnessAgent-main
powershell -NoProfile -ExecutionPolicy Bypass `
  -File .\goose_web\start_vllm_web.ps1
```

This keeps WSL alive, starts both llama.cpp containers, waits for port 8000,
and runs the Windows Goose Web backend. When the `GooseWeb` Scheduled Task is
installed, the normal daily operation is:

```powershell
schtasks /Run /TN GooseWeb
schtasks /Run /TN pk-mcp-proxy
schtasks /Run /TN dtm-mcp-proxy
```

To restart Goose Web:

```powershell
schtasks /End /TN GooseWeb
schtasks /Run /TN GooseWeb
```

Open `http://127.0.0.1:8799`.

## Required Goose provider settings

The live file is
`%APPDATA%\Block\goose\config\config.yaml`:

```yaml
GOOSE_PROVIDER: openai
GOOSE_MODEL: qwen3.6-27b-q3ks
OPENAI_HOST: http://127.0.0.1:8000
OPENAI_BASE_PATH: v1/chat/completions
OPENAI_API_KEY: sk-local
GOOSE_CONTEXT_LIMIT: 163840
```

## Verification

```powershell
Invoke-RestMethod http://127.0.0.1:8000/props |
  Select-Object model_alias, total_slots, default_generation_settings
Invoke-RestMethod http://127.0.0.1:8001/health
Invoke-RestMethod http://127.0.0.1:8799/api/health

cd C:\path\to\HarnessAgent-main
powershell -NoProfile -ExecutionPolicy Bypass `
  -File .\test_mcp_servers.ps1
```

Also verify that these return MCP responses:

```text
http://127.0.0.1:8765/mcp
http://127.0.0.1:8766/mcp
```

In Goose Web, test:

```text
Search the knowledge base for SSD wear and NVMe SMART health
```

## Files not used by this Alienware deployment

These files may still be useful for Linux, other GPUs, or fallback operation.
They should not be followed for this machine:

- `goose_web/start_ollama_web.ps1`: Ollama chat fallback only.
- `config/docker-compose.yaml`: older container-oriented HarnessAgent path.
- `docs/GOOSE_OLLAMA_RUNBOOK.md`: fallback documentation.
- `docs/GOOSE_LLAMACPP_WINDOWS.md`: detailed background; this file is the
  deployment entry point.
- `*-backup`, `diff*.diff`, `logs/`, and test files under `workspace/`: local
  artifacts, not installation sources.

Do not delete Linux documentation or Linux scripts. They remain the supported
Linux deployment path.
