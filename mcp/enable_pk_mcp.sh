#!/usr/bin/env bash
# Enable the PersonalKnowledge (PK) KB MCP as a Goose extension named `pk`.
#
# Adds (or, with PK_MCP_REPLACE=1, re-points) the `pk` extension in the LIVE Goose
# config (~/.config/goose/config.yaml), then validates with `goose info -v` and
# AUTO-RESTORES the previous config on failure. The harness keeps that config
# READ-ONLY (self-strip guard, see ../docs/install_results.md); this script
# unlocks -> edits -> re-locks -> refreshes the .bak. Idempotent (no-op if `pk`
# already present, unless PK_MCP_REPLACE).
#
# Default transport: stdio via mcp/qb10_pk_mcp.sh (self-contained; no proxy/port/
# sudo). PK is fast stateless retrieval, so stdio's per-spawn cost is one embed call.
#
# Use streamable_http instead (via the pk-mcp-proxy on :8766) with PK_MCP_URI:
#   PK_MCP_URI=http://127.0.0.1:8766/mcp ./enable_pk_mcp.sh
#
# Switch an already-enabled pk to a different transport:
#   PK_MCP_REPLACE=1 PK_MCP_URI=http://127.0.0.1:8766/mcp ./enable_pk_mcp.sh   # stdio -> http
#   PK_MCP_REPLACE=1 ./enable_pk_mcp.sh                                        # (back to) stdio
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CFG="${GOOSE_CONFIG:-$HOME/.config/goose/config.yaml}"
PK_LAUNCHER="$HERE/qb10_pk_mcp.sh"
BLOCK=""; PRE=""
trap 'rm -f "$BLOCK" "$PRE" 2>/dev/null || true' EXIT

[ -f "$CFG" ] || { echo "ERROR: goose config not found at $CFG (is goose installed/configured?)" >&2; exit 1; }
grep -qE '^extensions:[[:space:]]*$' "$CFG" || { echo "ERROR: no 'extensions:' section in $CFG" >&2; exit 1; }

EXISTS=0; grep -qE '^[[:space:]]{2}pk:[[:space:]]*$' "$CFG" && EXISTS=1
if [ "$EXISTS" = 1 ] && [ -z "${PK_MCP_REPLACE:-}" ]; then
  echo "[enable_pk_mcp] 'pk' extension already present in $CFG -- nothing to do."
  echo "[enable_pk_mcp] (to re-point its transport, re-run with PK_MCP_REPLACE=1)"
  exit 0
fi

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

# snapshot for auto-restore, then unlock -> (remove old pk if replacing) -> insert -> re-lock
PRE="$(mktemp)"; cp -f "$CFG" "$PRE"
chmod u+w "$CFG"
if [ "$EXISTS" = 1 ]; then
  # drop the existing pk block (its 2-space key line + 4-space children), then re-insert
  awk '
    /^  pk:[[:space:]]*$/ { inpk=1; next }
    inpk && /^  [^ ]/ { inpk=0 }
    inpk && /^[^ ]/   { inpk=0 }
    inpk { next }
    { print }
  ' "$PRE" | sed "/^extensions:[[:space:]]*$/r $BLOCK" > "$CFG"
  echo "[enable_pk_mcp] re-pointed existing 'pk' block."
else
  sed "/^extensions:[[:space:]]*$/r $BLOCK" "$PRE" > "$CFG"
fi
chmod a-w "$CFG"

# validate: pk present exactly once AND goose can still load the config
if [ "$(grep -cE '^[[:space:]]{2}pk:[[:space:]]*$' "$CFG")" = "1" ] && goose info -v >/dev/null 2>&1; then
  cp -f "$CFG" "$CFG.bak"
  echo "[enable_pk_mcp] enabled 'pk' and validated (goose info -v OK)."
  echo "[enable_pk_mcp] backup refreshed: $CFG.bak ; config left read-only: $(ls -l "$CFG" | awk '{print $1}')"
  [ -n "${PK_MCP_URI:-}" ] && echo "[enable_pk_mcp] NOTE: streamable_http needs the pk-mcp-proxy up on that port (:8766)."
  echo "[enable_pk_mcp] restart any running goose session/web to pick up changes."
else
  echo "[enable_pk_mcp] VALIDATION FAILED -- restoring previous config from snapshot." >&2
  chmod u+w "$CFG"; cp -f "$PRE" "$CFG"; chmod a-w "$CFG"
  exit 1
fi
