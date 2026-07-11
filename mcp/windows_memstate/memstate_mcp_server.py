"""Windows memory-state MCP server (FastMCP, streamable HTTP, 127.0.0.1:8786).

poolmon/RamMap-style memory attribution: which pool tag/driver is using/leaking kernel memory, physical
memory composition, and pool-tag trend baselines. Run ELEVATED. READ-ONLY. Goose connects via
type: streamable_http, uri: http://127.0.0.1:8786/mcp.
"""
import datetime as dt
import json
import os
import threading
from typing import Optional

from mcp.server.fastmcp import FastMCP

import native
import pooltags

mcp = FastMCP("memstate", host="127.0.0.1", port=8786)

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
BASELINE_PATH = os.environ.get("MEMSTATE_BASELINES", os.path.join(DATA_DIR, "memstate_baselines.json"))
_lock = threading.Lock()
_MB = 1048576.0
_GB = 1073741824.0


def _now_iso():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


@mcp.tool()
def pool_tags(sort_by: str = "nonpaged", top_n: int = 30, filter: Optional[str] = None) -> dict:
    """Pool-tag usage (poolmon view): per-tag paged/nonpaged pool bytes, sorted. The kernel-leak hunt.

    sort_by: nonpaged | paged | nonpaged_allocs. filter: substring on the tag. High nonpaged for a tag
    (esp. growing over time -- see baseline_diff) points at the leaking driver (see tag_driver).
    """
    try:
        rows = native.pool_tags_raw()
    except Exception as e:
        return {"error": str(e)}
    keyfn = {"nonpaged": lambda r: r["nonpaged_used"], "paged": lambda r: r["paged_used"],
             "nonpaged_allocs": lambda r: r["nonpaged_allocs"]}.get(sort_by, lambda r: r["nonpaged_used"])
    flt = (filter or "").lower()
    total_np = sum(r["nonpaged_used"] for r in rows)
    total_pg = sum(r["paged_used"] for r in rows)
    total_tag_count = len(rows)
    if flt:
        rows = [r for r in rows if flt in r["tag"].lower()]
    rows.sort(key=keyfn, reverse=True)
    out = []
    for r in rows[:int(top_n)]:
        out.append({"tag": r["tag"], "description": pooltags.describe(r["tag"]),
                    "nonpaged_mb": round(r["nonpaged_used"] / _MB, 2),
                    "paged_mb": round(r["paged_used"] / _MB, 2),
                    "nonpaged_allocs": r["nonpaged_allocs"],
                    "nonpaged_outstanding": r["nonpaged_allocs"] - r["nonpaged_frees"],
                    "paged_outstanding": r["paged_allocs"] - r["paged_frees"]})
    return {"total_nonpaged_mb": round(total_np / _MB, 1), "total_paged_mb": round(total_pg / _MB, 1),
            "total_tag_count": total_tag_count, "matched_tag_count": len(rows), "count": len(out),
            "tags": out}


@mcp.tool()
def memory_composition() -> dict:
    """Physical-memory composition (RamMap view): standby / modified / free / zeroed page pools in GB."""
    try:
        m = native.memory_list_raw()
        perf = native.performance_info()
    except Exception as e:
        return {"error": str(e)}
    pg = m["page_size"]

    def gb(pages):
        return round(pages * pg / _GB, 2)
    return {"physical_total_gb": gb(perf["physical_total_pages"]),
            "physical_available_gb": gb(perf["physical_available_pages"]),
            "standby_gb": gb(m["standby_pages"]), "modified_gb": gb(m["modified_pages"]),
            "free_gb": gb(m["free_pages"]), "zeroed_gb": gb(m["zero_pages"]),
            "bad_gb": gb(m["bad_pages"]),
            "standby_by_priority_gb": [gb(p) for p in m["standby_by_priority_pages"]]}


