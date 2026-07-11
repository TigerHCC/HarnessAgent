import importlib
import disk_health as dh


def test_valid_volume():
    assert dh._valid_volume("C:")
    assert not dh._valid_volume("C")


def test_as_list():
    assert dh._as_list(None) == []
    assert dh._as_list(5) == [5]
    assert dh._as_list([1, 2]) == [1, 2]


def test_volume_state_rejects_bad_volume():
    assert "error" in dh.volume_state(volume="nope")


def test_baseline_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("DISK_BASELINES", str(tmp_path / "bl.json"))
    importlib.reload(dh)
    monkeypatch.setattr(dh, "disk_health", lambda: {"count": 1, "disks": [
        {"device_id": 0, "wear_pct": 0, "temperature_c": 55, "read_errors": 0,
         "write_errors": 0, "power_on_hours": 100}]})
    saved = dh.health_baseline_save(name="b")
    assert saved["disk_count"] == 1
    # temperature rises + power-on hours advance
    monkeypatch.setattr(dh, "disk_health", lambda: {"count": 1, "disks": [
        {"device_id": 0, "wear_pct": 1, "temperature_c": 62, "read_errors": 0,
         "write_errors": 0, "power_on_hours": 130}]})
    d = dh.health_baseline_diff(name="b")
    assert d["deltas"]["0"]["temperature_c"]["delta"] == 7
    assert d["deltas"]["0"]["power_on_hours"]["delta"] == 30


def test_baseline_diff_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("DISK_BASELINES", str(tmp_path / "bl2.json"))
    importlib.reload(dh)
    assert "error" in dh.health_baseline_diff(name="nope")


def test_baseline_corrupt_does_not_raise(tmp_path, monkeypatch):
    p = tmp_path / "bl3.json"
    monkeypatch.setenv("DISK_BASELINES", str(p))
    importlib.reload(dh)
    p.write_text("[]", encoding="utf-8")                       # non-dict -> coerced to {}
    assert "error" in dh.health_baseline_diff(name="x")
    p.write_text('{"y": {"ts": "t"}}', encoding="utf-8")        # missing 'disks'
    assert "error" in dh.health_baseline_diff(name="y")
    # a non-dict per-disk value must not raise (base.get guard)
    p.write_text('{"z": {"ts": "t", "disks": {"0": null}}}', encoding="utf-8")
    monkeypatch.setattr(dh, "disk_health", lambda: {"count": 1, "disks": [{"device_id": 0}]})
    r = dh.health_baseline_diff(name="z")
    assert "deltas" in r and r["deltas"] == {}   # null disk skipped, no AttributeError


# --- real-data smoke --------------------------------------------------------
def test_disk_health_real():
    h = dh.disk_health()
    if "error" in h:
        return
    assert "disks" in h
    for d in h["disks"]:
        assert "device_id" in d and "health" in d


def test_volume_state_real():
    v = dh.volume_state("C:")
    assert v.get("volume") == "C:"
    assert "dirty" in v or "dirty_error" in v
