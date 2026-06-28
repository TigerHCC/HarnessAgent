#!/usr/bin/env bash
# Launch the Goose Harness web UI (stdlib Python HTTP bridge -> `goose run`).
# Open http://<this-box-ip>:8799 from any machine on the LAN.
#
#   ./serve_web.sh                          # bind 0.0.0.0:8799 (LAN), no token
#   GOOSE_WEB_TOKEN=mysecret ./serve_web.sh # require a token (recommended for LAN)
#   GOOSE_WEB_HOST=127.0.0.1 ./serve_web.sh # local-only
#   GOOSE_WEB_PORT=9000 ./serve_web.sh
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PATH="$HOME/.local/bin:$PATH"
command -v goose >/dev/null 2>&1 || { echo "goose not found on PATH (~/.local/bin)"; exit 1; }
exec python3 "$HERE/server.py"
