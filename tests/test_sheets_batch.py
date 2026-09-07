"""Tests for Google Sheets write batching helpers."""

from services.sheets_write_batch import coalesce_board_pick_tasks
from utils.sheet_layout import get_sheet_layout


def _task(cell_suffix: str, pokemon: str, **overrides) -> dict:
    base = {
        "position_in_division": 0,
        "pick_index": 0,
        "pokemon_name": pokemon,
        "division_block_start_row": 10,
        "num_coaches": 16,
    }
    base.update(overrides)
    return base


def test_coalesce_board_pick_tasks_dedupes_same_cell():
    layout = get_sheet_layout()
    tasks = [
        _task("a", "Gengar", position_in_division=0, pick_index=0),
        _task("b", "Typhlosion", position_in_division=0, pick_index=0),
    ]
    merged = coalesce_board_pick_tasks(tasks, layout)
    assert len(merged) == 1
    assert merged[0][1] == "Typhlosion"


def test_coalesce_board_pick_tasks_keeps_distinct_cells():
    layout = get_sheet_layout()
    tasks = [
        _task("a", "Gengar", position_in_division=0, pick_index=0),
        _task("b", "Typhlosion", position_in_division=1, pick_index=0),
    ]
    merged = coalesce_board_pick_tasks(tasks, layout)
    assert len(merged) == 2
    names = {name for _, name in merged}
    assert names == {"Gengar", "Typhlosion"}


def test_queue_status_write_suppressed_in_simulation():
    from services.sheets_service import SheetsService

    svc = SheetsService()
    svc.suppress_misc_writes(True)
    svc.queue_status_write("Acuity", "should not enqueue")
    assert svc._misc_queue.empty()


def test_queue_pick_write_records_tracked_cell(tmp_path):
    from services.sheets_service import SheetsService

    svc = SheetsService()
    svc.start_board_pick_tracking(str(tmp_path / "board_pick_cells.json"))
    svc._ensure_board_writer = lambda: type("W", (), {"enqueue": staticmethod(lambda _t: None)})()

    svc.queue_pick_write(
        division_name="Acuity",
        position_in_division=0,
        pick_index=0,
        pokemon_name="Gengar",
        division_block_start_row=3,
        num_coaches=16,
    )
    assert svc._tracked_board_cells
    assert all(isinstance(cell, str) and cell for cell in svc._tracked_board_cells)
