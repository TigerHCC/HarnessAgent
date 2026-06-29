"""Read-only Event Log API spike. Confirms Evt API usage + message formatting."""
import win32evtlog
import xml.etree.ElementTree as ET

NS = "{http://schemas.microsoft.com/win/2004/08/events/event}"


def main():
    flags = win32evtlog.EvtQueryChannelPath | win32evtlog.EvtQueryReverseDirection
    xpath = "*[System[(Level=2 or Level=1)]]"  # errors / critical
    h = win32evtlog.EvtQuery("System", flags, xpath)
    got = 0
    while got < 3:
        try:
            evts = win32evtlog.EvtNext(h, 3)
        except Exception as e:
            print("EvtNext stop:", repr(e))
            break
        if not evts:
            break
        for e in evts:
            xml = win32evtlog.EvtRender(e, win32evtlog.EvtRenderEventXml)
            prov = ET.fromstring(xml).find(f"{NS}System/{NS}Provider").get("Name")
            eid = ET.fromstring(xml).find(f"{NS}System/{NS}EventID").text
            print(f"\n--- provider={prov} event_id={eid} ---")
            try:
                meta = win32evtlog.EvtOpenPublisherMetadata(prov, None, 0, 0)
                msg = win32evtlog.EvtFormatMessage(meta, e, 0, None, win32evtlog.EvtFormatMessageEvent)
                print("MESSAGE:", (msg or "")[:200].replace("\n", " "))
            except Exception as ex:
                print("FormatMessage err:", repr(ex)[:120])
            got += 1
            if got >= 3:
                break


if __name__ == "__main__":
    main()
