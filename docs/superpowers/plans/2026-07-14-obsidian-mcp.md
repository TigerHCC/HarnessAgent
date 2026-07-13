# Obsidian MCP (`windows_obsidian`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the `obsidian` MCP: filesystem access to an Obsidian vault (read/search/list/links/tags/frontmatter, plus gated create/update), enforcing vault path confinement and single-use content-bound confirm tokens.

**Architecture:** A thin FastMCP server delegating to four focused modules — `tokens` (write-gate, pure), `config` (path resolution), `vault` (path confinement + file primitives), `index` (markdown parsing + queries). The two security-critical modules (`vault` confinement, `tokens`) are pure/near-pure and exhaustively unit-tested. All tests run against a fake temp vault; the real vault is never touched except by one opt-in live read test.

**Tech Stack:** Python 3.13, `mcp` (FastMCP, streamable-http), `pyyaml`, `pytest`. PowerShell 5.1 for the scheduled-task scripts. Follows the `mcp/windows_*` conventions.

## Global Constraints

- Directory `mcp/windows_obsidian/`; extension id **`obsidian`**; server name `FastMCP("obsidian", ...)`.
- Bind **`127.0.0.1:8790`**, transport `streamable-http`, endpoint `/mcp`.
- Scheduled task **`Obsidian-MCP`**, **`RunLevel Limited`** (NOT Highest), `-AtLogOn`, current user. `start_obsidian_mcp.ps1` has **no elevation check**.
- **Path confinement:** every tool path funnels through `vault.resolve()`, which rejects absolute paths, `..`, non-`.md`, and `.obsidian/`, and confirms the realpath stays inside the vault. Nothing outside the vault's `.md` files is ever read or written.
- **Write gate:** `create` and `update` require a single-use confirm token = `sha256(json([op,path,mode,content]))[:16]`, TTL `confirm_ttl_seconds` (default 120). No delete tool. `overwrite` is gated like any write.
- Only `*.md` is scanned; `.obsidian/`, attachments, images, and all non-`.md` files are ignored everywhere.
- Config is `config.json` with `${}` expansion + env override (`OBSIDIAN_MCP_<KEY>`, plus `OBSIDIAN_VAULT` alias for the vault path).
- No test touches the real vault except `tests/test_live.py`, gated behind `OBSIDIAN_MCP_LIVE_TESTS=1` and read-only.
- All file reads/writes use `encoding="utf-8"`; writes are atomic (temp + `os.replace`) with `newline=""`.

---

### Task 1: `tokens.py` — single-use content-bound confirm tokens (pure, security-critical)

**Files:**
- Create: `mcp/windows_obsidian/tokens.py`
- Create: `mcp/windows_obsidian/conftest.py`
- Test: `mcp/windows_obsidian/tests/test_tokens.py`

**Interfaces:**
- Produces:
  - `TOKEN_TTL_SECONDS: int` (= 120)
  - `make_token(op, path, mode, content) -> str`
  - `verify_token(op, path, mode, content, token, *, now, issued_at, ttl=TOKEN_TTL_SECONDS) -> bool`

- [ ] **Step 1: Create `conftest.py`**

```python
"""Make the windows_obsidian modules importable from tests/ regardless of pytest invocation."""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
```

- [ ] **Step 2: Write the failing test**

```python
# mcp/windows_obsidian/tests/test_tokens.py
import tokens


def test_roundtrip():
    t = tokens.make_token("create", "a/b.md", "", "hello")
    assert tokens.verify_token("create", "a/b.md", "", "hello", t, now=100.0, issued_at=100.0)


def test_bound_to_each_field():
    t = tokens.make_token("update", "a.md", "append", "X")
    assert not tokens.verify_token("create", "a.md", "append", "X", t, now=1, issued_at=1)   # op
    assert not tokens.verify_token("update", "b.md", "append", "X", t, now=1, issued_at=1)   # path
    assert not tokens.verify_token("update", "a.md", "overwrite", "X", t, now=1, issued_at=1)  # mode
    assert not tokens.verify_token("update", "a.md", "append", "Y", t, now=1, issued_at=1)   # content


def test_expiry():
    t = tokens.make_token("create", "a.md", "", "X")
    assert tokens.verify_token("create", "a.md", "", "X", t, now=220.0, issued_at=100.0)      # exactly 120
    assert not tokens.verify_token("create", "a.md", "", "X", t, now=221.0, issued_at=100.0)  # 121 > ttl


def test_ttl_override():
    t = tokens.make_token("create", "a.md", "", "X")
    assert not tokens.verify_token("create", "a.md", "", "X", t, now=131.0, issued_at=100.0, ttl=30)


def test_empty_token_rejected():
    assert not tokens.verify_token("create", "a.md", "", "X", "", now=1, issued_at=1)
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd mcp/windows_obsidian && python -m pytest tests/test_tokens.py -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'tokens'`).

- [ ] **Step 4: Write `tokens.py`**

```python
"""Confirm-token logic for the obsidian write tools (create/update). Pure: no I/O.

A write is gated -- the first call returns a preview + a token bound to a hash of the exact
op+path+mode+content; the caller must call again with that token. Single-use (the server pops it),
TTL-limited. A token for one write cannot authorize a different one.
"""
import hashlib
import json

TOKEN_TTL_SECONDS = 120


def _digest(op, path, mode, content):
    payload = json.dumps([op, path, mode, content], separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def make_token(op, path, mode, content):
    return _digest(op, path, mode, content)


def verify_token(op, path, mode, content, token, *, now, issued_at, ttl=TOKEN_TTL_SECONDS):
    if not token or token != _digest(op, path, mode, content):
        return False
    return (now - issued_at) <= ttl
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd mcp/windows_obsidian && python -m pytest tests/test_tokens.py -q`
Expected: PASS (5 tests).

- [ ] **Step 6: Commit**

```bash
git add mcp/windows_obsidian/tokens.py mcp/windows_obsidian/conftest.py mcp/windows_obsidian/tests/test_tokens.py
git commit -m "feat(obsidian): single-use content-bound confirm tokens"
```

---

### Task 2: `config.py` + `config.json` — vault path resolution

**Files:**
- Create: `mcp/windows_obsidian/config.py`
- Create: `mcp/windows_obsidian/config.json`
- Test: `mcp/windows_obsidian/tests/test_config.py`

