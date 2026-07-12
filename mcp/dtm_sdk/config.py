"""Config loading for the dtmsdk MCP.

Resolution order for every value: ${var} expansion (against sibling top-level keys + built-in
repo_root) -> environment override (DTM_SDK_<UPPER_SNAKE>) -> as-is. Path existence is recorded in
_resolved so dtm_health() can point at the exact failing key rather than a bare FileNotFoundError.
"""
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
# repo_root = .../HarnessAgent (this file is at HarnessAgent/mcp/dtm_sdk/config.py)
REPO_ROOT = os.path.normpath(os.path.join(HERE, "..", ".."))
_VAR_RE = re.compile(r"\$\{([a-z_]+)\}")


class ConfigError(Exception):
    pass


def env_key(dotted):
    return "DTM_SDK_" + dotted.upper().replace(".", "_")


def _expand(value, scope):
    if not isinstance(value, str):
        return value
    prev = None
    out = value
    # iterate so ${docs_root} (which itself contains ${repo_root}) fully resolves
    while out != prev:
        prev = out
        out = _VAR_RE.sub(lambda m: str(scope.get(m.group(1), m.group(0))), out)
    return out


def load(path=None):
    path = path or os.environ.get("DTM_SDK_CONFIG") or os.path.join(HERE, "config.json")
    with open(path, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    # scalar top-level keys form the variable scope, plus repo_root
    scope = {"repo_root": REPO_ROOT.replace("\\", "/")}
    for k, v in cfg.items():
        if isinstance(v, str):
            scope[k] = v
    # env can override the scope scalars before expansion
    for k in list(scope):
        ek = env_key(k)
        if ek in os.environ:
            scope[k] = os.environ[ek]
    # re-expand scope entries that reference each other (e.g. docs_root -> repo_root)
    for k in list(scope):
        scope[k] = _expand(scope[k], scope)

    def resolve(v):
        return _expand(v, scope)

    resolved_map = {}

    def do_section(section):
        out = {}
        for name, raw in (cfg.get(section) or {}).items():
            r = resolve(raw)
            ek = env_key("%s.%s" % (section, name))
            source = "config"
            if ek in os.environ:
                r = os.environ[ek]
                source = "env"
            out[name] = r
            resolved_map["%s.%s" % (section, name)] = {
                "raw": raw, "resolved": r, "exists": os.path.exists(r), "source": source}
        return out

    cfg["executables"] = do_section("executables")
    cfg["datatype_tables"] = do_section("datatype_tables")

    howto = resolve(cfg.get("howto", ""))
    if env_key("howto") in os.environ:
        howto = os.environ[env_key("howto")]
    cfg["howto"] = howto
    resolved_map["howto"] = {"raw": cfg.get("howto"), "resolved": howto,
                             "exists": os.path.exists(howto) if howto else False, "source": "config"}

    # scalars with env override
    ts = cfg.get("timeout_seconds", 120)
    if env_key("timeout_seconds") in os.environ:
        ts = int(os.environ[env_key("timeout_seconds")])
    cfg["timeout_seconds"] = int(ts)
    cfg.setdefault("timeout_overrides", {})

    for scalar in ("app_id", "app_name", "samples_root", "docs_root"):
        if env_key(scalar) in os.environ:
            cfg[scalar] = os.environ[env_key(scalar)]

    if bool(cfg.get("app_id")) != bool(cfg.get("app_name")):
        raise ConfigError("app_id and app_name must be set together (or both left null)")

    cfg["_resolved"] = resolved_map
    return cfg
