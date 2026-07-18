from datetime import datetime
import pytest
import cron

def test_daily_next_run_rolls_to_tomorrow():
    now = datetime(2026, 7, 18, 10, 0)
    nxt = cron.next_run("cron", "0 9 * * *", now)
    assert nxt == datetime(2026, 7, 19, 9, 0)

def test_hourly_next_run_is_next_top_of_hour():
    now = datetime(2026, 7, 18, 10, 30)
    assert cron.next_run("cron", "0 * * * *", now) == datetime(2026, 7, 18, 11, 0)

def test_step_field_every_15_min():
    now = datetime(2026, 7, 18, 10, 7)
    assert cron.next_run("cron", "*/15 * * * *", now) == datetime(2026, 7, 18, 10, 15)

def test_day_of_week_monday_only():
    now = datetime(2026, 7, 18, 12, 0)   # 2026-07-18 is a Saturday
    assert cron.next_run("cron", "0 9 * * 1", now) == datetime(2026, 7, 20, 9, 0)

def test_at_future_returns_datetime_past_returns_none():
    now = datetime(2026, 7, 18, 10, 0)
    assert cron.next_run("at", "2026-07-18T20:00", now) == datetime(2026, 7, 18, 20, 0)
    assert cron.next_run("at", "2026-07-18T09:00", now) is None

def test_validate_rejects_bad_cron_and_bad_at():
    with pytest.raises(ValueError):
        cron.validate("cron", "0 9 * *")        # only 4 fields
    with pytest.raises(ValueError):
        cron.validate("cron", "99 9 * * *")     # minute out of range
    with pytest.raises(ValueError):
        cron.validate("at", "not-a-date")

def test_describe_labels():
    assert cron.describe("cron", "0 9 * * *") == "每日 09:00"
    assert cron.describe("cron", "0 * * * *") == "每小時"
    assert cron.describe("at", "2026-07-18T20:00").startswith("一次性 2026-07-18 20:00")