**Interfaces:**
- Produces:
  - `env_key(name) -> str` (`"vault_path"` → `"OBSIDIAN_MCP_VAULT_PATH"`)
  - `load(path=None) -> dict` with keys `vault_path` (str), `max_search_results` (int), `max_file_bytes` (int), `confirm_ttl_seconds` (int), and `_resolved` (`{"vault_path": {raw, resolved, exists}}`).

- [ ] **Step 1: Create `config.json`**

```json
{
  "vault_path": "C:/Users/a9027/source/Agentic/doc/DTMKnowledge/Telemetry",
  "max_search_results": 50,
  "max_file_bytes": 1048576,
  "confirm_ttl_seconds": 120
}
```

- [ ] **Step 2: Write the failing test**

```python
# mcp/windows_obsidian/tests/test_config.py
import json
import config


def _write(tmp_path, obj):
    p = tmp_path / "config.json"
    p.write_text(json.dumps(obj), encoding="utf-8")
    return str(p)


def _base(**kw):
    d = {"vault_path": "V", "max_search_results": 50, "max_file_bytes": 1048576, "confirm_ttl_seconds": 120}
    d.update(kw)
    return d


def test_defaults_passthrough(tmp_path):
    cfg = config.load(_write(tmp_path, _base()))
    assert cfg["vault_path"] == "V"
    assert cfg["max_search_results"] == 50
    assert cfg["confirm_ttl_seconds"] == 120


def test_var_expansion(tmp_path):
    cfg = config.load(_write(tmp_path, _base(vault_path="${repo_root}/vault")))
    assert cfg["vault_path"].endswith("/vault")
    assert "${" not in cfg["vault_path"]


def test_vault_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("OBSIDIAN_MCP_VAULT_PATH", "OVERRIDE")
    assert config.load(_write(tmp_path, _base()))["vault_path"] == "OVERRIDE"


def test_vault_alias_override(tmp_path, monkeypatch):
    monkeypatch.setenv("OBSIDIAN_VAULT", "ALIASED")
    assert config.load(_write(tmp_path, _base()))["vault_path"] == "ALIASED"


def test_int_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("OBSIDIAN_MCP_MAX_FILE_BYTES", "42")
    assert config.load(_write(tmp_path, _base()))["max_file_bytes"] == 42


def test_resolved_reports_existence(tmp_path):
    cfg = config.load(_write(tmp_path, _base(vault_path=str(tmp_path))))
    assert cfg["_resolved"]["vault_path"]["exists"] is True
    cfg2 = config.load(_write(tmp_path, _base(vault_path=str(tmp_path / "nope"))))
    assert cfg2["_resolved"]["vault_path"]["exists"] is False


def test_env_key():
    assert config.env_key("vault_path") == "OBSIDIAN_MCP_VAULT_PATH"
    assert config.env_key("max_file_bytes") == "OBSIDIAN_MCP_MAX_FILE_BYTES"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd mcp/windows_obsidian && python -m pytest tests/test_config.py -q`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 4: Write `config.py`**

```python
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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd mcp/windows_obsidian && python -m pytest tests/test_config.py -q`
Expected: PASS (7 tests).

- [ ] **Step 6: Commit**

```bash
git add mcp/windows_obsidian/config.py mcp/windows_obsidian/config.json mcp/windows_obsidian/tests/test_config.py
git commit -m "feat(obsidian): config with var expansion + env override"
```

---

### Task 3: `vault.py` — path confinement + file primitives (security-critical)

**Files:**
- Create: `mcp/windows_obsidian/vault.py`
- Test: `mcp/windows_obsidian/tests/test_vault.py`

**Interfaces:**
- Produces:
  - `VaultError(Exception)`
  - `resolve(vault_root, rel) -> str` (absolute path) or raises `VaultError`
  - `note_exists(vault_root, rel) -> bool`
  - `read_note(vault_root, rel) -> str`
  - `write_note(vault_root, rel, content, *, must_be_new=False) -> None` (atomic; raises `VaultError` if `must_be_new` and it exists)
  - `walk_md(vault_root) -> list[str]` (vault-relative posix paths of every `.md`, skipping `.obsidian/`)

- [ ] **Step 1: Write the failing test**

```python
# mcp/windows_obsidian/tests/test_vault.py
import os
import pytest
import vault


@pytest.fixture
def v(tmp_path):
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "a.md").write_text("hi", encoding="utf-8")
    (tmp_path / ".obsidian").mkdir()
    (tmp_path / ".obsidian" / "app.json").write_text("{}", encoding="utf-8")
    (tmp_path / "img.png").write_bytes(b"\x89PNG")
    return str(tmp_path)


def test_resolve_accepts_in_vault_md(v):
    p = vault.resolve(v, "notes/a.md")
    assert p.endswith("a.md") and os.path.isfile(p)


@pytest.mark.parametrize("bad", [
    "../evil.md", "notes/../../evil.md", "/etc/passwd.md", "C:/Windows/x.md",
    "notes/a.txt", ".obsidian/app.md", "", "..\\..\\x.md",
])
def test_resolve_rejects(v, bad):
    with pytest.raises(vault.VaultError):
        vault.resolve(v, bad)


def test_note_exists(v):
    assert vault.note_exists(v, "notes/a.md")
    assert not vault.note_exists(v, "notes/missing.md")


def test_write_note_atomic_and_read(v):
    vault.write_note(v, "notes/new.md", "content")
    assert vault.read_note(v, "notes/new.md") == "content"


def test_write_must_be_new_rejects_existing(v):
    with pytest.raises(vault.VaultError):
        vault.write_note(v, "notes/a.md", "x", must_be_new=True)


def test_write_creates_subdirs(v):
    vault.write_note(v, "sub/deep/n.md", "x")
    assert vault.note_exists(v, "sub/deep/n.md")


def test_walk_md_only_md_skips_obsidian(v):
    found = vault.walk_md(v)
    assert "notes/a.md" in found
    assert not any(".obsidian" in f for f in found)
    assert not any(f.endswith(".png") for f in found)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd mcp/windows_obsidian && python -m pytest tests/test_vault.py -q`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Write `vault.py`**

