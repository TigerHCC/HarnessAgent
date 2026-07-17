"""Config loading for the dtm_deploy MCP: ${var} expansion (against sibling string keys + repo_root)
-> env override (DTM_DEPLOY_MCP_<KEY>) -> as-is.
"""
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
# repo_root = .../HarnessAgent (this file is at HarnessAgent/mcp/dtm_deploy/config.py)
REPO_ROOT = os.path.normpath(os.path.join(HERE, "..", ".."))
_VAR_RE = re.compile(r"\$\{([a-z_]+)\}")


def env_key(name):
    return "DTM_DEPLOY_MCP_" + name.upper()


def _expand(value, scope):
    if not isinstance(value, str):
        return value
    prev, out = None, value
    while out != prev:
        prev = out
        out = _VAR_RE.sub(lambda m: str(scope.get(m.group(1), m.group(0))), out)
    return out


def load(path=None):
    path = path or os.environ.get("DTM_DEPLOY_MCP_CONFIG") or os.path.join(HERE, "config.json")
    with open(path, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    scope = {"repo_root": REPO_ROOT.replace("\\", "/")}
    for k, v in cfg.items():
        if isinstance(v, str):
            scope[k] = v
    for k in list(scope):
        if env_key(k) in os.environ:
            scope[k] = os.environ[env_key(k)]
    for k in list(scope):
        scope[k] = _expand(scope[k], scope)

    cfg.setdefault("consent_registry_path", r"HKLM\SOFTWARE\Dell\Notification Manager\Telemetry")
    cfg.setdefault("consent_value_name", "ConsentOverride")
    cfg.setdefault("consent_value_data", 1)
    cfg.setdefault("plugin_dest_dir", r"C:\Program Files\Dell\DTP\TransmissionPlugins")
    cfg.setdefault("dtp_service_name", "DellTechHub")
    cfg.setdefault("dtp_process_name_patterns", ["Dell.TechHub*", "Dell.CoreServices.Client*"])
    cfg.setdefault("heartbeat_log_path", "")
    cfg.setdefault("download_path", "")

    for key in ("download_path", "plugin_dest_dir", "heartbeat_log_path", "dtp_service_name"):
        cfg[key] = scope.get(key, cfg.get(key, ""))

    for key, default in (("verify_poll_interval_seconds", 180), ("verify_poll_timeout_seconds", 3300),
                          ("confirm_ttl_seconds", 120)):
        val = cfg.get(key, default)
        if env_key(key) in os.environ:
            val = os.environ[env_key(key)]
        cfg[key] = int(val)

    dp = cfg.get("download_path", "")
    cfg["_resolved"] = {
        "download_path": {"raw": scope.get("download_path"), "resolved": dp,
                           "exists": bool(dp) and os.path.isdir(dp)},
        "plugin_dest_dir": {"resolved": cfg["plugin_dest_dir"],
                            "exists": os.path.isdir(cfg["plugin_dest_dir"])},
    }
    return cfg
