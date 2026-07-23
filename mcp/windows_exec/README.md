# Windows Execution-Evidence MCP

A local, **read-only** MCP server that answers *"what executed on this machine, when, how often, and
what did it load"* from binary/encoded artifacts the shell can't read — the natural complement to the
[`windows_srum`](../windows_srum) MCP (SRUM says how much CPU/network an app used; this says exactly
when it launched and what it loaded).

Sources: **Prefetch** (`.pf`, MAM/Xpress-Huffman compressed) · **BAM** (per-user last-exec times) ·
**UserAssist** (GUI run counts + focus time) · **ShimCache** (AppCompatCache presence evidence).
Rationale + ranking: [`docs/windows-diagnostic-mcp-candidates.md`](../../docs/windows-diagnostic-mcp-candidates.md)
(candidate #2). Fourth sibling of srum(8777)/eventlog(8778)/crash(8779).

## Tools (7)
| Tool | What it answers |
|---|---|
| `prefetch_list(filter=None, max=50)` | Per-exe last run + run count + hash, newest first. |
| `prefetch_detail(name)` | One `.pf` in full: 8 last-run times, run count, volume, loaded-file list. |
| `bam_list(max=200)` | Per-user last-execution time of each exe since recent boots (needs admin). |
| `userassist_list(max=200)` | GUI-launched program run counts + last run + focus time (current user). |
| `shimcache_list(filter=None, max=200)` | Executables the compat engine has seen + the file's mtime. |
| `exec_timeline(hours=24, filter=None, max=200)` | Merged Prefetch/BAM/UserAssist timeline, newest first. |
| `exec_health()` | Admin status, Prefetch enabled + count, BAM/UserAssist/ShimCache counts. |

Every tool returns a structured `{...}` (errors as `{"error": ...}`), never raises. Caps are observable
(`truncated` / `total` / `file_count`).

## Run it
```powershell
.\start_exec_mcp.ps1                       # elevated
# or persist as a logon Scheduled Task 'mcp-exec':
.\install_task.ps1 ; Start-ScheduledTask -TaskName mcp-exec
```
Serves `http://127.0.0.1:8780/mcp` (streamable HTTP). Pure stdlib parsing — `ctypes` ntdll for the
Prefetch decompress, `winreg`/`struct` for the rest; `pywin32` only to resolve BAM SIDs → user names.

## goose extension config
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

## Gotchas
- **Prefetch decompress** needs `RtlDecompressBufferEx` + a workspace (plain `RtlDecompressBuffer`
  returns `STATUS_UNSUPPORTED_COMPRESSION`). SCCA **v31** (this box): run_count is at 0xC8 (v30: 0xD0);
  last-run times at 0x80.
- **ShimCache `last_modified` is the file's mtime, not execution time** — presence evidence. It's
  excluded from `exec_timeline` for that reason.
- Prefetch can be disabled on SSD-tuned/VM images (`EnablePrefetcher != 3`) — `exec_health` reports it.
- BAM/ShimCache need admin (SYSTEM hive); UserAssist is the current user's hive. Not elevated → those
  tools return a structured "requires admin" note; UserAssist still works.
- Read-only: no artifact is written/cleared; `prefetch_detail` is confined to a `.pf` basename.

## Files
`exec_mcp_server.py` (FastMCP, 7 tools) · `prefetch_reader.py` (MAM decompress + SCCA parse) ·
`registry_forensics.py` (BAM/UserAssist/ShimCache) · `start_exec_mcp.ps1` / `install_task.ps1` /
`uninstall_task.ps1` · `tests/`. Amcache.hve is out of scope for v1 (needs hive load/parse).

Batch-test this server with the [central MCP suite instructions](../README.md#test-all-local-mcp-servers).
