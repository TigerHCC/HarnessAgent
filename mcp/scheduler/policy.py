"""Confirm-token gating for the scheduler's mutating tools. A token is a digest bound to the action name
and its argument dict; single-use enforcement + TTL live in the server. Mirrors dtm_sdk/policy.py.
"""
import hashlib
import json

TOKEN_TTL_SECONDS = 120

MUTATING = {"sched_create", "sched_update", "sched_delete",
            "sched_pause", "sched_resume", "sched_run_now"}


def _digest(action, args):
    payload = "%s|%s" % (action, json.dumps(args, sort_keys=True, separators=(",", ":"),
                                            ensure_ascii=False))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def make_token(action, args):
    return _digest(action, args)


def verify_token(action, args, token, *, now, issued_at):
    if not token or token != _digest(action, args):
        return False
    return (now - issued_at) <= TOKEN_TTL_SECONDS
