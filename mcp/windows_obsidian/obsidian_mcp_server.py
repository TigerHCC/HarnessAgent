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
def obsidian_update(path: str, mode: str, content: str, confirm_token: str = "",
                    heading: str = "") -> dict:
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
