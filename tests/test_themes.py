from datetime import datetime

import pytest

from app.scheduler import due_entries
from app.themes import THEMES, ScheduleEntry, get_theme, parse_schedule_plan


def test_parse_default_plan():
    entries = parse_schedule_plan("mon 12:00 promo; wed 12:00 product; fri 12:00 news")
    assert [(e.day, e.hour, e.minute, e.theme) for e in entries] == [
        (0, 12, 0, "promo"), (2, 12, 0, "product"), (4, 12, 0, "news"),
    ]
    assert entries[0].day_code == "mon" and entries[0].day_name == "понедельник"


def test_parse_is_tolerant_to_case_and_spaces():
    entries = parse_schedule_plan(" TUE 9:30 Free ;; sun 23:59 news ")
    assert [(e.day, e.hour, e.minute, e.theme) for e in entries] == [(1, 9, 30, "free"), (6, 23, 59, "news")]


@pytest.mark.parametrize("raw", ["", "mon 12:00", "xyz 12:00 promo", "mon 25:00 promo", "mon 12:00 unknown", "monday 12:00 promo"])
def test_parse_errors(raw):
    with pytest.raises(ValueError):
        parse_schedule_plan(raw)


def test_get_theme_fallback():
    assert get_theme("product").title == "Товар или категория"
    assert get_theme(None).key == "free"
    assert get_theme("nope").key == "free"
    assert {k for k, t in THEMES.items() if t.suggests} == {"promo", "product", "news"}


def test_due_entries():
    schedule = [ScheduleEntry(0, 12, 0, "promo"), ScheduleEntry(0, 18, 0, "free"), ScheduleEntry(2, 12, 0, "product")]
    monday_13 = datetime(2026, 9, 28, 13, 0)
    assert [e.theme for e in due_entries(schedule, monday_13)] == ["promo"]
    monday_19 = datetime(2026, 9, 28, 19, 0)
    assert [e.theme for e in due_entries(schedule, monday_19)] == ["promo", "free"]
    assert due_entries(schedule, datetime(2026, 9, 28, 11, 59)) == []
    assert due_entries(schedule, datetime(2026, 9, 29, 15, 0)) == []
