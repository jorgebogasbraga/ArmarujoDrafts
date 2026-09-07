"""Tests for the nightly window where pick timers stop counting."""

from datetime import datetime
from unittest.mock import MagicMock

import pytest
import pytz

from services.draft.timer_service import DraftTimerService
from services.persistence_service import PersistenceService
from tests.helpers import make_state
from utils.quiet_hours import QuietHours

LISBON = pytz.timezone("Europe/Lisbon")

NIGHT_CONFIG = {
    "enabled": True,
    "timezone": "Europe/Lisbon",
    "pause_at": "23:00",
    "resume_at": "09:00",
}


def local(year, month, day, hour, minute=0) -> datetime:
    return LISBON.localize(datetime(year, month, day, hour, minute))


@pytest.fixture
def night() -> QuietHours:
    return QuietHours(NIGHT_CONFIG)


class TestWindow:
    def test_disabled_by_default(self):
        assert QuietHours({}).is_quiet(local(2026, 9, 6, 3)) is False

    def test_inside_window_after_midnight(self, night):
        assert night.is_quiet(local(2026, 9, 6, 3)) is True

    def test_inside_window_before_midnight(self, night):
        assert night.is_quiet(local(2026, 9, 5, 23, 30)) is True

    def test_boundaries_are_half_open(self, night):
        assert night.is_quiet(local(2026, 9, 5, 23, 0)) is True
        assert night.is_quiet(local(2026, 9, 6, 9, 0)) is False

    def test_outside_window_during_the_day(self, night):
        assert night.is_quiet(local(2026, 9, 5, 14)) is False

    def test_identical_bounds_disable_the_window(self):
        quiet = QuietHours({**NIGHT_CONFIG, "resume_at": "23:00"})
        assert quiet.enabled is False

    def test_unknown_timezone_falls_back(self):
        quiet = QuietHours({**NIGHT_CONFIG, "timezone": "Mars/Olympus"})
        assert str(quiet.timezone) == "Europe/Lisbon"

    def test_daytime_window_does_not_wrap(self):
        quiet = QuietHours({**NIGHT_CONFIG, "pause_at": "02:00", "resume_at": "06:00"})
        assert quiet.is_quiet(local(2026, 9, 6, 4)) is True
        assert quiet.is_quiet(local(2026, 9, 6, 23)) is False


class TestResumeTime:
    def test_next_resume_same_night(self, night):
        resume = night.next_resume(local(2026, 9, 5, 23, 30))
        assert resume.astimezone(LISBON) == local(2026, 9, 6, 9)

    def test_next_resume_after_midnight(self, night):
        resume = night.next_resume(local(2026, 9, 6, 3))
        assert resume.astimezone(LISBON) == local(2026, 9, 6, 9)

    def test_no_resume_outside_window(self, night):
        assert night.next_resume(local(2026, 9, 5, 14)) is None

    def test_seconds_to_boundary_counts_down_to_pause(self, night):
        seconds = night.seconds_until_next_boundary(local(2026, 9, 5, 22))
        assert seconds == pytest.approx(3600, abs=1)

    def test_seconds_to_boundary_counts_down_to_resume(self, night):
        seconds = night.seconds_until_next_boundary(local(2026, 9, 6, 8))
        assert seconds == pytest.approx(3600, abs=1)


class TestDeadlines:
    def test_deadline_unaffected_during_the_day(self, night):
        start = local(2026, 9, 5, 14)
        deadline = night.deadline_for(3 * 3600, at=start)
        assert datetime.fromtimestamp(deadline, LISBON) == local(2026, 9, 5, 17)

    def test_deadline_skips_over_the_night(self, night):
        # One hour runs before 23:00, the remaining two resume at 09:00.
        start = local(2026, 9, 5, 22)
        deadline = night.deadline_for(3 * 3600, at=start)
        assert datetime.fromtimestamp(deadline, LISBON) == local(2026, 9, 6, 11)

    def test_deadline_from_inside_the_window_starts_at_resume(self, night):
        start = local(2026, 9, 6, 2)
        deadline = night.deadline_for(2 * 3600, at=start)
        assert datetime.fromtimestamp(deadline, LISBON) == local(2026, 9, 6, 11)

    def test_deadline_is_plain_wall_clock_when_disabled(self):
        quiet = QuietHours({})
        start = local(2026, 9, 5, 22)
        deadline = quiet.deadline_for(3 * 3600, at=start)
        assert datetime.fromtimestamp(deadline, LISBON) == local(2026, 9, 6, 1)


class TestTimerService:
    def _timers(self, tmp_path, quiet: QuietHours) -> DraftTimerService:
        return DraftTimerService(
            persistence=PersistenceService(data_dir=str(tmp_path)),
            on_timeout=MagicMock(),
            on_replacement_timeout=MagicMock(),
            quiet_hours=quiet,
        )

    @pytest.mark.asyncio
    async def test_timer_is_held_during_quiet_hours(self, tmp_path):
        quiet = QuietHours(NIGHT_CONFIG)
        quiet.is_quiet = lambda at=None: True
        timers = self._timers(tmp_path, quiet)
        state = make_state()

        timers.start_timer(state)

        # Full pick time is banked on the coach, but no countdown is running.
        assert state.current_coach.pick_timer_remaining == state.pick_time_initial
        assert timers._timer_tasks.get(state.division_name) is None

    @pytest.mark.asyncio
    async def test_held_timer_keeps_its_remaining_seconds(self, tmp_path):
        quiet = QuietHours(NIGHT_CONFIG)
        quiet.is_quiet = lambda at=None: True
        timers = self._timers(tmp_path, quiet)
        state = make_state()

        timers.resume_timer_with_remaining(state, 1234.0)
        assert timers._snapshot_remaining(state) == pytest.approx(1234.0)

    @pytest.mark.asyncio
    async def test_timer_runs_outside_quiet_hours(self, tmp_path):
        quiet = QuietHours(NIGHT_CONFIG)
        quiet.is_quiet = lambda at=None: False
        timers = self._timers(tmp_path, quiet)
        state = make_state()

        timers.start_timer(state)
        assert timers._timer_tasks.get(state.division_name) is not None
        timers.cancel_timer(state)
