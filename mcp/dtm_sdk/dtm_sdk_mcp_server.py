"""DTM Sample/SDK Util MCP (FastMCP, streamable HTTP, 127.0.0.1:8789).

Wraps the five DTP sample utilities. UNLIKE the windows_* diagnostic MCPs this is NOT read-only:
some commands transmit telemetry to Dell or mutate DTP config, so every command not on its util's
safe allowlist requires an argv-bound, single-use confirm token. Runs ELEVATED (the utils require it).
Goose connects via type: streamable_http, uri: http://127.0.0.1:8789/mcp.
"""
import ctypes
import subprocess
import time
from typing import List

from mcp.server.fastmcp import FastMCP

import config
import datatypes
import howto
import policy
import runner

mcp = FastMCP("dtmsdk", host="127.0.0.1", port=8789)

_CFG = None            # lazily loaded so an import never fails on a bad config
_TABLES = {}           # kind -> rows
_HOWTO_TEXT = None
_TOKENS = {}           # token -> (util, command, args, issued_at)

# util key -> DtpUtilHelper JSON support (platinum does NOT share it)
_JSON_UTILS = {"dtmutil", "instrumentation", "analytics", "transmission"}


def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def cfg():
    global _CFG
    if _CFG is None:
        _CFG = config.load()
    return _CFG


def _exe_for(util):
    path = cfg().get("executables", {}).get(util)
    if path and __import__("os").path.exists(path):
        return path
    return None


def _tables():
    if not _TABLES:
        for kind, path in cfg().get("datatype_tables", {}).items():
            try:
                _TABLES[kind] = datatypes.load_table(path)
            except Exception:
                _TABLES[kind] = []
    return _TABLES


def _howto_text():
    global _HOWTO_TEXT
    if _HOWTO_TEXT is None:
        try:
            with open(cfg()["howto"], "r", encoding="utf-8") as f:
                _HOWTO_TEXT = f.read()
        except Exception:
            _HOWTO_TEXT = ""
    return _HOWTO_TEXT


def dellhub_state():
    try:
        out = subprocess.run(["sc", "query", "DellTechHub"], capture_output=True, text=True, timeout=10)
        if "does not exist" in (out.stdout + out.stderr):
            return "absent"
        if "RUNNING" in out.stdout:
            return "running"
        if "STOPPED" in out.stdout:
            return "stopped"
        return "unknown"
    except Exception:
        return "unknown"


def _timeout_for(util, command):
    c = cfg()
    key = "%s:%s" % (util, command)
    return int(c.get("timeout_overrides", {}).get(key, c.get("timeout_seconds", 120)))


def _with_appid(args):
    c = cfg()
    if c.get("app_id") and c.get("app_name"):
        return ["--id", c["app_id"], "--appName", c["app_name"]] + list(args)
    return list(args)


def _dispatch(util, command, args, confirm_token):
    args = list(args or [])
    if not policy.validate_command(command):
        return {"error": "invalid command string: %r" % command}
    exe = _exe_for(util)
    if not exe:
        key = "executables.%s" % util
        return {"error": "%s executable not found (config key '%s'); run dtm_health for details"
                % (util, key)}
    if not is_admin():
        return {"error": "not elevated; the DTP utils require Administrator", "is_admin": False}

    category = policy.classify(util, command)
    if category != "safe":
        now = time.time()
        if confirm_token:
            rec = _TOKENS.get(confirm_token)
            if rec and rec[0] == util and rec[1] == command and rec[2] == args \
                    and policy.verify_token(util, command, args, confirm_token,
                                            now=now, issued_at=rec[3]):
                del _TOKENS[confirm_token]   # single-use
            else:
                confirm_token = ""           # fall through to re-issue a preview
        if not confirm_token:
            token = policy.make_token(util, command, args)
            _TOKENS[token] = (util, command, args, now)
            reasons = {"egress": "transmits data from this machine to Dell",
                       "state": "changes DTP/system configuration",
                       "action": "triggers work or does not terminate on its own",
                       "unknown": "is not on the safe allowlist (unrecognised command)"}
            argv = runner.build_argv(exe, command, _with_appid(args),
                                     json_flag=(util in _JSON_UTILS))
            return {"requires_confirmation": True, "confirm_token": token,
                    "command_line": " ".join(argv), "category": category,
                    "reason": reasons.get(category, reasons["unknown"]),
                    "expires_in_seconds": policy.TOKEN_TTL_SECONDS}

    return runner.run(exe, command, _with_appid(args),
                      timeout=_timeout_for(util, command),
                      json_flag=(util in _JSON_UTILS), env_json=(util in _JSON_UTILS))


