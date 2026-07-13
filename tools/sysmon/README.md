# Sysmon — feeds the eventlog MCP (no new MCP needed)

[Sysmon](https://learn.microsoft.com/sysinternals/downloads/sysmon) (System Monitor) is a Microsoft
Sysinternals tool that logs rich telemetry — **process creation** (with hashes, command line, parent),
**network connections** (per process), **driver loads**, **remote-thread injection**, and **WMI
persistence** — into the `Microsoft-Windows-Sysmon/Operational` Event Log channel. The existing
[`windows_eventlog`](../../mcp/windows_eventlog) MCP can then query it immediately (no new server):

```
query_events(channel="Microsoft-Windows-Sysmon/Operational", event_ids=[1], hours=24)   # process creation
query_events(channel="Microsoft-Windows-Sysmon/Operational", event_ids=[3], hours=1)     # network connections
query_events(channel="Microsoft-Windows-Sysmon/Operational", event_ids=[6], hours=168)   # driver loads
```

## Files
- `Sysmon.zip` — the Microsoft Sysinternals download (**committed**), containing `Sysmon.exe` (x86),
  `Sysmon64.exe` (x64), `Sysmon64a.exe` (ARM64), and `Eula.txt`. Verified: signed by *Microsoft Windows
  Publisher*, v15.21. The setup script extracts the arch-appropriate binary from it; the loose `.exe`s
  it unpacks are gitignored (they come from the zip). Re-fetch a newer version from
  <https://download.sysinternals.com/files/Sysmon.zip>.
- `sysmon-config.xml` — a **low-noise starter config** (committed). Logs the six diagnostic-valuable
  event types in full; the firehose types (ImageLoad/FileCreate/Registry/DNS/…) are explicitly OFF so
  the log isn't flooded. Tune later by adding `<Rule>` children.

## Install

**Automatic (part of the MCP one-click setup).** `setup_mcp_servers.ps1` now installs Sysmon by
default: it extracts the arch-appropriate binary from the committed `Sysmon.zip`, and — if Sysmon is
**not** already installed — runs `-accepteula -i sysmon-config.xml`. If Sysmon **is** already installed,
it refreshes the audit config (`-c`) instead of reinstalling. Opt out with `-SkipSysmon`.

```powershell
powershell -ExecutionPolicy Bypass -File .\setup_mcp_servers.ps1              # installs/refreshes Sysmon too
powershell -ExecutionPolicy Bypass -File .\setup_mcp_servers.ps1 -SkipSysmon  # leave Sysmon alone
```

> This installs a Microsoft **kernel driver** and configures system auditing, and `-accepteula`
> **accepts the Sysinternals EULA** (`Eula.txt`) on your behalf. It runs only when *you* run the setup
> script (elevated). Pass `-SkipSysmon` if you don't want that.

**Manual (run elevated).** `Sysmon.zip` ships the x64/x86/ARM64 binaries; pick your arch:
```powershell
Expand-Archive .\Sysmon.zip -DestinationPath . -Force
& ".\Sysmon64.exe" -accepteula -i ".\sysmon-config.xml"     # Sysmon64a.exe on ARM64, Sysmon.exe on x86
```
Verify: `Get-WinEvent -ListLog Microsoft-Windows-Sysmon/Operational` (LogName present, RecordCount > 0
after a few process launches). Update config later: `.\Sysmon64.exe -c .\sysmon-config.xml`.
Uninstall (removes driver + service): `.\Sysmon64.exe -u`.
