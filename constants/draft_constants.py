from enum import Enum


class DraftStatus(str, Enum):
    PENDING = "pending"       # Division loaded but draft not started
    ACTIVE = "active"         # Draft running normally
    PAUSED = "paused"         # Paused by admin or waiting for replacement
    WAITING_REPLACE = "waiting_replace"  # 3 skips reached, seeking replacement
    REPLACEMENT_PENDING = "replacement_pending"  # ← NOVO
    COMPLETED = "completed"   # All picks done


class SnakeDirection(int, Enum):
    FORWARD = 1
    BACKWARD = -1


class PickType(str, Enum):
    NORMAL = "normal"
    MAKEUP = "makeup"       # Pick made after a skip
    BANK = "bank"           # Auto-picked from pick bank
    TERA = "tera"           # Tera captain designation


# Embed colours per Pokémon type (hex)
TYPE_COLOURS: dict[str, int] = {
    "normal":   0xA8A878,
    "fire":     0xF08030,
    "water":    0x6890F0,
    "electric": 0xF8D030,
    "grass":    0x78C850,
    "ice":      0x98D8D8,
    "fighting": 0xC03028,
    "poison":   0xA040A0,
    "ground":   0xE0C068,
    "flying":   0xA890F0,
    "psychic":  0xF85888,
    "bug":      0xA8B820,
    "rock":     0xB8A038,
    "ghost":    0x705898,
    "dragon":   0x7038F8,
    "dark":     0x705848,
    "steel":    0xB8B8D0,
    "fairy":    0xEE99AC,
    "stellar":  0x40B5A5,
    "unknown":  0x68A090,
}

DEFAULT_EMBED_COLOUR = 0x5865F2  # Discord blurple

# Sheets column mapping (0-indexed) — adjust to match your actual Sheet layout
SHEETS_DRAFT_BOARD_COLUMNS = {
    "pick_number":   0,
    "coach_name":    1,
    "pokemon_name":  2,
    "points_cost":   3,
    "round":         4,
}

# Sheet layout — loaded from sheet_layout.json (override there for other leagues)
from utils.sheet_layout import get_sheet_layout

_layout = get_sheet_layout()
_sheet_names = _layout.sheet_names
_grid = _layout.card_grid

MASTER_POOL_SHEET_NAME = _layout.master_pool_sheet
PARTICIPANTS_SHEET_NAME = _layout.participants_sheet
DRAFTING_POOL_SHEET_NAME = _layout.draft_board_sheet

DIVISION_ORDER = _layout.divisions_order

CARD_FIRST_NAME_ROW = _grid.first_name_row
CARD_FIRST_NAME_COL = _grid.first_name_col
CARDS_PER_ROW_BLOCK = _grid.cards_per_row_block
CARD_COLUMN_STEP = _grid.column_step
CARD_ROW_STEP = _grid.row_step
CARD_NAME_TO_DROPDOWN_COL_OFFSET = _grid.name_to_dropdown_col_offset
CARD_DROPDOWN_START_ROW_OFFSET = _grid.dropdown_start_row_offset

PARTICIPANTS_START_ROW = _layout.participants_start_row()
