"""Windows SRUM + live metrics MCP server (FastMCP, streamable HTTP, 127.0.0.1:8777).

Run ELEVATED for SRUM tools (live tools work either way). Goose connects via
type: streamable_http, uri: http://127.0.0.1:8777/mcp  (Goose 1.39 dropped SSE).
"""
from mcp.server.fastmcp import FastMCP

import live_metrics
import srum_reader

mcp = FastMCP("srum", host="127.0.0.1", port=8777)


@mcp.tool()
def live_snapshot() -> dict:
    """Current CPU/memory/disk/network/power snapshot + top processes (real-time, not SRUM)."""
    return live_metrics.snapshot()


@mcp.tool()
def top_processes(by: str = "cpu", n: int = 10) -> list:
    """Top N processes by 'cpu' or 'memory' (real-time)."""
    return live_metrics.top_processes(by=by, n=n)


@mcp.tool()
def srum_app_usage(hours: int = 24, top_n: int = 20) -> dict:
    """Historical per-app CPU cycle time + bytes read/written from SRUM (needs admin)."""
    return srum_reader.app_usage(hours=hours, top_n=top_n)


@mcp.tool()
def srum_network_usage(hours: int = 24, top_n: int = 20) -> dict:
    """Historical per-app network bytes sent/received from SRUM (needs admin)."""
    return srum_reader.network_usage(hours=hours, top_n=top_n)


@mcp.tool()
def srum_energy_usage(hours: int = 24, top_n: int = 20) -> dict:
    """Historical per-app energy usage from SRUM, best-effort per local schema (needs admin).

    NOTE: many systems (esp. desktops) do not populate per-app energy — values may be 0.
    """
    return srum_reader.energy_usage(hours=hours, top_n=top_n)


@mcp.tool()
def srum_health() -> dict:
    """SRUM DB info, admin status, tables found, parser status, cache age."""
    return srum_reader.health()


def list_tool_names():
    """Test helper: names of registered tools."""
    import asyncio
    return [t.name for t in asyncio.run(mcp.list_tools())]


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
