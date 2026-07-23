# Windows Disk / Storage MCP

A local, **read-only** MCP server for the storage angle nothing else in the suite covers:

- **What files changed, and when** -- the NTFS **USN change journal**: "which config/driver/DLL changed
  in the minutes before the crash", "which directory is churning / exploding". The shell can't read it
  usefully (`fsutil` emits megabytes of FRN-not-path records).
- **Is the disk dying** -- SMART / storage-reliability counters (NVMe wear %, temperature, media/read/write
  errors, power-on hours), with a saved baseline so the **trend** (the real signal) is answerable.
- **Volume integrity** -- dirty bit (pending chkdsk = slow boot / boot loop), NTFS repair state, VSS
  shadow copies, free space.

Eighth sibling of srum(8777)/eventlog(8778)/crash(8779)/exec(8780)/drift(8781)/netconn(8782)/perfmon(8783).
Completes tier-1. Rationale:
[`docs/windows-diagnostic-mcp-candidates.md`](../../docs/windows-diagnostic-mcp-candidates.md) (candidate #6).

## Tools (7)
| Tool | What it answers |
|---|---|
| `recent_file_changes(minutes=60, path_filter, reasons, max=200, volume="C:")` | What files changed recently (USN journal), one row per file, newest first. |
| `directory_churn(minutes=60, top_n=20, volume="C:")` | Which directories are churning (temp/log/AV/installer). |
| `disk_health()` | Per-disk SMART/reliability: health, wear %, temperature, errors, power-on hours. |
| `health_baseline_save(name)` / `health_baseline_diff(name)` | Trend reliability counters over time. |
| `volume_state(volume="C:")` | Dirty bit, NTFS repair state, shadow copies, free/size. |
| `disk_status()` | Admin, USN journal info (id, first/next USN, span), disk count, baselines. |

Every tool returns a structured `{...}` (errors as `{"error": ...}`), never raises. Caps observable.

## Run it
```powershell
.\start_disk_mcp.ps1                       # elevated
.\install_task.ps1 ; Start-ScheduledTask -TaskName mcp-disk
```
Serves `http://127.0.0.1:8784/mcp`. Pure stdlib -- `ctypes` DeviceIoControl for the USN journal,
PowerShell storage cmdlets (`Get-PhysicalDisk` / `Get-StorageReliabilityCounter` / `fsutil`) for health.
Baselines in `data/` (gitignored; override with `DISK_BASELINES`).

## goose extension config
```yaml
  disk:
    type: streamable_http
    bundled: false
    name: disk
    enabled: true
    uri: http://127.0.0.1:8784/mcp
    headers: {}
    env_keys: []
    timeout: 120
    description: Windows storage diagnostics (USN file-change journal + SMART health + volume state) via local elevated MCP server (127.0.0.1:8784)
```

## Notes
- **USN reads forward from a valid boundary only** -- to get "recent" changes the reader scans the whole
  journal from the first record and filters by timestamp (this box's ~40 MB journal is ~1 s). An arbitrary
  mid-journal StartUsn returns ERROR_INVALID_PARAMETER, so there is no cheaper "seek to recent".
- Journal **wraps** (retention hours-days by write load); `disk_status` reports first/next USN span so
  coverage is visible.
- USN_RECORD **v2 (64-bit FRN)** and **v3 (128-bit)** are both parsed; full path is resolved via
  `OpenFileById` + `GetFinalPathNameByHandle` (best-effort -- deleted/special files fall back to the name).
- Needs **admin** (raw volume handle). Read-only: no write/format/defrag/chkdsk is ever invoked;
  `volume_state` only *queries* (`fsutil dirty query` / `repair state`, never `set`/`repair`).
- `Get-StorageReliabilityCounter` fields are firmware-dependent (some NVMe leave errors/hours blank -> null).

## Files
`disk_mcp_server.py` (FastMCP, 7 tools) · `usn_reader.py` (USN journal via ctypes) · `disk_health.py`
(SMART/reliability/volume via PowerShell + JSON baselines) · `start_disk_mcp.ps1` / `install_task.ps1` /
`uninstall_task.ps1` · `tests/` · `data/`. MFT space-forensics scan is v2 (out of scope).

Batch-test this server with the [central MCP suite instructions](../README.md#test-all-local-mcp-servers).
