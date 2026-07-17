# DTM Deploy MCP (`dtm_deploy`)

A local, **elevated** MCP server that wraps the elevated half of
`ccp/tools/DTMTransmissionAutoTest-/Run-DTPSetup.ps1`: uninstall, enable-user-consent,
insert-test-plugin, install, enable-transmission, plus verify-collection/verify-heartbeat. Binds
**`127.0.0.1:8792`**, transport `streamable-http`, endpoint `/mcp`.

Pure-Python: COM `WindowsInstaller.Installer` (via `win32com`) for MSI property reads and
`RelatedProducts` lookup, `msiexec.exe` via `subprocess` for the actual install/uninstall action
(there is no COM-only way to run a full logged MSI install), `winreg` for the consent registry write,
`DTMUtil.exe` via `subprocess` for transmission config. No dependency on the `.ps1` scripts.

Pair this with [`dtm_download`](../dtm_download/README.md) (unelevated): pass the `msi_path` it
returns into `dtm_install`/`dtm_run_pipeline`. The two servers share no state or IPC.

---

## ⚠️ WARNING — this MCP is NOT read-only

Every system-mutating tool is **confirm-token gated**, the same pattern as `dtmsdk`: the first call
(no/stale `confirm_token`) returns a preview + a token bound to the exact tool name + args; calling
again with that token executes the action. Single-use, 120s TTL by default (`confirm_ttl_seconds`).

| Tool | Gated? | What it does |
|---|---|---|
| `dtm_uninstall(msi_path="", product_name="", confirm_token="")` | yes | Uninstalls an existing DTP install, identified via `msi_path` (reads UpgradeCode/ProductName) or `product_name` (registry DisplayName match). |
| `dtm_enable_user_consent(confirm_token="")` | yes | Writes the DTP telemetry `ConsentOverride` registry DWORD under HKLM. |
| `dtm_insert_test_plugin(plugin_path, force=False, confirm_token="")` | yes | Copies a test plugin DLL into the DTP `TransmissionPlugins` directory. |
| `dtm_install(msi_path, confirm_token="")` | yes | Installs a DTP MSI silently via `msiexec`. |
| `dtm_enable_transmission(confirm_token="")` | yes | Runs `DTMUtil.exe configure-orchestrator` to enable realtime + midnight transmission. |
| `dtm_run_pipeline(msi_path, plugin_path="", skip=[], confirm_token="")` | yes (whole pipeline, one token) | Runs uninstall -> consent -> insert_test_plugin -> install -> enable_transmission in sequence. `skip` = subset of `["uninstall","consent","plugin","install","transmission"]`. |
| `dtm_verify_collection(datatype_name="CameraInfo")` | no | Triggers an on-demand collection via `DtpInstrumentationUtil.exe` and checks for the expected success messages. |
| `dtm_verify_heartbeat(log_path="", advance_days=1, wait_seconds=3300, skip_date_change=False, build_version="")` | no | Advances the system date, polls the transmission log for HB/OTP success, restores the date (`finally`). Blocks for up to `wait_seconds`; treat as heavyweight even though it is not confirm-gated. |
| `dtm_deploy_health()` | no | admin state, DellTechHub service state, resolved paths, gated/safe tool lists. |

> The server binds loopback with **no authentication**. Any local process can reach it, so on this box
> the confirmation gate is the only thing standing between a caller and a real uninstall/install.

## Configuration

`config.json`: `download_path` (used to auto-resolve `DTMUtil.exe`/`DtpInstrumentationUtil.exe` for
transmission/verify), `consent_registry_path`/`consent_value_name`/`consent_value_data`,
`plugin_dest_dir`, `dtp_service_name`, `dtp_process_name_patterns`, `heartbeat_log_path`, poll
intervals/timeouts, `confirm_ttl_seconds`. Every key can be overridden via `DTM_DEPLOY_MCP_<KEY>` env
vars (see `config.py`).

## Running

```powershell
# one-off, foreground (for testing) -- run from an elevated shell
.\start_dtm_deploy_mcp.ps1

# persist across logons (elevated shell needed)
.\install_task.ps1
.\uninstall_task.ps1   # remove
```

## Tests

```powershell
pip install -r requirements.txt
python -m pytest tests -q
```
`msi.py`/`transmission.py`/`verify.py` are tested with mocked `subprocess`/registry/filesystem calls
(no live msiexec/registry/system-clock mutation in the unit suite); `policy.py`/`plugin.py`/
`consent.py` are exercised directly (the consent test uses a private `HKEY_CURRENT_USER` test key, not
HKLM).
