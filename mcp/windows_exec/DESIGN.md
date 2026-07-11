# Windows Execution-Evidence MCP — Design Spec

> Date: 2026-07-11 · Status: approved → implementation
> Location: `HarnessAgent/mcp/windows_exec/` · Boundary: **never modifies `PersonalKnowledge-GB10`**
> Fourth sibling of srum(8777)/eventlog(8778)/crash(8779). Rationale + ranking:
> `docs/windows-diagnostic-mcp-candidates.md` (candidate #2, value 5).

## 1. Goal
Answer *"what executed on this machine, when, how often, and what did it load"* from binary/encoded
artifacts the shell cannot read — the perfect complement to SRUM (which says how much CPU/network an app
used, but not exactly when it launched):
- **Prefetch** (`C:\Windows\Prefetch\*.pf`) — per-exe last-8 run times, run count, loaded file list.
- **BAM** (Background Activity Moderator) — per-user last-execution time of each exe since recent boots.
- **UserAssist** — GUI-launched program run counts + last-run + focus time (what the user actually uses).
- **ShimCache** (AppCompatCache) — presence/last-modified evidence for executables (fills gaps when
  Prefetch is off or SRUM was purged).
- **`exec_timeline`** — a merged, normalized timeline across all four (the capstone).

## 2. Architecture
```
  goose (USER mode) ──streamable_http──▶ 127.0.0.1:8780/mcp
   └ extension: exec                         │
                                             ▼
                    exec_mcp_server.py  (ELEVATED / admin, read-only)
                      ├ prefetch_reader.py  (MAM/Xpress-Huffman decompress + SCCA v30/31 parse)
                      └ registry_forensics.py (BAM / UserAssist / ShimCache via winreg; device-path map)
```
Runs **elevated**: Prefetch dir, BAM (SYSTEM hive), and ShimCache need admin. Bind `127.0.0.1:8780`,
streamable HTTP (FastMCP). Pure **stdlib** parsing (`ctypes` ntdll for decompress; `winreg`; `struct`).

## 3. Ground truth (verified on this box, build 26100)
- **Prefetch**: header `MAM\x04` + uint32 uncompressed_size, then Xpress-Huffman — decompress via
  `RtlGetCompressionWorkSpaceSize` + `RtlDecompressBufferEx(COMPRESSION_FORMAT_XPRESS_HUFF=0x0004)`
  (plain `RtlDecompressBuffer` returns STATUS_UNSUPPORTED_COMPRESSION). Decompressed = **SCCA v31**:
  version@0x00, 'SCCA'@0x04, exe-name (UTF-16)@0x10, hash@0x4C; file-info section @0x54 →
  filename_strings off@0x64/size@0x68, volumes off@0x6C/count@0x70; **8× last-run FILETIME @0x80**;
  **run_count @0xC8 (v31)** / @0xD0 (v30). Loaded-files list is UTF-16 NUL-separated at filename_strings.
- **BAM**: `HKLM\SYSTEM\CurrentControlSet\Services\bam\State\UserSettings\<SID>` — each value name is a
  device path (`\Device\HarddiskVolume3\Windows\...\x.exe`), value data[:8] = last-exec FILETIME.
- **UserAssist**: `HKCU\...\Explorer\UserAssist\{GUID}\Count` — value names ROT13-encoded; 72-byte
  entries: run_count@0x04, focus_ms@0x0C, last-run FILETIME@0x3C. (Session aggregate entry is large →
  skip.)
- **ShimCache**: `HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\AppCompatCache\AppCompatCache`
  REG_BINARY: header 0x34; entries signature `10ts`; per entry: path-len(u16)+path(UTF-16) +
  last-modified FILETIME (the FILE's $SI mtime — **presence evidence, NOT run time**).

## 4. Tool surface (7)
- `prefetch_list(filter=None, max=50)` → `[{exe, run_count, last_run, hash, pf_file}]`, newest first.
- `prefetch_detail(name)` → one `.pf`: `{exe, hash, version, run_count, run_times:[8], volume, files:[…], files_truncated}`.
- `bam_list(max=200)` → `[{exe, path, last_exec, sid, user}]`, newest first.
- `userassist_list(max=200)` → `[{name, run_count, last_run, focus_seconds, user}]`.
- `shimcache_list(filter=None, max=200)` → `[{path, last_modified, position}]` (last_modified = file
  mtime, not exec time — labelled).
- `exec_timeline(hours=24, filter=None, max=200)` → merged `[{time, source, exe, detail}]` across
  Prefetch run-times / BAM / UserAssist, newest first (ShimCache excluded: no exec time).
- `exec_health()` → `{is_admin, prefetch_enabled, prefetch:{files|error}, registry:{bam:{entries|error},
  userassist:{entries|error}, shimcache:{entries_parsed|error}}}`.

All caps observable (`*_truncated` / counts). Every tool returns a structured `{error:…}`, never raises.

## 5. Safety / robustness
- Read-only; no artifact is written/cleared. `prefetch_detail(name)` confines `name` to a basename in
  `C:\Windows\Prefetch` (no separators / `.`/`..`).
- Untrusted binary parse (a `.pf` is written by the OS but treated defensively): every offset/length
  bounds-checked; decompressed size capped; attacker-controlled string/list lengths capped.
- Registry reads use `KEY_WOW64_64KEY`; per-source failures are structured, not fatal.
- FILETIME conversion guards overflow (huge/garbage values → None, never raises).

## 6. Files
`exec_mcp_server.py` (FastMCP, 7 tools) · `prefetch_reader.py` · `registry_forensics.py` ·
`start_exec_mcp.ps1` / `install_task.ps1` / `uninstall_task.ps1` (Scheduled Task `Exec-MCP`, 8780) ·
`requirements.txt` · `README.md` · `tests/`.

## 7. goose extension
```yaml
  exec:
    type: streamable_http
    bundled: false
    name: exec
    enabled: true
    uri: http://127.0.0.1:8780/mcp
    headers: {}
    env_keys: []
    timeout: 120
    description: Windows execution evidence (Prefetch/BAM/UserAssist/ShimCache + timeline) via local elevated MCP server (127.0.0.1:8780)
```

## 8. Out of scope (YAGNI)
Amcache.hve (needs hive load/parse — deferred to v2; documented). Offline/other-user hives. Full SCCA
metrics/trace-chain arrays. Cross-machine. Writing/clearing artifacts.

## 9. Open risks
- SCCA run_count offset differs v30 (0xD0) vs v31 (0xC8) — pick by version; flag if the value looks
  implausible. Older `.pf` (v23/26) not this box's format → return version + best-effort, mark uncertain.
- ShimCache entry layout can shift by build → parse defensively, stop at first malformed entry, report
  how many parsed.
- BAM device-path → drive-letter mapping is best-effort (`QueryDosDevice`); on failure keep the raw
  device path.
