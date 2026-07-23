# Windows Audio Diagnostics MCP Design

## Goal

Give the agent a read-only audio-diagnostics MCP that pinpoints the common Windows audio failures:
no multimedia output, no meeting/communications output, microphone not working, glitchy/stuttering
output — across Bluetooth, USB, and onboard/wired devices. It reports and recommends; it never changes
audio settings.

## Why / research (validated on this machine 2026-07-24)

- **Core Audio via pycaw + comtypes** (pip-installable, verified: 25 endpoints enumerated) is the
  richest source: per-endpoint state (Active / Unplugged / Disabled / NotPresent), render vs capture
  (endpoint-id prefix `{0.0.0…}` vs `{0.0.1…}`), default device per ROLE, endpoint volume/mute, and
  per-app sessions. The user's "no multimedia" vs "no meeting" audio maps exactly to the separate
  Core Audio roles **Multimedia** vs **Communications** — a wrong/disconnected Communications default
  is the classic "music works, meetings are silent".
- **PnP / WMI** (`Get-PnpDevice`, `Win32_SoundDevice`): driver status (OK/Error/Disabled) and the
  Bluetooth **A2DP (stereo media)** vs **Hands-Free/HFP (mono call + mic)** profile split — both were
  visible for every BT device on this box, so "connected but no media / mono" is detectable.
- **Registry** (winreg): microphone privacy (`…\CapabilityAccessManager\ConsentStore\microphone`
  global + per-app), device power-management and format under `…\MMDevices\Audio`.
- **Services** (`Audiosrv`, `AudioEndpointBuilder`): audio-stack health.
- **Event log / ETW** (`Microsoft-Windows-Audio`, System log): audio driver errors + glitch events.
  Full DPC-latency measurement needs a kernel ETW trace (heavy); we use lightweight glitch-risk
  indicators by default, with an optional short trace.

## Placement (canonical 18th MCP)

- New module `mcp/windows_audio/` on `127.0.0.1:8796`, streamable-http, task `mcp-audio`, RunLevel
  **Highest** (ETW trace + HKLM MMDevices reads). Added to `config/mcp_servers.json` as the 18th
  canonical entry.
- The canonical port validation changes from "17 contiguous 8777-8793" to an **explicit allowed set
  {8777..8793} ∪ {8796}** (8794/8795 stay the manifest-external markitdown/docstruct). Updated in:
  `setup_mcp_servers.ps1`, `tools/mcp_watchdog/mcp_watchdog.ps1`, `tests/test_mcp_manifest.py`,
  `tests/test_mcp_batch.py`, `scripts/test_mcp_servers.py`. Being canonical means it is installed by
  `setup_mcp_servers.ps1`, probed by `test_mcp_servers.ps1`, and supervised by the watchdog — all via
  the existing manifest loop; only the count/port-set validators change.

## Scope

New files only under `mcp/windows_audio/` plus the manifest entry and the five validator edits above.
No changes to other MCPs, goose_web, markitdown/docstruct.

### Units (small, testable)

- `coreaudio.py` — pycaw wrappers, lazily imported; every function degrades gracefully to
  `{"available": false, "error": ...}` when pycaw/comtypes is missing. Pure-ish (COM in, dicts out):
  `list_endpoints()` (state, flow=render|capture, id, name, volume, mute), `default_for_roles()`
  (console/multimedia/communications → endpoint + state, render & capture), `list_sessions()`
  (process name, state, mute, volume).
- `winaudio.py` — non-pycaw sources (stdlib + subprocess + winreg): `services()` (Audiosrv/
  AudioEndpointBuilder status), `pnp_audio()` (Get-PnpDevice: MEDIA + AudioEndpoint + Bluetooth →
  driver status + BT A2DP/HFP classification), `mic_privacy()` (ConsentStore global + per-app deny),
  `device_power_format(endpoint_id)` (power-management + shared-mode format from MMDevices).
- `glitch.py` — `glitch_indicators()` (power-management "allow turn off", exclusive-mode, sample-rate
  mismatch across active endpoints, recent audio driver errors) and `short_trace(seconds)` (optional
  WPR/tracelog capture of Microsoft-Windows-Audio glitch events; elevated).
- `config.py` + `config.json` — `trace_seconds_default` (0 = indicators only), timeouts. Mirrors the
  dtm_download config pattern.
