"""Datatype GUID table lookup. Directly defuses the HowTo's case-sensitivity trap: names resolve
case-insensitively and a miss returns near-match suggestions instead of a bare 'not found'.
"""
import csv
import difflib


def load_table(path):
    with open(path, "r", encoding="utf-8", newline="") as f:
        return [dict(row) for row in csv.DictReader(f)]


def search(rows, term=None, commodity=None, max=50):
    term_l = (term or "").lower()
    comm_l = (commodity or "").lower()
    out = []
    for r in rows:
        if term_l and term_l not in r.get("Name", "").lower():
            continue
        if comm_l and comm_l != r.get("CommodityType", "").lower():
            continue
        out.append(r)
        if len(out) >= max:
            break
    return out


def find_one(rows, name):
    nl = (name or "").lower()
    for r in rows:
        if r.get("Name", "").lower() == nl:
            return r
    return None


def suggest(rows, name, n=5):
    return difflib.get_close_matches(name, [r.get("Name", "") for r in rows], n=n, cutoff=0.5)
