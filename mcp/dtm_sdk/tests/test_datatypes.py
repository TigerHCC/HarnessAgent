# mcp/dtm_sdk/tests/test_datatypes.py
import datatypes


ROWS = [
    {"Name": "BatteryStaticData", "GUID": "g1", "CommodityType": "Battery"},
    {"Name": "BatteryDynamicData", "GUID": "g2", "CommodityType": "Battery"},
    {"Name": "ActivePenInfo", "GUID": "g3", "CommodityType": "Stylus"},
]


def test_find_one_is_case_insensitive():
    assert datatypes.find_one(ROWS, "batterystaticdata")["GUID"] == "g1"
    assert datatypes.find_one(ROWS, "BATTERYSTATICDATA")["GUID"] == "g1"


def test_find_one_missing_returns_none():
    assert datatypes.find_one(ROWS, "Nope") is None


def test_search_substring_and_commodity():
    names = [r["Name"] for r in datatypes.search(ROWS, term="battery")]
    assert names == ["BatteryStaticData", "BatteryDynamicData"]
    assert len(datatypes.search(ROWS, commodity="Stylus")) == 1


def test_suggest_near_miss():
    s = datatypes.suggest(ROWS, "BatteryStatic")
    assert "BatteryStaticData" in s


def test_load_table_reads_real_csv(tmp_path):
    p = tmp_path / "t.csv"
    p.write_text('"Name","GUID"\n"Foo","abc"\n', encoding="utf-8")
    rows = datatypes.load_table(str(p))
    assert rows == [{"Name": "Foo", "GUID": "abc"}]
