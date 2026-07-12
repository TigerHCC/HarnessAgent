# DTM Sample/SDK Util MCP (`dtmsdk`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the `dtmsdk` MCP server that wraps the five DTP sample utilities (65 commands) plus the three datatype tables and the HowTo, gated by an argv-bound confirmation token, with all paths in one redeployable config.

**Architecture:** A thin FastMCP server (`dtm_sdk_mcp_server.py`) delegating to five pure/near-pure modules — `config` (path resolution), `policy` (classification + confirm tokens), `runner` (subprocess), `datatypes` (CSV lookup), `howto` (doc extraction). The security-critical logic (`policy`) is pure and exhaustively unit-tested; the runner is tested against a fake echo exe so no real util (and no data egress) is ever triggered by the suite.

**Tech Stack:** Python 3.13, `mcp` (FastMCP, streamable-http), `pyyaml`, `pytest`. PowerShell 5.1 for the scheduled-task scripts. Follows the existing `mcp/windows_*` conventions.

## Global Constraints

- Extension id is **`dtmsdk`** (never `dtm` — that is the GB10 RAG agent). Server name passed to `FastMCP("dtmsdk", ...)`.
- Bind **`127.0.0.1:8789`**, transport `streamable-http`, endpoint `/mcp`.
- Scheduled task name **`DtmSdk-MCP`**, `RunLevel Highest`, `-AtLogOn`, current user.
- Commands are validated against `^[a-z0-9][a-z0-9 -]*$` and executed as an **argv list, never via a shell**.
- A command not on its util's `safe` allowlist (dangerous OR unrecognised) requires a confirm token.
- Confirm token = first 16 hex chars of `sha256("<util>|<command>|<json-argv>")`; **single-use**, expires after **120 s**.
- Utils output YAML by default; for the four `DtpUtilHelper` utils (dtmutil, instrumentation, analytics, transmission) the runner sets env `DTPUTIL_JSON_OUTPUT=true` and passes `--json`. **DTMPlatinumUtil gets neither** (it does not share `DtpUtilHelper`).
- Output parse order is `json.loads` → `yaml.safe_load` → raw text; a parse failure never turns a successful command into a failure.
- The test suite must **never execute a real util** except in the explicitly gated Phase-1 live tests (skipped unless elevated AND Dell TechHub running AND `DTM_SDK_LIVE_TESTS=1`), and even then only safe/local commands.
- `is_admin()` uses `ctypes.windll.shell32.IsUserAnAdmin()` wrapped in try/except → False, matching the other MCPs.
- All Python file reads/writes that matter use `encoding="utf-8"`.

**The 65 commands (verbatim from `Sample_Utilities_HowTo.md`):**

```
dtmutil (17):       configure-orchestrator, apply-app-configuration, clear-app-configuration,
                    validate-app-configuration, workflow start, workflow status,
                    workflow retrieve collection, workflow retrieve analysis, workflow retrieve alert,
                    workflow cancel, workflow history, bundle-transmission-status,
                    bundle-transmission-date-range, retrieve-bundle-id, invoke-emergency,
                    configure-proxy, reset-proxy
instrumentation(15):collect, periodic-collect, subscribe, retrieve, client-retrieve, retrieve-file,
                    retrieve-requests, get-commodity, set-commodity, subscribe-commodity, metadata,
                    enable-datatype, reset-datatype-state, emit-custom-software-telemetry-event, unregister
analytics (19):     custom-analysis, daily-analysis, weekly-analysis, default-alert, custom-alert,
                    register-alert, subscribe, create-alert-subscriptions, retrieve-alert-subscriptions,
                    listen-alert-subscriptions, retrieve-analysis, retrieve-alert, retrieve-alerts,
                    retrieve-client-alerts, retrieve-custom, metadata, temporary-enable,
                    retrieve-temporary-enabling-requests, unregister
transmission (7):   collect-transmit, retrieve-transmit, periodic-transmit, file-upload,
                    transmission-status, cancel, unregister
platinum (7):       platinum-event, platinum-upload, platinum-heartbeat, platinum-ping,
                    transmission-status, configure-proxy, reset-proxy
```

**The 24 `safe` commands (everything else needs confirmation):**

```
dtmutil:        validate-app-configuration, workflow status, workflow retrieve collection,
                workflow retrieve analysis, workflow retrieve alert, workflow history,
                bundle-transmission-status, bundle-transmission-date-range, retrieve-bundle-id
instrumentation:retrieve, client-retrieve, retrieve-requests, get-commodity, metadata
analytics:      retrieve-analysis, retrieve-alert, retrieve-alerts, retrieve-client-alerts,
                retrieve-custom, retrieve-alert-subscriptions, retrieve-temporary-enabling-requests,
                metadata
transmission:   transmission-status
platinum:       transmission-status
```

---

### Task 1: `policy.py` — classification + confirm tokens (pure, security-critical)

**Files:**
- Create: `mcp/dtm_sdk/policy.py`
- Create: `mcp/dtm_sdk/conftest.py`
- Test: `mcp/dtm_sdk/tests/test_policy.py`

**Interfaces:**
- Produces:
  - `UTILS: dict[str, list[str]]` — the 5 utils → their full command lists (verbatim above).
  - `SAFE: dict[str, set[str]]` — the 24 safe commands.
  - `CATEGORY: dict[tuple[str,str], str]` — `(util, command)` → `"egress"|"state"|"action"` for the 41 gated commands (used only to word the preview).
  - `classify(util, command) -> str` — returns `"safe"`, or the category for gated, or `"unknown"` (treated as gated).
  - `is_safe(util, command) -> bool`
  - `make_token(util, command, args) -> str` — `sha256("<util>|<command>|<json.dumps(args)>")[:16]`.
  - `verify_token(util, command, args, token, *, now, issued_at) -> bool` — hash matches AND `now - issued_at <= 120`.
  - `validate_command(command) -> bool` — matches `^[a-z0-9][a-z0-9 -]*$`.

- [ ] **Step 1: Create `conftest.py`** (lets tests import the modules regardless of pytest invocation)

```python
"""Make the dtm_sdk modules importable from tests/ regardless of pytest invocation."""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
```

- [ ] **Step 2: Write the failing test**

