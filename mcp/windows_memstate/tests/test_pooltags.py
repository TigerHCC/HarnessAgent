import pooltags


def test_describe():
    assert pooltags.describe("EtwB") and "ETW" in pooltags.describe("EtwB")
    # space-padded lookup
    assert pooltags.describe("File") is not None
    assert pooltags.describe("ZZZZ") is None
    assert pooltags.describe(None) is None


def test_tag_driver_validation():
    assert "error" in pooltags.tag_driver("toolong")
    assert "error" in pooltags.tag_driver("")


def test_tag_driver_scan_real():
    # scan for a very common tag; result shape must be well-formed (matches may be 0+)
    r = pooltags.tag_driver("Ntfn")
    assert "drivers" in r and isinstance(r["drivers"], list)
    assert "driver_count" in r


def test_tag_driver_uniform_shape():
    # fresh and cached calls must return the SAME key set
    r1 = pooltags.tag_driver("MmSt")   # first call: fresh (cached False)
    r2 = pooltags.tag_driver("MmSt")   # second call: cached True
    assert set(r1.keys()) == set(r2.keys())
    for k in ("tag", "description", "drivers", "driver_count", "scanned_drivers", "cached", "note"):
        assert k in r1 and k in r2
    assert r1["cached"] is False and r2["cached"] is True


def test_tag_driver_does_not_cache_on_missing_dir(monkeypatch):
    monkeypatch.setattr(pooltags, "DRIVERS_DIR", "C:\\no\\such\\dir\\x")
    pooltags._scan_cache.clear()
    r = pooltags.tag_driver("Zqxw")
    assert r["drivers"] == [] and r["cached"] is False
    # a failed/skipped scan must NOT be cached (so it can retry later)
    assert "Zqxw".ljust(4) not in pooltags._scan_cache
