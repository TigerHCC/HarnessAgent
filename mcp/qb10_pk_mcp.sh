#!/usr/bin/env bash
# Launch the PersonalKnowledge (PK) KB MCP server as a stdio MCP server for Goose.
#
# Goose runs this as the `pk` stdio extension. Tools (serverInfo personal-kb):
#   search_kb      - semantic search over the pk_* ChromaDB collections (outlook/
#                    jira/confluence/onenote/markdown/summaries); embeds via vLLM :8001
#   get_document   - full text of a KB markdown file (sandboxed to kb/ + DTMKnowledge/)
#   list_sources   - per-source chunk counts
# PK is STATELESS retrieval (no rerank, no LLM generation), so first-call latency is
# just one embedding call -- stdio is a good fit (no warm proxy needed, unlike dtm).
#
# Canonical tree = GB10-workspace/pk-mcp -- the SAME checkout the live pk-mcp-proxy
# systemd unit serves on :8766, so the stdio path and the streamable_http path
# resolve to the same data/chromadb. Override PK_ROOT if your checkout lives
# elsewhere. Two hard requirements (see $PK_ROOT/docs/PK_MCP.md), both handled here:
#   1. Run with the project venv interpreter (has chromadb). A bare `python` lets the
#      repo's ./chromadb/ data dir shadow the chromadb package and retrieval fails.
#   2. Run with cwd = project root, so ./chromadb resolves.
# This wrapper does NOT modify that tree.
set -euo pipefail
PK_ROOT="${PK_ROOT:-/home/nvidia/Downloads/GB10-workspace/pk-mcp}"
cd "$PK_ROOT"
exec "$PK_ROOT/venv/bin/python" kb_query.py --mcp-mode
