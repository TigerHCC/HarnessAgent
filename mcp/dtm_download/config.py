"""Config loading for the dtm_download MCP: ${var} expansion (against sibling string keys + repo_root)
-> env override (DTM_DOWNLOAD_MCP_<KEY>) -> as-is. The Artifactory bearer token is NEVER read from
config.json -- it is env-only (DTM_DOWNLOAD_ARTIFACTORY_TOKEN) so it never appears in a committed file
or a tool argument.
"""
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
# repo_root = .../HarnessAgent (this file is at HarnessAgent/mcp/dtm_download/config.py)
REPO_ROOT = os.path.normpath(os.path.join(HERE, "..", ".."))
_VAR_RE = re.compile(r"\$\{([a-z_]+)\}")

TOKEN_ENV_VAR = "DTM_DOWNLOAD_ARTIFACTORY_TOKEN"


def env_key(name):
    return "DTM_DOWNLOAD_MCP_" + name.upper()


def _expand(value, scope):
    if not isinstance(value, str):
        return value
    prev, out = None, value
    while out != prev:
        prev = out
        out = _VAR_RE.sub(lambda m: str(scope.get(m.group(1), m.group(0))), out)
    return out


def load(path=None):
    path = path or os.environ.get("DTM_DOWNLOAD_MCP_CONFIG") or os.path.join(HERE, "config.json")
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

    cfg["download_path"] = scope.get("download_path", cfg.get("download_path", "./downloads/dtm"))
    cfg["artifactory_base_url"] = scope.get("artifactory_base_url", cfg.get("artifactory_base_url", ""))
    cfg["repo"] = scope.get("repo", cfg.get("repo", ""))

    for key, default in (("download_timeout_seconds", 600), ("connect_timeout_seconds", 15)):
        val = cfg.get(key, default)
        if env_key(key) in os.environ:
            val = os.environ[env_key(key)]
        cfg[key] = int(val)

    cfg.setdefault("zip_filter", ["*DTPInstallers*x64*Release*", "*DTPSamples*x64*Release*"])
    cfg.setdefault("csv_files", [
        "InstrumentationDatatypeTable.csv", "AlertDatatypeTable.csv", "AnalysisDatatypeTable.csv",
    ])
    cfg.setdefault("html_files", [
        {"file": "Datatypes.html", "label": "Datatypes"},
        {"file": "packages.html", "label": "Packages"},
        {"file": "packagesByProject.html", "label": "Packages by Project"},
        {"file": "projectsByPackage.html", "label": "Projects by Package"},
    ])
    cfg.setdefault("default_channel", "Daily")

    dp = cfg["download_path"]
    cfg["_resolved"] = {
        "download_path": {"raw": scope.get("download_path"), "resolved": dp,
                           "exists": bool(dp) and os.path.isdir(dp)},
        "token_present": {"resolved": bool(os.environ.get(TOKEN_ENV_VAR))},
    }
    return cfg


def get_token():
    """The Artifactory bearer token -- env-only, never persisted in config.json or logged."""
    return os.environ.get(TOKEN_ENV_VAR, "")
