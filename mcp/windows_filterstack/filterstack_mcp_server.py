"""Windows filter-stack MCP server (FastMCP, streamable HTTP, 127.0.0.1:8787).

Maps the filtering stacks where AV/VPN/EDR/backup insert themselves (filesystem minifilters + network
filters) -- the #1 cause of "the whole machine is slow". Run ELEVATED (fltmc needs admin). READ-ONLY.
Goose connects via type: streamable_http, uri: http://127.0.0.1:8787/mcp.
"""
from typing import Optional
from mcp.server.fastmcp import FastMCP

import parsers

mcp = FastMCP("filterstack", host="127.0.0.1", port=8787)


@mcp.tool()
def minifilters(filter: Optional[str] = None, third_party_only: bool = False) -> dict:
    """Filesystem minifilters (what sits in EVERY file operation), sorted by altitude, with class +
    third-party detection. third_party_only shows just non-Microsoft (AV/VPN/EDR/backup) filters --
    the "why is file IO slow" view."""
    return parsers.minifilters(filter=filter, third_party_only=third_party_only)


@mcp.tool()
def filter_instances(volume: str = "C:") -> dict:
    """Minifilter instances attached to a specific volume (from fltmc instances)."""
    return parsers.filter_instances(volume=volume)


@mcp.tool()
def network_filters() -> dict:
    """Network filter drivers: NDIS lightweight-filter adapter bindings + the Winsock LSP catalog."""
    return parsers.network_filters()


@mcp.tool()
def altitude_lookup(altitude: str) -> dict:
    """Classify a minifilter altitude -> its Filter Manager load-order group (Anti-Virus / Encryption / ...)."""
    cls, meaning = parsers.altitude_class(altitude)
    if cls is None:
        return {"altitude": altitude, "class": None, "meaning": "unallocated / unknown altitude range"}
    return {"altitude": altitude, "class": cls, "meaning": meaning}


@mcp.tool()
def baseline_save(name: str = "default") -> dict:
    """Snapshot the current minifilter set as a named baseline (to detect a new/leftover filter later)."""
    return parsers.baseline_save(name=name)


@mcp.tool()
def baseline_diff(name: str = "default") -> dict:
    """Minifilters that appeared/disappeared since a baseline -- new AV/VPN filter, or leftover from an uninstall."""
    return parsers.baseline_diff(name=name)


@mcp.tool()
def filterstack_health() -> dict:
    """Admin status, fltmc OK, minifilter count."""
    return parsers.health()


def list_tool_names():
    import asyncio
    return [t.name for t in asyncio.run(mcp.list_tools())]


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