```python
"""Vault path confinement + file primitives. Every path from a tool funnels through resolve(), which
guarantees a .md file INSIDE the vault: no absolute paths, no '..', no .obsidian/, and (via realpath)
no symlink escape. Nothing else is ever read or written.
"""
import os


class VaultError(Exception):
    pass


def resolve(vault_root, rel):
    if not rel or not isinstance(rel, str):
        raise VaultError("empty path")
    r = rel.replace("\\", "/")
    if r.startswith("/") or (len(r) > 1 and r[1] == ":"):
        raise VaultError("absolute paths are not allowed: %r" % rel)
    parts = [p for p in r.split("/") if p and p != "."]
    if ".." in parts:
        raise VaultError("path traversal ('..') is not allowed: %r" % rel)
    if not r.lower().endswith(".md"):
        raise VaultError("only .md files are allowed: %r" % rel)
    if parts and parts[0] == ".obsidian":
        raise VaultError(".obsidian/ is off-limits: %r" % rel)
    root_real = os.path.realpath(vault_root)
    abs_path = os.path.realpath(os.path.join(root_real, *parts))
    try:
        inside = os.path.commonpath([root_real, abs_path]) == root_real
    except ValueError:              # different drives, etc.
        inside = False
    if not inside:
        raise VaultError("path escapes the vault: %r" % rel)
    return abs_path


def note_exists(vault_root, rel):
    return os.path.isfile(resolve(vault_root, rel))


def read_note(vault_root, rel):
    with open(resolve(vault_root, rel), "r", encoding="utf-8") as f:
        return f.read()


def write_note(vault_root, rel, content, *, must_be_new=False):
    abs_path = resolve(vault_root, rel)
    if must_be_new and os.path.exists(abs_path):
        raise VaultError("note already exists: %r" % rel)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    tmp = abs_path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(content)
    os.replace(tmp, abs_path)


def walk_md(vault_root):
    root_real = os.path.realpath(vault_root)
    out = []
    for dirpath, dirnames, filenames in os.walk(root_real):
        dirnames[:] = [d for d in dirnames if d != ".obsidian"]
        for fn in filenames:
            if fn.lower().endswith(".md"):
                rel = os.path.relpath(os.path.join(dirpath, fn), root_real).replace("\\", "/")
                out.append(rel)
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd mcp/windows_obsidian && python -m pytest tests/test_vault.py -q`
Expected: PASS (test_resolve_rejects is parametrized ×8, so ~14 test items).

- [ ] **Step 5: Commit**

```bash
git add mcp/windows_obsidian/vault.py mcp/windows_obsidian/tests/test_vault.py
git commit -m "feat(obsidian): vault path confinement + atomic file primitives"
```

---

### Task 4: `index.py` — markdown parsing + queries

**Files:**
- Create: `mcp/windows_obsidian/index.py`
- Test: `mcp/windows_obsidian/tests/test_index.py`

**Interfaces:**
- Consumes: `vault.walk_md`, `vault.read_note`, `vault.note_exists`.
- Produces (pure parse, on text):
  - `parse_frontmatter(text) -> (dict, body_str)`
  - `parse_headings(text) -> list[{level, text}]`
  - `parse_wikilinks(text) -> list[{target, heading, alias}]`
  - `parse_tags(text, frontmatter=None) -> list[str]`
  - `replace_section(text, heading, new_content) -> str` (raises `KeyError` if heading absent)
- Produces (queries, take `vault_root`):
  - `search(vault_root, query, in_content=True, in_name=True, folder="", max_results=50, max_file_bytes=1048576) -> list[{path, name, snippet}]`
  - `backlinks(vault_root, rel) -> list[str]`
  - `outlinks(vault_root, rel) -> list[{target, resolved}]`
  - `find(vault_root, key, value="") -> list[str]`

- [ ] **Step 1: Write the failing test**

```python
# mcp/windows_obsidian/tests/test_index.py
import os
import pytest
import index


@pytest.fixture
def v(tmp_path):
    def w(rel, text):
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    w("Alpha.md", "---\ntags: [proj, x]\nstatus: open\n---\n# Alpha\nlinks to [[Beta]] and [[Beta#Sec|B]].\n#inline\n")
    w("sub/Beta.md", "# Beta\n## Sec\nbody of sec\n## Other\nother body\nrefers to [[Alpha]].\n")
    w("Gamma.md", "no frontmatter, mentions battery telemetry.\n")
    return str(tmp_path)


def test_parse_frontmatter():
    fm, body = index.parse_frontmatter("---\nstatus: open\ntags: [a, b]\n---\n# Title\nbody\n")
    assert fm["status"] == "open" and fm["tags"] == ["a", "b"]
    assert body.startswith("# Title")


def test_parse_frontmatter_none():
    fm, body = index.parse_frontmatter("# Title\nno fm\n")
    assert fm == {} and body.startswith("# Title")


def test_parse_headings():
    hs = index.parse_headings("# A\n## B\ntext\n### C\n")
    assert hs == [{"level": 1, "text": "A"}, {"level": 2, "text": "B"}, {"level": 3, "text": "C"}]


def test_parse_wikilinks():
    ls = index.parse_wikilinks("see [[Beta]], [[Beta#Sec|alias]], [[a/c]]")
    assert {"target": "Beta", "heading": None, "alias": None} in ls
    assert {"target": "Beta", "heading": "Sec", "alias": "alias"} in ls
    assert {"target": "a/c", "heading": None, "alias": None} in ls


def test_parse_tags_inline_and_frontmatter():
    fm = {"tags": ["fm1", "fm2"]}
    tags = index.parse_tags("body #inline and #two/nested here", frontmatter=fm)
    assert set(tags) == {"inline", "two/nested", "fm1", "fm2"}


def test_parse_tags_ignores_headings_and_csharp():
    assert index.parse_tags("# Heading\nC# is a language\n") == []


def test_replace_section():
    text = "# T\n## Sec\nold body\n## Next\nkeep\n"
    out = index.replace_section(text, "Sec", "new body\n")
    assert "new body" in out and "old body" not in out and "keep" in out


def test_replace_section_missing_raises():
    with pytest.raises(KeyError):
        index.replace_section("# T\nbody\n", "Nope", "x")


def test_search_name_and_content(v):
    by_name = index.search(v, "beta", in_content=False)
    assert any(r["path"] == "sub/Beta.md" for r in by_name)
    by_content = index.search(v, "battery telemetry", in_name=False)
    assert any(r["path"] == "Gamma.md" for r in by_content)


def test_search_folder_filter(v):
    res = index.search(v, "beta", in_content=False, folder="sub")
    assert all(r["path"].startswith("sub/") for r in res)


def test_search_skips_large_files(v):
    res = index.search(v, "battery", in_name=False, max_file_bytes=5)   # Gamma is bigger than 5 bytes
    assert not any(r["path"] == "Gamma.md" for r in res)


def test_backlinks(v):
    # Beta links to Alpha, Alpha links to Beta -> each is a backlink of the other
    assert "sub/Beta.md" in index.backlinks(v, "Alpha.md")
    assert "Alpha.md" in index.backlinks(v, "sub/Beta.md")


def test_outlinks_resolution(v):
    links = index.outlinks(v, "Alpha.md")
    targets = {l["target"]: l["resolved"] for l in links}
    assert targets.get("Beta") == "sub/Beta.md"


def test_find_by_frontmatter(v):
    assert "Alpha.md" in index.find(v, "status", "open")
    assert index.find(v, "status", "closed") == []
    assert "Alpha.md" in index.find(v, "status")     # key present, any value
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd mcp/windows_obsidian && python -m pytest tests/test_index.py -q`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Write `index.py`**