- `windows_audio_mcp_server.py` — FastMCP tools only.
- Scaffolding: `requirements.txt`, `install_task.ps1` (task `mcp-audio`, RunLevel Highest, AtLogOn,
  hidden launcher), `uninstall_task.ps1`, `README.md`, `conftest.py`, `tests/`.

### Tools (7, all read-only)

- `audio_health()` — Audiosrv/AudioEndpointBuilder status, endpoint/default counts, and a red-flag
  summary (default multimedia/communications render Active? any device in Error? mic privacy denied?).
  Check first.
- `audio_devices()` — every render+capture endpoint: name, state, flow, default-role membership,
  volume, mute, bus/type (USB / Bluetooth / Onboard / Display, from PnP), format/sample-rate.
- `audio_defaults()` — the three roles × render & capture, each with its default device + state,
  explicitly flagging **no_multimedia_output** (multimedia render not Active / muted / vol 0) and
  **no_communications_output** (communications render not Active — the classic silent-meeting case).
- `audio_microphone()` — default capture state/mute/level + Windows mic privacy (global Allow/Deny and
  the per-app deny list) → the "mic not working" causes, ranked.
- `audio_bluetooth()` — BT audio devices with connection state (Active vs Unplugged=disconnected) and
  active profile (A2DP media vs HFP call), flagging "connected but no media" (HFP-only) and "set as
  communications default but disconnected".
- `audio_sessions()` — per-app sessions (process, state, mute, volume) → "one app is muted in the
  mixer".
- `audio_glitches(trace_seconds: int = 0)` — glitch-risk indicators always; if `trace_seconds > 0`,
  also runs a short elevated ETW trace and returns the glitch events found.

## Symptom → tool map

| symptom | primary tool(s) |
|---|---|
| 無多媒體聲音輸出 | audio_defaults (multimedia) · audio_devices · audio_sessions |
| 無會議聲音輸出 | audio_defaults (communications) |
| 麥克風無作用 | audio_microphone (state/mute/privacy) · audio_bluetooth (HFP) |
| 聲音斷斷續續 (glitch) | audio_glitches |
| 藍芽 / 有線 | audio_bluetooth · audio_devices (bus) |

## Error handling

- pycaw/comtypes missing → every coreaudio tool returns `{"available": false, ...}` with a one-line
  install hint; the non-pycaw tools (services, pnp, mic privacy, glitch indicators) still work, so the
  MCP degrades rather than fails. `audio_health` reports the degraded state.
- Per-endpoint COM property failures (pycaw warns on some devices) are caught per field; the endpoint
  is still listed with the fields that resolved.
- `short_trace` requires elevation and the WPR/tracelog tool; if unavailable it returns a clear error
  and the indicators still stand.
- Subprocess/registry/service reads are individually try/wrapped; one failing source never fails the
  whole tool.

## Security

- Strictly read-only: the module never switches the default device, unmutes, grants mic access, or
  changes any audio setting. It diagnoses and recommends; remediation is the user's/agent's to do
  elsewhere. No confirm-token gating (nothing mutates).
- RunLevel Highest for ETW/registry breadth; loopback bind; no secrets. Results may include device
  and app names (mild PII) — returned to the caller only, nothing persisted.

## Tests

- Pure-function tests with mocked pycaw / subprocess / winreg: endpoint-state parsing, render/capture
  split, role-default flagging (no_multimedia/no_communications), mic-privacy parse (global + per-app),
  BT A2DP-vs-HFP classification, glitch indicators, and graceful degradation when pycaw is absent.
- `audio_health` shape test.
- Manifest/validator tests updated to 18 entries on the allowed set; the batch `test_mcp_servers.ps1`
  picks the module up and exercises `audio_health`.
- Manual acceptance: `audio_health`/`audio_defaults`/`audio_bluetooth` on this machine reproduce the
  observed devices (iFi/FIIO USB, Sony/Bose/Pixel BT with A2DP+HFP, Realtek/Dell), and the sidebar
  shows the audio card after a goose_web config refresh.

## Documentation

- `mcp/windows_audio/README.md`: tools, symptom map, data sources, the pycaw dependency + degradation,
  the read-only stance, glitch-trace note.
- `mcp/README.md` + `setup_mcp_servers.ps1` header: note the 18th canonical server (audio, 8796) and
  the non-contiguous canonical port set.
