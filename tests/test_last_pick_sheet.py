"""The completing pick must still write the Pokémon name onto the Drafting Pool card."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from services.draft_service import DraftService
from tests.helpers import make_pokemon, make_state


@pytest.mark.asyncio
async def test_completing_pick_queues_sheet_write(monkeypatch):
    persistence = MagicMock()
    persistence.load_all.return_value = {}
    sheets = MagicMock()
    sheets.compute_division_block_start_row.return_value = 3
    alias = MagicMock()
    alias.resolve.side_effect = lambda raw, names: (
        next((n for n in names if n.lower() == raw.strip().lower()), None),
        False,
    )
    alias.find_related_forms.return_value = []

    monkeypatch.setattr(
        "services.draft_service.pick_announcement",
        lambda *args, **kwargs: (MagicMock(), MagicMock()),
    )
    monkeypatch.setattr(
        "utils.division_helper.load_division_config",
        lambda: [{"name": "TestDivision"}],
    )

    svc = DraftService(persistence, sheets, alias)
    svc.admin_log = MagicMock()
    svc.admin_log.pick_made = AsyncMock()
    svc.admin_log.draft_completed = AsyncMock()

    state = make_state(num_coaches=1, team_size=1, total_points=20)
    gengar = make_pokemon("Gengar", 8, pokedex_id=94)
    state.pokemon_pool = {gengar.name.lower(): gengar}
    svc.states[state.division_name] = state

    ok, message, _, _ = await svc._execute_pick(state, "1", "Gengar")
    assert ok
    assert message == "DRAFT_COMPLETE"
    sheets.queue_pick_write.assert_called_once()
    kwargs = sheets.queue_pick_write.call_args.kwargs
    assert kwargs["pokemon_name"] == "Gengar"
    assert kwargs["pick_index"] == 0
    assert kwargs["position_in_division"] == 0
    svc.admin_log.pick_made.assert_awaited_once()
    svc.admin_log.draft_completed.assert_awaited_once()
    persistence.delete.assert_called_once_with(state.division_name)
