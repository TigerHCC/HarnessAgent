"""Windows process-inspection MCP server (FastMCP, streamable HTTP, 127.0.0.1:8785).

Process Explorer-style answers: who locks a file, why a process is hung (wait chains / deadlock),
loaded DLLs (+ signatures), handle-leak candidates. Run ELEVATED for full cross-process detail.
Goose connects via type: streamable_http, uri: http://127.0.0.1:8785/mcp.
"""
from typing import Optional
from mcp.server.fastmcp import FastMCP

import native
import procdetail as pd

mcp = FastMCP("procinspect", host="127.0.0.1", port=8785)


@mcp.tool()
def who_locks(path: str) -> dict:
    """Which processes currently have this file/directory OPEN (the #1 'can't delete/update' question).

    Uses the Restart Manager API. path = a file or directory path.
    """
    try:
        return native.who_locks(path)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def wait_chain(pid: Optional[int] = None, tid: Optional[int] = None) -> dict:
    """Why is a process/thread HUNG: the wait chain (Thread -> lock -> owning thread) + deadlock flag.

    Give a pid (checks all its threads) or a specific tid. Reports dependency chains and whether a
    deadlock cycle was detected.
    """
    try:
        return native.wait_chain(pid=pid, tid=tid)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def process_detail(pid: int) -> dict:
    """Deep detail for one process: exe, cmdline, parent, user, handles, threads, memory, cpu, files, conns."""
    try:
        return pd.process_detail(pid)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def loaded_modules(pid: int, filter: Optional[str] = None, check_signatures: bool = False,
                   max: int = 300) -> dict:
    """DLLs/modules loaded into a process. filter = substring; check_signatures verifies Authenticode
    (slower) and lists any unsigned/untrusted -- e.g. an injected or hijacked DLL in a misbehaving app."""
    try:
        return pd.loaded_modules(pid, filter=filter, check_signatures=check_signatures, max=max)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def top_handle_users(n: int = 15) -> dict:
    """Processes ranked by open-handle count -- the leak-candidate view (a process with a runaway handle count)."""
    try:
        return pd.top_handle_users(n=n)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def find_process(name: str = "", max: int = 50) -> dict:
    """Find processes by name substring, with pid / handle count / thread count / user."""
    try:
        return pd.find_process(name=name, max=max)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def procinspect_health() -> dict:
    """Admin status, psutil availability, process count."""
    try:
        return pd.health()
    except Exception as e:
        return {"error": str(e)}


def list_tool_names():
    import asyncio
    return [t.name for t in asyncio.run(mcp.list_tools())]


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
