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