```python
# mcp/dtm_sdk/tests/test_policy.py
import json
import policy


def test_all_65_commands_present():
    total = sum(len(v) for v in policy.UTILS.values())
    assert total == 65
    assert set(policy.UTILS) == {"dtmutil", "instrumentation", "analytics", "transmission", "platinum"}


def test_safe_count_is_24_and_all_real():
    assert sum(len(v) for v in policy.SAFE.values()) == 24
    for util, cmds in policy.SAFE.items():
        for c in cmds:
            assert c in policy.UTILS[util], f"{util}:{c} not a real command"


def test_classify_safe_vs_gated():
    assert policy.classify("instrumentation", "metadata") == "safe"
    assert policy.classify("transmission", "collect-transmit") == "egress"
    assert policy.classify("instrumentation", "enable-datatype") == "state"
    assert policy.classify("instrumentation", "collect") == "action"


def test_retrieve_file_is_not_safe():
    # elevated write to a caller-chosen path -> dangerous despite being a "retrieve"
    assert not policy.is_safe("instrumentation", "retrieve-file")


def test_bundle_date_range_is_safe_despite_its_name():
    # calls RetrieveBundleTransmissionStatusItemsAsync -- a query
    assert policy.is_safe("dtmutil", "bundle-transmission-date-range")


def test_unknown_command_is_gated_not_safe():
    assert policy.classify("instrumentation", "brand-new-command") == "unknown"
    assert not policy.is_safe("instrumentation", "brand-new-command")


def test_token_roundtrip():
    t = policy.make_token("transmission", "collect-transmit", ["--datatype-name", "X"])
    assert policy.verify_token("transmission", "collect-transmit", ["--datatype-name", "X"],
                               t, now=100.0, issued_at=100.0)


def test_token_bound_to_args():
    t = policy.make_token("transmission", "collect-transmit", ["--datatype-name", "X"])
    # same token, different args -> rejected
    assert not policy.verify_token("transmission", "collect-transmit", ["--datatype-name", "Y"],
                                   t, now=100.0, issued_at=100.0)


def test_token_bound_to_command():
    t = policy.make_token("transmission", "collect-transmit", [])
    assert not policy.verify_token("transmission", "cancel", [], t, now=100.0, issued_at=100.0)


def test_token_expires():
    t = policy.make_token("transmission", "cancel", [])
    assert not policy.verify_token("transmission", "cancel", [], t, now=221.0, issued_at=100.0)
    assert policy.verify_token("transmission", "cancel", [], t, now=220.0, issued_at=100.0)


def test_validate_command():
    assert policy.validate_command("workflow retrieve collection")
    assert policy.validate_command("collect-transmit")
    assert not policy.validate_command("collect; rm -rf /")
    assert not policy.validate_command("--flag")
    assert not policy.validate_command("")
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd mcp/dtm_sdk && python -m pytest tests/test_policy.py -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'policy'`).

- [ ] **Step 4: Write `policy.py`**

```python
"""Command classification + confirm-token logic for the dtmsdk MCP. Pure: no I/O, no subprocess.

A command is either on its util's SAFE allowlist (run directly) or it is gated (dangerous or
unrecognised -> the caller must supply a confirm token bound to the exact util+command+args).
Classification is derived from each command's documented SDK method, not its name.
"""
import hashlib
import json
import re

TOKEN_TTL_SECONDS = 120
_CMD_RE = re.compile(r"^[a-z0-9][a-z0-9 -]*$")

UTILS = {
    "dtmutil": [
        "configure-orchestrator", "apply-app-configuration", "clear-app-configuration",
        "validate-app-configuration", "workflow start", "workflow status",
        "workflow retrieve collection", "workflow retrieve analysis", "workflow retrieve alert",
        "workflow cancel", "workflow history", "bundle-transmission-status",
        "bundle-transmission-date-range", "retrieve-bundle-id", "invoke-emergency",
        "configure-proxy", "reset-proxy",
    ],
    "instrumentation": [
        "collect", "periodic-collect", "subscribe", "retrieve", "client-retrieve", "retrieve-file",
        "retrieve-requests", "get-commodity", "set-commodity", "subscribe-commodity", "metadata",
        "enable-datatype", "reset-datatype-state", "emit-custom-software-telemetry-event", "unregister",
    ],
    "analytics": [
        "custom-analysis", "daily-analysis", "weekly-analysis", "default-alert", "custom-alert",
        "register-alert", "subscribe", "create-alert-subscriptions", "retrieve-alert-subscriptions",
        "listen-alert-subscriptions", "retrieve-analysis", "retrieve-alert", "retrieve-alerts",
        "retrieve-client-alerts", "retrieve-custom", "metadata", "temporary-enable",
        "retrieve-temporary-enabling-requests", "unregister",
    ],
    "transmission": [
        "collect-transmit", "retrieve-transmit", "periodic-transmit", "file-upload",
        "transmission-status", "cancel", "unregister",
    ],
    "platinum": [
        "platinum-event", "platinum-upload", "platinum-heartbeat", "platinum-ping",
        "transmission-status", "configure-proxy", "reset-proxy",
    ],
}

SAFE = {
    "dtmutil": {
        "validate-app-configuration", "workflow status", "workflow retrieve collection",
        "workflow retrieve analysis", "workflow retrieve alert", "workflow history",
        "bundle-transmission-status", "bundle-transmission-date-range", "retrieve-bundle-id",
    },
    "instrumentation": {"retrieve", "client-retrieve", "retrieve-requests", "get-commodity", "metadata"},
    "analytics": {
        "retrieve-analysis", "retrieve-alert", "retrieve-alerts", "retrieve-client-alerts",
        "retrieve-custom", "retrieve-alert-subscriptions", "retrieve-temporary-enabling-requests",
        "metadata",
    },
    "transmission": {"transmission-status"},
    "platinum": {"transmission-status"},
}

# (util, command) -> category, for the 41 gated commands. Used only to word the confirmation preview.
_EGRESS = {
    ("dtmutil", "invoke-emergency"),
    ("instrumentation", "emit-custom-software-telemetry-event"),
    ("transmission", "collect-transmit"), ("transmission", "retrieve-transmit"),
    ("transmission", "periodic-transmit"), ("transmission", "file-upload"),
    ("platinum", "platinum-event"), ("platinum", "platinum-upload"),
    ("platinum", "platinum-heartbeat"), ("platinum", "platinum-ping"),
}
_STATE = {
    ("dtmutil", "configure-orchestrator"), ("dtmutil", "apply-app-configuration"),
    ("dtmutil", "clear-app-configuration"), ("dtmutil", "configure-proxy"), ("dtmutil", "reset-proxy"),
    ("instrumentation", "set-commodity"), ("instrumentation", "enable-datatype"),
    ("instrumentation", "reset-datatype-state"), ("instrumentation", "unregister"),
    ("analytics", "register-alert"), ("analytics", "create-alert-subscriptions"),
    ("analytics", "temporary-enable"), ("analytics", "unregister"),
    ("transmission", "unregister"),
    ("platinum", "configure-proxy"), ("platinum", "reset-proxy"),
}
# everything gated and not egress/state is "action" (triggers work or does not terminate)


def validate_command(command):
    return bool(_CMD_RE.match(command or ""))


def is_safe(util, command):
    return command in SAFE.get(util, set())


def classify(util, command):
    if is_safe(util, command):
        return "safe"
    if command not in UTILS.get(util, []):
        return "unknown"
    if (util, command) in _EGRESS:
        return "egress"
    if (util, command) in _STATE:
        return "state"
    return "action"


def _digest(util, command, args):
    payload = "%s|%s|%s" % (util, command, json.dumps(list(args), separators=(",", ":")))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def make_token(util, command, args):
    return _digest(util, command, args)


def verify_token(util, command, args, token, *, now, issued_at):
    if not token or token != _digest(util, command, args):
        return False
    return (now - issued_at) <= TOKEN_TTL_SECONDS
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd mcp/dtm_sdk && python -m pytest tests/test_policy.py -q`
Expected: PASS (11 tests).

