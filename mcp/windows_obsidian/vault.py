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
    # Reject ':' anywhere -- on NTFS "note.md:evil" opens an Alternate Data Stream, which is a
    # non-.md store invisible to walk_md. (The drive-letter form is already caught above.)
    if ":" in r:
        raise VaultError("':' is not allowed in a path: %r" % rel)
    parts = [p for p in r.split("/") if p and p != "."]
    if ".." in parts:
        raise VaultError("path traversal ('..') is not allowed: %r" % rel)
    if not r.lower().endswith(".md"):
        raise VaultError("only .md files are allowed: %r" % rel)
    # Block .obsidian at ANY level, case-insensitively (Windows FS is case-insensitive), matching
    # walk_md's per-level pruning.
    if any(p.lower() == ".obsidian" for p in parts):
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
        dirnames[:] = [d for d in dirnames if d.lower() != ".obsidian"]
        for fn in filenames:
            if fn.lower().endswith(".md"):
                rel = os.path.relpath(os.path.join(dirpath, fn), root_real).replace("\\", "/")
                out.append(rel)
    return out
