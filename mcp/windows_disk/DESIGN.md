# Windows Disk / Storage MCP — Design Spec

> Date: 2026-07-11 · Status: approved → implementation
> Location: `HarnessAgent/mcp/windows_disk/` · Boundary: **never modifies `PersonalKnowledge-GB10`**
> Eighth sibling of srum/eventlog/crash/exec/drift/netconn/perfmon. Rationale + ranking:
> `docs/windows-diagnostic-mcp-candidates.md` (candidate #6, value 5). Completes tier-1 storage coverage.

## 1. Goal
The storage angle nothing else in the suite covers:
- **What files changed, and when** — the NTFS USN change journal ("which config/driver/DLL changed in
  the minutes before the crash", "which directory is churning / exploding"). shell can't read it usefully
  (`fsutil` emits megabytes of FRN-not-path records).
- **Is the disk dying** — SMART / storage-reliability counters (NVMe wear %, temperature, media/read/write
  errors), with a saved baseline so the *trend* (the real signal) is answerable.
- **Volume integrity** — dirty bit (pending chkdsk = slow boot / boot loop), NTFS self-heal state, VSS
  shadow copies, fragmentation.

## 2. Architecture
```
  goose (USER mode) ──streamable_http──▶ 127.0.0.1:8784/mcp
   └ extension: disk                         │
                                             ▼
                    disk_mcp_server.py  (ELEVATED / admin, READ-ONLY vs the system)
                      ├ usn_reader.py    (ctypes: CreateFileW \\.\C: + DeviceIoControl QUERY/READ_USN_JOURNAL
                      │                   + OpenFileById/GetFinalPathNameByHandle for FRN->path; no MCP deps)
                      └ disk_health.py   (Get-PhysicalDisk / Get-StorageReliabilityCounter / fsutil / VSS
                                          via subprocess; JSON health baselines; no MCP deps)
```
Runs **elevated** (raw volume handle for USN needs admin; reliability IOCTLs need admin). Bind
`127.0.0.1:8784`, streamable HTTP (FastMCP). **READ-ONLY** vs the system — the only write is its own JSON
health baseline (`data/`, gitignored). Pure stdlib + PowerShell storage cmdlets (no third-party pip).

## 3. Ground truth (verified on this box)
- USN: `CreateFileW("\\.\C:", GENERIC_READ, FILE_SHARE_READ|WRITE, OPEN_EXISTING)` (ctypes restype/argtypes
  set so the 64-bit HANDLE isn't truncated); `DeviceIoControl(FSCTL_QUERY_USN_JOURNAL=0x000900f4)` →
  USN_JOURNAL_DATA (UsnJournalID u64, FirstUsn/NextUsn/LowestValidUsn/MaxUsn i64). Read recent via
  `FSCTL_READ_USN_JOURNAL=0x000900bb` with READ_USN_JOURNAL_DATA_V0 (StartUsn i64, ReasonMask u32,
  ReturnOnlyOnClose u32, Timeout u64, BytesToWaitFor u64, UsnJournalID u64 = 40 bytes), starting at
  `max(LowestValidUsn, NextUsn - window)`; output = next-USN (8 bytes) + USN_RECORD_V2 records
  (RecordLength u32, Major/Minor u16, FileRef u64, ParentFileRef u64, Usn i64, TimeStamp FILETIME i64,
  Reason u32, SourceInfo u32, SecurityId u32, FileAttributes u32, FileNameLength u16, FileNameOffset u16,
  FileName WCHAR[]). Verified: recent RENAME_NEW records with correct timestamps + names.
- Health: `Get-PhysicalDisk` (FriendlyName, MediaType, HealthStatus, Size) + `Get-StorageReliabilityCounter`
  (Wear, Temperature, Read/WriteErrorsTotal, PowerOnHours) → JSON. NVMe SK hynix, Healthy, 60 C, Wear 0.
- Volume: `fsutil dirty query C:` (NOT Dirty), `fsutil repair state C:` (Clean 0x00), `Win32_ShadowCopy`
  (2 shadow copies).

## 4. Tool surface (7)
- `recent_file_changes(minutes=60, path_filter=None, reasons=None, max=200)` → recent USN records
  `[{time, name, path, reason, file_ref, parent_ref, attributes}]`, newest first, deduped by (path, reason).
  reasons = list of reason names (CREATE/DELETE/RENAME_NEW/DATA_OVERWRITE/…) to filter.
- `directory_churn(minutes=60, top_n=20)` → `[{directory, change_count, sample_files}]` — which dirs are
  churning (temp explosion, log spam, installer gone wrong).
- `disk_health()` → per physical disk `{device_id, friendly_name, media_type, health, size_gb, wear_pct,
  temperature_c, power_on_hours, read_errors, write_errors}` from SMART/reliability counters.
- `health_baseline_save(name="default")` / `health_baseline_diff(name="default")` → persist + numeric-delta
  the reliability counters over time (the trend is the diagnostic signal: "has wear/temp/errors moved?").
- `volume_state(volume="C:")` → `{dirty, repair_state, shadow_copies, fragmentation_pct?, free_gb, size_gb}`.
- `disk_status()` → `{is_admin, usn:{journal_id, first_usn, next_usn, span_bytes}|error, disks:n, baselines}`.

Every tool returns a structured `{error:…}`, never raises. Caps observable (`truncated`/`total`).

## 5. USN reader (`usn_reader.py`)
1. Open the volume once per call (`\\.\<vol>`), QUERY the journal, READ from `max(lowest, next - WINDOW)`
   in a loop (following the returned next-USN) until timestamp < cutoff or a byte cap is hit.
2. Parse USN_RECORD_V2 (bounds-checked, variable length). FILETIME → ISO (overflow-guarded).
3. FRN→path: resolve `ParentFileReferenceNumber` via `OpenFileById` (FILE_ID_DESCRIPTOR, FileIdType) +
   `GetFinalPathNameByHandleW(VOLUME_NAME_DOS)` → parent dir; join with FileName. Cache parent-FRN→dir
   per call. Best-effort — on failure keep the bare filename.
4. Reason bitmask → set of reason names. Dedup (path, aggregated-reason) so a file touched N times in one
   op isn't N rows.

## 6. Safety
Read-only: the volume handle is GENERIC_READ; no write/format/defrag/chkdsk is ever invoked (volume_state
only *queries* via `fsutil dirty query` / `fsutil repair state`, never `set`/`repair`). `path_filter`/
`volume` are validated (volume must match `^[A-Za-z]:$`). ctypes HANDLEs sized correctly; every handle
closed in `finally`. The only write is the JSON health baseline (atomic os.replace under a lock).

## 7. Files
`disk_mcp_server.py` (FastMCP, 7 tools) · `usn_reader.py` · `disk_health.py` · `start_disk_mcp.ps1` /
`install_task.ps1` / `uninstall_task.ps1` (Scheduled Task `Disk-MCP`, 8784) · `requirements.txt`
(mcp, pytest) · `README.md` · `tests/` · `data/` (gitignored).

## 8. goose extension
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

## 9. Out of scope (YAGNI)
Full MFT space-forensics scan (FSCTL_ENUM_USN_DATA "what's eating disk" — v2). $MFT $SI/$FN timestamp
forensics. Writing/repairing/defragging. Non-fixed / removable volumes by default. Cross-machine.

## 10. Open risks
- USN journal wraps (retention hours–days by write load) → `disk_status` reports first/next USN span so
  coverage is visible; reading below LowestValidUsn returns ERROR_JOURNAL_ENTRY_DELETED → clamp start.
- USN_RECORD_V3 (128-bit FRN on ReFS/newer) → detect MajorVersion==3 and parse the wider refs, else V2.
- OpenFileById can fail for deleted/special files → path falls back to the bare filename.
- Get-StorageReliabilityCounter fields are firmware-dependent (some NVMe leave errors/hours blank) →
  emit nulls, never fail.
