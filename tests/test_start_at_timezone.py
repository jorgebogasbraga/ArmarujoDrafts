"""Tests for timezone resolution and DST-aware scheduling."""

from datetime import datetime

import pytz

from utils.timezone_helper import (
    get_league_default_timezone_name,
    parse_timezone,
    resolve_invoker_timezone,
    schedule_local_time,
    timezone_from_discord_locale,
)


def test_discord_locale_pt_maps_to_lisbon():
    tz = timezone_from_discord_locale("pt-PT")
    assert tz is not None
    assert str(tz) == "Europe/Lisbon"


def test_gmt_plus_zero_maps_to_lisbon_iana():
    tz = parse_timezone("GMT+0")
    assert str(tz) == "Europe/Lisbon"


def test_gmt_plus_one_maps_to_lisbon_iana():
    tz = parse_timezone("GMT+1")
    assert str(tz) == "Europe/Lisbon"


def test_summer_schedule_matches_wall_clock():
    tz = pytz.timezone("Europe/Lisbon")
    ref = tz.localize(datetime(2026, 7, 28, 15, 0))
    utc = schedule_local_time(tz, 20, 0, now=ref)
    local = utc.astimezone(tz)
    assert local.hour == 20
    assert local.utcoffset().total_seconds() == 3600


def test_winter_schedule_matches_wall_clock():
    tz = pytz.timezone("Europe/Lisbon")
    ref = tz.localize(datetime(2026, 1, 15, 15, 0))
    utc = schedule_local_time(tz, 20, 0, now=ref)
    local = utc.astimezone(tz)
    assert local.hour == 20
    assert local.utcoffset().total_seconds() == 0


def test_gmt_zero_alias_not_one_hour_late_in_summer():
    tz = parse_timezone("GMT+0")
    ref = tz.localize(datetime(2026, 7, 28, 15, 0))
    utc = schedule_local_time(tz, 20, 0, now=ref)
    local = utc.astimezone(tz)
    assert local.hour == 20


def test_resolve_invoker_prefers_bot_language_over_en_us():
    from utils.timezone_helper import _timezone_store

    old = _timezone_store._cache.copy()
    _timezone_store._cache = {}
    try:
        res = resolve_invoker_timezone(
            "999",
            discord_locale="en-US",
            guild_locale="en-US",
            bot_language="pt",
        )
        assert str(res.tz) == "Europe/Lisbon"
        assert res.source == "bot_language"
    finally:
        _timezone_store._cache = old


def test_league_default_is_lisbon():
    assert get_league_default_timezone_name() == "Europe/Lisbon"