- [ ] **Step 6: Commit**

```bash
git add mcp/dtm_sdk/policy.py mcp/dtm_sdk/conftest.py mcp/dtm_sdk/tests/test_policy.py
git commit -m "feat(dtmsdk): command classification + argv-bound confirm tokens"
```

---

### Task 2: `config.py` — path resolution (`${}` expansion, env override, auto-probe)

**Files:**
- Create: `mcp/dtm_sdk/config.py`
- Create: `mcp/dtm_sdk/config.json`
- Test: `mcp/dtm_sdk/tests/test_config.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `load(path=None) -> dict` — reads `config.json` (or `$DTM_SDK_CONFIG`), applies `${}` expansion then env overrides, returns the resolved config dict with an added `_resolved: {key: {raw, resolved, exists, source}}` map for the executables/tables/howto.
  - `env_key(dotted) -> str` — `"samples_root"` → `"DTM_SDK_SAMPLES_ROOT"`, `"timeout_seconds"` → `"DTM_SDK_TIMEOUT_SECONDS"`.
  - `ConfigError(Exception)` — raised on `app_id` xor `app_name`.

**Interface note for later tasks:** the returned dict has `executables` (5 resolved paths), `datatype_tables` (3), `howto` (str), `timeout_seconds` (int), `timeout_overrides` (dict), `app_id`/`app_name` (str|None), `policy` (unused at runtime — `policy.py` is the source of truth; the config block is documentation/override room only in this phase).

- [ ] **Step 1: Create `config.json`**

```json
{
  "samples_root": "C:/Users/a9027/source/Agentic/DTM/DTPSamples-4.0.0.9390_x64_Release/Samples",
  "docs_root": "${repo_root}/docs/dtm_sdk_doc",

  "executables": {
    "dtmutil":         "${samples_root}/DTMUtil/bin/Release/DTMUtil.exe",
    "instrumentation": "${samples_root}/DtpInstrumentationUtil.SubAgent/bin/Release/DtpInstrumentationUtil.exe",
    "analytics":       "${samples_root}/DtpAnalyticsUtil.SubAgent/bin/Release/DtpAnalyticsUtil.exe",
    "transmission":    "${samples_root}/DtpTransmissionUtil.SubAgent/bin/Release/DtpTransmissionUtil.exe",
    "platinum":        "${samples_root}/DTMPlatinumUtil.SubAgent/bin/Release/DTMPlatinumUtil.exe"
  },

  "datatype_tables": {
    "instrumentation": "${docs_root}/InstrumentationDatatypeTable.csv",
    "analysis":        "${docs_root}/AnalysisDatatypeTable.csv",
    "alert":           "${docs_root}/AlertDatatypeTable.csv"
  },
  "howto": "${docs_root}/Sample_Utilities_HowTo.md",

  "app_id": null,
  "app_name": null,

  "timeout_seconds": 120,
  "timeout_overrides": { "transmission:collect-transmit": 600 }
}
```

- [ ] **Step 2: Write the failing test**

```python
# mcp/dtm_sdk/tests/test_config.py
import json
import os
import config


def _write(tmp_path, obj):
    p = tmp_path / "config.json"
    p.write_text(json.dumps(obj), encoding="utf-8")
    return str(p)


def test_var_expansion(tmp_path):
    p = _write(tmp_path, {"samples_root": "R", "executables": {"dtmutil": "${samples_root}/a.exe"},
                          "datatype_tables": {}, "howto": "", "timeout_seconds": 120,
                          "timeout_overrides": {}, "app_id": None, "app_name": None})
    cfg = config.load(p)
    assert cfg["executables"]["dtmutil"] == "R/a.exe"


def test_env_override(tmp_path, monkeypatch):
    p = _write(tmp_path, {"samples_root": "R", "executables": {"dtmutil": "${samples_root}/a.exe"},
                          "datatype_tables": {}, "howto": "", "timeout_seconds": 120,
                          "timeout_overrides": {}, "app_id": None, "app_name": None})
    monkeypatch.setenv("DTM_SDK_SAMPLES_ROOT", "OVERRIDE")
    cfg = config.load(p)
    assert cfg["executables"]["dtmutil"] == "OVERRIDE/a.exe"


def test_timeout_env_override_is_int(tmp_path, monkeypatch):
    p = _write(tmp_path, {"samples_root": "R", "executables": {}, "datatype_tables": {}, "howto": "",
                          "timeout_seconds": 120, "timeout_overrides": {}, "app_id": None, "app_name": None})
    monkeypatch.setenv("DTM_SDK_TIMEOUT_SECONDS", "300")
    cfg = config.load(p)
    assert cfg["timeout_seconds"] == 300


def test_resolved_map_reports_existence(tmp_path):
    real = tmp_path / "a.exe"
    real.write_text("x", encoding="utf-8")
    p = _write(tmp_path, {"samples_root": str(tmp_path), "executables": {"dtmutil": "${samples_root}/a.exe",
               "analytics": "${samples_root}/missing.exe"}, "datatype_tables": {}, "howto": "",
               "timeout_seconds": 120, "timeout_overrides": {}, "app_id": None, "app_name": None})
    cfg = config.load(p)
    assert cfg["_resolved"]["executables.dtmutil"]["exists"] is True
    assert cfg["_resolved"]["executables.analytics"]["exists"] is False


def test_appid_without_appname_raises(tmp_path):
    p = _write(tmp_path, {"samples_root": "R", "executables": {}, "datatype_tables": {}, "howto": "",
                          "timeout_seconds": 120, "timeout_overrides": {}, "app_id": "abc", "app_name": None})
    try:
        config.load(p)
        assert False, "expected ConfigError"
    except config.ConfigError:
        pass


def test_env_key():
    assert config.env_key("samples_root") == "DTM_SDK_SAMPLES_ROOT"
    assert config.env_key("timeout_seconds") == "DTM_SDK_TIMEOUT_SECONDS"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd mcp/dtm_sdk && python -m pytest tests/test_config.py -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'config'`).

- [ ] **Step 4: Write `config.py`**

```python
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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd mcp/dtm_sdk && python -m pytest tests/test_config.py -q`
Expected: PASS (6 tests).

- [ ] **Step 6: Commit**

```bash
git add mcp/dtm_sdk/config.py mcp/dtm_sdk/config.json mcp/dtm_sdk/tests/test_config.py
git commit -m "feat(dtmsdk): config with var expansion, env override, existence map"
```

---

### Task 3: `datatypes.py` — CSV lookup (case-insensitive, near-miss suggestions)

**Files:**
- Create: `mcp/dtm_sdk/datatypes.py`
- Test: `mcp/dtm_sdk/tests/test_datatypes.py`

**Interfaces:**
- Consumes: `config.load()["datatype_tables"]` (3 resolved CSV paths).
- Produces:
  - `load_table(path) -> list[dict]` — CSV rows as dicts (uses `csv.DictReader`).
  - `search(rows, term=None, commodity=None, max=50) -> list[dict]` — substring match on Name (case-insensitive), optional CommodityType filter.
  - `find_one(rows, name) -> dict | None` — exact case-insensitive Name match.
  - `suggest(rows, name, n=5) -> list[str]` — near-miss Names via `difflib.get_close_matches`.

- [ ] **Step 1: Write the failing test**

```python
# mcp/dtm_sdk/tests/test_datatypes.py
import datatypes


