# Windows Audio Diagnostics MCP

An **elevated, loopback** MCP server giving the Windows goose harness audio diagnostics tools for
**hardware/driver health**, **codec/role defaults**, **microphone privacy**, and **Bluetooth profiles**.
Reads from Core Audio (pycaw/comtypes, with graceful degradation), PnP device state, Windows Registry,
system services, and optional ETW glitch traces. goose (user mode) connects over `http://127.0.0.1:8796/mcp`
(streamable HTTP) so the server can run elevated while goose stays unprivileged. See `DESIGN.md` / `PLAN.md`.

## Tools (7)

| Tool | Purpose |
|---|---|
| `audio_health()` | Audio-stack health + red-flag summary: service status, no-multimedia/no-communications output, microphone privacy denials |
| `audio_devices()` | All render + capture endpoints with state (Active/Unplugged/Disabled/NotPresent), flow, PnP driver/bus view |
| `audio_defaults()` | Default device per role (console/multimedia/communications) for render + capture, flagging no_multimedia_output / no_communications_output |
| `audio_microphone()` | Microphone diagnosis: default capture presence/state + Windows mic privacy (global + per-app denies) |
| `audio_bluetooth()` | Bluetooth audio devices with active profile (a2dp stereo-media vs hfp mono-call); flags HFP-only (no media) / A2DP-only (no call-mic) |
| `audio_sessions()` | Per-app audio sessions (process, state) — find an app that is muted/inactive in the mixer |
| `audio_glitches(trace_seconds)` | Glitch/stutter diagnosis: risk indicators (sample-rate mismatch, recent audio driver errors) always; if trace_seconds > 0, runs an elevated ETW trace |

## Symptom → Tool Map

| Symptom | Tool(s) | Data Source |
|---|---|---|
| No multimedia output (speakers silent) | `audio_health`, `audio_defaults`, `audio_devices` | Core Audio default roles, pycaw endpoint state |
| No meeting/communications output (Teams/Zoom silent) | `audio_health`, `audio_defaults` | Core Audio communications default, pycaw |
| Microphone not working | `audio_microphone`, `audio_health` | pycaw capture defaults, Windows Privacy (registry) |
| Audio glitches / stuttering | `audio_glitches` | ETW trace (Audio Diagnostics, Kernel General), risk indicators (sample-rate, driver errors) |
| Bluetooth speaker/headset issues | `audio_bluetooth`, `audio_devices` | PnP device class/name, active profile detection |
| App audio muted in mixer | `audio_sessions` | pycaw session state, mute flag |
| Hardware/driver health overview | `audio_health`, `audio_devices` | Windows Audio service (Audiosrv), PnP status, pycaw availability |

## Install

```powershell
cd HarnessAgent\mcp\windows_audio
python -m pip install -r requirements.txt
```

## Run (ELEVATED for ETW trace and Core Audio access)

- On demand: PowerShell **as Administrator** → `.\start_audio_mcp.ps1` (if it exists; otherwise use install_task)
- Persistent (auto-start elevated at logon): as Administrator → `.\install_task.ps1` then
  `Start-ScheduledTask -TaskName mcp-audio`. Remove with `.\uninstall_task.ps1`.

## Wire into goose

The `audio` extension is in `config/windows_config.yaml`:

```yaml
  audio:
    type: streamable_http
    bundled: false
    name: audio
    enabled: true
    uri: http://127.0.0.1:8796/mcp
    headers: {}
    env_keys: []
    timeout: 120
    description: Windows Audio Diagnostics (hardware/codec/privacy/glitch) via local elevated MCP server (127.0.0.1:8796)
```

Deploy it to the live config:

```powershell
Copy-Item ..\..\config\windows_config.yaml "$env:APPDATA\Block\goose\config\config.yaml" -Force
```

## Verify

```powershell
$env:GOOSE_MODE="auto"
goose run --no-session -t "Call audio_health, then audio_devices, then audio_defaults."
goose run --no-session -t "Check audio_microphone and audio_bluetooth."
goose run --no-session -t "Call audio_glitches (no trace, the default) to check for risk indicators."
```

## Notes / Gotchas

- **Server MUST be elevated** for ETW tracing (`audio_glitches` with `trace_seconds > 0`). Core Audio queries work as a normal user; ETW requires admin.
- **Read-only** — no tool changes, mutes, or adjusts any audio setting. Diagnoses only, recommends remediation.
- **pycaw is optional** — if pycaw/comtypes is missing, all Core Audio queries return `{"available": False, "error": ...}`, but `audio_glitches` risk indicators and `audio_bluetooth` (PnP-based) continue working.
- **Glitch trace is optional and off by default**: `config.json` sets `trace_seconds_default: 0`. Pass `trace_seconds > 0` to trigger ETW. Trace runs up to the configured `trace_max_seconds` (default 30) to bound resource use. The trace is elevated (requires the server to be running as Administrator).
- Goose 1.39 uses `streamable_http` + `/mcp` (NOT `sse`).
- Core Audio enumerates endpoints even when pycaw is unavailable; the MCP gracefully falls back to reporting `coreaudio_available: False` with explanatory messages.

## Files

| File | Purpose |
|---|---|
| `windows_audio_mcp_server.py` | FastMCP server (7 tools, serves 127.0.0.1:8796) |
| `coreaudio.py` | Core Audio (pycaw/comtypes) wrappers + graceful degradation for defaults, devices, sessions |
| `winaudio.py` | Windows-specific audio queries: PnP audio devices, services, microphone privacy, Bluetooth profile classification |
| `glitch.py` | Glitch risk indicators + optional ETW Audio Diagnostics trace |
| `config.py`, `config.json` | Trace timeout/max, defaults (env-override via WINAUDIO_MCP_* keys) |
| `start_audio_mcp.ps1` | On-demand launch script (if present) |
| `install_task.ps1`, `uninstall_task.ps1` | Scheduled Task registration (elevated, at logon) |
| `requirements.txt` | mcp, anyio, pycaw, comtypes, pytest |
| `tests/` | pytest unit + smoke tests |

Batch-test this server with the [central MCP suite instructions](../README.md#test-all-local-mcp-servers).
