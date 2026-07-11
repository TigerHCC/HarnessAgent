import importlib
import os

import pytest


@pytest.fixture()
def store(tmp_path, monkeypatch):
    # isolate the DB per test
    monkeypatch.setenv("DRIFT_DB", str(tmp_path / "drift.db"))
    import drift_store
    importlib.reload(drift_store)
    return drift_store


def test_diff_maps_added_removed_changed(store):
    a = {
        ("services", "svcA"): {"category": "services", "key": "svcA", "name": "A",
                               "detail": {"start": "Manual"}, "value_hash": "h1"},
        ("services", "svcB"): {"category": "services", "key": "svcB", "name": "B",
                               "detail": {"start": "Auto"}, "value_hash": "h2"},
    }
    b = {
        ("services", "svcB"): {"category": "services", "key": "svcB", "name": "B",
                               "detail": {"start": "Manual"}, "value_hash": "h2CHANGED"},
        ("services", "svcC"): {"category": "services", "key": "svcC", "name": "C",
                               "detail": {"start": "Auto"}, "value_hash": "h3"},
    }
    added, removed, changed = store._diff_maps(a, b)
    assert [x["key"] for x in added] == ["svcC"]
    assert [x["key"] for x in removed] == ["svcA"]
    assert [x["key"] for x in changed] == ["svcB"]
    assert changed[0]["from"] == {"start": "Auto"} and changed[0]["to"] == {"start": "Manual"}


def test_diff_category_filter(store):
    a = {("services", "s"): {"category": "services", "key": "s", "name": "s", "detail": {}, "value_hash": "1"}}
    b = {("autoruns", "r"): {"category": "autoruns", "key": "r", "name": "r", "detail": {}, "value_hash": "2"}}
    added, removed, changed = store._diff_maps(a, b, category="services")
    assert added == [] and [x["key"] for x in removed] == ["s"]


def test_snapshot_and_diff_roundtrip(store):
    snap = store.snapshot_now(note="baseline")
    assert snap["snapshot_id"] >= 1
    assert snap["total_items"] > 0
    lst = store.list_snapshots()
    assert lst["count"] == 1
    # diff latest vs live now => should succeed (likely near-empty)
    d = store.diff()
    assert "summary" in d and "added" in d
    # diff against a missing snapshot id
    assert "error" in store.diff(a=9999)


def test_what_changed_since_by_id_and_date(store):
    snap = store.snapshot_now()
    by_id = store.what_changed_since(snap["snapshot_id"])
    assert "summary" in by_id
    by_date = store.what_changed_since("2000-01-01")
    assert "error" in by_date  # no snapshot before 2000
    future = store.what_changed_since("2999-01-01")
    assert "summary" in future  # snapshot exists before this date


def test_what_changed_since_date_only_includes_same_day(store):
    snap = store.snapshot_now()
    ts = store.list_snapshots()["snapshots"][0]["ts"]  # full ISO, e.g. 2026-07-11T..
    date_only = ts[:10]
    # a bare date must SELECT the same-day snapshot (not skip it via lexical comparison)
    res = store.what_changed_since(date_only)
    assert "summary" in res, res
    assert "error" not in res


def test_list_snapshots_has_counts(store):
    store.snapshot_now()
    snaps = store.list_snapshots()["snapshots"]
    assert snaps and isinstance(snaps[0]["counts"], dict)
    assert sum(snaps[0]["counts"].values()) == snaps[0]["total_items"]


def test_conn_bare_db_filename_does_not_crash(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DRIFT_DB", "drift_bare.db")  # bare filename, dirname == ""
    import importlib
    import drift_store
    importlib.reload(drift_store)
    res = drift_store.list_snapshots()  # must not raise FileNotFoundError
    assert "snapshots" in res


def test_current_and_health(store):
    cur = store.current(category="programs", max=5)
    assert "items" in cur and cur["count"] <= 5
    assert "error" in store.current(category="bogus")
    h = store.health()
    assert "is_admin" in h and "live_counts" in h and "collectors_ok" in h
