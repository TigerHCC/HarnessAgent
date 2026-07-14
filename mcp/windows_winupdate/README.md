# Windows Update-History MCP

A local, **read-only** MCP that answers *"what did Windows Update install right before the problem
started"* and *"why does this update keep failing"*. Systems very often break right after an update — this
correlates the problem onset with what was installed, and decodes update-failure HRESULTs the shell has no
table for. Full history (incl. **failures**) is only available via the WUA COM API — `Get-HotFix` misses
most of it.

Twelfth (final) diagnostic MCP. Rationale:
[`docs/windows-diagnostic-mcp-candidates.md`](../../docs/windows-diagnostic-mcp-candidates.md)
(`winupdate-history`). A live probe surfaced **4 real update failures** (Intel driver 0x8024200B,
Microsoft.UI.Xaml 0x80240034).

## Tools (5)
| Tool | What it answers |
|---|---|
| `update_history(max=100, failures_only=False)` | WU history: date/title/KB/operation/result + decoded HRESULT. |
| `installed_updates(max=200)` | Installed hotfixes/KBs + install date (Get-HotFix). |
| `pending_state()` | Reboot-pending / pending-file-rename state + true OS build (patch level). |
| `hresult_decode(code)` | Decode a WU/CBS servicing HRESULT (e.g. 0x800f0922). |
| `winupdate_health()` | Admin, WUA reachable, history count, reboot-pending, table size. |

Every tool returns a structured `{...}` (errors as `{"error": ...}`), never raises.

## Run it
```powershell
.\start_winupdate_mcp.ps1                        # elevation NOT required
.\install_task.ps1 ; Start-ScheduledTask -TaskName Winupdate-MCP
```
Serves `http://127.0.0.1:8788/mcp`. Pure stdlib — calls the WUA COM API + `Get-HotFix` through a short
PowerShell subprocess (avoids COM apartment-threading issues in the FastMCP workers) and reads pending
state from the registry. No files written.

## goose extension config
```yaml
  winupdate:
    type: streamable_http
    bundled: false
    name: winupdate
    enabled: true
    uri: http://127.0.0.1:8788/mcp
    headers: {}
    env_keys: []
    timeout: 120
    description: Windows Update history + failure HRESULTs + pending-reboot state via local MCP server (127.0.0.1:8788)
```

## Typical use
```
update_history(failures_only=True)   # which updates failed, and the decoded reason
update_history(max=20)               # what installed recently (correlate with when the problem began)
pending_state()                      # is a CU stuck pending a reboot
hresult_decode("0x800f0922")         # -> CBS_E_INSTALLERS_FAILED + what to do
```

## Notes
- **Read-only**: only queries WU history / hotfixes / registry; no update is installed / uninstalled / hidden.
- The WUA COM call runs via a short PowerShell subprocess; a slow call hits a 60 s timeout and returns
  `{"error": ...}`.
- The HRESULT table is curated (common WU/CBS codes); an unknown code returns the raw hex with null name.
- `Get-HotFix` `InstalledOn` is date-only (local date, no UTC shift). CBS.log deep parsing is a future v2.

## Files
`winupdate_mcp_server.py` (FastMCP, 5 tools) · `winupdate.py` (WUA history + Get-HotFix + pending + HRESULT
table) · `start_winupdate_mcp.ps1` / `install_task.ps1` / `uninstall_task.ps1` · `tests/`.

Batch-test this server with the [central MCP suite instructions](../README.md#test-all-local-mcp-servers).
