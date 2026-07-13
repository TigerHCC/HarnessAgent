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
    # confinement-review regressions:
    ".Obsidian/note.md", ".OBSIDIAN/note.md",   # case-insensitive .obsidian
    "sub/.obsidian/note.md", "sub/.Obsidian/note.md",  # nested .obsidian at any level
    "note.md:evil.md", "a/b.md:stream",         # NTFS Alternate Data Stream via ':'
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
