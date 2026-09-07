"""Tests for match time analysis."""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytz

from utils.match_scheduling import analyze_match_time
from utils.timezone_helper import TimezoneResolution


def _res(tz_name: str) -> TimezoneResolution:
    tz = pytz.timezone(tz_name)
    return TimezoneResolution(tz=tz, source="test", detail=tz_name)


def test_opponent_sleep_warning():
    utc = pytz.UTC.localize(datetime(2026, 7, 30, 3, 0))
    analysis = analyze_match_time(
        utc,
        _res("Europe/Lisbon"),
        _res("Europe/Lisbon"),
    )
    keys = [key for key, _ in analysis.warning_keys]
    assert "match.time_warn.opponent_sleep" in keys


def test_reasonable_hour_ok():
    utc = pytz.UTC.localize(datetime(2026, 7, 30, 18, 0))
    analysis = analyze_match_time(
        utc,
        _res("Europe/Lisbon"),
        _res("Europe/Lisbon"),
    )
    keys = [key for key, _ in analysis.warning_keys]
    assert keys == ["match.time_warn.ok"]


if __name__ == "__main__":
    test_opponent_sleep_warning()
    test_reasonable_hour_ok()
    print("OK")
