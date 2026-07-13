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
