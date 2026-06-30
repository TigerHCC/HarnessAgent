#!/usr/bin/env bash
# Launch the DTM Knowledge Agent as an MCP stdio server for Goose.
#
# Goose runs this as the `dtm` stdio extension. The DTM server has two hard
# requirements (see $PK_ROOT/dtm_agent/SETUP.md):
#   1. Run with the project venv interpreter (has chromadb installed). A bare
#      `python` makes the repo's chromadb/ data dir shadow the package.
#   2. Run with cwd = project root, so ./chromadb and DTMKnowledge/ resolve.
#
# Canonical tree = GB10-workspace/dtm-agent -- the SAME checkout the live
# dtm-mcp-proxy systemd unit serves on :8765, so the stdio path and the
# streamable_http path resolve to the same data/chromadb. Override PK_ROOT if
# your checkout lives elsewhere. This wrapper does NOT modify that tree.
set -euo pipefail
PK_ROOT="${PK_ROOT:-/home/nvidia/Downloads/GB10-workspace/dtm-agent}"
cd "$PK_ROOT"
exec "$PK_ROOT/venv/bin/python" -m dtm_agent mcp
