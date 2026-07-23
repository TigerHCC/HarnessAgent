"""Windows Audio Diagnostics MCP (FastMCP, streamable HTTP, 127.0.0.1:8796).

Read-only: pinpoints no-multimedia / no-meeting output (Core Audio roles), mic-not-working
(state/mute/privacy), glitches (risk indicators + optional trace), and Bluetooth A2DP-vs-HFP. It only
diagnoses and recommends -- it never changes an audio setting. pycaw is optional (graceful
degradation). 18th canonical MCP. Goose connects via streamable_http, uri http://127.0.0.1:8796/mcp.
"""
import anyio
from mcp.server.fastmcp import FastMCP

import config
import coreaudio
import winaudio
import glitch

mcp = FastMCP("windows_audio", host="127.0.0.1", port=8796)
_CFG = config.load()
_T = _CFG["subprocess_timeout"]


def _defaults_impl():
    d = coreaudio.default_for_roles()
    flags = coreaudio.flag_defaults(d.get("render", {})) if d.get("available") else \
        {"no_multimedia_output": None, "no_communications_output": None, "reasons": ["pycaw unavailable"]}
    return {"available": d.get("available", False), "render": d.get("render", {}),
            "capture": d.get("capture", {}), "flags": flags, "error": d.get("error")}


def _devices_impl():
    eps = coreaudio.list_endpoints()
    pnp = winaudio.pnp_audio(_T)
    return {"available": eps.get("available", False), "endpoints": eps.get("endpoints", []),
            "pnp": pnp, "error": eps.get("error")}


def _microphone_impl():
    d = coreaudio.default_for_roles()
    cap = d.get("capture", {}) if d.get("available") else {}
    no_default = all(("error" in cap.get(r, {"error": "x"})) for r in ("multimedia", "communications")) \
        if cap else True
    return {"privacy": winaudio.mic_privacy(), "capture_defaults": cap,
            "no_default_capture": bool(no_default), "coreaudio_available": d.get("available", False)}


def _bluetooth_impl():
    rows = winaudio.pnp_audio(_T)
    bt = []
    for r in rows:
        nm = r.get("name") or ""
        low = nm.lower()
        # BT audio endpoints show up as MEDIA/AudioEndpoint with hands-free/a2dp/known BT names
        if any(k in low for k in ("hands-free", "a2dp", "bluetooth")) or r.get("class") == "Bluetooth":
            bt.append({"name": nm, "status": r.get("status"), "profile": winaudio.classify_bt(nm)})
    return {"bluetooth": bt,
            "note": "a2dp = stereo media; hfp = mono call+mic. HFP-only = no media; A2DP-only = no call mic."}


def _glitches_impl(trace_seconds):
    eps = coreaudio.list_endpoints()
    # active endpoints' formats are best-effort; indicators still run without them
    ind = glitch.glitch_indicators(active_formats=[], timeout=_T)
    result = {"indicators": ind}
    ts = trace_seconds if trace_seconds is not None else _CFG["trace_seconds_default"]
    if ts and int(ts) > 0:
        result["trace"] = glitch.short_trace(int(ts), _CFG["trace_max_seconds"], _T + int(ts) + 30)
    return result


def _health_impl():
    d = coreaudio.default_for_roles()
    svc = winaudio.services(_T)
    flags = coreaudio.flag_defaults(d.get("render", {})) if d.get("available") else {}
    priv = winaudio.mic_privacy()
    red = []
    if svc.get("Audiosrv", {}).get("status") != "Running":
        red.append("Windows Audio service (Audiosrv) not Running")
    if flags.get("no_multimedia_output"):
        red.append("no multimedia output (see audio_defaults)")
    if flags.get("no_communications_output"):
        red.append("no meeting/communications output (see audio_defaults)")
    if priv.get("global") == "Deny":
        red.append("microphone access is globally denied")
    return {"ok": True, "services": svc, "coreaudio_available": d.get("available", False),
            "red_flags": red}


@mcp.tool()
async def audio_health() -> dict:
    """Audio-stack health + red-flag summary: service status, whether multimedia/communications output
    or the mic are obviously broken. Check this first."""
    return await anyio.to_thread.run_sync(_health_impl)


@mcp.tool()
async def audio_devices() -> dict:
    """All render + capture endpoints with state (Active/Unplugged/Disabled/NotPresent), flow, and the
    PnP driver/bus view. The full inventory."""
    return await anyio.to_thread.run_sync(_devices_impl)


@mcp.tool()
async def audio_defaults() -> dict:
    """Default device per role (console/multimedia/communications) for render + capture, flagging
    no_multimedia_output and no_communications_output (the classic silent-meeting case)."""
    return await anyio.to_thread.run_sync(_defaults_impl)


@mcp.tool()
async def audio_microphone() -> dict:
    """Microphone diagnosis: default capture presence/state + Windows mic privacy (global + per-app
    denies). Covers 'mic not working'."""
    return await anyio.to_thread.run_sync(_microphone_impl)


@mcp.tool()
async def audio_bluetooth() -> dict:
    """Bluetooth audio devices with their active profile (a2dp media vs hfp call). Flags 'connected but
    no media' (hfp-only) and mono-call cases."""
    return await anyio.to_thread.run_sync(_bluetooth_impl)


@mcp.tool()
async def audio_sessions() -> dict:
    """Per-app audio sessions (process, state) -- find an app that is muted/inactive in the mixer."""
    return await anyio.to_thread.run_sync(coreaudio.list_sessions)


@mcp.tool()
async def audio_glitches(trace_seconds: int = 0) -> dict:
    """Glitch/stutter diagnosis: risk indicators (sample-rate mismatch, recent audio driver errors)
    always; if trace_seconds > 0, also runs a short elevated ETW trace (clamped to the configured max)."""
    return await anyio.to_thread.run_sync(_glitches_impl, trace_seconds)


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
