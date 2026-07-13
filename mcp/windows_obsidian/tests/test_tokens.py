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
