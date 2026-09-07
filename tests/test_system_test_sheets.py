"""Sheets rollback for system tests must never rewrite a full tab (formulas)."""

from models.pick_bank import PickBank
from services.sheets_service import SheetsService
from simulation.system_test import MultiDivisionSystemTest, SystemTestReport
from tests.helpers import make_state


def _connected_sheets() -> SheetsService:
    svc = SheetsService()
    svc._client = object()
    svc._spreadsheet = object()
    return svc


def test_snapshot_tabs_does_not_dump_full_sheets():
    assert SheetsService().snapshot_tabs() == {}


def test_restore_tabs_ignores_legacy_full_tab_snapshot():
    svc = _connected_sheets()
    cleared: list[str] = []
    restored: list[dict] = []
    svc._clear_board_pick_cells = lambda cells: cleared.extend(cells)
    svc._restore_participant_row_cells = lambda rows: restored.extend(rows)

    svc.restore_tabs({
        "Drafting Pool": [["=FORMULA(A1)", "keep me"]],
        "Participants": [["wipe", "this", "tab"]],
    })

    assert cleared == []
    assert restored == []


def test_restore_system_test_edits_uses_tracked_cells_and_participant_rows(tmp_path):
    svc = _connected_sheets()
    cleared: list[str] = []
    restored: list[dict] = []
    svc._clear_board_pick_cells = lambda cells: cleared.extend(cells)
    svc._restore_participant_row_cells = lambda rows: restored.extend(rows)

    cells_path = tmp_path / "board_pick_cells.json"
    cells_path.write_text('["C10", "E22"]', encoding="utf-8")

    svc.restore_system_test_edits({
        "board_pick_cells": ["A5"],
        "board_pick_cells_path": str(cells_path),
        "participants_rows": [{"row": 12, "name": "OldCoach", "discord_id": "1"}],
    })

    assert cleared == ["A5", "C10", "E22"]
    assert restored == [{"row": 12, "name": "OldCoach", "discord_id": "1"}]


def test_restore_uses_replacement_rows_not_entire_snapshot():
    svc = _connected_sheets()
    restored: list[dict] = []
    svc._clear_board_pick_cells = lambda _cells: None
    svc._restore_participant_row_cells = lambda rows: restored.extend(rows)
    svc._map_participant_discord_rows = lambda: {"360": 44}

    svc.restore_system_test_edits({
        "participants_rows": [
            {"row": 5, "name": "Folgado", "discord_id": "1"},
            {
                "row": 20,
                "name": "BanditBoyd",
                "team_name": "Venus Weedusaur",
                "discord_id": "509",
                "logo_url": "old.png",
                "timezone": "GMT-4",
            },
        ],
        "replacements": [{
            "old": {"discord_id": "509", "name": "BanditBoyd"},
            "new": {"discord_id": "360", "name": "Toniblast"},
        }],
    })

    assert len(restored) == 1
    assert restored[0]["name"] == "BanditBoyd"
    assert restored[0]["discord_id"] == "509"
    assert restored[0]["row"] == 44


def test_resolve_participant_restore_rows_prefers_live_replacement_id():
    from services.sheets_service import resolve_participant_restore_rows

    snapshot = [{
        "row": 20,
        "name": "BanditBoyd",
        "team_name": "Venus Weedusaur",
        "discord_id": "509",
        "logo_url": "old.png",
        "timezone": "GMT-4",
    }]
    replacements = [{
        "old": {"discord_id": "509", "name": "BanditBoyd"},
        "new": {"discord_id": "360", "name": "Toniblast"},
    }]
    rows = resolve_participant_restore_rows(
        snapshot,
        replacements,
        live_id_to_row={"360": 44, "509": 20},
    )
    assert len(rows) == 1
    assert rows[0]["row"] == 44
    assert rows[0]["name"] == "BanditBoyd"
    assert rows[0]["discord_id"] == "509"


def test_resolve_participant_restore_rows_uses_old_id_after_partial_rollback():
    from services.sheets_service import resolve_participant_restore_rows

    snapshot = [{
        "row": 12,
        "name": "Bit",
        "discord_id": "199",
        "team_name": "Big Black Chomps",
        "logo_url": "",
        "timezone": "GMT+1",
    }]
    replacements = [{
        "old": {"discord_id": "199", "name": "Bit"},
        "new": {"discord_id": "150", "name": "dudetaiga"},
    }]
    rows = resolve_participant_restore_rows(
        snapshot,
        replacements,
        live_id_to_row={"199": 12},
    )
    assert rows[0]["row"] == 12
    assert rows[0]["name"] == "Bit"


def test_batch_set_cells_retries_quota_then_succeeds(monkeypatch):
    svc = _connected_sheets()
    calls = {"n": 0}

    class Spreadsheet:
        def values_batch_update(self, _body):
            calls["n"] += 1
            if calls["n"] < 3:
                raise RuntimeError("APIError: [429]: Quota exceeded")

    svc._spreadsheet = Spreadsheet()
    monkeypatch.setattr("services.sheets_service.time.sleep", lambda _s: None)
    svc._sheet_range = lambda sheet, cell: f"{sheet}!{cell}"
    svc.BATCH_RANGE_CHUNK = 80
    svc._batch_set_cells("Participants", [("D12", "BanditBoyd")])
    assert calls["n"] == 3


def test_restore_participant_rows_raises_on_failure(monkeypatch):
    svc = _connected_sheets()

    def boom(_sheet, _pairs):
        raise RuntimeError("APIError: [429]: Quota exceeded")

    monkeypatch.setattr(svc, "_batch_set_cells", boom)
    try:
        svc._restore_participant_row_cells([{
            "row": 20,
            "name": "BanditBoyd",
            "team_name": "Venus Weedusaur",
            "discord_id": "509",
            "logo_url": "",
            "timezone": "GMT-4",
        }])
    except RuntimeError as exc:
        assert "429" in str(exc)
    else:
        raise AssertionError("expected restore to raise")


def test_current_bank_round_only_when_active_plan_matches():
    state = make_state()
    coach = state.coaches[0]
    bank = PickBank(coach_discord_id=coach.discord_id, division_name=state.division_name)
    bank.set_plan(6, ["Gengar"])
    bank.activate()
    state.pick_banks[coach.discord_id] = bank
    state.current_round = 6

    assert MultiDivisionSystemTest._current_bank_round(None, state) == (
        coach.discord_id,
        6,
    )

    state.current_round = 5
    assert MultiDivisionSystemTest._current_bank_round(None, state) is None


def test_failed_embed_shows_error_not_none():
    runner = MultiDivisionSystemTest.__new__(MultiDivisionSystemTest)
    runner.report = SystemTestReport(
        success=False,
        error="division_config.json is missing or empty.",
        snapshot_taken=False,
    )

    async def _run():
        return await runner.build_summary_embed()

    import asyncio

    embed = asyncio.run(_run())
    result = next(f.value for f in embed.fields if f.name == "Result")
    rollback = next(f.value for f in embed.fields if f.name == "Rollback")
    assert "missing" in result
    assert "None" not in result
    assert "No snapshot" in rollback