```python
"""Markdown parsing (frontmatter/headings/wikilinks/tags) + vault queries (search/backlinks/outlinks/
find). Parse functions are pure (operate on text); query functions read the vault via vault.py.
"""
import os
import re

import vault

try:
    import yaml
except Exception:
    yaml = None

_FM_RE = re.compile(r"^---\r?\n(.*?)\r?\n---\r?\n", re.S)
_WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")
_TAG_RE = re.compile(r"(?:^|\s)#([A-Za-z0-9_][A-Za-z0-9_/-]*)")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$", re.M)


def parse_frontmatter(text):
    m = _FM_RE.match(text)
    if not m:
        return {}, text
    fm = {}
    if yaml is not None:
        try:
            loaded = yaml.safe_load(m.group(1))
            if isinstance(loaded, dict):
                fm = loaded
        except Exception:
            fm = {}
    return fm, text[m.end():]


def parse_headings(text):
    return [{"level": len(h[0]), "text": h[1].strip()} for h in _HEADING_RE.findall(text)]


def parse_wikilinks(text):
    out = []
    for raw in _WIKILINK_RE.findall(text):
        s, alias, heading = raw, None, None
        if "|" in s:
            s, alias = s.split("|", 1)
        if "#" in s:
            s, heading = s.split("#", 1)
        out.append({"target": s.strip(),
                    "heading": heading.strip() if heading else None,
                    "alias": alias.strip() if alias else None})
    return out


def parse_tags(text, frontmatter=None):
    tags = set(_TAG_RE.findall(text))
    ft = (frontmatter or {}).get("tags")
    if isinstance(ft, str):
        tags.update(t.strip() for t in re.split(r"[,\s]+", ft) if t.strip())
    elif isinstance(ft, list):
        tags.update(str(t).strip() for t in ft if str(t).strip())
    return sorted(tags)


def replace_section(text, heading, new_content):
    """Replace the body under the first `#{1,6} <heading>` line, up to the next heading of the same or
    higher level (or EOF). Keeps the heading line. Raises KeyError if the heading is not found."""
    lines = text.splitlines(keepends=True)
    start = None
    level = None
    for i, ln in enumerate(lines):
        m = re.match(r"^(#{1,6})\s+(.*?)\s*$", ln)
        if m and m.group(2).strip() == heading.strip():
            start = i
            level = len(m.group(1))
            break
    if start is None:
        raise KeyError(heading)
    end = len(lines)
    for j in range(start + 1, len(lines)):
        m = re.match(r"^(#{1,6})\s+", lines[j])
        if m and len(m.group(1)) <= level:
            end = j
            break
    body = new_content if new_content.endswith("\n") else new_content + "\n"
    return "".join(lines[:start + 1]) + body + "".join(lines[end:])


def _name_no_ext(rel):
    return rel.rsplit("/", 1)[-1][:-3]  # strip '.md'


def search(vault_root, query, in_content=True, in_name=True, folder="", max_results=50,
           max_file_bytes=1048576):
    q = (query or "").lower()
    folder = (folder or "").replace("\\", "/").strip("/")
    out = []
    for rel in vault.walk_md(vault_root):
        if folder and not (rel == folder or rel.startswith(folder + "/")):
            continue
        name = rel.rsplit("/", 1)[-1]
        snippet = None
        hit = in_name and q in name.lower()
        if not hit and in_content:
            abs_path = vault.resolve(vault_root, rel)
            try:
                if os.path.getsize(abs_path) <= max_file_bytes:
                    text = vault.read_note(vault_root, rel)
                    idx = text.lower().find(q)
                    if idx >= 0:
                        hit = True
                        s = max(0, idx - 40)
                        snippet = text[s:idx + len(q) + 40].replace("\n", " ")
            except OSError:
                pass
        if hit:
            out.append({"path": rel, "name": name, "snippet": snippet})
            if len(out) >= max_results:
                break
    return out


def backlinks(vault_root, rel):
    target_name = _name_no_ext(rel).lower()
    target_stem = rel[:-3].lower()
    out = []
    for other in vault.walk_md(vault_root):
        if other == rel:
            continue
        try:
            text = vault.read_note(vault_root, other)
        except OSError:
            continue
        for link in parse_wikilinks(text):
            t = link["target"].replace("\\", "/").lower()
            if t == target_name or t == target_stem or t.rsplit("/", 1)[-1] == target_name:
                out.append(other)
                break
    return out


def outlinks(vault_root, rel):
    text = vault.read_note(vault_root, rel)
    all_md = vault.walk_md(vault_root)
    by_name = {}
    for m in all_md:
        by_name.setdefault(_name_no_ext(m).lower(), m)
    out = []
    for link in parse_wikilinks(text):
        t = link["target"].replace("\\", "/")
        resolved = None
        cand = t if t.lower().endswith(".md") else t + ".md"
        if cand in all_md:
            resolved = cand
        else:
            resolved = by_name.get(t.rsplit("/", 1)[-1].lower())
        out.append({"target": link["target"], "resolved": resolved})
    return out


def find(vault_root, key, value=""):
    out = []
    for rel in vault.walk_md(vault_root):
        try:
            fm, _ = parse_frontmatter(vault.read_note(vault_root, rel))
        except OSError:
            continue
        if key in fm and (value == "" or str(fm[key]) == value or
                          (isinstance(fm[key], list) and value in [str(x) for x in fm[key]])):
            out.append(rel)
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd mcp/windows_obsidian && python -m pytest tests/test_index.py -q`
Expected: PASS (15 tests).

- [ ] **Step 5: Commit**

```bash
git add mcp/windows_obsidian/index.py mcp/windows_obsidian/tests/test_index.py
git commit -m "feat(obsidian): markdown parsing + search/backlinks/outlinks/find"
```

---

### Task 5: `obsidian_mcp_server.py` — 10 FastMCP tools + confirm store

**Files:**
- Create: `mcp/windows_obsidian/obsidian_mcp_server.py`
- Create: `mcp/windows_obsidian/requirements.txt`
- Test: `mcp/windows_obsidian/tests/test_server.py`

**Interfaces:**
- Consumes: all of `config`, `vault`, `index`, `tokens`.
- Produces (importable for tests):
  - `cfg() -> dict`, `_vault() -> str`
  - `_gated_write(op, path, mode, content, confirm_token) -> dict` — the shared body behind create/update.
  - the FastMCP tools: `obsidian_read`, `obsidian_search`, `obsidian_list`, `obsidian_tags`, `obsidian_backlinks`, `obsidian_links`, `obsidian_find`, `obsidian_create`, `obsidian_update`, `obsidian_health`.
- The confirm store is `_TOKENS = {token: (op, path, mode, content, issued_at)}`; consumed (popped) on a valid confirmed call.

- [ ] **Step 1: Create `requirements.txt`**

```
mcp>=1.2
pyyaml>=6.0
pytest>=8.0
```

- [ ] **Step 2: Write the failing test**

```python
# mcp/windows_obsidian/tests/test_server.py
import os
import pytest
import obsidian_mcp_server as srv


@pytest.fixture
def vault_cfg(tmp_path, monkeypatch):
    (tmp_path / "Note.md").write_text("---\nstatus: open\n---\n# Note\n[[Other]] #tag\n", encoding="utf-8")
    (tmp_path / "Other.md").write_text("# Other\nbody\n", encoding="utf-8")
    cfg = {"vault_path": str(tmp_path), "max_search_results": 50, "max_file_bytes": 1048576,
           "confirm_ttl_seconds": 120, "_resolved": {"vault_path": {"exists": True}}}
    monkeypatch.setattr(srv, "cfg", lambda: cfg)
    srv._TOKENS.clear()
    return tmp_path


def test_read(vault_cfg):
    r = srv.obsidian_read("Note.md")
    assert r["frontmatter"]["status"] == "open"
    assert "tag" in r["tags"]
    assert any(l["target"] == "Other" for l in r["wikilinks"])


def test_search_and_list(vault_cfg):
    assert any(x["path"] == "Note.md" for x in srv.obsidian_search("note", in_content=False)["results"])
    assert srv.obsidian_list()["count"] == 2


def test_backlinks_and_links(vault_cfg):
    assert "Note.md" in srv.obsidian_backlinks("Other.md")["backlinks"]
    assert any(l["resolved"] == "Other.md" for l in srv.obsidian_links("Note.md")["links"])


def test_find(vault_cfg):
    assert "Note.md" in srv.obsidian_find("status", "open")["notes"]


def test_create_requires_confirmation(vault_cfg):
    r = srv.obsidian_create("New.md", "hello")
    assert r["requires_confirmation"] is True and r["confirm_token"]
    assert not (vault_cfg / "New.md").exists()   # nothing written yet


def test_create_with_token_writes(vault_cfg):
    prev = srv.obsidian_create("New.md", "hello")
    r = srv.obsidian_create("New.md", "hello", prev["confirm_token"])
    assert r["ok"] is True
    assert (vault_cfg / "New.md").read_text(encoding="utf-8") == "hello"


def test_create_on_existing_errors(vault_cfg):
    prev = srv.obsidian_create("Note.md", "x")
    r = srv.obsidian_create("Note.md", "x", prev["confirm_token"])
    assert "error" in r and "exist" in r["error"].lower()


def test_token_for_other_write_rejected(vault_cfg):
    prev = srv.obsidian_create("New.md", "hello")
    # reuse the token for DIFFERENT content -> must not write
    r = srv.obsidian_create("New.md", "DIFFERENT", prev["confirm_token"])
    assert r.get("requires_confirmation") is True


def test_token_single_use(vault_cfg):
    prev = srv.obsidian_create("New.md", "hello")
    tok = prev["confirm_token"]
    assert srv.obsidian_create("New.md", "hello", tok)["ok"] is True
    assert srv.obsidian_create("New.md", "hello", tok).get("requires_confirmation") is True


def test_update_append(vault_cfg):
    prev = srv.obsidian_update("Other.md", "append", "\nmore")
    r = srv.obsidian_update("Other.md", "append", "\nmore", prev["confirm_token"])
    assert r["ok"] is True
    assert (vault_cfg / "Other.md").read_text(encoding="utf-8").endswith("more")


def test_update_missing_note_errors(vault_cfg):
    prev = srv.obsidian_update("Ghost.md", "append", "x")
    r = srv.obsidian_update("Ghost.md", "append", "x", prev["confirm_token"])
    assert "error" in r


def test_path_traversal_rejected(vault_cfg):
    r = srv.obsidian_create("../evil.md", "x")
    assert "error" in r


def test_health_shape(vault_cfg):
    h = srv.obsidian_health()
    for k in ("vault_path", "exists", "note_count", "writable", "gated_ops"):
        assert k in h
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd mcp/windows_obsidian && python -m pytest tests/test_server.py -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'obsidian_mcp_server'`).

- [ ] **Step 4: Write `obsidian_mcp_server.py`**

```python
"""Obsidian vault MCP (FastMCP, streamable HTTP, 127.0.0.1:8790).

