"""Tests for coach replacement logic."""

from constants.draft_constants import DraftStatus
from models.pokemon import Pokemon
from services.draft.replacement_handler import ReplacementHandler
from tests.helpers import make_coach, make_pokemon, make_state


class TestReplacementHandler:
    def _state_with_skipped_coach(self):
        state = make_state(num_coaches=3, team_size=3, total_points=30)
        old = state.coaches[1]
        old.discord_id = "old-123"
        old.skip_count = 3
        old.makeup_picks_owed = 2
        old.team = [make_pokemon("Pikachu", 5)]
        old.remaining_points = 25
        state.makeup_queue = [("old-123", 1), ("old-123", 2), ("old-123", 3)]
        state.status = DraftStatus.WAITING_REPLACE
        state.pending_replace_coach_id = "old-123"
        return state, old

    def test_apply_replacement_transfers_team(self):
        state, old = self._state_with_skipped_coach()
        success, msg = ReplacementHandler.apply(
            state,
            new_coach_discord_id="new-456",
            new_coach_name="NewCoach",
        )
        assert success
        new = state.get_coach_by_id("new-456")
        assert new is not None
        assert len(new.team) == 1
        assert new.team[0].name == "Pikachu"

    def test_apply_replacement_pending_makeups(self):
        state, _ = self._state_with_skipped_coach()
        ReplacementHandler.apply(state, "new-456", "NewCoach")
        assert state.status == DraftStatus.REPLACEMENT_PENDING
        assert state.replacement_coach_discord_id == "new-456"
        assert state.replacement_picks_owed >= 1

    def test_apply_replacement_transfers_pick_bank(self):
        state, old = self._state_with_skipped_coach()
        from models.pick_bank import PickBank
        state.pick_banks[old.discord_id] = PickBank(
            coach_discord_id=old.discord_id,
            division_name=state.division_name,
        )
        ReplacementHandler.apply(state, "new-456", "NewCoach")
        assert "new-456" in state.pick_banks
        assert old.discord_id not in state.pick_banks

    def test_apply_fails_if_not_waiting(self):
        state = make_state()
        state.status = DraftStatus.ACTIVE
        success, msg = ReplacementHandler.apply(state, "new", "New")
        assert not success
