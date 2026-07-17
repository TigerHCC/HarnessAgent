"""Confirm-token logic for the dtm_deploy gated tools. Pure: no I/O, no subprocess.

Every tool that mutates the system (uninstall, install, enable_user_consent, insert_test_plugin,
enable_transmission, run_pipeline) is gated: the first call with confirm_token="" returns a preview +
a token bound to a hash of the exact tool name + args; the caller must call again with that token to
actually execute. Single-use (the server pops it), TTL-limited. A token for one call cannot authorize
a different one (different tool name or different args).
"""
import hashlib
import json
import time

TOKEN_TTL_SECONDS = 120

GATED_TOOLS = {
    "dtm_uninstall", "dtm_enable_user_consent", "dtm_insert_test_plugin",
    "dtm_install", "dtm_enable_transmission", "dtm_run_pipeline",
}
SAFE_TOOLS = {"dtm_verify_collection", "dtm_verify_heartbeat", "dtm_deploy_health"}


def is_gated(tool):
    return tool in GATED_TOOLS


def _digest(tool, args):
    payload = json.dumps([tool, args], sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def make_token(tool, args):
    return _digest(tool, args)


def verify_token(tool, args, token, *, now, issued_at, ttl=TOKEN_TTL_SECONDS):
    if not token or token != _digest(tool, args):
        return False
    return (now - issued_at) <= ttl


class TokenStore:
    """In-memory single-use confirm-token store, keyed by token string."""

    def __init__(self, ttl=TOKEN_TTL_SECONDS):
        self._tokens = {}
        self.ttl = ttl

    def issue(self, tool, args):
        now = time.time()
        self._prune(now)
        token = make_token(tool, args)
        self._tokens[token] = (tool, args, now)
        return token

    def consume(self, tool, args, token):
        """Returns True and removes the token iff it matches tool+args and has not expired."""
        if not token:
            return False
        rec = self._tokens.get(token)
        if not rec or rec[0] != tool or rec[1] != args:
            return False
        now = time.time()
        if not verify_token(tool, args, token, now=now, issued_at=rec[2], ttl=self.ttl):
            self._tokens.pop(token, None)
            return False
        del self._tokens[token]
        return True

    def _prune(self, now):
        expired = [t for t, r in self._tokens.items() if now - r[2] > self.ttl]
        for t in expired:
            self._tokens.pop(t, None)
