"""Tests for natural timer skip helper."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from constants.draft_constants import DraftStatus
from simulation.timer_helpers import wait_for_natural_timer_skip


@pytest.mark.asyncio
async def test_wait_for_natural_timer_skip_detects_increment(monkeypatch):
    draft = MagicMock()
    coach = MagicMock()
    coach.discord_id = "c1"
    coach.skip_count = 0

    state = MagicMock()
    state.status = DraftStatus.ACTIVE
    state.current_coach = coach
    state.pick_time_initial = 3600
    state.pick_time_second = 3600
    state.pick_time_final = 3600

    updated_coach = MagicMock()
    updated_coach.skip_count = 1

    updated_state = MagicMock()
    updated_state.get_coach_by_id.return_value = updated_coach

    draft._get_state.side_effect = [state, updated_state]
    draft.timers.cancel_timer = MagicMock()
    draft.timers.start_timer = MagicMock()

    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    ok = await wait_for_natural_timer_skip(
        draft, "Acuity", "c1", timer_seconds=2, wait_buffer=1
    )

    assert ok is True
    draft.timers.start_timer.assert_called_once()


@pytest.mark.asyncio
async def test_wait_for_natural_timer_skip_wrong_coach():
    draft = MagicMock()
    coach = MagicMock()
    coach.discord_id = "other"

    state = MagicMock()
    state.status = DraftStatus.ACTIVE
    state.current_coach = coach

    draft._get_state.return_value = state

    ok = await wait_for_natural_timer_skip(draft, "Acuity", "c1")
    assert ok is False
