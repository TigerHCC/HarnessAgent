"""Windows execution-evidence MCP server (FastMCP, streamable HTTP, 127.0.0.1:8780).

Run ELEVATED (Prefetch dir, BAM/ShimCache SYSTEM hive need admin; UserAssist is per-user). Goose
connects via type: streamable_http, uri: http://127.0.0.1:8780/mcp  (Goose 1.39 dropped SSE).
"""
from typing import Optional
from mcp.server.fastmcp import FastMCP

import prefetch_reader as pf
import registry_forensics as rf

mcp = FastMCP("exec", host="127.0.0.1", port=8780)


@mcp.tool()
def prefetch_list(filter: Optional[str] = None, max: int = 50) -> dict:
    """Prefetch: per-exe last run + run count + hash, newest first. filter = substring of the .pf name."""
    return pf.prefetch_list(filter=filter, max=max)


@mcp.tool()
def prefetch_detail(name: str) -> dict:
    """One Prefetch file in full: 8 last-run times, run count, volume, and the loaded-file list.

    name = the .pf file name (or exe name) shown by prefetch_list (basename only).
    """
    return pf.prefetch_detail(name)


@mcp.tool()
def bam_list(max: int = 200) -> dict:
    """BAM: per-user last-execution time of each exe since recent boots (needs admin), newest first."""
    return rf.bam_list(max=max)


@mcp.tool()
def userassist_list(max: int = 200) -> dict:
    """UserAssist: GUI-launched program run counts + last run + focus time (current user)."""
    return rf.userassist_list(max=max)


@mcp.tool()
def shimcache_list(filter: Optional[str] = None, max: int = 200) -> dict:
    """ShimCache (AppCompatCache): executables the compat engine has seen, with the file's mtime.

    NOTE: last_modified is the file's $StandardInfo mtime (presence evidence), NOT execution time.
    """
    return rf.shimcache_list(filter=filter, max=max)


@mcp.tool()
def exec_timeline(hours: int = 24, filter: Optional[str] = None, max: int = 200) -> dict:
    """Merged execution timeline (Prefetch run-times + BAM + UserAssist), newest first.

    filter = substring on the exe/name. ShimCache is excluded (it has no execution time). If a source
    is unreadable (e.g. not elevated), it is reported in source_errors — never silently dropped.
    """
    import datetime as dt
    try:
        hrs, mx = int(hours), int(max)
    except (TypeError, ValueError):
        return {"error": "hours and max must be integers"}
    if hrs < 0:
        hrs = 0
    if mx < 0:
        mx = 0
    try:
        cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hrs)
    except OverflowError:
        return {"error": "hours out of range"}
    flt = (filter or "").lower()
    events, source_errors = [], {}

    def add(source, exe, iso, detail):
        if not iso:
            return
        try:
            when = dt.datetime.fromisoformat(iso)
        except ValueError:
            return
        if when < cutoff:
            return
        if flt and flt not in (exe or "").lower():
            return
        events.append({"time": iso, "source": source, "exe": exe, "detail": detail})

    # Prefetch (needs admin; iter_ returns empty when not elevated -> check explicitly)
    if not pf.is_admin():
        source_errors["Prefetch"] = "requires admin (server not elevated)"
    else:
        try:
            for exe, iso, rc in pf.iter_prefetch_runs():
                add("Prefetch", exe, iso, {"run_count": rc})
        except Exception as e:
            source_errors["Prefetch"] = str(e)
    # BAM (bam_list returns an {error} dict on failure rather than raising)
    b = rf.bam_list(max=1_000_000)
    if "error" in b:
        source_errors["BAM"] = b["error"]
    else:
        for row in b["bam"]:
            add("BAM", row["exe"], row.get("last_exec"), {"user": row.get("user")})
    # UserAssist
    u = rf.userassist_list(max=1_000_000)
    if "error" in u:
        source_errors["UserAssist"] = u["error"]
    else:
        for row in u["userassist"]:
            add("UserAssist", row["name"], row.get("last_run"), {"run_count": row.get("run_count")})

    events.sort(key=lambda e: e["time"], reverse=True)
    total = len(events)
    out = events[:mx]
    res = {"window_hours": hrs, "count": len(out), "total_matching": total,
           "truncated": total > len(out), "timeline": out}
    if source_errors:
        res["source_errors"] = source_errors
    return res


@mcp.tool()
def exec_health() -> dict:
    """Admin status, Prefetch enabled + count, BAM/UserAssist/ShimCache entry counts."""
    h = {"is_admin": pf.is_admin(), "prefetch_enabled": pf.prefetch_enabled()}
    try:
        pl = pf.prefetch_list(max=1)
        h["prefetch"] = {"error": pl["error"]} if "error" in pl else {"files": pl.get("total_matching")}
    except Exception as e:
        h["prefetch"] = {"error": str(e)}
    h["registry"] = rf.health()
    return h


def list_tool_names():
    import asyncio
    return [t.name for t in asyncio.run(mcp.list_tools())]


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
