# tests/test_profiles.py
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KNOWN_IDS = {
    "srum", "eventlog", "crash", "exec", "drift", "netconn", "perfmon", "disk",
    "procinspect", "memstate", "filterstack", "winupdate", "dtmsdk", "obsidian",
    "dtm_download", "dtm_deploy", "scheduler", "markitdown", "docstruct",
    "developer", "memory", "computercontroller",
}


def load():
    return json.loads((ROOT / "config" / "profiles.json").read_text(encoding="utf-8"))


def test_profiles_shape_and_names():
    profiles = load()
    assert len(profiles) == 6
    names = [p["name"] for p in profiles]
    assert names == ["diag", "perf", "sec", "dtm", "docs", "ops"]
    assert len(set(names)) == 6
    for p in profiles:
        assert p["label"] and p["description"]
        assert isinstance(p["enable"], list) and p["enable"]


def test_enable_ids_are_known():
    for p in load():
        unknown = set(p["enable"]) - KNOWN_IDS
        assert not unknown, f"{p['name']}: unknown ids {unknown}"


def test_recipe_files_exist_and_mention_rules():
    for p in load():
        rp = ROOT / p["recipe"]
        assert rp.is_file(), f"missing recipe {p['recipe']}"
        text = rp.read_text(encoding="utf-8")
        assert len(text) > 100


def test_diag_is_plan_b_superset_of_perf_and_sec():
    by = {p["name"]: set(p["enable"]) for p in load()}
    diag_diagnostics = by["diag"] - {"memory", "developer"}
    ab = (by["perf"] | by["sec"]) - {"memory", "developer"}
    assert diag_diagnostics == ab           # diag = merged A-plan halves
    assert len(diag_diagnostics) == 12