Filesystem access to an Obsidian vault. Read/search/list/links/tags/frontmatter are free; create/update
are gated by a single-use, content-bound confirm token. Every path is confined to the vault's .md files
(vault.resolve). Runs UNELEVATED -- it only reads/writes user files. Goose connects via
type: streamable_http, uri: http://127.0.0.1:8790/mcp.
"""
import os
import time
from typing import List

from mcp.server.fastmcp import FastMCP

import config
import index
import tokens
import vault

mcp = FastMCP("obsidian", host="127.0.0.1", port=8790)

_CFG = None
_TOKENS = {}   # token -> (op, path, mode, content, issued_at)


def cfg():
    global _CFG
    if _CFG is None:
        _CFG = config.load()
    return _CFG


def _vault():
    return cfg()["vault_path"]


# ---- read / query tools ---------------------------------------------------
@mcp.tool()
def obsidian_read(path: str) -> dict:
    """Read a note: full content, parsed frontmatter, headings, outgoing wikilinks, and tags.
    path is vault-relative (e.g. 'sub/Note.md')."""
    try:
        text = vault.read_note(_vault(), path)
    except vault.VaultError as e:
        return {"error": str(e)}
    except OSError:
        return {"error": "note not found: %r" % path}
    fm, body = index.parse_frontmatter(text)
    return {"path": path, "content": text, "frontmatter": fm,
            "headings": index.parse_headings(text), "wikilinks": index.parse_wikilinks(text),
            "tags": index.parse_tags(body, frontmatter=fm)}


@mcp.tool()
def obsidian_search(query: str, in_content: bool = True, in_name: bool = True,
                    folder: str = "", max: int = 50) -> dict:
    """Search notes by filename and/or content (case-insensitive substring). Content search skips files
    larger than max_file_bytes. Returns matching paths with a snippet."""
    c = cfg()
    n = min(max, c["max_search_results"])
    res = index.search(_vault(), query, in_content=in_content, in_name=in_name, folder=folder,
                       max_results=n, max_file_bytes=c["max_file_bytes"])
    return {"query": query, "count": len(res), "results": res}


@mcp.tool()
def obsidian_list(folder: str = "", max: int = 200) -> dict:
    """List .md notes (optionally under a folder), with size + mtime."""
    root = _vault()
    folder_n = (folder or "").replace("\\", "/").strip("/")
    out = []
    for rel in vault.walk_md(root):
        if folder_n and not (rel == folder_n or rel.startswith(folder_n + "/")):
            continue
        try:
            st = os.stat(vault.resolve(root, rel))
            out.append({"path": rel, "bytes": st.st_size, "mtime": int(st.st_mtime)})
        except (OSError, vault.VaultError):
            continue
        if len(out) >= max:
            break
    return {"count": len(out), "notes": out}


@mcp.tool()
def obsidian_tags(tag: str = "") -> dict:
    """No arg: all tags with note counts. With a tag: the notes carrying it (inline #tag or frontmatter
    tags:)."""
    root = _vault()
    if not tag:
        counts = {}
        for rel in vault.walk_md(root):
            try:
                text = vault.read_note(root, rel)
            except OSError:
                continue
            fm, body = index.parse_frontmatter(text)
            for t in index.parse_tags(body, frontmatter=fm):
                counts[t] = counts.get(t, 0) + 1
        return {"tags": [{"tag": k, "count": v} for k, v in sorted(counts.items())]}
    hits = []
    for rel in vault.walk_md(root):
        try:
            text = vault.read_note(root, rel)
        except OSError:
            continue
        fm, body = index.parse_frontmatter(text)
        if tag.lstrip("#") in index.parse_tags(body, frontmatter=fm):
            hits.append(rel)
    return {"tag": tag.lstrip("#"), "count": len(hits), "notes": hits}


@mcp.tool()
def obsidian_backlinks(path: str) -> dict:
    """Notes that link to this note via [[...]]."""
    try:
        vault.resolve(_vault(), path)
    except vault.VaultError as e:
        return {"error": str(e)}
    return {"path": path, "backlinks": index.backlinks(_vault(), path)}


@mcp.tool()
def obsidian_links(path: str) -> dict:
    """This note's outgoing wikilinks + whether each target resolves to an existing note."""
    try:
        return {"path": path, "links": index.outlinks(_vault(), path)}
    except vault.VaultError as e:
        return {"error": str(e)}
    except OSError:
        return {"error": "note not found: %r" % path}