ROWS = [
    {"Name": "BatteryStaticData", "GUID": "g1", "CommodityType": "Battery"},
    {"Name": "BatteryDynamicData", "GUID": "g2", "CommodityType": "Battery"},
    {"Name": "ActivePenInfo", "GUID": "g3", "CommodityType": "Stylus"},
]


def test_find_one_is_case_insensitive():
    assert datatypes.find_one(ROWS, "batterystaticdata")["GUID"] == "g1"
    assert datatypes.find_one(ROWS, "BATTERYSTATICDATA")["GUID"] == "g1"


def test_find_one_missing_returns_none():
    assert datatypes.find_one(ROWS, "Nope") is None


def test_search_substring_and_commodity():
    names = [r["Name"] for r in datatypes.search(ROWS, term="battery")]
    assert names == ["BatteryStaticData", "BatteryDynamicData"]
    assert len(datatypes.search(ROWS, commodity="Stylus")) == 1


def test_suggest_near_miss():
    s = datatypes.suggest(ROWS, "BatteryStatic")
    assert "BatteryStaticData" in s


def test_load_table_reads_real_csv(tmp_path):
    p = tmp_path / "t.csv"
    p.write_text('"Name","GUID"\n"Foo","abc"\n', encoding="utf-8")
    rows = datatypes.load_table(str(p))
    assert rows == [{"Name": "Foo", "GUID": "abc"}]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd mcp/dtm_sdk && python -m pytest tests/test_datatypes.py -q`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Write `datatypes.py`**

```python
"""Datatype GUID table lookup. Directly defuses the HowTo's case-sensitivity trap: names resolve
case-insensitively and a miss returns near-match suggestions instead of a bare 'not found'.
"""
import csv
import difflib


def load_table(path):
    with open(path, "r", encoding="utf-8", newline="") as f:
        return [dict(row) for row in csv.DictReader(f)]


def search(rows, term=None, commodity=None, max=50):
    term_l = (term or "").lower()
    comm_l = (commodity or "").lower()
    out = []
    for r in rows:
        if term_l and term_l not in r.get("Name", "").lower():
            continue
        if comm_l and comm_l != r.get("CommodityType", "").lower():
            continue
        out.append(r)
        if len(out) >= max:
            break
    return out


def find_one(rows, name):
    nl = (name or "").lower()
    for r in rows:
        if r.get("Name", "").lower() == nl:
            return r
    return None


def suggest(rows, name, n=5):
    return difflib.get_close_matches(name, [r.get("Name", "") for r in rows], n=n, cutoff=0.5)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd mcp/dtm_sdk && python -m pytest tests/test_datatypes.py -q`
Expected: PASS (5 tests).

- [ ] **Step 5: Sanity-check against the real CSVs**

Run: `cd mcp/dtm_sdk && python -c "import datatypes as d; rows=d.load_table('../../docs/dtm_sdk_doc/InstrumentationDatatypeTable.csv'); print(len(rows), d.find_one(rows,'activepeninfo')['GUID'])"`
Expected: prints `163 490a5a44-5a76-4b1f-8bc2-13e1de80b268`.

- [ ] **Step 6: Commit**

```bash
git add mcp/dtm_sdk/datatypes.py mcp/dtm_sdk/tests/test_datatypes.py
git commit -m "feat(dtmsdk): case-insensitive datatype lookup with near-miss suggestions"
```

---

### Task 4: `howto.py` — section extraction from the HowTo

**Files:**
- Create: `mcp/dtm_sdk/howto.py`
- Test: `mcp/dtm_sdk/tests/test_howto.py`

**Interfaces:**
- Consumes: `config.load()["howto"]` (path to `Sample_Utilities_HowTo.md`).
- Produces:
  - `util_section(text, util) -> str` — the `## <Util> --` section (up to the next `## `).
  - `command_help(text, util, command) -> str` — within a util section, the `#### \`command\`` block (up to the next `#### ` or `### `); falls back to the whole util section if the exact command block is not found.
  - `UTIL_HEADINGS: dict[str,str]` — maps the 5 util keys to their heading names (`"dtmutil"` → `"DTMUtil"`, `"instrumentation"` → `"DtpInstrumentationUtil"`, etc).

- [ ] **Step 1: Write the failing test**

```python
# mcp/dtm_sdk/tests/test_howto.py
import howto

DOC = """# Title

## DTMUtil -- DTM Client SDK Utility

Intro to dtmutil.

#### `workflow status`

Query workflow status.

#### `workflow start`

Start it.

## DtpInstrumentationUtil -- Instrumentation SDK Utility

Intro to instrumentation.
"""


def test_util_section():
    s = howto.util_section(DOC, "dtmutil")
    assert "Intro to dtmutil" in s
    assert "DtpInstrumentationUtil" not in s


def test_command_help():
    s = howto.command_help(DOC, "dtmutil", "workflow status")
    assert "Query workflow status" in s
    assert "Start it" not in s


def test_command_help_falls_back_to_section():
    s = howto.command_help(DOC, "dtmutil", "no-such-command")
    assert "Intro to dtmutil" in s
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd mcp/dtm_sdk && python -m pytest tests/test_howto.py -q`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Write `howto.py`**

