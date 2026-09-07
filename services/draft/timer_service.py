"""
Async pick timers with token-based cancellation (prevents stale timeouts).

Timers use pick_timer_remaining on the coach as the source of truth so that
bot downtime does not consume pick time.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Awaitable, Callable, Optional

from models.coach import Coach
from models.draft_state import DraftState
from services.persistence_service import PersistenceService
from utils.quiet_hours import QuietHours, get_quiet_hours

logger = logging.getLogger(__name__)

TimeoutCallback = Callable[[str], Awaitable[None]]

PERSIST_INTERVAL = 30.0


class DraftTimerService:
    def __init__(
        self,
        persistence: PersistenceService,
        on_timeout: TimeoutCallback,
        on_replacement_timeout: TimeoutCallback,
        quiet_hours: Optional[QuietHours] = None,
    ) -> None:
        self.persistence = persistence
        self.on_timeout = on_timeout
        self.on_replacement_timeout = on_replacement_timeout
        self.quiet_hours = quiet_hours or get_quiet_hours()
        self._timer_tasks: dict[str, asyncio.Task] = {}
        self._timer_tokens: dict[str, int] = {}
        self._tick_starts: dict[str, float] = {}

    def get_pick_time(self, coach: Coach, state: DraftState) -> int:
        if coach.skip_count == 0:
            return state.pick_time_initial
        if coach.skip_count == 1:
            return state.pick_time_second
        return state.pick_time_final

    def start_timer(self, state: DraftState) -> None:
        self.cancel_timer(state, persist=False)
        coach = state.current_coach
        if not coach:
            return

        seconds = float(self.get_pick_time(coach, state))
        coach.set_pick_timer(seconds)
        self._start_loop(state, seconds, self.on_timeout)

    def start_replacement_timer(self, state: DraftState) -> None:
        self.cancel_timer(state, persist=False)
        coach = state.get_coach_by_id(state.replacement_coach_discord_id or "")
        if not coach:
            return

        seconds = float(state.pick_time_initial)
        coach.set_pick_timer(seconds)
        self._start_loop(state, seconds, self.on_replacement_timeout)

    def resume_timer_with_remaining(
        self, state: DraftState, remaining_seconds: float
    ) -> None:
        self.cancel_timer(state, persist=False)
        coach = state.current_coach
        if coach:
            coach.set_pick_timer(remaining_seconds)
        self._start_loop(state, remaining_seconds, self.on_timeout)

    def resume_replacement_timer_with_remaining(
        self, state: DraftState, remaining_seconds: float
    ) -> None:
        self.cancel_timer(state, persist=False)
        coach = state.get_coach_by_id(state.replacement_coach_discord_id or "")
        if coach:
            coach.set_pick_timer(remaining_seconds)
        self._start_loop(state, remaining_seconds, self.on_replacement_timeout)

    def freeze_timer(self, state: DraftState) -> Optional[float]:
        """Cancel the running task and persist remaining seconds."""
        remaining = self._snapshot_remaining(state)
        self.cancel_timer(state, persist=False)
        coach = self._active_timed_coach(state)
        if coach and remaining is not None:
            coach.set_pick_timer(remaining)
            self.persistence.save(state)
        return remaining

    def cancel_timer(self, state: DraftState, *, persist: bool = True) -> None:
        division = state.division_name
        if persist:
            remaining = self._snapshot_remaining(state)
            coach = self._active_timed_coach(state)
            if coach and remaining is not None:
                coach.set_pick_timer(remaining)

        task = self._timer_tasks.pop(division, None)
        if task and not task.done():
            task.cancel()
        self._tick_starts.pop(division, None)
        state._timer_task = None
        old = self._timer_tokens.get(division, 0)
        self._timer_tokens[division] = old + 1

        if persist:
            self.persistence.save(state)

    def active_timed_coach(self, state: DraftState) -> Optional[Coach]:
        """Whoever the running clock belongs to (a replacement takes priority)."""
        return self._active_timed_coach(state)

    def _active_timed_coach(self, state: DraftState) -> Optional[Coach]:
        if state.replacement_coach_discord_id:
            return state.get_coach_by_id(state.replacement_coach_discord_id)
        return state.current_coach

    def _snapshot_remaining(self, state: DraftState) -> Optional[float]:
        division = state.division_name
        coach = self._active_timed_coach(state)
        if not coach:
            return None

        tick_start = self._tick_starts.get(division)
        if tick_start is not None and coach.pick_timer_remaining is not None:
            elapsed = time.time() - tick_start
            return max(0.0, coach.pick_timer_remaining - elapsed)

        if coach.pick_timer_remaining is not None:
            return coach.pick_timer_remaining
        if coach.pick_deadline is not None:
            return max(0.0, coach.pick_deadline - time.time())
        return None

    def _start_loop(
        self,
        state: DraftState,
        seconds: float,
        callback: TimeoutCallback,
    ) -> None:
        division = state.division_name
        token = self._timer_tokens.get(division, 0) + 1
        self._timer_tokens[division] = token
        coach = self._active_timed_coach(state)
        coach_name = coach.name if coach else "?"

        if self.quiet_hours.is_quiet():
            # Inside the nightly window the clock stands still. The remaining
            # seconds are already on the coach; the watcher restarts the loop
            # at resume time. Picks are still allowed meanwhile.
            self._tick_starts.pop(division, None)
            logger.info(
                "[Timer] Quiet hours — holding %.0fs for %s in %s (token=%d)",
                seconds,
                coach_name,
                division,
                token,
            )
            self.persistence.save(state)
            return

        self._tick_starts[division] = time.time()
        task = asyncio.create_task(
            self._timer_loop(state, seconds, token, callback)
        )
        self._timer_tasks[division] = task
        state._timer_task = task
        logger.info(
            "[Timer] Started for %s in %s: %.0fs (token=%d)",
            coach_name,
            division,
            seconds,
            token,
        )
        self.persistence.save(state)

    async def _timer_loop(
        self,
        state: DraftState,
        seconds: float,
        token: int,
        callback: TimeoutCallback,
    ) -> None:
        division = state.division_name
        deadline = time.time() + seconds

        try:
            while True:
                if self._timer_tokens.get(division) != token:
                    return

                now = time.time()
                remaining = deadline - now
                if remaining <= 0:
                    break

                chunk = min(PERSIST_INTERVAL, remaining)
                await asyncio.sleep(chunk)

                if self._timer_tokens.get(division) != token:
                    return

                coach = self._active_timed_coach(state)
                if coach:
                    coach.set_pick_timer(max(0.0, deadline - time.time()))
                    self._tick_starts[division] = time.time()
                    self.persistence.save(state)

            if self._timer_tokens.get(division) != token:
                return

            coach = self._active_timed_coach(state)
            if coach:
                coach.clear_pick_timer()
                self.persistence.save(state)
            logger.info(
                "[Timer] Deadline reached for %s in %s (token=%d)",
                coach.name if coach else "?",
                division,
                token,
            )
            await callback(division)
        except asyncio.CancelledError:
            pass

    def start_watchdog(self, get_states: Callable[[], dict]) -> None:
        """Safety net: fire overdue timeouts if the asyncio sleep loop dies silently."""
        asyncio.create_task(self._watchdog_loop(get_states))

    async def _watchdog_loop(self, get_states: Callable[[], dict]) -> None:
        from constants.draft_constants import DraftStatus

        while True:
            await asyncio.sleep(60.0)
            if self.quiet_hours.is_quiet():
                continue
            now = time.time()
            for state in get_states().values():
                if state.status != DraftStatus.ACTIVE:
                    continue

                division = state.division_name
                coach = self._active_timed_coach(state)
                if not coach or not coach.pick_deadline:
                    continue

                overdue = now - coach.pick_deadline
                if overdue < 5.0:
                    continue

                task = self._timer_tasks.get(division)
                if task is not None and not task.done():
                    continue

                logger.warning(
                    "[Timer] Watchdog: %s in %s is %.0fs overdue — firing timeout",
                    coach.name,
                    division,
                    overdue,
                )
                asyncio.create_task(self.on_timeout(division))