# ---- lookup tools ---------------------------------------------------------
@mcp.tool()
def dtm_datatypes(kind: str, search: str = "", commodity: str = "", max: int = 50) -> dict:
    """Search DTP datatype tables. kind = instrumentation | analysis | alert. Filter by Name substring
    (search) and/or CommodityType. Returns rows with Name, GUID and metadata."""
    rows = _tables().get(kind)
    if rows is None:
        return {"error": "unknown kind %r; use instrumentation|analysis|alert" % kind}
    hits = datatypes.search(rows, term=search or None, commodity=commodity or None, max=max)
    return {"kind": kind, "count": len(hits), "rows": hits}


@mcp.tool()
def dtm_datatype(name: str) -> dict:
    """One datatype in full (name, GUID, dependencies), matched case-insensitively across all three
    tables. On a miss, returns near-match suggestions."""
    for kind, rows in _tables().items():
        hit = datatypes.find_one(rows, name)
        if hit:
            return {"kind": kind, "datatype": hit}
    sugg = []
    for rows in _tables().values():
        sugg += datatypes.suggest(rows, name)
    return {"error": "datatype %r not found" % name, "suggestions": sugg[:8]}


@mcp.tool()
def dtm_help(util: str, command: str = "") -> dict:
    """Return the HowTo documentation for a util (dtmutil|instrumentation|analytics|transmission|
    platinum), or a specific command within it. Use this to learn a command's real options."""
    if util not in policy.UTILS:
        return {"error": "unknown util %r" % util, "utils": list(policy.UTILS)}
    text = _howto_text()
    if command:
        return {"util": util, "command": command, "help": howto.command_help(text, util, command)}
    return {"util": util, "help": howto.util_section(text, util)}


@mcp.tool()
def dtm_health() -> dict:
    """Server + environment health: admin, Dell TechHub service state, resolved exe/table/howto paths
    and whether each exists. Check this first when a run fails."""
    c = cfg()
    return {
        "is_admin": is_admin(),
        "dell_techhub": dellhub_state(),
        "executables": {k: c["_resolved"].get("executables.%s" % k) for k in c.get("executables", {})},
        "datatype_tables": {k: {"exists": c["_resolved"].get("datatype_tables.%s" % k, {}).get("exists"),
                                "rows": len(_tables().get(k, []))} for k in c.get("datatype_tables", {})},
        "howto": c["_resolved"].get("howto"),
        "app_id_configured": bool(c.get("app_id")),
    }


# ---- execution tools (one per util) --------------------------------------
@mcp.tool()
def dtm_run_dtmutil(command: str, args: List[str] = [], confirm_token: str = "") -> dict:
    """Run DTMUtil (IDtmClientSdk: orchestrator config, workflows, bundle transmission). Safe commands
    run directly; others return a confirm_token you must pass back. See dtm_help('dtmutil')."""
    return _dispatch("dtmutil", command, args, confirm_token)


@mcp.tool()
def dtm_run_instrumentation(command: str, args: List[str] = [], confirm_token: str = "") -> dict:
    """Run DtpInstrumentationUtil (data collection/retrieval, commodities, datatype state). Safe
    commands run directly; others need a confirm_token. See dtm_help('instrumentation')."""
    return _dispatch("instrumentation", command, args, confirm_token)


@mcp.tool()
def dtm_run_analytics(command: str, args: List[str] = [], confirm_token: str = "") -> dict:
    """Run DtpAnalyticsUtil (analysis, alerts, subscriptions, retrieval). Safe commands run directly;
    others need a confirm_token. See dtm_help('analytics')."""
    return _dispatch("analytics", command, args, confirm_token)


@mcp.tool()
def dtm_run_transmission(command: str, args: List[str] = [], confirm_token: str = "") -> dict:
    """Run DtpTransmissionUtil (collect+transmit, retrieve+transmit, file upload). Almost everything
    here transmits data to Dell and needs a confirm_token. See dtm_help('transmission')."""
    return _dispatch("transmission", command, args, confirm_token)


@mcp.tool()
def dtm_run_platinum(command: str, args: List[str] = [], confirm_token: str = "") -> dict:
    """Run DTMPlatinumUtil (Platinum event logging, upload, heartbeat/ping). Most commands contact
    Dell and need a confirm_token. See dtm_help('platinum')."""
    return _dispatch("platinum", command, args, confirm_token)


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
