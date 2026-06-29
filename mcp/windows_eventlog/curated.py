"""Curated Event Log scenarios: user behavior (Security) + system errors. No MCP deps."""
import eventlog_reader as reader

SECURITY_EVENT_IDS = {
    4624: "logon", 4625: "failed logon", 4634: "logoff", 4647: "user-initiated logoff",
    4648: "explicit-cred logon", 4672: "special privileges assigned", 4720: "account created",
    4722: "account enabled", 4723: "password change", 4724: "password reset",
    4725: "account disabled", 4726: "account deleted", 4728: "added to global group",
    4732: "added to local group", 4756: "added to universal group", 4740: "account lockout",
}


def user_activity(hours=24, max=100):
    if not reader.is_admin():
        return {"error": "Security log requires admin; start the server elevated.", "is_admin": False}
    res = reader.query_events(channel="Security", event_ids=list(SECURITY_EVENT_IDS),
                              hours=hours, max=max)
    for e in res.get("events", []):
        e["activity"] = SECURITY_EVENT_IDS.get(e.get("event_id"), "other")
    return {"window_hours": hours, **res}


def error_summary(hours=24, channels=("System", "Application"), include_warning=False, top_n=20):
    levels = [1, 2] + ([3] if include_warning else [])
    groups = {}
    for ch in channels:
        res = reader.query_events(channel=ch, level=levels, hours=hours, max=2000)
        for e in res.get("events", []):  # reverse-time order: first seen per key is latest
            key = (e.get("provider"), e.get("event_id"))
            g = groups.get(key)
            if g is None:
                g = groups[key] = {"provider": e.get("provider"), "event_id": e.get("event_id"),
                                   "level": e.get("level"), "channel": ch, "count": 0,
                                   "latest_time": e.get("time"), "latest_message": e.get("message")}
            g["count"] += 1
    ranked = sorted(groups.values(), key=lambda x: x["count"], reverse=True)[:int(top_n)]
    return {"window_hours": hours, "channels": list(channels), "groups": ranked}
