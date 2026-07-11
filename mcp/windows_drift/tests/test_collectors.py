import collectors as col


def test_value_hash_stable_and_order_independent():
    a = col.value_hash({"x": 1, "y": 2})
    b = col.value_hash({"y": 2, "x": 1})
    assert a == b
    assert a != col.value_hash({"x": 1, "y": 3})


def test_collect_autoruns_shape():
    items = list(col.collect_autoruns())
    for it in items:
        assert it["category"] == "autoruns"
        assert "|" in it["key"]
        assert "command" in it["detail"]


def test_collect_returns_items_and_errors():
    items, errors = col.collect()
    assert isinstance(items, list)
    assert isinstance(errors, dict)
    cats = {it["category"] for it in items}
    # autoruns + programs work without admin; at least one category should yield items
    assert cats  # non-empty on any real Windows box
    for it in items:
        assert "value_hash" in it and it["category"] in col.CATEGORIES


def test_collect_unknown_category():
    items, errors = col.collect(category="bogus")
    assert items == []
    assert "bogus" in errors


def test_programs_have_names():
    items, _ = col.collect(category="programs")
    for it in items:
        assert it["name"]
        assert it["key"].count("\\") >= 1
