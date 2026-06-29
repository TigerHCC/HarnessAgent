#!/usr/bin/env bash
# Launch the PersonalKnowledge DTM Knowledge Agent as an MCP stdio server for Goose.
#
# Goose runs this as the `dtm` stdio extension. The DTM server has two hard
# requirements (see PersonalKnowledge/dtm_agent/SETUP.md):
#   1. Run with the project venv interpreter (has chromadb installed). A bare
#      `python` makes the repo's chromadb/ data dir shadow the package.
#   2. Run with cwd = project root, so ./chromadb and DTMKnowledge/ resolve.
# This wrapper encapsulates both. It lives in HarnessAgent/ and does NOT modify
# the PersonalKnowledge repo.
set -euo pipefail
PK_ROOT="/home/nvidia/Downloads/PersonalKnowledge"
cd "$PK_ROOT"
exec "$PK_ROOT/venv/bin/python" -m dtm_agent mcp
