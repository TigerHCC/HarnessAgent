"""Windows disk/storage MCP server (FastMCP, streamable HTTP, 127.0.0.1:8784).

Run ELEVATED (raw volume handle for the USN journal + reliability IOCTLs need admin). READ-ONLY vs the
system. Goose connects via type: streamable_http, uri: http://127.0.0.1:8784/mcp.
"""
from typing import Optional
from mcp.server.fastmcp import FastMCP

import usn_reader as usn
import disk_health as dh

mcp = FastMCP("disk", host="127.0.0.1", port=8784)


@mcp.tool()
def recent_file_changes(minutes: int = 60, path_filter: Optional[str] = None,
                        reasons: Optional[list] = None, max: int = 200, volume: str = "C:") -> dict:
    """What files changed on the volume in the last N minutes (NTFS USN journal), newest first.

    One row per file (union of change reasons). path_filter = substring; reasons = names to filter
    (FILE_CREATE, FILE_DELETE, RENAME_NEW_NAME, DATA_OVERWRITE, ...). The killer 'what changed before
    the crash' tool. Needs admin.
    """
    return usn.recent_file_changes(minutes=minutes, path_filter=path_filter, reasons=reasons,
                                   max=max, volume=volume)


@mcp.tool()
def directory_churn(minutes: int = 60, top_n: int = 20, volume: str = "C:") -> dict:
    """Which directories are churning (most file changes) in the last N minutes -- temp explosion, log
    spam, AV scanning, installer gone wrong. From the USN journal. Needs admin."""
    return usn.directory_churn(minutes=minutes, top_n=top_n, volume=volume)


@mcp.tool()
def disk_health() -> dict:
    """Per physical disk SMART/reliability: health, media type, wear %, temperature, errors, power-on hours."""
    return dh.disk_health()


@mcp.tool()
def health_baseline_save(name: str = "default") -> dict:
    """Persist the current reliability counters as a named baseline (for trend analysis)."""
    return dh.health_baseline_save(name=name)


@mcp.tool()
def health_baseline_diff(name: str = "default") -> dict:
    """Numeric delta of reliability counters now vs a baseline -- has wear/temperature/errors moved?"""
    return dh.health_baseline_diff(name=name)


@mcp.tool()
def volume_state(volume: str = "C:") -> dict:
    """Volume integrity: dirty bit (pending chkdsk), NTFS repair state, shadow copies, free/size."""
    return dh.volume_state(volume=volume)


@mcp.tool()
def disk_status() -> dict:
    """Admin status, USN journal info (id, first/next USN, span), disk count, saved baselines."""
    return {"is_admin": usn.is_admin(), "usn": usn.usn_status(),
            "disks": dh.disk_health().get("count"), **dh.health()}


def list_tool_names():
    import asyncio
    return [t.name for t in asyncio.run(mcp.list_tools())]


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