```python
"""Extract sections from Sample_Utilities_HowTo.md so the agent can read a command's real options
instead of us hard-coding 65 signatures.
"""
import re

UTIL_HEADINGS = {
    "dtmutil": "DTMUtil",
    "instrumentation": "DtpInstrumentationUtil",
    "analytics": "DtpAnalyticsUtil",
    "transmission": "DtpTransmissionUtil",
    "platinum": "DTMPlatinumUtil",
}


def util_section(text, util):
    heading = UTIL_HEADINGS.get(util)
    if not heading:
        return ""
    m = re.search(r"^## %s\b.*?$" % re.escape(heading), text, re.M)
    if not m:
        return ""
    start = m.start()
    nxt = re.search(r"^## ", text[m.end():], re.M)
    end = m.end() + nxt.start() if nxt else len(text)
    return text[start:end].strip()


def command_help(text, util, command):
    section = util_section(text, util)
    if not section:
        return ""
    m = re.search(r"^#### `%s`.*?$" % re.escape(command), section, re.M)
    if not m:
        return section
    start = m.start()
    nxt = re.search(r"^(#### |### )", section[m.end():], re.M)
    end = m.end() + nxt.start() if nxt else len(section)
    return section[start:end].strip()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd mcp/dtm_sdk && python -m pytest tests/test_howto.py -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Sanity-check against the real HowTo**

Run: `cd mcp/dtm_sdk && python -c "import howto,config; c=config.load(); t=open(c['howto'],encoding='utf-8').read(); print('collect-transmit' in howto.command_help(t,'transmission','collect-transmit'))"`
Expected: prints `True`.

- [ ] **Step 6: Commit**

```bash
git add mcp/dtm_sdk/howto.py mcp/dtm_sdk/tests/test_howto.py
git commit -m "feat(dtmsdk): HowTo section + per-command extraction"
```

---

### Task 5: `runner.py` — subprocess execution, timeout, output parsing

**Files:**
- Create: `mcp/dtm_sdk/runner.py`
- Test: `mcp/dtm_sdk/tests/test_runner.py`
- Create (test fixture): `mcp/dtm_sdk/tests/fake_util.py`

**Interfaces:**
- Consumes: `policy.validate_command`, resolved exe path, `timeout_seconds`.
- Produces:
  - `build_argv(exe, command, args, *, json_flag) -> list[str]` — prefix (`[exe]`, or `list(exe)` if `exe` is already a list/tuple) `+ command.split() + (["--json"] if json_flag else []) + args`. The list form exists so tests can invoke the fake util as `[python, fake_util.py]` (Python needs the script path *before* the command words).
  - `run(exe, command, args, *, timeout, json_flag, env_json) -> dict` — executes argv-list (no shell); returns `{ok, exit_code, command_line, parsed, stdout_raw, stderr, duration_seconds, format, timed_out}`. `env_json` toggles `DTPUTIL_JSON_OUTPUT=true`.
  - `parse_output(text) -> (value, fmt)` — `json.loads` → `yaml.safe_load` → `(text, "text")`.

- [ ] **Step 1: Create the fake util fixture** (a real exe stand-in; no DTP, no admin)

```python
# mcp/dtm_sdk/tests/fake_util.py
"""Stand-in for a DTP util: echoes how it was invoked so the runner can be tested without a real
util (and without any data egress). Controlled by argv:
  --emit json|yaml|text   what to print on stdout
  --exit N                exit code
  --sleep S               sleep S seconds (to exercise the timeout path)
"""
import json
import os
import sys
import time


def main():
    argv = sys.argv[1:]
    emit = "json"
    code = 0
    sleep = 0.0
    for i, a in enumerate(argv):
        if a == "--emit" and i + 1 < len(argv):
            emit = argv[i + 1]
        elif a == "--exit" and i + 1 < len(argv):
            code = int(argv[i + 1])
        elif a == "--sleep" and i + 1 < len(argv):
            sleep = float(argv[i + 1])
    if sleep:
        time.sleep(sleep)
    payload = {"argv": argv, "json_env": os.environ.get("DTPUTIL_JSON_OUTPUT")}
    if emit == "json":
        print(json.dumps(payload))
    elif emit == "yaml":
        print("argv:")
        for a in argv:
            print("  - %s" % a)
    else:
        print("plain text output " + " ".join(argv))
    sys.exit(code)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Write the failing test**

```python
# mcp/dtm_sdk/tests/test_runner.py
import os
import sys
import runner

HERE = os.path.dirname(os.path.abspath(__file__))
FAKE = os.path.join(HERE, "fake_util.py")


def _run(command, args, **kw):
    # invoke the fake util through the python interpreter; the [python, script] list is the exe
    # prefix, so the script lands before the command words (Python needs argv[1] to be the script).
    kw.setdefault("timeout", 10)
    kw.setdefault("json_flag", False)
    kw.setdefault("env_json", False)
    return runner.run([sys.executable, FAKE], command, args, **kw)


def test_build_argv_adds_json_flag():
    argv = runner.build_argv("X.exe", "workflow status", ["--id", "7"], json_flag=True)
    assert argv == ["X.exe", "workflow", "status", "--json", "--id", "7"]


def test_build_argv_accepts_list_prefix():
    argv = runner.build_argv(["py", "u.py"], "metadata", ["--id", "7"], json_flag=False)
    assert argv == ["py", "u.py", "metadata", "--id", "7"]


def test_run_parses_json():
    r = _run("metadata", ["--emit", "json"])
    assert r["ok"] and r["exit_code"] == 0
    assert r["format"] == "json"
    assert "metadata" in r["parsed"]["argv"]


def test_run_parses_yaml_fallback():
    r = _run("metadata", ["--emit", "yaml"])
    assert r["format"] == "yaml"
    assert "argv" in r["parsed"]


def test_run_text_fallback_never_fails_the_command():
    r = _run("metadata", ["--emit", "text"])
    assert r["ok"] is True
    assert r["format"] == "text"
    assert "plain text output" in r["stdout_raw"]


def test_run_nonzero_exit():
    r = _run("metadata", ["--emit", "json", "--exit", "3"])
    assert r["ok"] is False
    assert r["exit_code"] == 3


def test_env_json_sets_dtputil_var():
    r = _run("metadata", ["--emit", "json"], env_json=True)
    assert r["parsed"]["json_env"] == "true"


def test_timeout_returns_partial_not_exception():
    r = _run("metadata", ["--emit", "json", "--sleep", "3"], timeout=1)
    assert r["timed_out"] is True
    assert r["ok"] is False
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd mcp/dtm_sdk && python -m pytest tests/test_runner.py -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'runner'`).

- [ ] **Step 4: Write `runner.py`**

```python
"""Subprocess execution for the dtmsdk utils. argv-list only (no shell); output parsed json -> yaml
-> text with the raw always preserved so a parse miss never looks like a command failure.
"""
import json
import os
import subprocess
import time

try:
    import yaml
except Exception:  # pyyaml is a declared dep; degrade to json+text if somehow absent
    yaml = None


def build_argv(exe, command, args, *, json_flag):
    prefix = list(exe) if isinstance(exe, (list, tuple)) else [exe]
    argv = prefix + command.split()
    if json_flag:
        argv.append("--json")
    return argv + list(args)


def parse_output(text):
    stripped = (text or "").strip()
    if not stripped:
        return None, "text"
    try:
        return json.loads(stripped), "json"
    except ValueError:
        pass
    if yaml is not None:
        try:
            v = yaml.safe_load(stripped)
            if isinstance(v, (dict, list)):
                return v, "yaml"
        except Exception:
            pass
    return text, "text"


def run(exe, command, args, *, timeout, json_flag, env_json):
    argv = build_argv(exe, command, args, json_flag=json_flag)
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    if env_json:
        env["DTPUTIL_JSON_OUTPUT"] = "true"
    start = time.monotonic()
    timed_out = False
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout, env=env)
        out, err, code = proc.stdout, proc.stderr, proc.returncode
    except subprocess.TimeoutExpired as e:
        timed_out = True
        out = e.stdout or ""
        if isinstance(out, bytes):
            out = out.decode("utf-8", "replace")
        err = e.stderr or ""
        if isinstance(err, bytes):
            err = err.decode("utf-8", "replace")
        code = -1
    dur = round(time.monotonic() - start, 3)
    parsed, fmt = parse_output(out)
    return {
        "ok": (code == 0 and not timed_out),
        "exit_code": code,
        "command_line": " ".join(argv),
        "parsed": parsed,
        "stdout_raw": out,
        "stderr": err,
        "duration_seconds": dur,
        "format": fmt,
        "timed_out": timed_out,
    }
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd mcp/dtm_sdk && python -m pytest tests/test_runner.py -q`
Expected: PASS (8 tests).

