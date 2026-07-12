"""Command classification + confirm-token logic for the dtmsdk MCP. Pure: no I/O, no subprocess.

A command is either on its util's SAFE allowlist (run directly) or it is gated (dangerous or
unrecognised -> the caller must supply a confirm token bound to the exact util+command+args).
Classification is derived from each command's documented SDK method, not its name.
"""
import hashlib
import json
import re

TOKEN_TTL_SECONDS = 120
_CMD_RE = re.compile(r"^[a-z0-9][a-z0-9 -]*$")

UTILS = {
    "dtmutil": [
        "configure-orchestrator", "apply-app-configuration", "clear-app-configuration",
        "validate-app-configuration", "workflow start", "workflow status",
        "workflow retrieve collection", "workflow retrieve analysis", "workflow retrieve alert",
        "workflow cancel", "workflow history", "bundle-transmission-status",
        "bundle-transmission-date-range", "retrieve-bundle-id", "invoke-emergency",
        "configure-proxy", "reset-proxy",
    ],
    "instrumentation": [
        "collect", "periodic-collect", "subscribe", "retrieve", "client-retrieve", "retrieve-file",
        "retrieve-requests", "get-commodity", "set-commodity", "subscribe-commodity", "metadata",
        "enable-datatype", "reset-datatype-state", "emit-custom-software-telemetry-event", "unregister",
    ],
    "analytics": [
        "custom-analysis", "daily-analysis", "weekly-analysis", "default-alert", "custom-alert",
        "register-alert", "subscribe", "create-alert-subscriptions", "retrieve-alert-subscriptions",
        "listen-alert-subscriptions", "retrieve-analysis", "retrieve-alert", "retrieve-alerts",
        "retrieve-client-alerts", "retrieve-custom", "metadata", "temporary-enable",
        "retrieve-temporary-enabling-requests", "unregister",
    ],
    "transmission": [
        "collect-transmit", "retrieve-transmit", "periodic-transmit", "file-upload",
        "transmission-status", "cancel", "unregister",
    ],
    "platinum": [
        "platinum-event", "platinum-upload", "platinum-heartbeat", "platinum-ping",
        "transmission-status", "configure-proxy", "reset-proxy",
    ],
}

SAFE = {
    "dtmutil": {
        "validate-app-configuration", "workflow status", "workflow retrieve collection",
        "workflow retrieve analysis", "workflow retrieve alert", "workflow history",
        "bundle-transmission-status", "bundle-transmission-date-range", "retrieve-bundle-id",
    },
    "instrumentation": {"retrieve", "client-retrieve", "retrieve-requests", "get-commodity", "metadata"},
    "analytics": {
        "retrieve-analysis", "retrieve-alert", "retrieve-alerts", "retrieve-client-alerts",
        "retrieve-custom", "retrieve-alert-subscriptions", "retrieve-temporary-enabling-requests",
        "metadata",
    },
    "transmission": {"transmission-status"},
    "platinum": {"transmission-status"},
}

# (util, command) -> category, for the 41 gated commands. Used only to word the confirmation preview.
_EGRESS = {
    ("dtmutil", "invoke-emergency"),
    ("instrumentation", "emit-custom-software-telemetry-event"),
    ("transmission", "collect-transmit"), ("transmission", "retrieve-transmit"),
    ("transmission", "periodic-transmit"), ("transmission", "file-upload"),
    ("platinum", "platinum-event"), ("platinum", "platinum-upload"),
    ("platinum", "platinum-heartbeat"), ("platinum", "platinum-ping"),
}
_STATE = {
    ("dtmutil", "configure-orchestrator"), ("dtmutil", "apply-app-configuration"),
    ("dtmutil", "clear-app-configuration"), ("dtmutil", "configure-proxy"), ("dtmutil", "reset-proxy"),
    ("instrumentation", "set-commodity"), ("instrumentation", "enable-datatype"),
    ("instrumentation", "reset-datatype-state"), ("instrumentation", "unregister"),
    ("analytics", "register-alert"), ("analytics", "create-alert-subscriptions"),
    ("analytics", "temporary-enable"), ("analytics", "unregister"),
    ("transmission", "unregister"),
    ("platinum", "configure-proxy"), ("platinum", "reset-proxy"),
}
# everything gated and not egress/state is "action" (triggers work or does not terminate)


def validate_command(command):
    return bool(_CMD_RE.match(command or ""))


def is_safe(util, command):
    return command in SAFE.get(util, set())


def classify(util, command):
    if is_safe(util, command):
        return "safe"
    if command not in UTILS.get(util, []):
        return "unknown"
    if (util, command) in _EGRESS:
        return "egress"
    if (util, command) in _STATE:
        return "state"
    return "action"


def _digest(util, command, args):
    payload = "%s|%s|%s" % (util, command, json.dumps(list(args), separators=(",", ":")))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def make_token(util, command, args):
    return _digest(util, command, args)


def verify_token(util, command, args, token, *, now, issued_at):
    if not token or token != _digest(util, command, args):
        return False
    return (now - issued_at) <= TOKEN_TTL_SECONDS