@mcp.tool()
def memory_overview() -> dict:
    """Overview: physical/commit totals, kernel paged/nonpaged pool, system-wide handle/process/thread counts."""
    try:
        p = native.performance_info()
    except Exception as e:
        return {"error": str(e)}
    pg = p["page_size"]
    return {"physical_total_gb": round(p["physical_total_pages"] * pg / _GB, 2),
            "physical_available_gb": round(p["physical_available_pages"] * pg / _GB, 2),
            "commit_total_gb": round(p["commit_total_pages"] * pg / _GB, 2),
            "commit_limit_gb": round(p["commit_limit_pages"] * pg / _GB, 2),
            "kernel_paged_mb": round(p["kernel_paged_pages"] * pg / _MB, 1),
            "kernel_nonpaged_mb": round(p["kernel_nonpaged_pages"] * pg / _MB, 1),
            "system_cache_mb": round(p["system_cache_pages"] * pg / _MB, 1),
            "handles": p["handles"], "processes": p["processes"], "threads": p["threads"]}


@mcp.tool()
def tag_driver(tag: str) -> dict:
    """Best-effort owning driver(s) for a pool tag: known-tag description + a scan of drivers\\*.sys."""
    try:
        return pooltags.tag_driver(tag)
    except Exception as e:
        return {"error": str(e)}


def _load():
    try:
        with open(BASELINE_PATH, "r", encoding="utf-8") as fh:
            obj = json.load(fh)
        return obj if isinstance(obj, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(data):
    d = os.path.dirname(BASELINE_PATH)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = BASELINE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False)
    os.replace(tmp, BASELINE_PATH)


@mcp.tool()
def baseline_save(name: str = "default") -> dict:
    """Snapshot per-tag nonpaged pool usage as a named baseline (for leak-trend analysis)."""
    try:
        rows = native.pool_tags_raw()
    except Exception as e:
        return {"error": str(e)}
    snap = {r["tag"]: r["nonpaged_used"] for r in rows}
    with _lock:
        data = _load()
        data[name] = {"ts": _now_iso(), "tags": snap}
        try:
            _save(data)
        except OSError as e:
            return {"error": f"could not write baseline: {e}"}
    return {"name": name, "ts": data[name]["ts"], "tag_count": len(snap)}


@mcp.tool()
def baseline_diff(name: str = "default", top_n: int = 20) -> dict:
    """Which pool tags GREW the most (nonpaged bytes) since a baseline -- the leak signal over time."""
    with _lock:
        data = _load()
    entry = data.get(name)
    if not isinstance(entry, dict) or not isinstance(entry.get("tags"), dict):
        return {"error": f"no valid baseline named '{name}'; call baseline_save first",
                "available": list(data.keys())}
    try:
        rows = native.pool_tags_raw()
    except Exception as e:
        return {"error": str(e)}
    base = entry["tags"]
    now = {r["tag"]: r["nonpaged_used"] for r in rows}
    deltas = []
    for tag, n in now.items():
        b = base.get(tag, 0)
        if not isinstance(b, (int, float)):
            continue  # corrupt/hand-edited baseline value -> skip, don't raise
        d = n - b
        if d != 0:
            deltas.append({"tag": tag, "description": pooltags.describe(tag),
                           "delta_mb": round(d / _MB, 2),
                           "from_mb": round(b / _MB, 2), "to_mb": round(n / _MB, 2)})
    deltas.sort(key=lambda x: x["delta_mb"], reverse=True)
    return {"name": name, "baseline_ts": entry.get("ts"), "top_growth": deltas[:int(top_n)]}


@mcp.tool()
def memstate_health() -> dict:
    """Admin status, ntdll query OK, pool-tag count, physical GB."""
    h = {"is_admin": native.is_admin()}
    try:
        h["tag_count"] = len(native.pool_tags_raw())
        h["ntdll_ok"] = True
    except Exception as e:
        h["ntdll_ok"] = False
        h["error"] = str(e)
    try:
        p = native.performance_info()
        h["physical_gb"] = round(p["physical_total_pages"] * p["page_size"] / _GB, 1)
    except Exception:
        pass
    with _lock:
        h["baselines"] = list(_load().keys())
    return h


def list_tool_names():
    import asyncio
    return [t.name for t in asyncio.run(mcp.list_tools())]


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
