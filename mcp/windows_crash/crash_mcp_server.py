"""Windows Crash / WER analysis MCP server (FastMCP, streamable HTTP, 127.0.0.1:8779).

Run ELEVATED for kernel dumps (C:\\Windows\\Minidump, MEMORY.DMP) and some WER ReportQueue folders;
the machine ReportArchive is usually readable either way. Goose connects via
type: streamable_http, uri: http://127.0.0.1:8779/mcp  (Goose 1.39 dropped SSE).
"""
from typing import Optional
from mcp.server.fastmcp import FastMCP

import wer_reader
import dump_reader

mcp = FastMCP("crash", host="127.0.0.1", port=8779)


@mcp.tool()
def crash_summary(days: int = 30, top_n: int = 20, include_noncrash: bool = False) -> dict:
    """Headline view: recent crashes/hangs from the WER store, deduped into buckets.

    Groups by (event_type, app, faulting_module, exception_code) with counts + first/last seen.
    Also reports available crash-dump counts so you know whether there's a BSOD dump to analyze.
    include_noncrash also folds in install/telemetry failures (default: real crashes/hangs only).
    """
    try:
        res = wer_reader.crash_summary(days=days, top_n=top_n, include_noncrash=include_noncrash)
        if "error" not in res:
            res["dumps"] = dump_reader.list_dumps().get("counts")
        return res
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def list_crashes(days: int = 30, event_type: Optional[str] = None, app: Optional[str] = None,
                 max: int = 50) -> dict:
    """Flat, newest-first list of parsed WER crash/hang reports.

    event_type: substring filter (e.g. 'APPCRASH', 'BEX64', 'AppHang'). app: substring (e.g. 'chrome').
    """
    return wer_reader.list_crashes(days=days, event_type=event_type, app=app, max=max)


@mcp.tool()
def get_crash(report_id: str) -> dict:
    """Full parsed Report.wer for one report (signatures, typed fields, OS info, attached files).

    report_id is the report folder name returned by crash_summary/list_crashes.
    """
    try:
        return wer_reader.get_crash(report_id)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def list_dumps() -> dict:
    """Enumerate crash dumps: C:\\Windows\\Minidump\\*.dmp, MEMORY.DMP, LiveKernelReports (size + time)."""
    try:
        return dump_reader.list_dumps()
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def analyze_dump(path: str, use_cdb: bool = False) -> dict:
    """Decode one dump. Kernel (BSOD) -> bugcheck code/name/params + build; user -> exception + modules.

    path must be under the OS dump dirs or the WER store. use_cdb=True runs !analyze -v if cdb.exe is
    installed (slow: downloads symbols; cached). Header parse works with no debugger installed.
    """
    return dump_reader.analyze_dump(path, use_cdb=use_cdb)


@mcp.tool()
def crash_health() -> dict:
    """Admin status, WER store paths + report counts, dump counts, cdb availability, table sizes."""
    try:
        h = wer_reader.health()
        h["dumps"] = dump_reader.health()
        return h
    except Exception as e:
        return {"error": str(e)}


def list_tool_names():
    """Test helper: names of registered tools."""
    import asyncio
    return [t.name for t in asyncio.run(mcp.list_tools())]


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
