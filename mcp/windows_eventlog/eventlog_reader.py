"""Windows Event Log reader via the pywin32 modern Evt API. No MCP deps.

API signatures confirmed by spike (SPIKE_NOTES.md):
- EvtOpenPublisherMetadata(provider)            # max 2 args
- EvtFormatMessage(meta, evt, EvtFormatMessageEvent)   # 3 args
- EvtNext raises on exhaustion -> try/except break.
"""
import ctypes
import xml.etree.ElementTree as ET
import win32evtlog

try:
    import win32security
except Exception:  # pragma: no cover
    win32security = None

NS = "{http://schemas.microsoft.com/win/2004/08/events/event}"
LEVELS = {1: "Critical", 2: "Error", 3: "Warning", 4: "Information", 5: "Verbose", 0: "Information"}


def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _build_xpath(level=None, event_ids=None, provider=None, hours=24):
    conds = []
    if hours:
        conds.append(f"TimeCreated[timediff(@SystemTime) <= {int(hours) * 3600 * 1000}]")
    if level is not None:
        levels = level if isinstance(level, (list, tuple)) else [level]
        conds.append("(" + " or ".join(f"Level={int(l)}" for l in levels) + ")")
    if event_ids:
        conds.append("(" + " or ".join(f"EventID={int(e)}" for e in event_ids) + ")")
    if provider:
        safe = str(provider).replace("'", "")
        conds.append(f"Provider[@Name='{safe}']")
    return "*" if not conds else "*[System[" + " and ".join(conds) + "]]"


def _sid_to_name(sid_str):
    if not sid_str or win32security is None:
        return sid_str
    try:
        sid = win32security.ConvertStringSidToSid(sid_str)
        name, domain, _ = win32security.LookupAccountSid(None, sid)
        return f"{domain}\\{name}" if domain else name
    except Exception:
        return sid_str


def _parse_event_xml(xml):
    root = ET.fromstring(xml)
    sysel = root.find(f"{NS}System")

    def find(tag):
        return sysel.find(f"{NS}{tag}") if sysel is not None else None

    prov = find("Provider")
    provider = prov.get("Name") if prov is not None else None
    eid_el, lvl_el, tc_el = find("EventID"), find("Level"), find("TimeCreated")
    rec_el, comp_el, sec_el = find("EventRecordID"), find("Computer"), find("Security")
    eid = eid_el.text if eid_el is not None else None
    lvl = lvl_el.text if lvl_el is not None else None
    rec = rec_el.text if rec_el is not None else None
    data = {}
    ed = root.find(f"{NS}EventData")
    if ed is not None:
        for i, d in enumerate(ed.findall(f"{NS}Data")):
            data[d.get("Name") or f"Data{i}"] = d.text
    return {
        "time": tc_el.get("SystemTime") if tc_el is not None else None,
        "provider": provider,
        "event_id": int(eid) if eid and str(eid).isdigit() else eid,
        "level": LEVELS.get(int(lvl) if lvl and str(lvl).isdigit() else -1, lvl),
        "record_id": int(rec) if rec and str(rec).isdigit() else rec,
        "computer": comp_el.text if comp_el is not None else None,
        "user": _sid_to_name(sec_el.get("UserID")) if sec_el is not None and sec_el.get("UserID") else None,
        "data": data,
    }


def _format_message(evt, provider):
    if not provider:
        return None
    try:
        meta = win32evtlog.EvtOpenPublisherMetadata(provider)
        msg = win32evtlog.EvtFormatMessage(meta, evt, win32evtlog.EvtFormatMessageEvent)
        return (msg or "").strip() or None
    except Exception:
        return None


def _iter(channel, xpath, max_n, include_xml=False):
    flags = win32evtlog.EvtQueryChannelPath | win32evtlog.EvtQueryReverseDirection
    h = win32evtlog.EvtQuery(channel, flags, xpath)
    out = []
    while len(out) < max_n:
        try:
            evts = win32evtlog.EvtNext(h, min(64, max_n - len(out)))
        except Exception:
            break  # ERROR_NO_MORE_ITEMS
        if not evts:
            break
        for e in evts:
            xml = win32evtlog.EvtRender(e, win32evtlog.EvtRenderEventXml)
            rec = _parse_event_xml(xml)
            rec["channel"] = channel
            rec["message"] = _format_message(e, rec["provider"]) or (
                "; ".join(f"{k}={v}" for k, v in rec["data"].items() if v) or None)
            if include_xml:
                rec["xml"] = xml
            out.append(rec)
            if len(out) >= max_n:
                break
    return out


def query_events(channel="System", level=None, event_ids=None, provider=None, hours=24, keyword=None, max=50):
    try:
        xpath = _build_xpath(level, event_ids, provider, hours)
        n = int(max)
        fetch = n * 5 if keyword else n
        if fetch < 1:
            fetch = 1
        rows = _iter(channel, xpath, fetch)
        if keyword:
            k = keyword.lower()
            rows = [r for r in rows if k in (r.get("message") or "").lower() or k in str(r.get("data")).lower()]
        rows = rows[:int(max)]
        return {"channel": channel, "count": len(rows), "events": rows}
    except Exception as e:
        return {"error": str(e), "channel": channel}


def list_channels(filter="", limit=100):
    names = []
    try:
        h = win32evtlog.EvtOpenChannelEnum()
        while True:
            try:
                name = win32evtlog.EvtNextChannelPath(h)
            except Exception:
                break
            if not name:
                break
            if filter.lower() in name.lower():
                names.append(name)
        names.sort()
        return {"count": min(len(names), int(limit)), "channels": names[:int(limit)]}
    except Exception as e:
        return {"error": str(e)}


def get_event(channel, record_id):
    try:
        xpath = f"*[System[(EventRecordID={int(record_id)})]]"
        rows = _iter(channel, xpath, 1, include_xml=True)
        return rows[0] if rows else {"error": "not found", "channel": channel, "record_id": record_id}
    except Exception as e:
        return {"error": str(e), "channel": channel}


def health():
    h = {"is_admin": is_admin(), "security_readable": False, "channels_total": None}
    try:
        h["channels_total"] = len(list_channels(limit=100000).get("channels", []))
    except Exception as e:
        h["channels_error"] = str(e)
    h["sample"] = {"System": query_events("System", hours=168, max=1).get("count"),
                   "Application": query_events("Application", hours=168, max=1).get("count")}
    sec = query_events("Security", hours=168, max=1)
    h["security_readable"] = "events" in sec and "error" not in sec
    if "error" in sec:
        h["security_error"] = sec["error"]
    return h
