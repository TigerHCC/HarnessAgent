import curated


def test_security_ids_present():
    assert 4624 in curated.SECURITY_EVENT_IDS and 4625 in curated.SECURITY_EVENT_IDS


def test_error_summary_shape():
    res = curated.error_summary(hours=720, top_n=5)
    assert "groups" in res and isinstance(res["groups"], list)
    if res["groups"]:
        g = res["groups"][0]
        assert {"provider", "event_id", "count"} <= set(g)
        assert g["count"] >= 1


def test_user_activity_admin_gated_or_events():
    res = curated.user_activity(hours=720, max=10)
    assert ("events" in res) or (res.get("is_admin") is False)
