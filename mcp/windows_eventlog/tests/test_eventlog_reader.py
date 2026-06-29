import eventlog_reader as r


def test_query_system_errors_shape():
    res = r.query_events(channel="System", level=2, hours=720, max=5)
    assert "events" in res, res
    if res["events"]:
        e = res["events"][0]
        assert isinstance(e["event_id"], int)
        assert e["channel"] == "System"
        assert "time" in e and "provider" in e
        assert isinstance(e["data"], dict)


def test_list_channels_includes_core():
    res = r.list_channels(filter="", limit=2000)
    assert "channels" in res
    assert "System" in res["channels"] and "Application" in res["channels"]


def test_health_shape():
    h = r.health()
    for k in ("is_admin", "security_readable", "channels_total"):
        assert k in h


def test_build_xpath():
    x = r._build_xpath(level=[1, 2], event_ids=[4624], provider="Foo", hours=24)
    assert "Level=1 or Level=2" in x and "EventID=4624" in x
    assert "Provider[@Name='Foo']" in x and "timediff(@SystemTime) <= 86400000" in x
