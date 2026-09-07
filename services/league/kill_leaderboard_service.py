"""Kill leaderboard aggregation from Google Sheets."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from services.sheets_service import SheetsService


class KillLeaderboardService:
    PAGE_SIZE = 10

    def __init__(self, sheets: SheetsService) -> None:
        self.sheets = sheets
        self._cache: dict[str, list[dict]] = {}

    def get_leaderboard(self, division: str, *, refresh: bool = False) -> list[dict]:
        key = division.lower()
        if not refresh and key in self._cache:
            return self._cache[key]

        rows = self.sheets.read_kill_stats(division)
        aggregated: dict[str, dict] = {}
        for row in rows:
            pokemon = row["pokemon"]
            if pokemon not in aggregated:
                aggregated[pokemon] = {
                    "pokemon": pokemon,
                    "kills": 0,
                    "deaths": 0,
                    "battles": 0,
                }
            aggregated[pokemon]["kills"] += row["kills"]
            aggregated[pokemon]["deaths"] += row["deaths"]
            aggregated[pokemon]["battles"] += row.get("battles", 0)

        ranked = sorted(
            aggregated.values(),
            key=lambda r: (-r["kills"], r["deaths"], r["pokemon"].lower()),
        )
        self._cache[key] = ranked
        return ranked

    def page(self, division: str, page: int, *, refresh: bool = False) -> tuple[list[dict], int]:
        all_rows = self.get_leaderboard(division, refresh=refresh)
        total_pages = max(1, (len(all_rows) + self.PAGE_SIZE - 1) // self.PAGE_SIZE)
        page = max(0, min(page, total_pages - 1))
        start = page * self.PAGE_SIZE
        return all_rows[start : start + self.PAGE_SIZE], total_pages

    def invalidate(self, division: str) -> None:
        self._cache.pop(division.lower(), None)
