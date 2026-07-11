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
- `Sysmon64.exe` — the tool (downloaded from Microsoft; **gitignored** — re-fetch from
  <https://download.sysinternals.com/files/Sysmon.zip>). Verified: signed by *Microsoft Windows
  Publisher*, v15.21.
- `sysmon-config.xml` — a **low-noise starter config** (committed). Logs the six diagnostic-valuable
  event types in full; the firehose types (ImageLoad/FileCreate/Registry/DNS/…) are explicitly OFF so
  the log isn't flooded. Tune later by adding `<Rule>` children.

## Install (run elevated — installs a kernel driver + accepts the Sysinternals EULA)
```powershell
& ".\Sysmon64.exe" -accepteula -i ".\sysmon-config.xml"
```
Verify: `Get-WinEvent -ListLog Microsoft-Windows-Sysmon/Operational` (LogName present, RecordCount > 0
after a few process launches). Update config later: `.\Sysmon64.exe -c .\sysmon-config.xml`.
Uninstall (removes driver + service): `.\Sysmon64.exe -u`.

> This is a security-relevant system change (kernel driver + audit configuration), so run the install
> yourself rather than having the agent do it.
