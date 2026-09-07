"""Tests for drafting coach timezone in pick deadlines."""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytz

from tests.helpers import make_coach
from utils.timezone_helper import format_coach_deadline, resolve_drafting_coach_timezone


def test_resolve_drafting_coach_timezone_uses_sheet():
    coach = make_coach("1", "Alice")
    coach.timezone = "Europe/Lisbon"
    res = resolve_drafting_coach_timezone(coach)
    assert str(res.tz) == "Europe/Lisbon"
    assert res.source == "coach_sheet"


def test_format_coach_deadline_uses_coach_timezone_not_invoker():
    coach = make_coach("1", "Alice")
    coach.timezone = "America/New_York"
    utc = datetime(2026, 8, 5, 12, 0, tzinfo=pytz.UTC)
    ts = int(utc.timestamp())
    line = format_coach_deadline(coach, ts, "en", include_relative=False)
    assert "08:00" in line or "07:00" in line
    assert "New_York" in line or "America" in line


def test_format_coach_deadline_relative_uses_discord_timestamps():
    coach = make_coach("1", "Alice")
    coach.timezone = "America/New_York"
    utc = datetime(2026, 8, 5, 12, 0, tzinfo=pytz.UTC)
    ts = int(utc.timestamp())
    line = format_coach_deadline(coach, ts, "en", include_relative=True)
    assert f"<t:{ts}:R>" in line
    assert f"<t:{ts}:f>" in line
    assert "{relative}" not in line
    assert "{absolute}" not in line


if __name__ == "__main__":
    test_resolve_drafting_coach_timezone_uses_sheet()
    test_format_coach_deadline_uses_coach_timezone_not_invoker()
    test_format_coach_deadline_relative_uses_discord_timestamps()
    print("OK")
