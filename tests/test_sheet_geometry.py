"""Tests for sheet grid geometry."""

from services.draft.sheet_geometry import (
    column_number_to_letter,
    compute_card_block_row_count,
    compute_card_block_sizes,
    compute_card_cell,
    compute_division_block_height,
    compute_division_block_start_row,
)
from utils.sheet_layout import SheetLayout, load_sheet_layout


FULL_CONFIG = [
    {"name": "Acuity", "num_coaches": 16, "team_size": 10},
    {"name": "Verity", "num_coaches": 14, "team_size": 10},
    {"name": "Valor", "num_coaches": 14, "team_size": 10},
    {"name": "Origin", "num_coaches": 14, "team_size": 10},
    {"name": "Lunar", "num_coaches": 14, "team_size": 10},
]

# Pokémon Champions: 4 cards per row, 4 rows of 16 coaches, picks in the same
# column as the coach name (F6:F13, J6:J13, N6:N13, R6:R13).
CHAMPIONS_LAYOUT = SheetLayout(
    raw={
        "card_grid": {
            "first_name_row": 3,
            "first_name_col": 6,
            "cards_per_row_block": 4,
            "column_step": 4,
            "row_step": 13,
            "name_to_dropdown_col_offset": 0,
            "dropdown_start_row_offset": 3,
            "division_gap_rows": 5,
            "row_fill": "fill",
        },
        "divisions_order": ["Champions"],
    },
    path=None,
)


class TestSheetGeometry:
    def test_column_letter_conversion(self):
        assert column_number_to_letter(1) == "A"
        assert column_number_to_letter(7) == "G"
        assert column_number_to_letter(8) == "H"
        assert column_number_to_letter(26) == "Z"
        assert column_number_to_letter(27) == "AA"

    def test_block_sizes_small_division(self):
        assert compute_card_block_sizes(6) == [6]

    def test_block_sizes_large_division_balances_rows(self):
        assert compute_card_block_sizes(14) == [7, 7]
        assert compute_card_block_sizes(16) == [8, 8]

    def test_division_block_height_two_row_blocks(self):
        layout = load_sheet_layout()
        # row_step(14) + dropdown offset(2) + team_size(10) = 26
        assert compute_division_block_height(16, 10, layout) == 26

    def test_compute_card_cell_first_pick_acuity(self):
        layout = load_sheet_layout()
        cell = compute_card_cell(
            position_in_division=0,
            division_block_start_row=3,
            pick_index=0,
            num_coaches=16,
            layout=layout,
        )
        assert cell == "H5"

    def test_compute_card_cell_first_pick_verity(self):
        layout = load_sheet_layout()
        cell = compute_card_cell(
            position_in_division=0,
            division_block_start_row=34,
            pick_index=0,
            num_coaches=14,
            layout=layout,
        )
        assert cell == "H36"

    def test_division_block_start_row_all_divisions(self):
        assert compute_division_block_start_row("Acuity", FULL_CONFIG) == 3
        assert compute_division_block_start_row("Verity", FULL_CONFIG) == 34
        assert compute_division_block_start_row("Valor", FULL_CONFIG) == 65
        assert compute_division_block_start_row("Origin", FULL_CONFIG) == 96
        assert compute_division_block_start_row("Lunar", FULL_CONFIG) == 127

    def test_first_pick_cells_match_live_sheet(self):
        layout = load_sheet_layout()
        expected = {
            "Acuity": ("H5", 3, 16),
            "Verity": ("H36", 34, 14),
            "Valor": ("H67", 65, 14),
            "Origin": ("H98", 96, 14),
            "Lunar": ("H129", 127, 14),
        }
        for name, (cell_ref, start_row, coaches) in expected.items():
            cell = compute_card_cell(0, start_row, 0, coaches, layout)
            assert cell == cell_ref, f"{name}: got {cell}"


class TestChampionsGrid:
    """Four cards per row across four row-blocks."""

    def test_four_rows_of_four(self):
        assert compute_card_block_sizes(16, CHAMPIONS_LAYOUT) == [4, 4, 4, 4]
        assert compute_card_block_row_count(16, CHAMPIONS_LAYOUT) == 4

    def test_fill_mode_packs_rows_before_overflowing(self):
        assert compute_card_block_sizes(14, CHAMPIONS_LAYOUT) == [4, 4, 4, 2]

    def test_first_row_card_columns(self):
        cells = [
            compute_card_cell(pos, 3, 0, 16, CHAMPIONS_LAYOUT) for pos in range(4)
        ]
        assert cells == ["F6", "J6", "N6", "R6"]

    def test_last_pick_of_first_card(self):
        assert compute_card_cell(0, 3, 7, 16, CHAMPIONS_LAYOUT) == "F13"

    def test_row_blocks_start_at_expected_rows(self):
        # Cards start at E3, E16, E29 and E42, so coach names sit on F3/F16/F29/F42
        # and the first pick of each block is three rows below.
        first_of_each_row = [
            compute_card_cell(pos, 3, 0, 16, CHAMPIONS_LAYOUT)
            for pos in (0, 4, 8, 12)
        ]
        assert first_of_each_row == ["F6", "F19", "F32", "F45"]

    def test_last_coach_last_pick(self):
        assert compute_card_cell(15, 3, 7, 16, CHAMPIONS_LAYOUT) == "R52"

    def test_division_height_covers_all_four_rows(self):
        # row_step(13) * 3 + dropdown offset(3) + team_size(8) = 50
        assert compute_division_block_height(16, 8, CHAMPIONS_LAYOUT) == 50
