"""Config-drift SQLite store: persist snapshots, diff them over time. No MCP deps.

The ONLY thing this MCP writes is this DB (data/drift.db) — never a system setting.
"""
import datetime as dt
import json
import os
import sqlite3

import collectors

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
DB_PATH = os.environ.get("DRIFT_DB", os.path.join(DATA_DIR, "drift.db"))
_MAX_DIFF = 500  # cap per added/removed/changed list


def _now_iso():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _conn():
    d = os.path.dirname(DB_PATH)
    if d:  # DRIFT_DB may be a bare filename (dirname == "") -> makedirs("") raises
        os.makedirs(d, exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.execute("""CREATE TABLE IF NOT EXISTS snapshots(
                   id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, note TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS items(
                   snapshot_id INTEGER NOT NULL, category TEXT, item_key TEXT,
                   name TEXT, detail_json TEXT, value_hash TEXT)""")
    c.execute("CREATE INDEX IF NOT EXISTS ix_items_snap ON items(snapshot_id)")
    return c


def _counts(items):
    out = {}
    for it in items:
        out[it["category"]] = out.get(it["category"], 0) + 1
    return out


def snapshot_now(note=None):
    items, errors = collectors.collect()
    ts = _now_iso()
    c = _conn()
    try:
        cur = c.execute("INSERT INTO snapshots(ts, note) VALUES(?, ?)", (ts, note))
        sid = cur.lastrowid
        c.executemany(
            "INSERT INTO items(snapshot_id, category, item_key, name, detail_json, value_hash) VALUES(?,?,?,?,?,?)",
            [(sid, it["category"], it["key"], it["name"],
              json.dumps(it["detail"], ensure_ascii=False), it["value_hash"]) for it in items])
        c.commit()
    finally:
        c.close()
    res = {"snapshot_id": sid, "ts": ts, "total_items": len(items), "counts": _counts(items)}
    if errors:
        res["collector_errors"] = errors
    return res


def list_snapshots():
    c = _conn()
    try:
        rows = c.execute("""SELECT s.id, s.ts, s.note, COUNT(i.snapshot_id)
                            FROM snapshots s LEFT JOIN items i ON i.snapshot_id=s.id
                            GROUP BY s.id ORDER BY s.id DESC""").fetchall()
        cat_rows = c.execute("SELECT snapshot_id, category, COUNT(*) FROM items GROUP BY snapshot_id, category").fetchall()
    finally:
        c.close()
    by_snap = {}
    for sid, cat, n in cat_rows:
        by_snap.setdefault(sid, {})[cat] = n
    return {"count": len(rows),
            "snapshots": [{"id": r[0], "ts": r[1], "note": r[2], "total_items": r[3],
                           "counts": by_snap.get(r[0], {})} for r in rows]}


def _load_snapshot(sid):
    c = _conn()
    try:
        exists = c.execute("SELECT ts FROM snapshots WHERE id=?", (sid,)).fetchone()
        if not exists:
            return None, None
        rows = c.execute("SELECT category, item_key, name, detail_json, value_hash FROM items WHERE snapshot_id=?",
                         (sid,)).fetchall()
    finally:
        c.close()
    d = {}
    for cat, key, name, dj, vh in rows:
        d[(cat, key)] = {"category": cat, "key": key, "name": name,
                         "detail": json.loads(dj) if dj else {}, "value_hash": vh}
    return d, exists[0]


def _live_map(category=None):
    items, errors = collectors.collect(category=category)
    return {(it["category"], it["key"]): it for it in items}, errors


def _latest_id():
    c = _conn()
    try:
        r = c.execute("SELECT id FROM snapshots ORDER BY id DESC LIMIT 1").fetchone()
    finally:
        c.close()
    return r[0] if r else None


def _diff_maps(a_map, b_map, category=None):
    def keep(k):
        return category is None or k[0] == category
    a_keys = {k for k in a_map if keep(k)}
    b_keys = {k for k in b_map if keep(k)}
    added, removed, changed = [], [], []
    for k in b_keys - a_keys:
        it = b_map[k]
        added.append({"category": k[0], "key": k[1], "name": it["name"], "detail": it["detail"]})
    for k in a_keys - b_keys:
        it = a_map[k]
        removed.append({"category": k[0], "key": k[1], "name": it["name"], "detail": it["detail"]})
    for k in a_keys & b_keys:
        if a_map[k]["value_hash"] != b_map[k]["value_hash"]:
            changed.append({"category": k[0], "key": k[1], "name": b_map[k]["name"],
                            "from": a_map[k]["detail"], "to": b_map[k]["detail"]})
    for lst in (added, removed, changed):
        lst.sort(key=lambda x: (x["category"], x["key"]))
    return added, removed, changed


def _cat_counts(lst):
    out = {}
    for x in lst:
        out[x["category"]] = out.get(x["category"], 0) + 1
    return out


def diff(a=None, b=None, category=None):
    # resolve a
    if a is None:
        a = _latest_id()
        if a is None:
            return {"error": "no snapshots yet; call snapshot_now() first"}
    try:
        a = int(a)
    except (TypeError, ValueError):
        return {"error": "snapshot id 'a' must be an integer"}
    a_map, a_ts = _load_snapshot(a)
    if a_map is None:
        return {"error": f"snapshot {a} not found"}
    # resolve b (None => live now)
    errors = {}
    if b is None:
        b_map, errors = _live_map(category)
        b_ts = _now_iso() + " (live)"
    else:
        try:
            b = int(b)
        except (TypeError, ValueError):
            return {"error": "snapshot id 'b' must be an integer or omitted (live)"}
        b_map, b_ts = _load_snapshot(b)
        if b_map is None:
            return {"error": f"snapshot {b} not found"}
    added, removed, changed = _diff_maps(a_map, b_map, category=category)
    res = {
        "from": {"snapshot": a, "ts": a_ts},
        "to": {"snapshot": b if b is not None else "live", "ts": b_ts},
        "summary": {"added": len(added), "removed": len(removed), "changed": len(changed)},
        "by_category": {"added": _cat_counts(added), "removed": _cat_counts(removed),
                        "changed": _cat_counts(changed)},
        "truncated": any(len(x) > _MAX_DIFF for x in (added, removed, changed)),
        "added": added[:_MAX_DIFF], "removed": removed[:_MAX_DIFF], "changed": changed[:_MAX_DIFF],
    }
    if errors:
        res["collector_errors"] = errors
    return res


def what_changed_since(ref):
    if ref is None:
        return {"error": "ref required (snapshot id or ISO date)"}
    sid = None
    # integer id?
    try:
        sid = int(ref)
    except (TypeError, ValueError):
        sid = None
    if sid is None:
        # treat as date; find latest snapshot at or before it.
        # A date-only ref ("2026-07-11") must include snapshots taken THAT day: stored ts are full
        # second-precision ISO ("2026-07-11T14:30:00+00:00") which sort AFTER the bare date, so use an
        # inclusive end-of-day upper bound. (A full-datetime ref is compared as-is.)
        upper = str(ref)
        if "T" not in upper:
            upper = upper + "T23:59:59+00:00"
        c = _conn()
        try:
            r = c.execute("SELECT id FROM snapshots WHERE ts <= ? ORDER BY ts DESC LIMIT 1",
                          (upper,)).fetchone()
        finally:
            c.close()
        if not r:
            return {"error": f"no snapshot at or before {ref}"}
        sid = r[0]
    return diff(a=sid, b=None)


def current(category=None, filter=None, max=200):
    if category and category not in collectors.CATEGORIES:
        return {"error": f"unknown category (valid: {', '.join(collectors.CATEGORIES)})"}
    items, errors = collectors.collect(category=category)
    flt = (filter or "").lower()
    rows = [{"category": it["category"], "key": it["key"], "name": it["name"], "detail": it["detail"]}
            for it in items
            if not flt or flt in it["key"].lower() or flt in (it["name"] or "").lower()
            or flt in json.dumps(it["detail"], ensure_ascii=False).lower()]
    rows.sort(key=lambda x: (x["category"], x["key"]))
    total = len(rows)
    res = {"count": min(total, int(max)), "total_matching": total, "truncated": total > int(max),
           "counts": _counts(items), "items": rows[:int(max)]}
    if errors:
        res["collector_errors"] = errors
    return res


def health():
    h = {"is_admin": collectors.is_admin(), "db_path": DB_PATH}
    try:
        snaps = list_snapshots()
        h["snapshot_count"] = snaps["count"]
        h["latest"] = snaps["snapshots"][0] if snaps["snapshots"] else None
    except Exception as e:
        h["db_error"] = str(e)
    items, errors = collectors.collect()
    h["live_counts"] = _counts(items)
    h["collectors_ok"] = {c: (c not in errors) for c in collectors.CATEGORIES}
    if errors:
        h["collector_errors"] = errors
    return h