- [ ] **Step 6: Commit**

```bash
git add mcp/dtm_sdk/runner.py mcp/dtm_sdk/tests/test_runner.py mcp/dtm_sdk/tests/fake_util.py
git commit -m "feat(dtmsdk): argv-list runner with timeout + json/yaml/text parse"
```

---

### Task 6: `dtm_sdk_mcp_server.py` — the 9 FastMCP tools + confirm-token store

**Files:**
- Create: `mcp/dtm_sdk/dtm_sdk_mcp_server.py`
- Create: `mcp/dtm_sdk/requirements.txt`
- Test: `mcp/dtm_sdk/tests/test_server.py`

**Interfaces:**
- Consumes: all of `config`, `policy`, `runner`, `datatypes`, `howto`.
- Produces (importable for tests, not just MCP-registered):
  - `is_admin() -> bool`
  - `dellhub_state() -> str` — `"running"|"stopped"|"absent"|"unknown"`.
  - `_dispatch(util, command, args, confirm_token) -> dict` — the shared body behind the five `dtm_run_*` tools: validate → classify → (issue-preview | verify-token) → run. **This is what the test drives.**
  - the FastMCP tools: `dtm_datatypes`, `dtm_datatype`, `dtm_help`, `dtm_health`, and `dtm_run_dtmutil/_instrumentation/_analytics/_transmission/_platinum`.
- The confirm-token store is an in-process dict `{token: (util, command, args, issued_at)}`; `_dispatch` records on preview and consumes (pops) on successful verify (single-use).

- [ ] **Step 1: Create `requirements.txt`**

```
mcp>=1.2
pyyaml>=6.0
pytest>=8.0
```

- [ ] **Step 2: Write the failing test** (drives `_dispatch` with a monkeypatched runner — no real util)

```python
# mcp/dtm_sdk/tests/test_server.py
import time
import dtm_sdk_mcp_server as srv


def _fake_run(*a, **k):
    return {"ok": True, "exit_code": 0, "command_line": "x", "parsed": {"ran": True},
            "stdout_raw": "", "stderr": "", "duration_seconds": 0.1, "format": "json", "timed_out": False}


def setup_function(_):
    srv._TOKENS.clear()


def _patch(monkeypatch, exe="FAKE.exe"):
    # _dispatch checks is_admin() before running; the test host may not be elevated, so force it.
    monkeypatch.setattr(srv, "is_admin", lambda: True)
    monkeypatch.setattr(srv, "_exe_for", lambda util: exe)
    monkeypatch.setattr(srv.runner, "run", _fake_run)


def test_safe_command_runs_without_token(monkeypatch):
    _patch(monkeypatch)
    r = srv._dispatch("instrumentation", "metadata", [], "")
    assert r["ok"] is True
    assert r["parsed"]["ran"] is True


def test_dangerous_command_requires_confirmation(monkeypatch):
    _patch(monkeypatch)
    r = srv._dispatch("transmission", "collect-transmit", ["--datatype-name", "X"], "")
    assert r["requires_confirmation"] is True
    assert r["category"] == "egress"
    assert r["confirm_token"]
    assert "collect-transmit" in r["command_line"]


def test_confirmation_token_executes(monkeypatch):
    _patch(monkeypatch)
    prev = srv._dispatch("transmission", "collect-transmit", ["--datatype-name", "X"], "")
    tok = prev["confirm_token"]
    r = srv._dispatch("transmission", "collect-transmit", ["--datatype-name", "X"], tok)
    assert r["ok"] is True


def test_token_for_other_command_rejected(monkeypatch):
    _patch(monkeypatch)
    prev = srv._dispatch("transmission", "collect-transmit", ["--datatype-name", "X"], "")
    tok = prev["confirm_token"]
    # reuse the token for a DIFFERENT command -> must not execute
    r = srv._dispatch("transmission", "cancel", [], tok)
    assert r.get("requires_confirmation") is True


def test_token_single_use(monkeypatch):
    _patch(monkeypatch)
    prev = srv._dispatch("transmission", "collect-transmit", ["--datatype-name", "X"], "")
    tok = prev["confirm_token"]
    assert srv._dispatch("transmission", "collect-transmit", ["--datatype-name", "X"], tok)["ok"]
    second = srv._dispatch("transmission", "collect-transmit", ["--datatype-name", "X"], tok)
    assert second.get("requires_confirmation") is True  # consumed


def test_bad_command_string_rejected(monkeypatch):
    _patch(monkeypatch)
    r = srv._dispatch("transmission", "cancel; whoami", [], "")
    assert "error" in r


def test_missing_exe_names_the_key(monkeypatch):
    _patch(monkeypatch, exe=None)
    r = srv._dispatch("instrumentation", "metadata", [], "")
    assert "error" in r and "instrumentation" in r["error"]


def test_health_shape(monkeypatch):
    h = srv.dtm_health()
    for k in ("is_admin", "dell_techhub", "executables", "datatype_tables", "howto"):
        assert k in h
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd mcp/dtm_sdk && python -m pytest tests/test_server.py -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'dtm_sdk_mcp_server'`).

- [ ] **Step 4: Write `dtm_sdk_mcp_server.py`**

```python
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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd mcp/dtm_sdk && python -m pytest tests/test_server.py -q`
Expected: PASS (8 tests). Note: `test_health_shape` calls the real `dellhub_state()` / `is_admin()`, which is fine — they return strings/bools without needing admin.

- [ ] **Step 6: Run the whole always-on suite**

Run: `cd mcp/dtm_sdk && python -m pytest tests/ -q --ignore=tests/test_live.py`
Expected: PASS (policy 11 + config 6 + datatypes 5 + howto 3 + runner 8 + server 8 = 41).

- [ ] **Step 7: Commit**

```bash
git add mcp/dtm_sdk/dtm_sdk_mcp_server.py mcp/dtm_sdk/requirements.txt mcp/dtm_sdk/tests/test_server.py
git commit -m "feat(dtmsdk): FastMCP server, 9 tools, argv-bound confirm-token dispatch"
```

---

### Task 7: PowerShell scripts + `setup_mcp_servers.ps1` registration

**Files:**
- Create: `mcp/dtm_sdk/start_dtm_sdk_mcp.ps1`
- Create: `mcp/dtm_sdk/install_task.ps1`
- Create: `mcp/dtm_sdk/uninstall_task.ps1`
- Modify: `setup_mcp_servers.ps1` (add the `dtmsdk` entry to `$MCPS`)

