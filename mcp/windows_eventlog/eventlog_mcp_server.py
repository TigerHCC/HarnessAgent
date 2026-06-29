"""Windows Event Log MCP server (FastMCP, streamable HTTP, 127.0.0.1:8778).

Run ELEVATED for the Security log (System/Application work either way). Goose connects via
type: streamable_http, uri: http://127.0.0.1:8778/mcp  (Goose 1.39 dropped SSE).
"""
from typing import Optional
from mcp.server.fastmcp import FastMCP

import eventlog_reader as reader
import curated

mcp = FastMCP("eventlog", host="127.0.0.1", port=8778)


@mcp.tool()
def list_channels(filter: str = "", limit: int = 100) -> dict:
    """List available Event Log channels (optionally filtered by substring)."""
    return reader.list_channels(filter=filter, limit=limit)


@mcp.tool()
def query_events(channel: str = "System", level: Optional[int] = None, event_ids: Optional[list] = None,
                 provider: Optional[str] = None, hours: int = 24, keyword: Optional[str] = None,
                 max: int = 50) -> dict:
    """Query events from a channel with filters.

    level: 1=Critical 2=Error 3=Warning 4=Information. event_ids: list of ints. provider: exact name.
    hours: lookback window. keyword: client-side filter on message/data. max: cap.
    """
    return reader.query_events(channel=channel, level=level, event_ids=event_ids,
                               provider=provider, hours=hours, keyword=keyword, max=max)


@mcp.tool()
def error_summary(hours: int = 24, channels: Optional[list] = None, include_warning: bool = False,
                  top_n: int = 20) -> dict:
    """System errors: Error/Critical events grouped by (provider, event_id) with counts + latest message."""
    chans = tuple(channels) if channels else ("System", "Application")
    return curated.error_summary(hours=hours, channels=chans, include_warning=include_warning, top_n=top_n)


@mcp.tool()
def user_activity(hours: int = 24, max: int = 100) -> dict:
    """User behavior: curated Security logon/logoff/account events (needs admin)."""
    return curated.user_activity(hours=hours, max=max)


@mcp.tool()
def get_event(channel: str, record_id: int) -> dict:
    """Full detail (message + EventData + raw XML) of one event by record id."""
    return reader.get_event(channel, record_id)


@mcp.tool()
def eventlog_health() -> dict:
    """Admin status, Security readability, total channels, sample counts."""
    return reader.health()


def list_tool_names():
    import asyncio
    return [t.name for t in asyncio.run(mcp.list_tools())]


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
