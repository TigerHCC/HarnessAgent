"""Windows live-network MCP server (FastMCP, streamable HTTP, 127.0.0.1:8782).

Run ELEVATED so owning PIDs of protected processes resolve. READ-ONLY vs the system (only writes its own
JSON baseline). Goose connects via type: streamable_http, uri: http://127.0.0.1:8782/mcp.
"""
from typing import Optional
from mcp.server.fastmcp import FastMCP

import netconn_reader as nr

mcp = FastMCP("netconn", host="127.0.0.1", port=8782)


@mcp.tool()
def connections(state: Optional[str] = None, proto: Optional[str] = None, pid: Optional[int] = None,
                port: Optional[int] = None, process: Optional[str] = None, max: int = 200) -> dict:
    """Current sockets with owning process + service. Filter by state/proto/pid/port/process(substring).

    state e.g. ESTABLISHED|LISTEN|TIME_WAIT|CLOSE_WAIT; proto TCP|UDP. Each row has pid, exe, and (for
    svchost) the hosted services.
    """
    return nr.connections(state=state, proto=proto, pid=pid, port=port, process=process, max=max)


@mcp.tool()
def listeners(max: int = 200) -> dict:
    """What is listening on this box: TCP LISTEN + bound UDP sockets, with owning process/service."""
    return nr.listeners(max=max)


@mcp.tool()
def connection_stats() -> dict:
    """Socket pressure: counts by state/proto, top processes (with TIME_WAIT/CLOSE_WAIT), ephemeral-port usage.

    Use for port-exhaustion / socket-leak diagnosis (a process with a huge TIME_WAIT/CLOSE_WAIT count).
    """
    return nr.connection_stats()


@mcp.tool()
def by_remote(ip: Optional[str] = None, max: int = 200) -> dict:
    """Outbound/established connections grouped by remote endpoint + owner. ip = substring filter.

    Answers 'what is process X talking to' and 'who is connected to remote IP Y'.
    """
    return nr.by_remote(ip=ip, max=max)


@mcp.tool()
def baseline_save(name: str = "default") -> dict:
    """Snapshot the current listeners + remote endpoints as a named baseline (persisted)."""
    return nr.baseline_save(name=name)


@mcp.tool()
def baseline_diff(name: str = "default") -> dict:
    """Diff the current listeners/remotes against a saved baseline: new listener (rogue) / new remote (beaconing)."""
    return nr.baseline_diff(name=name)


@mcp.tool()
def netconn_health() -> dict:
    """Admin status, psutil OK, socket count, service-map OK, saved baseline names."""
    return nr.health()


def list_tool_names():
    import asyncio
    return [t.name for t in asyncio.run(mcp.list_tools())]


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
