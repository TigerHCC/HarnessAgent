"""DTM Deploy MCP (FastMCP, streamable HTTP, 127.0.0.1:8792).

Wraps DTP uninstall/install/consent/plugin/transmission/verify -- the elevated half of
ccp/tools/DTMTransmissionAutoTest-/Run-DTPSetup.ps1 (download lives in the separate `dtm_download`
MCP; pass its returned msi_path into dtm_install). Runs ELEVATED (msiexec, HKLM registry writes, and
service control all require Administrator). Every system-mutating tool is confirm-token gated (see
policy.py); dtm_verify_collection/dtm_verify_heartbeat/dtm_deploy_health are safe and run directly.
Goose connects via type: streamable_http, uri: http://127.0.0.1:8792/mcp.
"""
import ctypes
import os
from typing import List

import anyio
from mcp.server.fastmcp import FastMCP

import config
import consent
import msi
import plugin
import policy
import transmission
import verify

mcp = FastMCP("dtm_deploy", host="127.0.0.1", port=8792)

_CFG = None
_TOKENS = None
_LIMITER = anyio.CapacityLimiter(4)


def cfg():
    global _CFG
    if _CFG is None:
        _CFG = config.load()
    return _CFG


def tokens():
    global _TOKENS
    if _TOKENS is None:
        _TOKENS = policy.TokenStore(ttl=cfg().get("confirm_ttl_seconds", 120))
    return _TOKENS


def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def dellhub_state():
    try:
        import win32service
        import win32serviceutil
        status = win32serviceutil.QueryServiceStatus(cfg()["dtp_service_name"])[1]
        return {win32service.SERVICE_RUNNING: "running",
                win32service.SERVICE_STOPPED: "stopped"}.get(status, "unknown")
    except Exception:
        return "absent_or_unknown"


def _gated(tool, args, confirm_token, preview, action):
    """Generic confirm-token gate: first call (no/stale token) returns a preview + token; a call with
    the matching token executes `action()` and returns its result."""
    if confirm_token and tokens().consume(tool, args, confirm_token):
        try:
            return action()
        except Exception as e:
            return {"error": str(e)}
    token = tokens().issue(tool, args)
    return {"requires_confirmation": True, "confirm_token": token, "preview": preview,
            "expires_in_seconds": cfg().get("confirm_ttl_seconds", 120)}


def _require_admin():
    if not is_admin():
        return {"error": "not elevated; dtm_deploy tools require Administrator", "is_admin": False}
    return None


# ---- gated (system-mutating) tools ----------------------------------------
def _uninstall_sync(msi_path, product_name, confirm_token):
    args = {"msi_path": msi_path, "product_name": product_name}
    admin_err = _require_admin()
    if admin_err:
        return admin_err
    if not msi_path and not product_name:
        return {"error": "provide msi_path or product_name to identify what to uninstall"}

    upgrade_code, target_name = "", product_name
    if msi_path:
        props = msi.get_msi_properties(msi_path)
        upgrade_code = props.get("UpgradeCode") or ""
        target_name = product_name or props.get("ProductName") or ""

    preview = {"action": "uninstall", "product_name": target_name, "upgrade_code": upgrade_code}
    return _gated("dtm_uninstall", args, confirm_token, preview,
                 lambda: _do_uninstall(upgrade_code, target_name))


def _do_uninstall(upgrade_code, target_name):
    codes = msi.find_products_to_uninstall(upgrade_code, target_name)
    if not codes:
        return {"status": "not_installed", "product_name": target_name}
    c = cfg()
    log_dir = os.path.join(config.HERE, "log")
    results = [msi.uninstall_product(code, log_dir) for code in codes]
    stop_info = msi.stop_dtp(c["dtp_service_name"], c["dtp_process_name_patterns"])
    still_installed, _ = msi.is_product_installed(target_name)
    return {"status": "uninstalled" if not still_installed else "still_installed",
            "results": results, "service_stop": stop_info}


@mcp.tool()
async def dtm_uninstall(msi_path: str = "", product_name: str = "", confirm_token: str = "") -> dict:
    """Uninstall an existing DTP install. Identify the target via msi_path (reads UpgradeCode/
    ProductName from the MSI to find related installed products) or product_name (exact registry
    DisplayName match). Gated: call once to get a confirm_token, then call again with it to execute."""
    return await anyio.to_thread.run_sync(_uninstall_sync, msi_path, product_name, confirm_token,
                                          limiter=_LIMITER)


