"""
Google Sheets card grid geometry — driven by sheet_layout.json.

Cards are laid out in row-blocks: `cards_per_row_block` cards side by side,
then the next block `row_step` rows further down. Leagues with 8 cards per row
(two blocks) and leagues with 4 cards per row (four blocks) both fall out of the
same maths.
"""

from __future__ import annotations

from typing import Optional

from utils.sheet_layout import SheetLayout, get_sheet_layout


def column_number_to_letter(n: int) -> str:
    """Convert a 1-indexed column number to its A1 letter (35 → 'AI')."""
    letters = ""
    while n > 0:
        n, remainder = divmod(n - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def _grid(layout: Optional[SheetLayout] = None):
    return (layout or get_sheet_layout()).card_grid


def compute_card_block_sizes(
    num_coaches: int,
    layout: Optional[SheetLayout] = None,
) -> list[int]:
    """
    How many cards sit in each row-block, top to bottom.

    `balanced` spreads coaches evenly over the blocks (14 coaches at 8 per row
    become 7 + 7); `fill` packs each row to capacity before starting the next
    (14 coaches at 4 per row become 4 + 4 + 4 + 2).
    """
    if num_coaches <= 0:
        return []

    g = _grid(layout)
    per_row = max(1, g.cards_per_row_block)
    blocks = -(-num_coaches // per_row)  # ceil

    if g.row_fill == "fill":
        sizes = [per_row] * (blocks - 1)
        sizes.append(num_coaches - per_row * (blocks - 1))
        return sizes

    base, extra = divmod(num_coaches, blocks)
    return [base + 1] * extra + [base] * (blocks - extra)


def compute_card_block_row_count(
    num_coaches: int,
    layout: Optional[SheetLayout] = None,
) -> int:
    """How many row-blocks a division occupies."""
    return len(compute_card_block_sizes(num_coaches, layout))


def locate_card_position(
    position_in_division: int,
    num_coaches: int,
    layout: Optional[SheetLayout] = None,
) -> tuple[int, int]:
    """Map a coach's draft position to (row_block_index, position_within_block)."""
    sizes = compute_card_block_sizes(num_coaches, layout)
    remaining = position_in_division
    for block, size in enumerate(sizes):
        if remaining < size:
            return block, remaining
        remaining -= size

    # Position beyond the configured coach count — keep it on the last block
    # rather than raising, so a stray write still lands somewhere predictable.
    last_block = max(0, len(sizes) - 1)
    return last_block, remaining + (sizes[last_block] if sizes else 0)


def compute_card_cell(
    position_in_division: int,
    division_block_start_row: int,
    pick_index: int,
    num_coaches: int,
    layout: Optional[SheetLayout] = None,
) -> str:
    g = _grid(layout)
    block, pos_in_block = locate_card_position(
        position_in_division, num_coaches, layout
    )

    base_col = g.first_name_col + g.column_step * pos_in_block
    dropdown_col = base_col + g.name_to_dropdown_col_offset
    name_row = division_block_start_row + g.row_step * block
    pick_row = name_row + g.dropdown_start_row_offset + pick_index
    return f"{column_number_to_letter(dropdown_col)}{pick_row}"


def compute_division_block_height(
    num_coaches: int,
    team_size: int,
    layout: Optional[SheetLayout] = None,
) -> int:
    """
    Vertical rows occupied by one division block on the draft board.

    From the first coach name row through the last pick dropdown row in the
    bottom row-block.
    """
    g = _grid(layout)
    blocks = max(1, compute_card_block_row_count(num_coaches, layout))
    pick_span = g.dropdown_start_row_offset + team_size
    return g.row_step * (blocks - 1) + pick_span


def compute_division_block_start_row(
    division_name: str,
    all_divisions_config: list[dict],
    layout: Optional[SheetLayout] = None,
) -> int:
    """Row of the first card name header for a division in the draft board sheet."""
    sheet_layout = layout or get_sheet_layout()
    g = sheet_layout.card_grid
    current_row = g.first_name_row

    for div in sheet_layout.divisions_order:
        cfg = next(
            (d for d in all_divisions_config if d["name"].lower() == div.lower()),
            None,
        )
        num_coaches = cfg.get("num_coaches", 0) if cfg else 0
        if num_coaches <= 0:
            continue

        if div.lower() == division_name.lower():
            return current_row

        team_size = cfg.get("team_size", 10) if cfg else 10
        current_row += (
            compute_division_block_height(num_coaches, team_size, sheet_layout)
            + g.division_gap_rows
        )

    return current_row
