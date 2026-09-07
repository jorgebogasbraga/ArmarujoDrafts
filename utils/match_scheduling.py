"""Match time sanity checks and dual-timezone preview for scheduling."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

import pytz

if TYPE_CHECKING:
    from utils.timezone_helper import TimezoneResolution


REASONABLE_HOUR_START = 9
REASONABLE_HOUR_END = 23
SLEEP_HOUR_END = 8


@dataclass(frozen=True)
class MatchTimeAnalysis:
    proposer_local: datetime
    opponent_local: datetime
    proposer_resolution: "TimezoneResolution"
    opponent_resolution: "TimezoneResolution"
    warning_keys: list[tuple[str, dict]] = field(default_factory=list)

    @property
    def has_strong_warning(self) -> bool:
        return any(key.endswith("_sleep") for key, _ in self.warning_keys)


def _hour_in_local(utc_dt: datetime, tz: pytz.BaseTzInfo) -> int:
    return utc_dt.astimezone(tz).hour


def analyze_match_time(
    scheduled_utc: datetime,
    proposer_tz: "TimezoneResolution",
    opponent_tz: "TimezoneResolution",
) -> MatchTimeAnalysis:
    if scheduled_utc.tzinfo is None:
        scheduled_utc = pytz.UTC.localize(scheduled_utc)
    else:
        scheduled_utc = scheduled_utc.astimezone(pytz.UTC)

    proposer_local = scheduled_utc.astimezone(proposer_tz.tz)
    opponent_local = scheduled_utc.astimezone(opponent_tz.tz)
    warnings: list[tuple[str, dict]] = []

    opp_hour = opponent_local.hour
    prop_hour = proposer_local.hour

    if opp_hour < SLEEP_HOUR_END or opp_hour >= REASONABLE_HOUR_END:
        warnings.append(
            (
                "match.time_warn.opponent_sleep",
                {
                    "hour": f"{opponent_local:%H:%M}",
                    "timezone": str(getattr(opponent_tz.tz, "zone", opponent_tz.tz)),
                },
            )
        )
    elif opp_hour < REASONABLE_HOUR_START:
        warnings.append(
            (
                "match.time_warn.opponent_early",
                {
                    "hour": f"{opponent_local:%H:%M}",
                    "timezone": str(getattr(opponent_tz.tz, "zone", opponent_tz.tz)),
                },
            )
        )

    if prop_hour < SLEEP_HOUR_END or prop_hour >= REASONABLE_HOUR_END:
        warnings.append(
            (
                "match.time_warn.proposer_late",
                {"hour": f"{proposer_local:%H:%M}"},
            )
        )

    if not warnings:
        warnings.append(("match.time_warn.ok", {}))

    return MatchTimeAnalysis(
        proposer_local=proposer_local,
        opponent_local=opponent_local,
        proposer_resolution=proposer_tz,
        opponent_resolution=opponent_tz,
        warning_keys=warnings,
    )


def format_local_datetime(dt: datetime, tz_resolution: "TimezoneResolution") -> str:
    from utils.timezone_helper import format_utc_offset

    iana = getattr(tz_resolution.tz, "zone", None) or str(tz_resolution.tz)
    offset = format_utc_offset(tz_resolution.tz, dt)
    return f"{dt.strftime('%Y-%m-%d %H:%M')} ({iana}, {offset})"
