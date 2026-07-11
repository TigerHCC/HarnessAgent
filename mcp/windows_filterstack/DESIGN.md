# Windows Filter-Stack MCP — Design Spec

> Date: 2026-07-11 · Status: approved → implementation
> Location: `HarnessAgent/mcp/windows_filterstack/` · Boundary: never modifies `PersonalKnowledge-GB10`
> Eleventh diagnostic MCP (tier-2). Rationale: `docs/windows-diagnostic-mcp-candidates.md` (`filterstack`).

## 1. Goal
Map the three filtering stacks where AV / VPN / EDR / backup products insert themselves — the **#1
real-world cause of "the whole machine is slow", high file-IO latency, and connections that fail with no
matching firewall rule**:
- **Filesystem minifilters** (Filter Manager) — which drivers sit in *every file operation*, and at what
  altitude (Anti-Virus / Activity-Monitor / Encryption / Backup class). `fltmc` emits this; the MCP
  parses + classifies it and flags third-party (non-Microsoft) filters.
- **Network filters** — NDIS lightweight filters (adapter bindings) + the Winsock LSP catalog.
- **Baseline diff** — a *new* filter appearing at the altitude where slowness began (a freshly installed
  or leftover-from-uninstall AV/VPN filter).

Verified this box already shows the value: `tmeyes`/`mshield` (Trend Micro) minifilters in the Anti-Virus
altitude band + `SysmonDrv` in Activity-Monitor.

## 2. Architecture
```
  goose (USER) ──streamable_http──▶ 127.0.0.1:8787/mcp
   └ extension: filterstack             │
                                        ▼
                filterstack_mcp_server.py  (ELEVATED, READ-ONLY)
                  └ parsers.py  (fltmc filters/instances, Get-NetAdapterBinding, netsh winsock show
                                 catalog; altitude->vendor-class map; JSON baselines. no MCP deps)
```
Runs **elevated** (`fltmc` needs admin). Bind `127.0.0.1:8787`, streamable HTTP (FastMCP). Read-only —
only *queries* (`fltmc filters/instances`, never `attach`/`detach`). Pure stdlib (subprocess) + JSON baseline.

## 3. Ground truth (verified this box)
- `fltmc filters` → fixed-width table `Filter Name | Num Instances | Altitude | Frame`. Altitude can be
  fractional (`385250.5`). Names have no spaces.
- `fltmc instances -v C:` → per-volume `Filter | Altitude | Instance Name | Frame | SprtFtrs | VlStatus`.
- Minifilter altitude bands classify the filter: 320000-329999 Anti-Virus, 360000-389999 Activity
  Monitor, 140000-149999 Encryption, 280000-289999 Continuous Backup, etc. (Microsoft's allocated ranges).
- `Get-NetAdapterBinding` → NDIS LWF bindings; `netsh winsock show catalog` → LSP catalog (539 lines here).

## 4. Tool surface (7)
- `minifilters(filter=None, third_party_only=False)` → `[{name, altitude, altitude_class, instances,
  frame, third_party?, driver_path?, signer?}]`, sorted by altitude desc — the "what filters every file op".
- `filter_instances(volume="C:")` → `[{filter, altitude, instance_name, frame}]` for a volume.
- `network_filters()` → `{ndis_bindings:[{adapter, display, component_id, enabled}], winsock_lsp:[…]}`.
- `altitude_lookup(altitude)` → `{altitude, class, meaning}` (the Filter Manager load-order group).
- `baseline_save(name="default")` / `baseline_diff(name="default")` → minifilters that appeared/disappeared
  since a baseline (new AV/VPN/leftover filter detection).
- `filterstack_health()` → `{is_admin, fltmc_ok, minifilter_count, third_party_count}`.

Every tool returns a structured `{error:…}`, never raises.

## 5. Safety
Read-only: `fltmc filters/instances`, `Get-NetAdapterBinding`, `netsh winsock show catalog` are all
queries — no filter is attached/detached, no binding changed. `volume`/`altitude` validated. Third-party
enrichment reads `%SystemRoot%\System32\drivers\<name>.sys` metadata read-only. Only write is the JSON
baseline (atomic os.replace under a lock).

## 6. Files
`filterstack_mcp_server.py` (FastMCP, 7 tools) · `parsers.py` · `start_filterstack_mcp.ps1` /
`install_task.ps1` / `uninstall_task.ps1` (Scheduled Task `Filterstack-MCP`, 8787) · `requirements.txt`
(mcp, pytest) · `README.md` · `tests/` · `data/` (gitignored).

## 7. goose extension
```yaml
  filterstack:
    type: streamable_http
    bundled: false
    name: filterstack
    enabled: true
    uri: http://127.0.0.1:8787/mcp
    headers: {}
    env_keys: []
    timeout: 120
    description: Windows filter-stack map (filesystem minifilters + NDIS/Winsock network filters + altitude classification) via local elevated MCP server (127.0.0.1:8787)
```

## 8. Out of scope (YAGNI)
Full WFP callout/filter enumeration (`netsh wfp show filters` is multi-MB XML — v2). Attaching/detaching
filters. GPO-managed firewall (that's fwaudit/netconn territory).

## 9. Open risks
- `fltmc` output is a fixed-width table; parse by "last 3 tokens are numeric" (name may in theory contain
  spaces — none observed). Non-English Windows keeps the same table columns (values are numeric/ASCII names).
- Third-party detection = Authenticode signer not "Microsoft" (best-effort; some MS filters are catalog-signed).
- NDIS binding enumeration returns Microsoft + third-party; flag by ComponentID.