**Interfaces:**
- Consumes: nothing (parity with the `windows_*` scripts).
- Produces: a startable server + a `DtmSdk-MCP` scheduled task + one-click registration.

- [ ] **Step 1: Write `start_dtm_sdk_mcp.ps1`**

```powershell
# Starts the DTM SDK MCP server elevated. Run as Administrator.
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$id = [Security.Principal.WindowsIdentity]::GetCurrent()
$admin = (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)
if (-not $admin) {
  Write-Host "[X] Must run elevated. The DTP sample utilities require Administrator." -ForegroundColor Red
  exit 1
}
$py = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $py) { Write-Host "[X] Python 3 not found on PATH." -ForegroundColor Red; exit 1 }
$env:PYTHONIOENCODING = "utf-8"
Write-Host "[*] Starting DTM SDK MCP on http://127.0.0.1:8789/mcp  (python: $py)" -ForegroundColor Cyan
& $py (Join-Path $here "dtm_sdk_mcp_server.py")
```

- [ ] **Step 2: Write `install_task.ps1`**

```powershell
# Registers a Scheduled Task that runs the DTM SDK MCP server elevated at logon. Run as Administrator.
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$id = [Security.Principal.WindowsIdentity]::GetCurrent()
$admin = (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)
if (-not $admin) { Write-Host "[X] Run this elevated (Administrator)." -ForegroundColor Red; exit 1 }

$py = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $py) { Write-Host "[X] Python 3 not found on PATH." -ForegroundColor Red; exit 1 }
$server = Join-Path $here "dtm_sdk_mcp_server.py"
$action = New-ScheduledTaskAction -Execute $py -Argument "`"$server`"" -WorkingDirectory $here
$trigger = New-ScheduledTaskTrigger -AtLogOn
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -RunLevel Highest -LogonType Interactive
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
Register-ScheduledTask -TaskName "DtmSdk-MCP" -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
Write-Host "[OK] Registered scheduled task 'DtmSdk-MCP' (elevated, at logon)." -ForegroundColor Green
Write-Host "     Start now: Start-ScheduledTask -TaskName DtmSdk-MCP" -ForegroundColor Cyan
Write-Host "     Remove:    .\uninstall_task.ps1" -ForegroundColor Cyan
```

- [ ] **Step 3: Write `uninstall_task.ps1`**

```powershell
# Removes the DTM SDK MCP scheduled task.
$ErrorActionPreference = "SilentlyContinue"
Unregister-ScheduledTask -TaskName "DtmSdk-MCP" -Confirm:$false
Write-Host "[OK] Removed scheduled task 'DtmSdk-MCP' (if it existed)." -ForegroundColor Green
```

- [ ] **Step 4: Register in `setup_mcp_servers.ps1`**

Add this entry to the `$MCPS` array (after the `winupdate` entry, before the closing `)`):

```powershell
  @{ name="dtmsdk";  dir="dtm_sdk";  port=8789; task="DtmSdk-MCP";
     desc="DTM Sample/SDK utilities (DTP client SDK CLI wrappers: instrumentation/analytics/transmission/DTM/platinum) via local elevated MCP server (127.0.0.1:8789). NOT read-only -- can transmit telemetry + change DTP config; gated by per-command confirmation." }
```

- [ ] **Step 5: Parse-check the PowerShell**

Run:
```powershell
powershell -NoProfile -Command "foreach($f in 'mcp/dtm_sdk/start_dtm_sdk_mcp.ps1','mcp/dtm_sdk/install_task.ps1','mcp/dtm_sdk/uninstall_task.ps1','setup_mcp_servers.ps1'){$e=$null;[void][System.Management.Automation.Language.Parser]::ParseFile((Resolve-Path $f),[ref]$null,[ref]$e); if($e){\"$f FAIL\"}else{\"$f ok\"}}"
```
Expected: all four `ok`.

- [ ] **Step 6: Verify the dynamic dep union now includes pyyaml**

Run:
```powershell
powershell -NoProfile -Command "$d=@(); Get-ChildItem mcp -Directory | ForEach-Object { $r=Join-Path $_.FullName 'requirements.txt'; if(Test-Path $r){ Get-Content $r | ForEach-Object { $l=$_.Trim(); if($l -and -not $l.StartsWith('#')){$d+=$l} } } }; ($d | Sort-Object -Unique) -join ', '"
```
Expected: the union now contains `pyyaml>=6.0` (proving Task 6's requirements.txt is picked up by the installer).

- [ ] **Step 7: Commit**

```bash
git add mcp/dtm_sdk/start_dtm_sdk_mcp.ps1 mcp/dtm_sdk/install_task.ps1 mcp/dtm_sdk/uninstall_task.ps1 setup_mcp_servers.ps1
git commit -m "feat(dtmsdk): PS start/install/uninstall + one-click registration"
```

---

### Task 8: Gated live tests — instrumentation + analytics only

**Files:**
- Create: `mcp/dtm_sdk/tests/test_live.py`

**Interfaces:**
- Consumes: the real server module + real utils. Skipped unless elevated AND Dell TechHub running AND `DTM_SDK_LIVE_TESTS=1`.

- [ ] **Step 1: Write the live test (safe/local commands only)**

```python
# mcp/dtm_sdk/tests/test_live.py
"""Phase-1 live tests: prove the plumbing against the REAL instrumentation + analytics utils.

Gated hard -- these execute real utils, so they run ONLY when elevated AND Dell TechHub is running
AND DTM_SDK_LIVE_TESTS=1. They exercise safe (read-only) commands and, for the confirm path, LOCAL
actions only. They never transmit, unregister, or change DTP config. dtmutil/transmission/platinum
live tests are deferred to phase 2 (see TODO_PHASE2.md), and upload APIs are excluded there.
"""
import os
import unittest

import dtm_sdk_mcp_server as srv

_LIVE = os.environ.get("DTM_SDK_LIVE_TESTS") == "1"


def _prereqs():
    if not _LIVE:
        return "DTM_SDK_LIVE_TESTS != 1"
    if not srv.is_admin():
        return "not elevated"
    if srv.dellhub_state() != "running":
        return "Dell TechHub not running (%s)" % srv.dellhub_state()
    if not srv._exe_for("instrumentation") or not srv._exe_for("analytics"):
        return "instrumentation/analytics exe not found"
    return None


@unittest.skipUnless(_prereqs() is None, _prereqs() or "prereqs unmet")
class Live(unittest.TestCase):
    def test_instrumentation_metadata_runs(self):
        r = srv._dispatch("instrumentation", "metadata", [], "")
        self.assertIn(r["format"], ("json", "yaml", "text"))
        self.assertEqual(r["timed_out"], False)

    def test_analytics_metadata_runs(self):
        r = srv._dispatch("analytics", "metadata", [], "")
        self.assertEqual(r["timed_out"], False)

    def test_instrumentation_collect_via_confirmation(self):
        # collect is a LOCAL action (no egress). Prove the confirm flow end-to-end on a real util.
        prev = srv._dispatch("instrumentation", "collect",
                             ["--datatype-name", "BatteryStaticData"], "")
        self.assertTrue(prev["requires_confirmation"])
        r = srv._dispatch("instrumentation", "collect",
                          ["--datatype-name", "BatteryStaticData"], prev["confirm_token"])
        self.assertIn("exit_code", r)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Verify it SKIPS cleanly when prereqs are absent**

