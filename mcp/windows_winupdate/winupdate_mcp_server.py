"""Windows Update-history MCP server (FastMCP, streamable HTTP, 127.0.0.1:8788).

Correlate problems with what a Windows Update installed, and explain WHY an update failed (WUA history +
decoded HRESULTs + pending-reboot state). READ-ONLY. Goose connects via
type: streamable_http, uri: http://127.0.0.1:8788/mcp.
"""
from mcp.server.fastmcp import FastMCP

import winupdate

mcp = FastMCP("winupdate", host="127.0.0.1", port=8788)


@mcp.tool()
def update_history(max: int = 100, failures_only: bool = False) -> dict:
    """Windows Update history (WUA), newest first: date, title, KB, operation, result, decoded HRESULT.

    failures_only shows just the failed installs -- 'why does this update keep failing' + 'what installed
    right before the problem started'. Full history incl. failures (Get-HotFix misses most of it).
    """
    try:
        return winupdate.update_history(max=max, failures_only=failures_only)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def installed_updates(max: int = 200) -> dict:
    """Installed hotfixes/KBs (Get-HotFix): KB id, type, install date."""
    try:
        return winupdate.installed_updates(max=max)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def pending_state() -> dict:
    """Is a servicing operation stuck: reboot-pending / pending-file-rename state + true OS build (patch level).

    A pending reboot after an update is a classic cause of update-loop instability.
    """
    try:
        return winupdate.pending_state()
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def hresult_decode(code: str) -> dict:
    """Decode a Windows Update / CBS servicing HRESULT (e.g. 0x800f0922) to its name + human meaning."""
    try:
        name, meaning = winupdate.decode_hresult(code)
        return {"code": code, "name": name, "meaning": meaning or "unknown/uncurated HRESULT"}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def winupdate_health() -> dict:
    """Admin status, WUA reachable, history count, reboot-pending, HRESULT table size."""
    try:
        return winupdate.health()
    except Exception as e:
        return {"error": str(e)}


def list_tool_names():
    import asyncio
    return [t.name for t in asyncio.run(mcp.list_tools())]


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