def _enable_user_consent_sync(confirm_token):
    admin_err = _require_admin()
    if admin_err:
        return admin_err
    c = cfg()
    args = {"registry_path": c["consent_registry_path"], "value_name": c["consent_value_name"],
            "value_data": c["consent_value_data"]}
    return _gated("dtm_enable_user_consent", args, confirm_token, {"action": "write_registry_dword", **args},
                 lambda: _do_enable_user_consent(args))


def _do_enable_user_consent(args):
    return consent.enable_user_consent(**args)


@mcp.tool()
async def dtm_enable_user_consent(confirm_token: str = "") -> dict:
    """Write the DTP telemetry ConsentOverride registry DWORD under HKLM (from config.json). Gated."""
    return await anyio.to_thread.run_sync(_enable_user_consent_sync, confirm_token, limiter=_LIMITER)


def _insert_test_plugin_sync(plugin_path, force, confirm_token):
    admin_err = _require_admin()
    if admin_err:
        return admin_err
    args = {"plugin_path": plugin_path, "force": force}
    dest_dir = cfg()["plugin_dest_dir"]
    return _gated("dtm_insert_test_plugin", args, confirm_token,
                 {"action": "copy_plugin", "plugin_path": plugin_path, "dest_dir": dest_dir, "force": force},
                 lambda: _do_insert_test_plugin(plugin_path, dest_dir, force))


def _do_insert_test_plugin(plugin_path, dest_dir, force):
    try:
        return plugin.insert_test_plugin(plugin_path, dest_dir, force=force)
    except plugin.PluginError as e:
        return {"error": str(e)}


@mcp.tool()
async def dtm_insert_test_plugin(plugin_path: str, force: bool = False, confirm_token: str = "") -> dict:
    """Copy a test plugin DLL into the DTP TransmissionPlugins directory (from config.json). Fails if
    the plugin already exists unless force=True. Gated."""
    return await anyio.to_thread.run_sync(_insert_test_plugin_sync, plugin_path, force, confirm_token,
                                          limiter=_LIMITER)


def _install_sync(msi_path, confirm_token):
    admin_err = _require_admin()
    if admin_err:
        return admin_err
    if not os.path.isfile(msi_path):
        return {"error": "MSI not found: %s" % msi_path}
    args = {"msi_path": msi_path}
    return _gated("dtm_install", args, confirm_token, {"action": "install_msi", "msi_path": msi_path},
                 lambda: _do_install(msi_path))


def _do_install(msi_path):
    log_dir = os.path.join(config.HERE, "log")
    if msi.pending_reboot():
        return {"warning": "pending reboot detected; install may fail with exit 1603",
                **msi.install_msi(msi_path, log_dir)}
    return msi.install_msi(msi_path, log_dir)


@mcp.tool()
async def dtm_install(msi_path: str, confirm_token: str = "") -> dict:
    """Install a DTP MSI silently via msiexec. Pass the msi_path returned by dtm_download's
    dtm_download_build tool. Gated."""
    return await anyio.to_thread.run_sync(_install_sync, msi_path, confirm_token, limiter=_LIMITER)


def _enable_transmission_sync(confirm_token):
    admin_err = _require_admin()
    if admin_err:
        return admin_err
    download_path = cfg().get("download_path", "")
    args = {"download_path": download_path}
    return _gated("dtm_enable_transmission", args, confirm_token,
                 {"action": "configure_orchestrator_transmission", "download_path": download_path},
                 lambda: _do_enable_transmission(download_path))


def _do_enable_transmission(download_path):
    try:
        exe = transmission.find_dtmutil_exe(download_path)
        return transmission.enable_transmission(exe)
    except transmission.TransmissionError as e:
        return {"error": str(e)}


@mcp.tool()
async def dtm_enable_transmission(confirm_token: str = "") -> dict:
    """Run DTMUtil.exe configure-orchestrator to enable realtime + midnight transmission (resolves
    DTMUtil.exe from the configured download_path). Gated."""
    return await anyio.to_thread.run_sync(_enable_transmission_sync, confirm_token, limiter=_LIMITER)


