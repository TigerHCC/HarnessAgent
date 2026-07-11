import ctypes
import native


def test_struct_sizes():
    assert ctypes.sizeof(native.SYSTEM_POOLTAG) == 40  # x64 layout


def test_pool_tags_raw():
    rows = native.pool_tags_raw()
    assert isinstance(rows, list) and len(rows) > 10
    r = rows[0]
    for k in ("tag", "paged_used", "nonpaged_used", "nonpaged_allocs", "nonpaged_frees"):
        assert k in r
    assert all(isinstance(x["nonpaged_used"], int) for x in rows)


def test_memory_list_raw():
    m = native.memory_list_raw()
    for k in ("zero_pages", "free_pages", "modified_pages", "standby_pages", "page_size"):
        assert k in m
    assert m["standby_pages"] >= 0
    assert len(m["standby_by_priority_pages"]) == 8


def test_performance_info():
    p = native.performance_info()
    assert p["physical_total_pages"] > 0
    assert p["handles"] > 0 and p["processes"] > 0 and p["threads"] > 0
    assert p["page_size"] in (4096, 8192, 65536)