@mcp.tool()
def obsidian_find(key: str, value: str = "") -> dict:
    """Notes whose YAML frontmatter has `key` (optionally == value)."""
    return {"key": key, "value": value, "notes": index.find(_vault(), key, value)}


# ---- write tools (gated) --------------------------------------------------
def _preview(op, path, mode, content):
    head = content if len(content) <= 400 else content[:400] + " …(%d bytes)" % len(content)
    return {"op": op, "path": path, "mode": mode, "content_preview": head}


def _do_write(op, path, mode, content):
    # create | append | overwrite only. replace_section is routed through _do_write_section.
    root = _vault()
    if op == "create":
        try:
            vault.write_note(root, path, content, must_be_new=True)
        except vault.VaultError as e:
            return {"error": str(e)}
        return {"ok": True, "op": "create", "path": path, "bytes": len(content)}
    if not vault.note_exists(root, path):
        return {"error": "note does not exist (use obsidian_create): %r" % path}
    existing = vault.read_note(root, path)
    if mode == "append":
        new_text = existing + content
    elif mode == "overwrite":
        new_text = content
    else:
        return {"error": "unknown mode %r" % mode}
    vault.write_note(root, path, new_text)
    return {"ok": True, "op": "update", "path": path, "mode": mode, "bytes": len(new_text)}


def _gated_write(op, path, mode, content, confirm_token, heading=""):
    try:
        vault.resolve(_vault(), path)
    except vault.VaultError as e:
        return {"error": str(e)}
    # fold heading into the token/content binding for replace_section so the confirm is exact
    eff_content = content if mode != "replace_section" else ("##%s##\n" % heading) + content
    now = time.time()
    if confirm_token:
        rec = _TOKENS.get(confirm_token)
        if rec and rec[:4] == (op, path, mode, eff_content) and tokens.verify_token(
                op, path, mode, eff_content, confirm_token, now=now, issued_at=rec[4],
                ttl=cfg()["confirm_ttl_seconds"]):
            del _TOKENS[confirm_token]
            if mode == "replace_section":
                return _do_write_section(path, heading, content)
            return _do_write(op, path, mode, content)
        confirm_token = ""
    token = tokens.make_token(op, path, mode, eff_content)
    _TOKENS[token] = (op, path, mode, eff_content, now)
    return {"requires_confirmation": True, "confirm_token": token,
            "preview": _preview(op, path, mode + (":" + heading if heading else ""), content),
            "expires_in_seconds": cfg()["confirm_ttl_seconds"]}


def _do_write_section(path, heading, content):
    root = _vault()
    if not vault.note_exists(root, path):
        return {"error": "note does not exist (use obsidian_create): %r" % path}
    existing = vault.read_note(root, path)
    try:
        new_text = index.replace_section(existing, heading, content)
    except KeyError:
        return {"error": "heading %r not found in %s" % (heading, path)}
    vault.write_note(root, path, new_text)
    return {"ok": True, "op": "update", "path": path, "mode": "replace_section", "bytes": len(new_text)}


