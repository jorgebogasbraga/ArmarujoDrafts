"""Tests for downtime-resilient pick timers."""

import time
from unittest.mock import MagicMock

import pytest

from constants.draft_constants import DraftStatus
from models.coach import Coach
from services.draft.timer_service import DraftTimerService
from services.persistence_service import PersistenceService
from tests.helpers import make_state


@pytest.fixture
def timer_setup(tmp_path):
    persistence = PersistenceService(data_dir=str(tmp_path))
    on_timeout = MagicMock()
    on_replacement = MagicMock()
    timers = DraftTimerService(
        persistence=persistence,
        on_timeout=on_timeout,
        on_replacement_timeout=on_replacement,
    )
    return persistence, timers, on_timeout


def test_pick_timer_remaining_survives_simulated_downtime(timer_setup):
    """Remaining seconds persisted on disk are not reduced by wall-clock downtime."""
    persistence, timers, _ = timer_setup
    state = make_state()
    coach = state.current_coach
    coach.set_pick_timer(10800.0)
    persistence.save(state)

    # Simulate 24h downtime — remaining should stay 10800 when reloaded
    loaded = persistence.load(state.division_name)
    assert loaded is not None
    reloaded_coach = loaded.current_coach
    assert reloaded_coach.pick_timer_remaining == pytest.approx(10800.0, rel=0.01)


@pytest.mark.asyncio
async def test_freeze_timer_preserves_remaining(timer_setup):
    persistence, timers, _ = timer_setup
    state = make_state()
    timers.start_timer(state)
    coach = state.current_coach
    initial = coach.pick_timer_remaining
    assert initial is not None and initial > 0

    time.sleep(0.05)
    remaining = timers.freeze_timer(state)
    assert remaining is not None
    assert remaining <= initial
    assert coach.pick_timer_remaining == pytest.approx(remaining, rel=0.01)
    timers.cancel_timer(state)


@pytest.mark.asyncio
async def test_resume_uses_remaining_not_wall_clock(timer_setup):
    persistence, timers, on_timeout = timer_setup
    state = make_state()
    coach = state.current_coach
    coach.set_pick_timer(3600.0)
    persistence.save(state)

    loaded = persistence.load(state.division_name)
    timers.resume_timer_with_remaining(loaded, loaded.current_coach.pick_timer_remaining)

    assert loaded.current_coach.pick_timer_remaining == pytest.approx(3600.0, rel=0.01)
    on_timeout.assert_not_called()


def test_coach_migration_from_legacy_deadline():
    """Legacy states with only pick_deadline get a remaining estimate on load."""
    coach = Coach.from_dict({
        "discord_id": "1",
        "name": "Test",
        "team_name": "Team",
        "team_logo_url": "",
        "timezone": "UTC",
        "division_name": "Test",
        "pick_deadline": time.time() + 7200,
    })
    assert coach.pick_timer_remaining is not None
    assert coach.pick_timer_remaining == pytest.approx(7200.0, abs=5.0)
