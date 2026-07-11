"""Windows config-drift MCP server (FastMCP, streamable HTTP, 127.0.0.1:8781).

Run ELEVATED (Services/Tasks need admin). READ-ONLY vs the system — the only thing it writes is its own
snapshot DB. Goose connects via type: streamable_http, uri: http://127.0.0.1:8781/mcp.
"""
from typing import Optional
from mcp.server.fastmcp import FastMCP

import drift_store as store

mcp = FastMCP("drift", host="127.0.0.1", port=8781)


@mcp.tool()
def snapshot_now(note: Optional[str] = None) -> dict:
    """Capture a point-in-time snapshot of autoruns/services/programs/tasks and persist it.

    Build a baseline NOW (and periodically) so 'what changed since last-good' is answerable later.
    """
    try:
        return store.snapshot_now(note=note)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def list_snapshots() -> dict:
    """List saved snapshots (id, timestamp, note, item count), newest first."""
    try:
        return store.list_snapshots()
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def current(category: Optional[str] = None, filter: Optional[str] = None, max: int = 200) -> dict:
    """Live enumeration (no persist) of current config. category: autoruns|services|programs|tasks.

    filter = substring on key/name/detail. Use this to inspect the current state directly.
    """
    try:
        return store.current(category=category, filter=filter, max=max)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def diff(a: Optional[int] = None, b: Optional[int] = None, category: Optional[str] = None) -> dict:
    """Diff two snapshots (added/removed/changed). a defaults to the latest snapshot; b defaults to LIVE now.

    category optionally restricts to autoruns|services|programs|tasks.
    """
    try:
        return store.diff(a=a, b=b, category=category)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def what_changed_since(ref: str) -> dict:
    """What changed between a past point and now. ref = a snapshot id OR an ISO date (e.g. 2026-07-01).

    Diffs the snapshot at/just-before ref against the live current config — the 'what changed since
    last-good' headline. Requires an earlier snapshot to exist.
    """
    try:
        return store.what_changed_since(ref)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def drift_health() -> dict:
    """Admin status, DB path, snapshot count, live collector counts + per-collector OK status."""
    try:
        return store.health()
    except Exception as e:
        return {"error": str(e)}


def list_tool_names():
    import asyncio
    return [t.name for t in asyncio.run(mcp.list_tools())]


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
