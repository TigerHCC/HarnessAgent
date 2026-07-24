# Goose with local llama.cpp chat and CPU embedding on Windows

This is the verified local Windows/WSL configuration for Goose and Goose Web.

## Model endpoints

| Role | Endpoint | Model | Runtime |
|---|---|---|---|
| Chat | `http://127.0.0.1:8000/v1/chat/completions` | `qwen3.6-27b-q3ks` | CUDA llama.cpp |
| Embedding | `http://127.0.0.1:8001/v1/embeddings` | `qwen-3-4b-embed` | CPU llama.cpp |

Chat uses approximately 13.9 GB of the 16 GB GPU. Embedding is intentionally
CPU-only, so it does not reduce chat GPU headroom. It reuses the GGUF installed
by Windows Ollama. The embedding response contains 2560-dimensional normalized
vectors matching the existing Personal and Telemetry ChromaDB collections.

## Goose configuration

The live file is:

```text
C:\Users\Dell\AppData\Roaming\Block\goose\config\config.yaml
```

Its chat-provider settings are:

```yaml
GOOSE_PROVIDER: openai
GOOSE_MODEL: qwen3.6-27b-q3ks
OPENAI_HOST: http://127.0.0.1:8000
OPENAI_BASE_PATH: v1/chat/completions
OPENAI_API_KEY: sk-local
GOOSE_CONTEXT_LIMIT: 163840
```

Goose has no independent embedding-provider field. The `pk` and `dtm` MCP
services make retrieval embedding calls. They must use:

```yaml
host: http://127.0.0.1:8001
path: v1/embeddings
model: qwen-3-4b-embed
dimensions: 2560
```

Do not add invented embedding keys to Goose's top-level configuration; Goose
may reject or rewrite unsupported settings.

## Goose Web

`goose_web/config.json` contains separate health rows for chat and embedding.
After changing that file, restart Goose Web:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File .\goose_web\start_vllm_web.ps1
```

Open `http://127.0.0.1:8799`. The health response should show both backends:

```powershell
Invoke-RestMethod http://127.0.0.1:8799/api/health |
  Select-Object model, provider, backends
```

## Start after a Windows reboot

The `dtm`, `pk`, diagnostic MCP, and watchdog scheduled tasks start when the
current user signs in. The Ubuntu WSL model services and Goose Web are started
together with:

```powershell
cd C:\Users\Dell\Downloads\DTMAgentic\HarnessAgent\HarnessAgent-main

powershell -NoProfile -ExecutionPolicy Bypass `
  -File .\goose_web\start_vllm_web.ps1
```

This script starts a hidden Ubuntu WSL keepalive, runs the explicit Windows
llama.cpp compose file, starts `qwen-chat` on port 8000 and
`qwen-embed-cpu` on port 8001, waits for chat health, and serves Goose Web on
port 8799. Keep the PowerShell process running while using Goose Web.

To start only the two model endpoints:

```powershell
$compose = '/mnt/c/Users/Dell/Downloads/DTMAgentic/HarnessAgent/HarnessAgent-LLM-main/docker-compose.windows.llamacpp.yaml'

Start-Process wsl.exe -WindowStyle Hidden `
  -ArgumentList @('-d','Ubuntu','--','sleep','infinity')

wsl -d Ubuntu -- docker compose -f $compose up -d qwen-chat qwen-embed-cpu
```

For the full Alienware reboot checklist and installation on another 16 GB GPU
device, see
`..\..\HarnessAgent-LLM-main\SETUP_WINDOWS_ALIENWARE.md`.

## Verification

```powershell
# Chat
$chat = @{
  model = 'qwen3.6-27b-q3ks'
  messages = @(@{ role = 'user'; content = 'Reply exactly: hello' })
} | ConvertTo-Json -Depth 5
Invoke-RestMethod -Method Post http://127.0.0.1:8000/v1/chat/completions `
  -ContentType application/json -Body $chat

# Embedding
$embed = @{
  model = 'qwen-3-4b-embed'
  input = 'embedding test'
} | ConvertTo-Json
$response = Invoke-RestMethod -Method Post http://127.0.0.1:8001/v1/embeddings `
  -ContentType application/json -Body $embed
$response.data[0].embedding.Count # expected: 2560
```

The model-server installation and management instructions are in
`..\HarnessAgent-LLM-main\INSTALL_LLAMACPP_WINDOWS.md`.
