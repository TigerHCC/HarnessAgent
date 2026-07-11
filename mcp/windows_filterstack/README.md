# Windows Filter-Stack MCP

A local, **read-only** MCP that maps the filtering stacks where **AV / VPN / EDR / backup** products
insert themselves -- the **#1 real-world cause of "the whole machine is slow"**, high file-IO latency,
and connections that fail with no matching firewall rule.

- **Filesystem minifilters** (Filter Manager) -- which drivers sit in *every file operation*, at what
  altitude (Anti-Virus / Activity-Monitor / Encryption / Backup class), and which are third-party. A live
  probe here found **two Anti-Virus-class minifilters in every file op: `tmeyes` (Trend Micro) and
  `mshield` (NordVPN)**.
- **Network filters** -- NDIS lightweight-filter adapter bindings + the Winsock LSP catalog.
- **Baseline diff** -- a *new* filter appearing (freshly installed or leftover-from-uninstall AV/VPN).

Eleventh diagnostic MCP (tier-2). Rationale:
[`docs/windows-diagnostic-mcp-candidates.md`](../../docs/windows-diagnostic-mcp-candidates.md) (`filterstack`).

## Tools (7)
| Tool | What it answers |
|---|---|
| `minifilters(filter=None, third_party_only=False)` | What filters every file operation (+ altitude class + vendor). |
| `filter_instances(volume="C:")` | Minifilter instances attached to a volume. |
| `network_filters()` | NDIS filter bindings + Winsock LSP catalog. |
| `altitude_lookup(altitude)` | Classify an altitude -> Filter Manager load-order group. |
| `baseline_save(name)` / `baseline_diff(name)` | Minifilters added/removed since a baseline. |
| `filterstack_health()` | Admin, fltmc OK, minifilter count. |

Every tool returns a structured `{...}` (errors as `{"error": ...}`), never raises.

## Run it
```powershell
.\start_filterstack_mcp.ps1                        # elevated (fltmc needs admin)
.\install_task.ps1 ; Start-ScheduledTask -TaskName Filterstack-MCP
```
Serves `http://127.0.0.1:8787/mcp`. Pure stdlib -- parses `fltmc filters/instances`,
`Get-NetAdapterBinding`, `netsh winsock show catalog`; resolves each filter's driver via the service
registry ImagePath and reads its `CompanyName` (WHQL-signed 3rd-party drivers show Microsoft as the
Authenticode signer, so CompanyName is the reliable third-party signal). Baselines in `data/`
(gitignored; override with `FILTERSTACK_BASELINES`).

## Notes
- **Read-only**: only *queries* (`fltmc filters/instances`, never `attach`/`detach`); no binding is changed.
- `third_party` is True/False when the driver's CompanyName resolves, `null` when it can't (honest unknown).
- Needs **admin** (fltmc). Full WFP callout/filter enumeration (`netsh wfp show filters`, multi-MB) is v2.

## Files
`filterstack_mcp_server.py` (FastMCP, 7 tools) · `parsers.py` (fltmc/NDIS/LSP parsing + altitude map +
ImagePath->CompanyName + baselines) · `start_filterstack_mcp.ps1` / `install_task.ps1` /
`uninstall_task.ps1` · `tests/` · `data/`.