@mcp.tool()
def obsidian_create(path: str, content: str, confirm_token: str = "") -> dict:
    """Create a NEW note (errors if it already exists). Returns a confirm_token you must pass back to
    actually write -- writes are gated. Never overwrites."""
    return _gated_write("create", path, "", content, confirm_token)


@mcp.tool()
def obsidian_update(path: str, mode: str, content: str, heading: str = "",
                    confirm_token: str = "") -> dict:
    """Update an existing note. mode = append | replace_section (needs heading) | overwrite. Gated:
    returns a confirm_token you must pass back. Never deletes."""
    if mode not in ("append", "replace_section", "overwrite"):
        return {"error": "unknown mode %r (append|replace_section|overwrite)" % mode}
    if mode == "replace_section" and not heading:
        return {"error": "replace_section requires a heading"}
    return _gated_write("update", path, mode, content, confirm_token, heading=heading)


@mcp.tool()
def obsidian_health() -> dict:
    """Vault path + existence + writability, note count, and the gated-op list. Check this first."""
    c = cfg()
    root = c["vault_path"]
    exists = bool(root) and os.path.isdir(root)
    try:
        count = len(vault.walk_md(root)) if exists else 0
    except OSError:
        count = 0
    return {"vault_path": root, "exists": exists, "writable": exists and os.access(root, os.W_OK),
            "note_count": count, "gated_ops": ["obsidian_create", "obsidian_update"],
            "ignored": [".obsidian/", "non-.md files"]}


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd mcp/windows_obsidian && python -m pytest tests/test_server.py -q`
Expected: PASS (14 tests).

- [ ] **Step 6: Run the whole always-on suite**

Run: `cd mcp/windows_obsidian && python -m pytest tests/ -q --ignore=tests/test_live.py`
Expected: PASS (tokens 5 + config 7 + vault ~14 + index 15 + server 14 = 55).

- [ ] **Step 7: Commit**

```bash
git add mcp/windows_obsidian/obsidian_mcp_server.py mcp/windows_obsidian/requirements.txt mcp/windows_obsidian/tests/test_server.py
git commit -m "feat(obsidian): FastMCP server, 10 tools, gated confirm-token writes"
```

---

### Task 6: PowerShell scripts + `setup_mcp_servers.ps1` registration (runlevel field)

**Files:**
- Create: `mcp/windows_obsidian/start_obsidian_mcp.ps1`
- Create: `mcp/windows_obsidian/install_task.ps1`
- Create: `mcp/windows_obsidian/uninstall_task.ps1`
- Modify: `setup_mcp_servers.ps1` ($MCPS entry + a per-entry `runlevel` in the task loop)

- [ ] **Step 1: Write `start_obsidian_mcp.ps1`** (NO elevation check — it only touches user files)

```powershell
# Starts the Obsidian MCP server. Does NOT need Administrator (it only reads/writes user files).
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$py = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $py) { Write-Host "[X] Python 3 not found on PATH." -ForegroundColor Red; exit 1 }
$env:PYTHONIOENCODING = "utf-8"
Write-Host "[*] Starting Obsidian MCP on http://127.0.0.1:8790/mcp  (python: $py)" -ForegroundColor Cyan
& $py (Join-Path $here "obsidian_mcp_server.py")
```

- [ ] **Step 2: Write `install_task.ps1`** (registering needs admin; the task RUNS Limited)

```powershell
# Registers a Scheduled Task that runs the Obsidian MCP at logon, UNELEVATED (RunLevel Limited).
# Registering a Scheduled Task itself requires Administrator (a Windows requirement).
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$id = [Security.Principal.WindowsIdentity]::GetCurrent()
$admin = (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)
if (-not $admin) { Write-Host "[X] Run this elevated to REGISTER the task (the server itself runs unelevated)." -ForegroundColor Red; exit 1 }

$py = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $py) { Write-Host "[X] Python 3 not found on PATH." -ForegroundColor Red; exit 1 }
$server = Join-Path $here "obsidian_mcp_server.py"
$action = New-ScheduledTaskAction -Execute $py -Argument "`"$server`"" -WorkingDirectory $here
$trigger = New-ScheduledTaskTrigger -AtLogOn
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -RunLevel Limited -LogonType Interactive
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
Register-ScheduledTask -TaskName "Obsidian-MCP" -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
Write-Host "[OK] Registered scheduled task 'Obsidian-MCP' (UNELEVATED, at logon)." -ForegroundColor Green
Write-Host "     Start now: Start-ScheduledTask -TaskName Obsidian-MCP" -ForegroundColor Cyan
Write-Host "     Remove:    .\uninstall_task.ps1" -ForegroundColor Cyan
```

- [ ] **Step 3: Write `uninstall_task.ps1`**

```powershell
# Removes the Obsidian MCP scheduled task.
$ErrorActionPreference = "SilentlyContinue"
Unregister-ScheduledTask -TaskName "Obsidian-MCP" -Confirm:$false
Write-Host "[OK] Removed scheduled task 'Obsidian-MCP' (if it existed)." -ForegroundColor Green
```

- [ ] **Step 4: Add the `$MCPS` entry** (after the `dtmsdk` entry) in `setup_mcp_servers.ps1`

```powershell
  @{ name="obsidian"; dir="windows_obsidian"; port=8790; task="Obsidian-MCP"; runlevel="Limited";
     desc="Obsidian vault access (read/search/link-graph/tags/frontmatter + gated create/update of markdown notes) via local MCP server (127.0.0.1:8790). Runs UNELEVATED; writes are per-note confirmation-gated." }
```

- [ ] **Step 5: Make the task loop honor a per-entry `runlevel`** — in `setup_mcp_servers.ps1`, change the principal line inside the `if (-not $SkipTasks)` block:

Replace:
```powershell
    $principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -RunLevel Highest -LogonType Interactive
```
with:
```powershell
    $rl = if ($m.runlevel) { $m.runlevel } else { "Highest" }
    $principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -RunLevel $rl -LogonType Interactive
```

- [ ] **Step 6: Parse-check**

Run:
```powershell
powershell -NoProfile -Command "foreach($f in 'mcp/windows_obsidian/start_obsidian_mcp.ps1','mcp/windows_obsidian/install_task.ps1','mcp/windows_obsidian/uninstall_task.ps1','setup_mcp_servers.ps1'){$e=$null;[void][System.Management.Automation.Language.Parser]::ParseFile((Resolve-Path $f),[ref]$null,[ref]$e); if($e){\"$f FAIL\"}else{\"$f ok\"}}"
```
Expected: all four `ok`.

