# Event Log API spike — confirmed (2026-06-29)

pywin32 `win32evtlog`, modern Evt API. ~1290 channels.

## Query / render — confirmed
- `flags = EvtQueryChannelPath | EvtQueryReverseDirection` (newest first).
- `h = EvtQuery(channel, flags, xpath)` — xpath is the structured query, e.g.
  `*[System[(Level=2 or Level=1) and TimeCreated[timediff(@SystemTime) <= 86400000]]]`.
- `EvtNext(h, count)` → list of event handles; **raises** `pywintypes.error`
  (ERROR_NO_MORE_ITEMS) when exhausted → wrap in try/except and break.
- `EvtRender(evt, EvtRenderEventXml)` → event XML string. Parse with ElementTree using
  namespace `{http://schemas.microsoft.com/win/2004/08/events/event}`.

## Human-readable message — CORRECTED signatures
The plan's 5-arg `EvtFormatMessage(meta, e, 0, None, Flags)` is WRONG for this pywin32.
Correct:
- `meta = EvtOpenPublisherMetadata(provider)`   # max 2 args; `(provider, None)` also ok.
  (3+ positional args → `TypeError: int can not be converted to Unicode`.)
- `msg = EvtFormatMessage(meta, evt, EvtFormatMessageEvent)`   # **3 args** (max 4).
  Returns the localized description (zh-TW on this box), e.g.
  "伺服器 {GUID} 沒有在指定的逾時內登錄 DCOM。"
- Wrap both in try/except → fall back to EventData rendering when a provider has no metadata.

## Reader `_format_message` (use this):
```python
def _format_message(evt, provider):
    if not provider:
        return None
    try:
        meta = win32evtlog.EvtOpenPublisherMetadata(provider)
        msg = win32evtlog.EvtFormatMessage(meta, evt, win32evtlog.EvtFormatMessageEvent)
        return (msg or "").strip() or None
    except Exception:
        return None
```
