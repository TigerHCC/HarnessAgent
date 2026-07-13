"""Confirm-token logic for the obsidian write tools (create/update). Pure: no I/O.

A write is gated -- the first call returns a preview + a token bound to a hash of the exact
op+path+mode+content; the caller must call again with that token. Single-use (the server pops it),
TTL-limited. A token for one write cannot authorize a different one.
"""
import hashlib
import json

TOKEN_TTL_SECONDS = 120


def _digest(op, path, mode, content):
    payload = json.dumps([op, path, mode, content], separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def make_token(op, path, mode, content):
    return _digest(op, path, mode, content)


def verify_token(op, path, mode, content, token, *, now, issued_at, ttl=TOKEN_TTL_SECONDS):
    if not token or token != _digest(op, path, mode, content):
        return False
    return (now - issued_at) <= ttl
