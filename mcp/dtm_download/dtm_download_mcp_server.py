"""DTM Download MCP (FastMCP, streamable HTTP, 127.0.0.1:8791).

Downloads DTP build artifacts (installer MSI, sample zips, datatype CSVs, doc HTML) from Artifactory.
Read-only w.r.t. the rest of the system -- it only writes into its own download_path -- so it needs no
confirm-token gating and runs UNELEVATED (RunLevel Limited scheduled task, like windows_obsidian).
Goose connects via type: streamable_http, uri: http://127.0.0.1:8791/mcp.
"""
import anyio
from mcp.server.fastmcp import FastMCP

import artifactory
import config

mcp = FastMCP("dtm_download", host="127.0.0.1", port=8791)

_CFG = None
_LIMITER = anyio.CapacityLimiter(2)   # cap concurrent downloads (network + disk heavy)


def cfg():
    global _CFG
    if _CFG is None:
        _CFG = config.load()
    return _CFG


def _download_build_sync(channel, build_id):
    token = config.get_token()
    try:
        return artifactory.download_build(cfg(), token, channel=channel or None, build_id=build_id or None)
    except artifactory.ArtifactoryError as e:
        return {"error": str(e)}


@mcp.tool()
async def dtm_download_build(channel: str = "", build_id: str = "") -> dict:
    """Download a DTP build from Artifactory: resolves the latest build in `channel` (Daily|Formal) if
    `build_id` is omitted, downloads + checksum-verifies the installer/sample zips and datatype CSVs,
    extracts the zips, and returns {download_path, msi_path, build_id, zips, extracted, csv_files}.
    The Artifactory token comes from the DTM_DOWNLOAD_ARTIFACTORY_TOKEN environment variable -- never
    pass it as an argument. Pass the returned msi_path to dtm_deploy's dtm_install tool to install it."""
    return await anyio.to_thread.run_sync(_download_build_sync, channel, build_id, limiter=_LIMITER)


@mcp.tool()
def dtm_list_builds(channel: str = "Daily", limit: int = 10) -> dict:
    """List available build folder names under DTP/<channel> in Artifactory (read-only query, newest
    first is not guaranteed -- sort client-side if needed)."""
    c = cfg()
    token = config.get_token()
    if not token:
        return {"error": "no Artifactory token set (DTM_DOWNLOAD_ARTIFACTORY_TOKEN)"}
    try:
        names = artifactory.list_build_children(c["artifactory_base_url"], c["repo"], channel, token,
                                                 timeout=c.get("connect_timeout_seconds", 15))
    except artifactory.ArtifactoryError as e:
        return {"error": str(e)}
    return {"channel": channel, "count": min(len(names), limit), "builds": sorted(names)[-limit:]}


@mcp.tool()
def dtm_download_health() -> dict:
    """Server health: whether the Artifactory token is set (not its value), resolved download_path +
    existence, and the configured base URL/repo. Check this first when a download fails."""
    c = cfg()
    return {
        "token_present": c["_resolved"]["token_present"]["resolved"],
        "download_path": c["_resolved"]["download_path"]["resolved"],
        "download_path_exists": c["_resolved"]["download_path"]["exists"],
        "artifactory_base_url": c["artifactory_base_url"],
        "repo": c["repo"],
        "default_channel": c.get("default_channel"),
    }


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
