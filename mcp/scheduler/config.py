"""Config loading for the scheduler MCP: ${var} expansion against sibling string keys + repo_root,
then SCHEDULER_MCP_<KEY> env override. Mirrors mcp/dtm_download/config.py.
"""
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.normpath(os.path.join(HERE, "..", ".."))
_VAR_RE = re.compile(r"\$\{([a-z_]+)\}")


def env_key(name):
    return "SCHEDULER_MCP_" + name.upper()


def _expand(value, scope):
    if not isinstance(value, str):
        return value
    prev, out = None, value
    while out != prev:
        prev = out
        out = _VAR_RE.sub(lambda m: str(scope.get(m.group(1), m.group(0))), out)
    return out


def load(path=None):
    path = path or os.environ.get("SCHEDULER_MCP_CONFIG") or os.path.join(HERE, "config.json")
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

    for k in ("workspace", "schedules_path", "runs_dir"):
        cfg[k] = os.path.normpath(scope.get(k, cfg.get(k, "")))
    for k, default in (("tick_seconds", 30), ("default_max_turns", 50), ("history_limit", 20)):
        val = scope.get(k, cfg.get(k, default))
        if env_key(k) in os.environ:
            val = os.environ[env_key(k)]
        cfg[k] = int(val)
    cfg["goose_bin"] = scope.get("goose_bin", cfg.get("goose_bin", "")) or ""
    return cfg
