"""Windows real-time performance MCP server (FastMCP, streamable HTTP, 127.0.0.1:8783).

PDH counters via ctypes (locale-safe English counters). Complements SRUM's psutil snapshot with disk
latency, pool nonpaged/paged, hard-paging, and Task-Manager-accurate CPU. Goose connects via
type: streamable_http, uri: http://127.0.0.1:8783/mcp.
"""
from typing import Optional
from mcp.server.fastmcp import FastMCP

import pdh_reader as pdh

mcp = FastMCP("perfmon", host="127.0.0.1", port=8783)


@mcp.tool()
def snapshot(delay_ms: int = 1000) -> dict:
    """Real-time system counters (CPU/disk/memory) via PDH. Rate counters use a delay_ms sample window.

    Includes what psutil can't: disk LATENCY (Avg Disk sec/Transfer), pool nonpaged/paged (kernel-leak
    detection), hard-paging (Pages/sec), and % Processor Utility (matches Task Manager).
    """
    try:
        return pdh.snapshot(delay_ms=max(0, int(delay_ms)))
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def bottleneck(delay_ms: int = 1000) -> dict:
    """Heuristic verdict: is the bottleneck right now CPU / disk latency / memory / paging? (thresholded)."""
    try:
        return pdh.bottleneck(delay_ms=max(0, int(delay_ms)))
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def counters(paths: list, delay_ms: int = 1000) -> dict:
    """Read arbitrary SINGLE-INSTANCE PDH counter paths (English names), e.g.
    ["\\\\Processor Information(_Total)\\\\% Processor Utility", "\\\\Paging File(_Total)\\\\% Usage"].

    Returns {"values": {path: value|null}, "counter_errors"?: {...}}. Rate counters are double-sampled
    with delay_ms. NOTE: wildcard/multi-instance paths (e.g. "\\\\GPU Engine(*)\\\\...") are NOT supported —
    they add successfully but the value read returns an error; use an explicit instance name instead.
    """
    try:
        if not isinstance(paths, list) or not paths:
            return {"error": "paths must be a non-empty list of counter path strings"}
        pmap = {str(p): str(p) for p in paths}
        values, status = pdh.read_counters(paths=pmap, delay_ms=max(0, int(delay_ms)))
        res = {"values": values}
        if status:
            res["counter_errors"] = status
        return res
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def baseline_save(name: str = "default", delay_ms: int = 1000) -> dict:
    """Snapshot the current counter set as a named baseline (persisted)."""
    try:
        return pdh.baseline_save(name=name, delay_ms=max(0, int(delay_ms)))
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def baseline_diff(name: str = "default", delay_ms: int = 1000) -> dict:
    """Numeric delta of every counter now vs a saved baseline (e.g. 'has nonpaged pool grown since?')."""
    try:
        return pdh.baseline_diff(name=name, delay_ms=max(0, int(delay_ms)))
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def perfmon_health() -> dict:
    """Admin status, PDH availability, a quick sample, and counter-set size."""
    try:
        return pdh.health()
    except Exception as e:
        return {"error": str(e)}


def list_tool_names():
    import asyncio
    return [t.name for t in asyncio.run(mcp.list_tools())]


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
