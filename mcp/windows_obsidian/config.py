"""Config loading for the obsidian MCP: ${var} expansion (against sibling string keys + repo_root)
-> env override (OBSIDIAN_MCP_<KEY>, plus OBSIDIAN_VAULT alias for vault_path) -> as-is.
"""
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.normpath(os.path.join(HERE, "..", ".."))
_VAR_RE = re.compile(r"\$\{([a-z_]+)\}")


def env_key(name):
    return "OBSIDIAN_MCP_" + name.upper()


def _expand(value, scope):
    if not isinstance(value, str):
        return value
    prev, out = None, value
    while out != prev:
        prev = out
        out = _VAR_RE.sub(lambda m: str(scope.get(m.group(1), m.group(0))), out)
    return out


def load(path=None):
    path = path or os.environ.get("OBSIDIAN_MCP_CONFIG") or os.path.join(HERE, "config.json")
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

    cfg["vault_path"] = scope.get("vault_path", cfg.get("vault_path", ""))
    if "OBSIDIAN_VAULT" in os.environ:            # convenience alias
        cfg["vault_path"] = os.environ["OBSIDIAN_VAULT"]

    for key, default in (("max_search_results", 50), ("max_file_bytes", 1048576),
                         ("confirm_ttl_seconds", 120)):
        val = cfg.get(key, default)
        if env_key(key) in os.environ:
            val = os.environ[env_key(key)]
        cfg[key] = int(val)

    vp = cfg["vault_path"]
    cfg["_resolved"] = {"vault_path": {"raw": scope.get("vault_path"), "resolved": vp,
                                       "exists": bool(vp) and os.path.isdir(vp)}}
    return cfg