def _run_pipeline_sync(msi_path, plugin_path, skip, confirm_token):
    admin_err = _require_admin()
    if admin_err:
        return admin_err
    skip = set(skip or [])
    args = {"msi_path": msi_path, "plugin_path": plugin_path, "skip": sorted(skip)}

    def action():
        steps = {}
        if "uninstall" not in skip:
            props = msi.get_msi_properties(msi_path) if msi_path else {}
            steps["uninstall"] = _do_uninstall(props.get("UpgradeCode") or "", props.get("ProductName") or "")
        if "consent" not in skip:
            c = cfg()
            steps["enable_user_consent"] = _do_enable_user_consent({
                "registry_path": c["consent_registry_path"], "value_name": c["consent_value_name"],
                "value_data": c["consent_value_data"]})
        if "plugin" not in skip and plugin_path:
            steps["insert_test_plugin"] = _do_insert_test_plugin(plugin_path, cfg()["plugin_dest_dir"], True)
        if "install" not in skip:
            if not os.path.isfile(msi_path):
                steps["install"] = {"error": "MSI not found: %s" % msi_path}
            else:
                steps["install"] = _do_install(msi_path)
        if "transmission" not in skip:
            steps["enable_transmission"] = _do_enable_transmission(cfg().get("download_path", ""))
        return {"steps": steps}

    return _gated("dtm_run_pipeline", args, confirm_token,
                 {"action": "run_full_pipeline", **args}, action)


@mcp.tool()
async def dtm_run_pipeline(msi_path: str, plugin_path: str = "", skip: List[str] = [],
                           confirm_token: str = "") -> dict:
    """Run the full deploy pipeline (uninstall -> consent -> insert_test_plugin -> install ->
    enable_transmission), mirroring Run-DTPSetup.ps1. skip = subset of
    ["uninstall","consent","plugin","install","transmission"]. Gated as a whole -- one confirm_token
    for the entire sequence."""
    return await anyio.to_thread.run_sync(_run_pipeline_sync, msi_path, plugin_path, skip, confirm_token,
                                          limiter=_LIMITER)


# ---- safe (non-gated) tools ------------------------------------------------
@mcp.tool()
async def dtm_verify_collection(datatype_name: str = "CameraInfo") -> dict:
    """Trigger an on-demand collection via DtpInstrumentationUtil.exe and verify the expected success
    messages appear. Read-only w.r.t. system state (no confirm_token needed)."""
    def run():
        try:
            return verify.verify_collection(cfg().get("download_path", ""), datatype_name=datatype_name)
        except verify.VerifyError as e:
            return {"error": str(e)}
    return await anyio.to_thread.run_sync(run, limiter=_LIMITER)


@mcp.tool()
async def dtm_verify_heartbeat(log_path: str = "", advance_days: int = 1, wait_seconds: int = 3300,
                               skip_date_change: bool = False, build_version: str = "") -> dict:
    """Verify the DTP transmission heartbeat: advances the system date, polls the transmission log for
    HB/OTP success, then restores the system date. This blocks for up to wait_seconds and temporarily
    changes the system clock -- no confirm_token gate is applied per the agreed scope, but callers
    should treat this as a heavyweight, environment-mutating (clock) operation."""
    c = cfg()
    def run():
        try:
            return verify.verify_heartbeat(
                log_path=log_path or c.get("heartbeat_log_path", ""), advance_days=advance_days,
                wait_seconds=wait_seconds, poll_interval=c.get("verify_poll_interval_seconds", 180),
                skip_date_change=skip_date_change, build_version=build_version)
        except verify.VerifyError as e:
            return {"error": str(e)}
    return await anyio.to_thread.run_sync(run, limiter=_LIMITER)


@mcp.tool()
def dtm_deploy_health() -> dict:
    """Server + environment health: admin, DellTechHub service state, resolved paths, and the gated
    tool list. Check this first when a call fails."""
    c = cfg()
    return {
        "is_admin": is_admin(),
        "dell_techhub_service": dellhub_state(),
        "download_path": c["_resolved"]["download_path"]["resolved"],
        "download_path_exists": c["_resolved"]["download_path"]["exists"],
        "plugin_dest_dir": c["_resolved"]["plugin_dest_dir"]["resolved"],
        "plugin_dest_dir_exists": c["_resolved"]["plugin_dest_dir"]["exists"],
        "gated_tools": sorted(policy.GATED_TOOLS),
        "safe_tools": sorted(policy.SAFE_TOOLS),
    }


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
