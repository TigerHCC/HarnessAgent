# MCP watchdog — restarts a wedged MCP server before it hangs the harness

## The problem it solves

Each local MCP server (`mcp/windows_*/`, `mcp/dtm_sdk/`, `mcp/windows_obsidian/`) runs a **single
asyncio event loop** (FastMCP, streamable-http). If that loop ever blocks, the TCP port keeps
**listening** — so a naive "port UP" check passes — but the server answers **no request**. And because
Goose initializes all MCP extensions **in parallel and waits for every one** (no per-MCP init timeout),
**one wedged server hangs the entire harness**: `goose run` (and therefore goose_web chat) freezes during
startup, before it ever reaches the model. This looks like a model problem when the model is fine.

This actually happened: the `dtmsdk` server wedged after ~a day; its raw `GET /mcp` timed out while the
port still listened, and both the goose CLI and goose_web hung. Restarting `dtmsdk` fixed it. The
watchdog makes that recovery automatic.

## What it does

`mcp_watchdog.ps1` runs one pass:
1. Loads and validates all 14 names, ports, and Scheduled Task names from
   [`../../config/mcp_servers.json`](../../config/mcp_servers.json), the same manifest consumed by
   `setup_mcp_servers.ps1`.
2. Probes each server with a cheap **raw HTTP `GET /mcp`** (a healthy endpoint answers `400`/`406`
   instantly; a wedged one times out). Raw HTTP — not a full MCP handshake — so the watchdog is light
   and doesn't itself hammer the servers.
3. Classifies each as **alive** / **wedged** (listening, no answer) / **down** (not listening).
4. For anything not alive: kills the owning PID (if any) and `Start-ScheduledTask` for its task, logging
   the restart to `watchdog.log`. A clean pass writes nothing (only restarts are logged).

`install_watchdog.ps1` registers it as the **`MCP-Watchdog`** Scheduled Task, elevated (it kills
processes and starts tasks), repeating **every 5 minutes** (the wedge took ~a day, so 5 min is ample).

## Use

```powershell
# See status without touching anything (safe anytime):
powershell -ExecutionPolicy Bypass -File .\mcp_watchdog.ps1 -DryRun

# One real pass (restarts wedged servers):
powershell -ExecutionPolicy Bypass -File .\mcp_watchdog.ps1

# Install / remove the every-5-min scheduled task (elevated):
.\install_watchdog.ps1
.\uninstall_watchdog.ps1
```

The one-click `..\..\setup_mcp_servers.ps1` installs the watchdog by default (opt out with
`-SkipWatchdog`).

## Notes

- A `-DryRun` pass is the quickest way to see which servers are answering right now.
- `-InventoryOnly` validates the manifest and emits its `name`/`port`/`task` inventory as JSON without
  probing, killing, or restarting anything.
- If a server is repeatedly restarted (check `watchdog.log`), that server has a real bug — the watchdog
  is a safety net, not a substitute for fixing the wedge. See `docs/HARDENING_BACKLOG.md`.
- The probe is raw HTTP so it can't detect a server that answers raw HTTP but wedges only the MCP
  protocol layer. The observed wedge killed the whole event loop (raw HTTP dead), which this catches.
