"""Shared helpers for natural timer-based skips in simulations."""

from __future__ import annotations

import asyncio
import logging

from constants.draft_constants import DraftStatus
from services.draft_service import DraftService

logger = logging.getLogger(__name__)


async def wait_for_natural_timer_skip(
    draft: DraftService,
    division_name: str,
    coach_discord_id: str,
    *,
    timer_seconds: int = 5,
    wait_buffer: int = 3,
) -> bool:
    """
    Shorten the active pick timer and wait for DraftTimerService to fire naturally.

    Returns True if the coach's skip_count increased.
    """
    state = draft._get_state(division_name)
    if not state or state.status != DraftStatus.ACTIVE:
        return False

    coach = state.current_coach
    if not coach or coach.discord_id != coach_discord_id:
        return False

    pre_skip = coach.skip_count
    orig_initial = state.pick_time_initial
    orig_second = state.pick_time_second
    orig_final = state.pick_time_final

    state.pick_time_initial = timer_seconds
    state.pick_time_second = timer_seconds
    state.pick_time_final = timer_seconds
    draft.timers.cancel_timer(state)
    draft.timers.start_timer(state)

    state.pick_time_initial = orig_initial
    state.pick_time_second = orig_second
    state.pick_time_final = orig_final

    await asyncio.sleep(timer_seconds + wait_buffer)

    state = draft._get_state(division_name)
    if not state:
        return False
    updated = state.get_coach_by_id(coach_discord_id)
    if updated and updated.skip_count > pre_skip:
        return True

    logger.warning(
        "[SimTimer] Timer did not increment skip for %s in %s (still %s)",
        coach_discord_id,
        division_name,
        updated.skip_count if updated else "?",
    )
    return False
