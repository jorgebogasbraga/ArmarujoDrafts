"""Standings computation from Google Sheets."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from services.sheets_service import SheetsService


class StandingsService:
    def __init__(self, sheets: SheetsService) -> None:
        self.sheets = sheets
        self._cache: dict[str, list[dict]] = {}

    def get_standings(self, division: str, *, refresh: bool = False) -> list[dict]:
        key = division.lower()
        if not refresh and key in self._cache:
            return self._cache[key]
        rows = self.sheets.read_standings(division)
        rows.sort(
            key=lambda r: (
                -r["wins"],
                -r["kill_diff"],
                0 if r.get("tiebreak_winner") else 1,
            )
        )
        self._cache[key] = rows
        return rows

    def invalidate(self, division: str) -> None:
        self._cache.pop(division.lower(), None)
