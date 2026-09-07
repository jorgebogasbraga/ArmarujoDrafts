"""Helpers for batched Google Sheets draft-board writes."""

from __future__ import annotations

import asyncio
import time
from collections import deque

from services.draft.sheet_geometry import compute_card_cell
from utils.sheet_layout import SheetLayout


def board_pick_cell_ref(task: dict, layout: SheetLayout) -> str:
    return compute_card_cell(
        task["position_in_division"],
        task["division_block_start_row"],
        task["pick_index"],
        task["num_coaches"],
        layout,
    )


def coalesce_board_pick_tasks(
    tasks: list[dict],
    layout: SheetLayout,
) -> list[tuple[str, str]]:
    """
    Merge pick tasks by target cell; last write wins.

    Returns list of (a1_cell, pokemon_name) in stable order.
    """
    by_cell: dict[str, str] = {}
    order: list[str] = []
    for task in tasks:
        cell = board_pick_cell_ref(task, layout)
        if cell not in by_cell:
            order.append(cell)
        by_cell[cell] = task["pokemon_name"]
    return [(cell, by_cell[cell]) for cell in order]


class SheetsWriteRateLimiter:
    """Paced token bucket — stays under Sheets quota without burst-then-stall."""

    def __init__(self, max_per_minute: int) -> None:
        self._max = max(1, max_per_minute)
        self._min_interval = 60.0 / self._max
        self._timestamps: deque[float] = deque()
        self._cooldown_until = 0.0
        self._next_slot = 0.0
        self._lock: asyncio.Lock | None = None

    def _get_lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    async def acquire(self) -> None:
        async with self._get_lock():
            while True:
                now = time.monotonic()
                wait_until = max(self._cooldown_until, self._next_slot)
                if now < wait_until:
                    await asyncio.sleep(wait_until - now)
                    continue

                now = time.monotonic()
                while self._timestamps and now - self._timestamps[0] >= 60.0:
                    self._timestamps.popleft()

                if len(self._timestamps) < self._max:
                    self._timestamps.append(now)
                    self._next_slot = now + self._min_interval
                    return

                wait = 60.0 - (now - self._timestamps[0]) + 0.05
                await asyncio.sleep(max(wait, 0.05))

    def note_rate_limit(self, retry_after_seconds: float = 60.0) -> None:
        self._cooldown_until = max(
            self._cooldown_until,
            time.monotonic() + retry_after_seconds,
        )
