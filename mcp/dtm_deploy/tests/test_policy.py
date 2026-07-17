import time

import policy


def test_gated_and_safe_sets_disjoint():
    assert policy.GATED_TOOLS.isdisjoint(policy.SAFE_TOOLS)


def test_make_token_deterministic():
    t1 = policy.make_token("dtm_install", {"msi_path": "a"})
    t2 = policy.make_token("dtm_install", {"msi_path": "a"})
    assert t1 == t2


def test_make_token_differs_by_args():
    t1 = policy.make_token("dtm_install", {"msi_path": "a"})
    t2 = policy.make_token("dtm_install", {"msi_path": "b"})
    assert t1 != t2


def test_verify_token_ttl_expired():
    tool, args = "dtm_install", {"msi_path": "a"}
    token = policy.make_token(tool, args)
    assert policy.verify_token(tool, args, token, now=1000, issued_at=800, ttl=120) is False
    assert policy.verify_token(tool, args, token, now=900, issued_at=800, ttl=120) is True


def test_token_store_issue_then_consume():
    store = policy.TokenStore(ttl=120)
    tool, args = "dtm_install", {"msi_path": "a"}
    token = store.issue(tool, args)
    assert store.consume(tool, args, token) is True
    # single-use: consuming again fails
    assert store.consume(tool, args, token) is False


def test_token_store_rejects_wrong_args():
    store = policy.TokenStore(ttl=120)
    token = store.issue("dtm_install", {"msi_path": "a"})
    assert store.consume("dtm_install", {"msi_path": "b"}, token) is False


def test_token_store_prunes_expired(monkeypatch):
    store = policy.TokenStore(ttl=1)
    token = store.issue("dtm_install", {"msi_path": "a"})
    time.sleep(1.1)
    assert store.consume("dtm_install", {"msi_path": "a"}, token) is False
