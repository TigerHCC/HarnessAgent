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


def test_update_replace_section(vault_cfg):
    # Sectioned note: replace the body under '## Sec', leave '## Keep' intact.
    (vault_cfg / "Sec.md").write_text("# T\n## Sec\nold\n## Keep\nkeep\n", encoding="utf-8")
    prev = srv.obsidian_update("Sec.md", "replace_section", "NEW\n", heading="Sec")
    assert prev["requires_confirmation"] is True
    r = srv.obsidian_update("Sec.md", "replace_section", "NEW\n", heading="Sec",
                            confirm_token=prev["confirm_token"])
    assert r["ok"] is True
    text = (vault_cfg / "Sec.md").read_text(encoding="utf-8")
    assert "NEW" in text and "old" not in text and "keep" in text


def test_update_replace_section_needs_heading(vault_cfg):
    r = srv.obsidian_update("Other.md", "replace_section", "x")   # no heading
    assert "error" in r and "heading" in r["error"].lower()


def test_replace_section_token_bound_to_heading(vault_cfg):
    (vault_cfg / "Sec.md").write_text("# T\n## A\na\n## B\nb\n", encoding="utf-8")
    prev = srv.obsidian_update("Sec.md", "replace_section", "X", heading="A")
    # reuse the token but aim at a DIFFERENT heading -> must not execute
    r = srv.obsidian_update("Sec.md", "replace_section", "X", heading="B",
                            confirm_token=prev["confirm_token"])
    assert r.get("requires_confirmation") is True


def test_path_traversal_rejected(vault_cfg):
    r = srv.obsidian_create("../evil.md", "x")
    assert "error" in r


def test_health_shape(vault_cfg):
    h = srv.obsidian_health()
    for k in ("vault_path", "exists", "note_count", "writable", "gated_ops"):
        assert k in h