- [ ] **Step 7: Commit**

```bash
git add mcp/windows_obsidian/start_obsidian_mcp.ps1 mcp/windows_obsidian/install_task.ps1 mcp/windows_obsidian/uninstall_task.ps1 setup_mcp_servers.ps1
git commit -m "feat(obsidian): PS scripts (RunLevel Limited) + one-click registration"
```

---

### Task 7: Gated live read test

**Files:**
- Create: `mcp/windows_obsidian/tests/test_live.py`

- [ ] **Step 1: Write the live test (read-only, gated)**

```python
# mcp/windows_obsidian/tests/test_live.py
"""Read-only smoke test against the REAL configured vault. Gated: runs only when
OBSIDIAN_MCP_LIVE_TESTS=1 and the vault exists. NEVER writes."""
import os
import unittest

import obsidian_mcp_server as srv

_LIVE = os.environ.get("OBSIDIAN_MCP_LIVE_TESTS") == "1"


def _reason():
    if not _LIVE:
        return "OBSIDIAN_MCP_LIVE_TESTS != 1"
    if not srv.obsidian_health().get("exists"):
        return "configured vault does not exist"
    return None


@unittest.skipUnless(_reason() is None, _reason() or "prereqs unmet")
class Live(unittest.TestCase):
    def test_health(self):
        h = srv.obsidian_health()
        self.assertTrue(h["exists"])
        self.assertGreater(h["note_count"], 0)

    def test_search_returns_something(self):
        res = srv.obsidian_search("the", in_name=False, max=5)
        self.assertIn("results", res)
```

- [ ] **Step 2: Verify it SKIPS cleanly**

Run: `cd mcp/windows_obsidian && python -m pytest tests/test_live.py -q`
Expected: 2 skipped (reason: `OBSIDIAN_MCP_LIVE_TESTS != 1`).

- [ ] **Step 3: Commit**

```bash
git add mcp/windows_obsidian/tests/test_live.py
git commit -m "test(obsidian): gated read-only live smoke test"
```

---

### Task 8: Docs — README + DESIGN + `mcp/README.md` cross-link

**Files:**
- Create: `mcp/windows_obsidian/README.md`
- Create: `mcp/windows_obsidian/DESIGN.md`
- Modify: `mcp/README.md`

- [ ] **Step 1: Write `mcp/windows_obsidian/README.md`** — cover: purpose (filesystem Obsidian vault access, complementary to `dtm` RAG); port 8790; the 10 tools one line each; the confirm-flow with a worked example (preview → confirm_token → write); path confinement (traversal/absolute/symlink/`.md`-only/`.obsidian` off-limits); config (`vault_path`, `${}`/env, one-line redeploy); **runs unelevated**; install via `install_task.ps1` or the one-click setup, uninstall; only `.md` scanned.

- [ ] **Step 2: Write `mcp/windows_obsidian/DESIGN.md`** — module map (tokens/config/vault/index/server, one responsibility each); the path-confinement argument (why one funnel, what each check defends); the token-binding argument (a token for write A can't do write B; single-use; TTL; content-bound); why no delete + no silent overwrite; the `replace_section` semantics.

- [ ] **Step 3: Add an `obsidian` subsection to `mcp/README.md`** after the dtmsdk subsection:

```markdown
### Obsidian vault MCP (`windows_obsidian/`, 127.0.0.1:8790) — the only unelevated MCP

`obsidian` gives the harness file-level access to an Obsidian vault (read/search/list, wikilink &
backlink graph, tag & frontmatter queries) plus **confirmation-gated** create/update of markdown notes.
Filesystem-based (no Obsidian app/plugin needed); complementary to the `dtm` RAG (semantic) — this is
exact structured access. **Runs unelevated** (RunLevel Limited) — it only reads/writes user files. Every
path is confined to the vault's `.md` files (no traversal/symlink escape); there is no delete and no
silent overwrite. Vault path lives in `windows_obsidian/config.json` (one-line redeploy). See
[`windows_obsidian/README.md`](windows_obsidian/README.md).
```

- [ ] **Step 4: Verify docs exist**

Run: `cd mcp/windows_obsidian && python -c "import os; print(all(os.path.exists(p) for p in ['README.md','DESIGN.md']))"`
Expected: `True`.

- [ ] **Step 5: Commit**

```bash
git add mcp/windows_obsidian/README.md mcp/windows_obsidian/DESIGN.md mcp/README.md
git commit -m "docs(obsidian): README, DESIGN, suite cross-link"
```

---

### Task 9: Full-suite verification + backlog update

**Files:**
- Modify: `docs/HARDENING_BACKLOG.md`

- [ ] **Step 1: Run the whole always-on suite**

Run: `cd mcp/windows_obsidian && python -m pytest tests/ -q --ignore=tests/test_live.py`
Expected: PASS (55 tests).

- [ ] **Step 2: Confirm live tests skip cleanly**

Run: `cd mcp/windows_obsidian && python -m pytest tests/test_live.py -q`
Expected: 2 skipped.

- [ ] **Step 3: Confirm the server imports and all 10 tool callables exist**

Run:
```bash
cd mcp/windows_obsidian && python -c "import obsidian_mcp_server as s; names=['obsidian_read','obsidian_search','obsidian_list','obsidian_tags','obsidian_backlinks','obsidian_links','obsidian_find','obsidian_create','obsidian_update','obsidian_health']; print(all(hasattr(s,n) for n in names), len(names))"
```
Expected: `True 10`.

- [ ] **Step 4: Note the write-capable MCP in the backlog** — under `## Open`, update the MCP-authentication item to note that `obsidian` (port 8790) is write-capable, so an unauthenticated local caller could write the vault via the confirm flow — further reinforcing the need for per-machine auth.

- [ ] **Step 5: Commit**

```bash
git add docs/HARDENING_BACKLOG.md
git commit -m "docs(backlog): obsidian is write-capable, reinforces MCP-auth item"
```

---

## Notes for the implementer

- Work from the repo root. The MCP modules import each other by bare name (`import vault`); `conftest.py` puts the dir on `sys.path` for tests, and the start script runs with the dir as cwd.
- **No test may touch the real vault** except `tests/test_live.py` (gated, read-only). Server tests monkeypatch `srv.cfg` to a temp vault.
- The confirm-token store is in-process and per-server-process (a restart clears pending previews) — intended.
- `replace_section` folds the heading into the token binding (via `eff_content`) so a confirmation is exact to (op, path, mode, heading, content).
