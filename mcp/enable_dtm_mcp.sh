#!/usr/bin/env bash
# Enable the PersonalKnowledge DTM Knowledge Agent MCP as a Goose extension `dtm`.
#
# Mirror of enable_pk_mcp.sh. Adds (or, with DTM_MCP_REPLACE=1, re-points) the
# `dtm` extension in the LIVE Goose config (~/.config/goose/config.yaml), then
# validates with `goose info -v` and AUTO-RESTORES the previous config on failure.
# The harness keeps that config READ-ONLY (self-strip guard, see
# ../docs/install_results.md); this script unlocks -> edits -> re-locks ->
# refreshes the .bak. Idempotent (no-op if `dtm` already present, unless REPLACE).
#
# Default transport: streamable_http -> ${DTM_MCP_URI:-http://127.0.0.1:8765/mcp}
#   DTM warms a reranker + routing centroids, so a warm HTTP proxy beats per-call
#   stdio (~110s vs ~167s cold). Keep the proxy up: the dtm-mcp-proxy systemd
#   service on :8765 (PersonalKnowledge/dtm_agent/dtm-mcp-proxy.service).
#
# Self-contained stdio instead (no proxy/port/sudo) via mcp/qb10_dtm_mcp.sh:
#   DTM_MCP_STDIO=1 ./enable_dtm_mcp.sh
#
# Switch an already-enabled dtm to a different transport:
#   DTM_MCP_REPLACE=1 DTM_MCP_STDIO=1 ./enable_dtm_mcp.sh     # http -> stdio
#   DTM_MCP_REPLACE=1 ./enable_dtm_mcp.sh                     # (back to) http
set -euo pipefail
export GOOSE_TELEMETRY_ENABLED=false   # privacy: this script runs `goose info`; never upload telemetry

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CFG="${GOOSE_CONFIG:-$HOME/.config/goose/config.yaml}"
DTM_LAUNCHER="$HERE/qb10_dtm_mcp.sh"
BLOCK=""; PRE=""
trap 'rm -f "$BLOCK" "$PRE" 2>/dev/null || true' EXIT

[ -f "$CFG" ] || { echo "ERROR: goose config not found at $CFG (is goose installed/configured?)" >&2; exit 1; }
grep -qE '^extensions:[[:space:]]*$' "$CFG" || { echo "ERROR: no 'extensions:' section in $CFG" >&2; exit 1; }

EXISTS=0; grep -qE '^[[:space:]]{2}dtm:[[:space:]]*$' "$CFG" && EXISTS=1
if [ "$EXISTS" = 1 ] && [ -z "${DTM_MCP_REPLACE:-}" ]; then
  echo "[enable_dtm_mcp] 'dtm' extension already present in $CFG -- nothing to do."
  echo "[enable_dtm_mcp] (to re-point its transport, re-run with DTM_MCP_REPLACE=1)"
  exit 0
fi

# build the dtm extension block (2-space indent to sit directly under 'extensions:')
BLOCK="$(mktemp)"
if [ -n "${DTM_MCP_STDIO:-}" ]; then
  chmod +x "$DTM_LAUNCHER" 2>/dev/null || true
  [ -x "$DTM_LAUNCHER" ] || { echo "ERROR: launcher not executable: $DTM_LAUNCHER" >&2; exit 1; }
  cat > "$BLOCK" <<EOF
  dtm:
    type: stdio
    bundled: false
    name: dtm
    enabled: true
    cmd: ${DTM_LAUNCHER}
    args: []
    env_keys: []
    timeout: 600
    description: PersonalKnowledge DTM Knowledge Agent (telemetry/triage/plugin/hw-spec RAG over ChromaDB) via stdio
EOF
  echo "[enable_dtm_mcp] transport: stdio -> ${DTM_LAUNCHER}"
else
  DTM_URI="${DTM_MCP_URI:-http://127.0.0.1:8765/mcp}"
  cat > "$BLOCK" <<EOF
  dtm:
    type: streamable_http
    bundled: false
    name: dtm
    enabled: true
    uri: ${DTM_URI}
    headers: {}
    env_keys: []
    timeout: 600
    description: PersonalKnowledge DTM Knowledge Agent (telemetry/triage/plugin/hw-spec RAG over ChromaDB) via streamable_http
EOF
  echo "[enable_dtm_mcp] transport: streamable_http -> ${DTM_URI}"
fi

# snapshot for auto-restore, then unlock -> (remove old dtm if replacing) -> insert -> re-lock
PRE="$(mktemp)"; cp -f "$CFG" "$PRE"
chmod u+w "$CFG"
if [ "$EXISTS" = 1 ]; then
  # drop the existing dtm block (its 2-space key line + 4-space children), then re-insert
  awk '
    /^  dtm:[[:space:]]*$/ { indtm=1; next }
    indtm && /^  [^ ]/ { indtm=0 }
    indtm && /^[^ ]/   { indtm=0 }
    indtm { next }
    { print }
  ' "$PRE" | sed "/^extensions:[[:space:]]*$/r $BLOCK" > "$CFG"
  echo "[enable_dtm_mcp] re-pointed existing 'dtm' block."
else
  sed "/^extensions:[[:space:]]*$/r $BLOCK" "$PRE" > "$CFG"
fi
chmod a-w "$CFG"

# validate: dtm present (exactly once) AND goose can still load the config
if [ "$(grep -cE '^[[:space:]]{2}dtm:[[:space:]]*$' "$CFG")" = "1" ] && goose info -v >/dev/null 2>&1; then
  cp -f "$CFG" "$CFG.bak"
  echo "[enable_dtm_mcp] enabled 'dtm' and validated (goose info -v OK)."
  echo "[enable_dtm_mcp] backup refreshed: $CFG.bak ; config left read-only: $(ls -l "$CFG" | awk '{print $1}')"
  [ -z "${DTM_MCP_STDIO:-}" ] && echo "[enable_dtm_mcp] NOTE: streamable_http needs the proxy up (dtm-mcp-proxy :8765)."
  echo "[enable_dtm_mcp] restart any running goose session/web to pick up changes."
else
  echo "[enable_dtm_mcp] VALIDATION FAILED -- restoring previous config from snapshot." >&2
  chmod u+w "$CFG"; cp -f "$PRE" "$CFG"; chmod a-w "$CFG"
  exit 1
fi
