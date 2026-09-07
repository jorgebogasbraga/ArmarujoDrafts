"""
Nightly window where pick timers stop counting.

This is not a paused draft: coaches can still `/pick`, banks still resolve, and
the draft stays ACTIVE. Only the clocks stand still, so nobody is skipped for
sleeping. An admin `/pause_draft` remains a separate, harder stop.

Configured under `quiet_hours` in league_config.json:

    "quiet_hours": {
      "enabled": true,
      "timezone": "Europe/Lisbon",
      "pause_at": "23:00",
      "resume_at": "09:00"
    }
"""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from datetime import datetime, time as dt_time, timedelta
from functools import lru_cache
from typing import Optional

import pytz

from utils.league_settings import get_league_settings

logger = logging.getLogger(__name__)

DEFAULT_TIMEZONE = "Europe/Lisbon"
DEFAULT_PAUSE_AT = "23:00"
DEFAULT_RESUME_AT = "09:00"

# Safety bound when walking a timer across repeated windows.
_MAX_WINDOW_CROSSINGS = 16


def _parse_hhmm(value, fallback: str) -> dt_time:
    raw = str(value or fallback).strip()
    try:
        hour, _, minute = raw.partition(":")
        return dt_time(int(hour), int(minute or 0))
    except ValueError:
        logger.warning("[QuietHours] Invalid time '%s' — using %s.", raw, fallback)
        hour, _, minute = fallback.partition(":")
        return dt_time(int(hour), int(minute or 0))


class QuietHours:
    """Reads the configured window and answers questions about it."""

    def __init__(self, config: Optional[dict] = None) -> None:
        cfg = dict(config if config is not None else get_league_settings().quiet_hours)
        self.enabled = bool(cfg.get("enabled", False))
        self.pause_at = _parse_hhmm(cfg.get("pause_at"), DEFAULT_PAUSE_AT)
        self.resume_at = _parse_hhmm(cfg.get("resume_at"), DEFAULT_RESUME_AT)

        tz_name = str(cfg.get("timezone") or DEFAULT_TIMEZONE)
        try:
            self.timezone = pytz.timezone(tz_name)
        except pytz.UnknownTimeZoneError:
            logger.error(
                "[QuietHours] Unknown timezone '%s' — falling back to %s.",
                tz_name,
                DEFAULT_TIMEZONE,
            )
            self.timezone = pytz.timezone(DEFAULT_TIMEZONE)

        if self.enabled and self.pause_at == self.resume_at:
            logger.error(
                "[QuietHours] pause_at equals resume_at (%s) — window disabled.",
                self.pause_at,
            )
            self.enabled = False

    # ── Time helpers ─────────────────────────────────────────────────────────

    def _aware(self, at: Optional[datetime]) -> datetime:
        if at is None:
            return datetime.now(pytz.UTC)
        if at.tzinfo is None:
            return pytz.UTC.localize(at)
        return at

    def to_local(self, at: Optional[datetime] = None) -> datetime:
        return self._aware(at).astimezone(self.timezone)

    def _local_at(self, reference: datetime, moment: dt_time) -> datetime:
        """The given local wall-clock time on the reference's local date."""
        local = reference.astimezone(self.timezone)
        naive = datetime.combine(local.date(), moment)
        return self.timezone.localize(naive)

    # ── Queries ──────────────────────────────────────────────────────────────

    def is_quiet(self, at: Optional[datetime] = None) -> bool:
        """True when pick timers should not be running."""
        if not self.enabled:
            return False
        now = self.to_local(at).time()
        if self.pause_at < self.resume_at:
            return self.pause_at <= now < self.resume_at
        # Window wraps past midnight (the usual 23:00 → 09:00 case).
        return now >= self.pause_at or now < self.resume_at

    def next_resume(self, at: Optional[datetime] = None) -> Optional[datetime]:
        """UTC-aware moment timers start again, or None outside the window."""
        if not self.is_quiet(at):
            return None
        now = self._aware(at)
        resume = self._local_at(now, self.resume_at)
        while resume <= now:
            resume = self._local_at(
                resume.astimezone(self.timezone) + timedelta(days=1), self.resume_at
            )
        return resume.astimezone(pytz.UTC)

    def next_pause(self, at: Optional[datetime] = None) -> datetime:
        """UTC-aware moment the window next opens."""
        now = self._aware(at)
        pause = self._local_at(now, self.pause_at)
        while pause <= now:
            pause = self._local_at(
                pause.astimezone(self.timezone) + timedelta(days=1), self.pause_at
            )
        return pause.astimezone(pytz.UTC)

    def resume_timestamp(self, at: Optional[datetime] = None) -> Optional[int]:
        """Unix timestamp of the next resume, for Discord <t:…> rendering."""
        resume = self.next_resume(at)
        return int(resume.timestamp()) if resume else None

    def seconds_until_next_boundary(self, at: Optional[datetime] = None) -> float:
        """Seconds until the window next opens or closes."""
        now = self._aware(at)
        target = self.next_resume(now) if self.is_quiet(now) else self.next_pause(now)
        return max(1.0, (target - now).total_seconds())

    # ── Deadlines ────────────────────────────────────────────────────────────

    def deadline_for(
        self, remaining_seconds: float, at: Optional[datetime] = None
    ) -> float:
        """
        Unix timestamp when a timer with this much left would actually expire,
        skipping over any quiet window along the way.
        """
        now = self._aware(at)
        if not self.enabled or remaining_seconds <= 0:
            return (now + timedelta(seconds=max(0.0, remaining_seconds))).timestamp()

        cursor = now
        left = float(remaining_seconds)
        for _ in range(_MAX_WINDOW_CROSSINGS):
            if self.is_quiet(cursor):
                cursor = self.next_resume(cursor) or cursor
                continue
            runnable = (self.next_pause(cursor) - cursor).total_seconds()
            if left <= runnable:
                return (cursor + timedelta(seconds=left)).timestamp()
            left -= runnable
            cursor = self.next_pause(cursor)
        return (cursor + timedelta(seconds=left)).timestamp()

    def describe_window(self) -> str:
        return (
            f"{self.pause_at.strftime('%H:%M')}–{self.resume_at.strftime('%H:%M')} "
            f"({self.timezone})"
        )


@lru_cache(maxsize=1)
def get_quiet_hours() -> QuietHours:
    return QuietHours()


def deadline_from_remaining(remaining_seconds: float) -> float:
    """Wall-clock expiry for a timer, accounting for the nightly window."""
    quiet = get_quiet_hours()
    if not quiet.enabled:
        return time.time() + remaining_seconds
    return quiet.deadline_for(remaining_seconds)


@contextmanager
def quiet_hours_suspended():
    """
    Turn the nightly window off for the duration of a block.

    Simulations compress pick timers down to seconds and expect them to fire.
    A test started at 22:50 would otherwise freeze at 23:00 and hang until
    morning. Everything shares one QuietHours instance, so this reaches the
    timer service and the deadline maths alike.
    """
    quiet = get_quiet_hours()
    was_enabled = quiet.enabled
    quiet.enabled = False
    try:
        yield
    finally:
        quiet.enabled = was_enabled
