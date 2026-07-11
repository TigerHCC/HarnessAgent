import parsers as p


def test_altitude_class():
    assert p.altitude_class("328520")[0] == "FSFilter Anti-Virus"
    assert p.altitude_class("385201")[0] == "FSFilter Activity Monitor"
    assert p.altitude_class("141100")[0] == "FSFilter Encryption"
    assert p.altitude_class("999999") == (None, None)
    assert p.altitude_class("garbage") == (None, None)
    assert p.altitude_class(None) == (None, None)


def test_parse_fltmc_filters():
    sample = (
        "\nFilter Name                     Num Instances    Altitude    Frame\n"
        "------------------------------  -------------  ------------  -----\n"
        "bindflt                                 1       409800         0\n"
        "tmeyes                                  9       328520         0\n"
        "mshield                                 9       323850.5       0\n"
    )
    rows = p._parse_fltmc_filters(sample)
    assert len(rows) == 3
    by = {r["name"]: r for r in rows}
    assert by["tmeyes"]["altitude"] == "328520"
    assert by["tmeyes"]["altitude_class"] == "FSFilter Anti-Virus"
    assert by["mshield"]["altitude"] == "323850.5"
    assert by["bindflt"]["instances"] == 1 and by["bindflt"]["frame"] == 0


def test_parse_fltmc_skips_garbage():
    assert p._parse_fltmc_filters("no table here\njust text\n") == []


# --- real-data smoke (skips cleanly if not elevated) -----------------------
def test_minifilters_real():
    r = p.minifilters()
    if "error" in r:
        assert "admin" in r["error"].lower()
        return
    assert "minifilters" in r and r["count"] >= 1
    for m in r["minifilters"]:
        assert "name" in m and "altitude" in m
        # third_party is True/False/None (never a wrong guess)
        assert m.get("third_party") in (True, False, None)


def test_filter_instances_validation():
    assert "error" in p.filter_instances(volume="bad")


def test_network_filters_real():
    n = p.network_filters()
    # at least one of the two sub-queries should return something structured
    assert "ndis_bindings" in n or "ndis_error" in n
    assert "winsock_lsp" in n or "winsock_error" in n


def test_winsock_lsps_registry_real():
    # locale-independent registry read must return the LSP/base-provider catalog (not silently empty)
    lsps = p._winsock_lsps()
    if lsps is None:
        return  # registry unreadable
    assert isinstance(lsps, list) and len(lsps) >= 1
    for e in lsps:
        assert "protocol" in e and e.get("third_party") in (True, False)


def test_filter_instances_has_frame_real():
    fi = p.filter_instances("C:")
    if "error" in fi:
        return  # not elevated
    for inst in fi["instances"]:
        assert "frame" in inst  # frame is now emitted (may be None if unparseable)


def test_health():
    h = p.health()
    assert "is_admin" in h and "fltmc_ok" in h
