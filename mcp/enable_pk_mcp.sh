#!/usr/bin/env bash
# Enable the PersonalKnowledge (PK) KB MCP as a Goose extension named `pk`.
#
# Adds a `pk` extension block to the LIVE Goose config (~/.config/goose/config.yaml),
# then validates it. The harness keeps that config READ-ONLY (the self-strip guard,
# see HarnessAgent/docs/install_results.md); this script briefly unlocks it, inserts
# the block, re-locks it, refreshes the .bak, and -- if goose can no longer load the
# config -- AUTO-RESTORES the previous version. Idempotent (re-running is a no-op).
#
# Default transport: stdio via mcp/pk_mcp.sh (self-contained; no proxy, no port, no
# sudo). PK is fast stateless retrieval, so stdio's per-spawn cost is one embed call.
#
# To use streamable_http instead (e.g. via the pk-mcp-proxy on :8766), set PK_MCP_URI:
#   PK_MCP_URI=http://127.0.0.1:8766/mcp ./enable_pk_mcp.sh
#
# Usage:
#   ./enable_pk_mcp.sh                 # enable pk over stdio
#   PK_MCP_URI=... ./enable_pk_mcp.sh  # enable pk over streamable_http
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CFG="${GOOSE_CONFIG:-$HOME/.config/goose/config.yaml}"
PK_LAUNCHER="$HERE/pk_mcp.sh"
BLOCK=""; PRE=""
trap 'rm -f "$BLOCK" "$PRE" 2>/dev/null || true' EXIT

[ -f "$CFG" ] || { echo "ERROR: goose config not found at $CFG (is goose installed/configured?)" >&2; exit 1; }

# idempotent: bail if a 'pk' extension already exists
if grep -qE '^[[:space:]]{2}pk:[[:space:]]*$' "$CFG"; then
  echo "[enable_pk_mcp] 'pk' extension already present in $CFG -- nothing to do."
  exit 0
fi
grep -qE '^extensions:[[:space:]]*$' "$CFG" || { echo "ERROR: no 'extensions:' section in $CFG" >&2; exit 1; }

# build the extension block (2-space indent to sit directly under 'extensions:')
BLOCK="$(mktemp)"
if [ -n "${PK_MCP_URI:-}" ]; then
  cat > "$BLOCK" <<EOF
  pk:
    type: streamable_http
    bundled: false
    name: pk
    enabled: true
    uri: ${PK_MCP_URI}
    headers: {}
    env_keys: []
    timeout: 600
    description: PersonalKnowledge KB MCP (search_kb / get_document / list_sources) via streamable_http
EOF
  echo "[enable_pk_mcp] transport: streamable_http -> ${PK_MCP_URI}"
else
  chmod +x "$PK_LAUNCHER" 2>/dev/null || true
  [ -x "$PK_LAUNCHER" ] || { echo "ERROR: launcher not executable: $PK_LAUNCHER" >&2; exit 1; }
  cat > "$BLOCK" <<EOF
  pk:
    type: stdio
    bundled: false
    name: pk
    enabled: true
    cmd: ${PK_LAUNCHER}
    args: []
    env_keys: []
    timeout: 600
    description: PersonalKnowledge KB MCP (search_kb / get_document / list_sources) via stdio
EOF
  echo "[enable_pk_mcp] transport: stdio -> ${PK_LAUNCHER}"
fi

# snapshot for auto-restore, then unlock -> insert after 'extensions:' -> re-lock
PRE="$(mktemp)"; cp -f "$CFG" "$PRE"
chmod u+w "$CFG"
sed "/^extensions:[[:space:]]*$/r $BLOCK" "$PRE" > "$CFG"
chmod a-w "$CFG"

# validate: pk present AND goose can still load the config
if grep -qE '^[[:space:]]{2}pk:[[:space:]]*$' "$CFG" && goose info -v >/dev/null 2>&1; then
  cp -f "$CFG" "$CFG.bak"
  echo "[enable_pk_mcp] enabled 'pk' and validated (goose info -v OK)."
  echo "[enable_pk_mcp] backup refreshed: $CFG.bak ; config left read-only: $(ls -l "$CFG" | awk '{print $1}')"
  echo "[enable_pk_mcp] restart any running goose session/web to pick up the new extension."
else
  echo "[enable_pk_mcp] VALIDATION FAILED -- restoring previous config from snapshot." >&2
  chmod u+w "$CFG"; cp -f "$PRE" "$CFG"; chmod a-w "$CFG"
  exit 1
fi
