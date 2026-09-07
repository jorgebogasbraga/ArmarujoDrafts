"""
Load and expose Google Sheets layout configuration from sheet_layout.json.

Leagues can customise tab names, column mappings, card grid geometry,
and division stacking order without touching Python code. Point
SHEET_LAYOUT_FILE at another file to run a different league.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from config import Config

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def resolve_layout_path(path: Path | str | None = None) -> Path:
    candidate = Path(path or Config.SHEET_LAYOUT_FILE)
    if candidate.is_absolute() or candidate.is_file():
        return candidate
    return PROJECT_ROOT / candidate


@dataclass(frozen=True)
class CardGridLayout:
    first_name_row: int
    first_name_col: int
    cards_per_row_block: int
    column_step: int
    row_step: int
    name_to_dropdown_col_offset: int
    dropdown_start_row_offset: int
    division_gap_rows: int
    single_block_fallback_rows: int
    row_fill: str


@dataclass(frozen=True)
class SheetLayout:
    raw: dict[str, Any]
    path: Path

    @property
    def sheet_names(self) -> dict[str, str]:
        return dict(self.raw.get("sheets", {}))

    @property
    def master_pool_sheet(self) -> str:
        return self.sheet_names.get("master_pool", "Sheet2")

    @property
    def participants_sheet(self) -> str:
        return self.sheet_names.get("participants", "Participants")

    @property
    def draft_board_sheet(self) -> str:
        return self.sheet_names.get("draft_board", "Drafting Pool")

    @property
    def status_log_sheet(self) -> str:
        return self.sheet_names.get("status_log", "draftStatus")

    @property
    def master_pool(self) -> dict[str, Any]:
        return self.raw.get("master_pool", {})

    @property
    def participants(self) -> dict[str, Any]:
        return self.raw.get("participants", {})

    @property
    def card_grid(self) -> CardGridLayout:
        g = self.raw.get("card_grid", {})
        return CardGridLayout(
            first_name_row=g.get("first_name_row", 3),
            first_name_col=g.get("first_name_col", 7),
            cards_per_row_block=g.get("cards_per_row_block", 8),
            column_step=g.get("column_step", 4),
            row_step=g.get("row_step", 14),
            name_to_dropdown_col_offset=g.get("name_to_dropdown_col_offset", 1),
            dropdown_start_row_offset=g.get("dropdown_start_row_offset", 2),
            division_gap_rows=g.get("division_gap_rows", 5),
            single_block_fallback_rows=g.get("single_block_fallback_rows", 10),
            row_fill=str(g.get("row_fill", "balanced")).lower(),
        )

    @property
    def divisions_order(self) -> list[str]:
        return list(self.raw.get("divisions_order", []))

    # ── Master pool ──────────────────────────────────────────────────────────

    def master_pool_column(self, key: str, default: Optional[int] = None) -> Optional[int]:
        """0-indexed column for a master pool field, or None when unmapped."""
        value = self.master_pool.get("columns", {}).get(key, default)
        return None if value is None else int(value)

    def master_pool_read_range(self) -> Optional[str]:
        """A1 range to read instead of the whole tab (keeps big Pokédex tabs cheap)."""
        rng = self.master_pool.get("read_range")
        return str(rng) if rng else None

    def master_pool_requires_points(self) -> bool:
        """When true, rows with an empty points cell are not part of this draft."""
        return bool(self.master_pool.get("require_points", False))

    # ── Participants ─────────────────────────────────────────────────────────

    def participants_read_range(self) -> str:
        start_row = self.participants.get("start_row", 5)
        template = self.participants.get("read_range", "D{start_row}:I1000")
        return template.format(start_row=start_row)

    def participants_start_row(self) -> int:
        return int(self.participants.get("start_row", 5))

    def draft_order(self) -> Optional[dict[str, Any]]:
        """
        Optional explicit draft order: a range of coach names already sorted by
        pick order. Without it, coaches are read in sheet order from first_coach.
        """
        cfg = self.participants.get("draft_order")
        if not cfg or not cfg.get("range"):
            return None
        return dict(cfg)


def load_sheet_layout(path: Path | str | None = None) -> SheetLayout:
    layout_path = resolve_layout_path(path)
    with open(layout_path, encoding="utf-8") as f:
        data = json.load(f)
    logger.debug("[SheetLayout] Loaded layout from %s", layout_path)
    return SheetLayout(raw=data, path=layout_path)


@lru_cache(maxsize=1)
def get_sheet_layout() -> SheetLayout:
    return load_sheet_layout()