Run: `cd mcp/dtm_sdk && python -m pytest tests/test_live.py -v`
Expected: all tests SKIPPED with a stated reason (unless you happen to be elevated with TechHub up and the env var set).

- [ ] **Step 3: Commit**

```bash
git add mcp/dtm_sdk/tests/test_live.py
git commit -m "test(dtmsdk): gated live tests for instrumentation + analytics"
```

---

### Task 9: Docs — README, DESIGN, TODO_PHASE2, and cross-links

**Files:**
- Create: `mcp/dtm_sdk/README.md`
- Create: `mcp/dtm_sdk/DESIGN.md`
- Create: `mcp/dtm_sdk/TODO_PHASE2.md`
- Modify: `mcp/README.md` (add `dtmsdk` to the suite table with its NOT-read-only caveat)

- [ ] **Step 1: Write `mcp/dtm_sdk/README.md`**

Content requirements (write it out fully in the file):
- One-line purpose; the 8789 port; the `dtmsdk` id and why not `dtm`.
- **A prominent warning** that this MCP is NOT read-only (transmits/mutates), unlike the diagnostic twelve.
- The 9 tools with one line each.
- The confirmation flow with a worked example (preview → confirm_token → execute).
- Config: the `${}`/env/auto-probe resolution, and "redeploy = change `samples_root`".
- Admin + Dell TechHub prerequisites; `dtm_health` first.
- Install: `.\install_task.ps1` or the one-click `setup_mcp_servers.ps1`; uninstall.
- A pointer to `dtm_help` for per-command options.

- [ ] **Step 2: Write `mcp/dtm_sdk/DESIGN.md`**

Content requirements:
- Module map (config/policy/runner/datatypes/howto/server), one responsibility each.
- Why classification is by SDK method not command name (the two worked examples).
- Why `retrieve-file` is gated.
- The token-binding argument (A-token cannot run B).
- The 24/41 split table.

- [ ] **Step 3: Write `mcp/dtm_sdk/TODO_PHASE2.md`**

```markdown
# dtmsdk — Phase 2 TODO

Phase 1 delivered the server, all 5 runners, lookup tools, config, policy, and tests. Live tests
this phase covered **instrumentation + analytics only** (safe/local commands).

## Deferred to Phase 2

- [ ] Live tests for **dtmutil** — workflow status/history/retrieve, bundle queries, validate-app-configuration.
- [ ] Live tests for **transmission** — `transmission-status`, and `collect-transmit`/`retrieve-transmit`
      through the confirm flow **only if** a safe test target exists.
- [ ] Live tests for **platinum** — `transmission-status`, `platinum-heartbeat`/`platinum-ping` through
      the confirm flow.

## Excluded from Phase 2 testing (do NOT automate)

- **All upload APIs:** `transmission file-upload`, `platinum platinum-upload`.
- Any egress command that sends real telemetry to Dell, and any `unregister` / config-mutating command.

Rationale: an automated test must not transmit telemetry, upload files, unregister the application, or
change DTP configuration on the user's machine.
```

- [ ] **Step 4: Add `dtmsdk` to `mcp/README.md`**

In the Windows-suite section, add a short subsection after the diagnostic table:

```markdown
### DTM Sample/SDK Util MCP (`dtm_sdk/`, 127.0.0.1:8789) — NOT read-only

`dtmsdk` wraps the five DTP sample utilities (65 commands) plus the datatype tables and HowTo. **Unlike
the twelve diagnostic MCPs above, it is not read-only** — some commands transmit telemetry to Dell or
change DTP configuration, so every command outside a per-util safe allowlist requires a per-command
confirmation token. Requires Administrator and a running Dell TechHub service. Paths live in
`dtm_sdk/config.json` (one-line redeploy via `samples_root`). See [`dtm_sdk/README.md`](dtm_sdk/README.md).
```

- [ ] **Step 5: Verify markdown links resolve**

Run: `cd mcp/dtm_sdk && python -c "import os; [print(p, os.path.exists(p)) for p in ['README.md','DESIGN.md','TODO_PHASE2.md']]"`
Expected: all three `True`.

- [ ] **Step 6: Commit**

```bash
git add mcp/dtm_sdk/README.md mcp/dtm_sdk/DESIGN.md mcp/dtm_sdk/TODO_PHASE2.md mcp/README.md
git commit -m "docs(dtmsdk): README, DESIGN, phase-2 TODO, suite cross-link"
```

---

### Task 10: Full-suite verification + backlog update

**Files:**
- Modify: `docs/HARDENING_BACKLOG.md` (note dtmsdk raises the MCP-auth item's severity)

- [ ] **Step 1: Run the whole always-on dtmsdk suite**

Run: `cd mcp/dtm_sdk && python -m pytest tests/ -q --ignore=tests/test_live.py`
Expected: PASS (41 tests).

- [ ] **Step 2: Confirm the live tests skip cleanly**

Run: `cd mcp/dtm_sdk && python -m pytest tests/test_live.py -q`
Expected: 3 skipped.

- [ ] **Step 3: Confirm the server imports and all 9 tool callables exist**

Run:
```bash
cd mcp/dtm_sdk && python -c "import dtm_sdk_mcp_server as s; names=['dtm_datatypes','dtm_datatype','dtm_help','dtm_health','dtm_run_dtmutil','dtm_run_instrumentation','dtm_run_analytics','dtm_run_transmission','dtm_run_platinum']; print(all(hasattr(s,n) for n in names), len(names))"
```
Expected: `True 9`. (`@mcp.tool()` returns the original function, so each tool is still importable and directly callable — which is also why `test_server.py` can call `srv.dtm_health()` directly.)

- [ ] **Step 4: Note the raised auth severity in the backlog**

Under `## Open`, update the MCP-authentication item to note that `dtmsdk` (state-changing, can egress) raises its severity from information-disclosure to potential unauthorised DTP control.

- [ ] **Step 5: Commit**

```bash
git add docs/HARDENING_BACKLOG.md
git commit -m "docs(backlog): dtmsdk raises the MCP-auth item's severity"
```

---

## Notes for the implementer

- Work from the repo root `C:\Users\a9027\source\Agentic\HarnessAgent`. The MCP modules import each other by bare name (`import policy`), which works because they live in the same dir and `conftest.py` puts that dir on `sys.path` for tests; at runtime FastMCP is launched with the dir as cwd (the start script does `Join-Path $here`).
- **Never run a real util from a test** except through `tests/test_live.py`, which is gated. The fake-util fixture is how the runner and dispatch are proven.
- If a live test can run in your environment, prefer the safe/local commands listed — do not add egress/upload/unregister commands to the automated suite.
- The confirm-token store is in-process and per-server-process; that is intended (a restart clears pending previews).
