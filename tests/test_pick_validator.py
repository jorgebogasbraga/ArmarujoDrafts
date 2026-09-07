"""Tests for pick validation logic."""

from constants.draft_constants import DraftStatus
from models.pokemon import Pokemon
from services.draft.pick_validator import PickValidator
from tests.helpers import make_coach, make_pokemon, make_state


class TestPickValidator:
    def test_can_afford_simple(self):
        coach = make_coach("1", "Alice", remaining_points=10)
        pokemon = make_pokemon("Gengar", 8)
        ok, msg = PickValidator.validate_can_afford(coach, pokemon)
        assert ok
        assert msg is None

    def test_cannot_afford(self):
        coach = make_coach("1", "Alice", remaining_points=5)
        pokemon = make_pokemon("Gengar", 8)
        ok, msg = PickValidator.validate_can_afford(coach, pokemon)
        assert not ok
        assert "cannot afford" in msg.lower()

    def test_points_budget_blocks_overspend(self):
        coach = make_coach("1", "Alice", remaining_points=10)
        pokemon = make_pokemon("Tyranitar", 9)
        ok, msg = PickValidator.validate_points_budget(coach, pokemon, team_size=3)
        assert not ok
        assert "remaining pick" in msg.lower()

    def test_points_budget_allows_valid_pick(self):
        coach = make_coach("1", "Alice", remaining_points=10)
        pokemon = make_pokemon("Pikachu", 3)
        ok, msg = PickValidator.validate_points_budget(coach, pokemon, team_size=3)
        assert ok

    def test_team_full_rejected(self):
        coach = make_coach("1", "Alice")
        coach.team = [make_pokemon(f"M{i}", 1) for i in range(3)]
        ok, msg = PickValidator.validate_team_not_full(coach, team_size=3)
        assert not ok

    def test_draft_not_active(self):
        state = make_state()
        state.status = DraftStatus.PAUSED
        ok, msg = PickValidator.validate_draft_status(state, False, "1")
        assert not ok
        assert msg == "__PAUSED_STAFF_ONLY__"

    def test_makeup_allowed_while_paused_for_staff(self):
        state = make_state()
        state.status = DraftStatus.PAUSED
        state.makeup_queue.append(("1", 2))
        ok, msg = PickValidator.validate_draft_status(
            state, True, "1", requester_is_staff=True
        )
        assert ok
        assert msg is None

    def test_makeup_blocked_while_paused_for_coach(self):
        state = make_state()
        state.status = DraftStatus.PAUSED
        state.makeup_queue.append(("1", 2))
        ok, msg = PickValidator.validate_draft_status(
            state, True, "1", requester_is_staff=False
        )
        assert not ok
        assert msg == "__PAUSED_STAFF_ONLY__"

    def test_pick_blocked_while_paused_for_coach(self):
        state = make_state()
        state.status = DraftStatus.PAUSED
        ok, msg = PickValidator.validate_draft_status(
            state, False, "1", requester_is_staff=False
        )
        assert not ok
        assert msg == "__PAUSED_STAFF_ONLY__"

    def test_pick_allowed_while_paused_for_staff(self):
        state = make_state()
        state.status = DraftStatus.PAUSED
        ok, msg = PickValidator.validate_draft_status(
            state, False, "1", requester_is_staff=True
        )
        assert ok

    def test_normal_pick_blocked_while_paused(self):
        state = make_state()
        state.status = DraftStatus.PAUSED
        ok, msg = PickValidator.validate_draft_status(state, False, "1")
        assert not ok
        assert msg == "__PAUSED_STAFF_ONLY__"

    def test_replacement_pending_blocks_normal_pick(self):
        state = make_state()
        state.status = DraftStatus.REPLACEMENT_PENDING
        state.replacement_coach_discord_id = "99"
        ok, msg = PickValidator.validate_draft_status(state, False, "1")
        assert not ok
        assert "paused" in msg.lower()

    def test_unambiguous_input(self):
        assert PickValidator.is_unambiguous_input("Ogerpon-Wellspring", "Ogerpon-Wellspring")
        assert not PickValidator.is_unambiguous_input("ogerpon", "Ogerpon-Wellspring")

    def test_validate_pick_combined(self):
        coach = make_coach("1", "Alice", remaining_points=15)
        pokemon = make_pokemon("Bulbasaur", 4)
        ok, msg = PickValidator.validate_pick(coach, pokemon, team_size=3)
        assert ok

    def test_species_conflict_blocks_second_form_same_coach(self):
        state = make_state()
        coach = state.coaches[0]
        urshifu_single = make_pokemon("Urshifu-Single-Strike", 8, pokedex_id=892)
        urshifu_rapid = make_pokemon("Urshifu-Rapid-Strike", 8, pokedex_id=892)
        state.pokemon_pool = {
            urshifu_single.name.lower(): urshifu_single,
            urshifu_rapid.name.lower(): urshifu_rapid,
        }
        coach.add_pokemon(urshifu_single)

        ok, taken_name, _ = PickValidator.find_species_conflict_on_team(coach, urshifu_rapid)
        assert not ok
        assert taken_name == "Urshifu-Single-Strike"

        ok, msg = PickValidator.validate_species_available(state, urshifu_rapid, coach)
        assert not ok
        assert "892" in msg
        assert "Urshifu-Single-Strike" in msg

    def test_species_conflict_allows_same_dex_different_coaches(self):
        state = make_state()
        coach_a = state.coaches[0]
        coach_b = state.coaches[1]
        rotom = make_pokemon("Rotom", 5, pokedex_id=479)
        rotom_heat = make_pokemon("Rotom-Heat", 5, pokedex_id=479)
        state.pokemon_pool = {
            rotom.name.lower(): rotom,
            rotom_heat.name.lower(): rotom_heat,
        }
        coach_a.add_pokemon(rotom)
        rotom.is_drafted = True
        rotom.drafted_by = coach_a.discord_id

        ok, msg = PickValidator.validate_species_available(state, rotom_heat, coach_b)
        assert ok
        assert msg is None

    def test_species_conflict_allows_different_dex(self):
        state = make_state()
        pikachu = make_pokemon("Pikachu", 3, pokedex_id=25)
        raichu = make_pokemon("Raichu", 5, pokedex_id=26)
        state.pokemon_pool = {
            pikachu.name.lower(): pikachu,
            raichu.name.lower(): raichu,
        }
        pikachu.is_drafted = True
        pikachu.drafted_by = "1"

        ok, msg = PickValidator.validate_species_available(state, raichu, state.coaches[1])
        assert ok
        assert msg is None

    def test_species_conflict_ignores_zero_dex(self):
        state = make_state()
        unknown = make_pokemon("Custom-Mon", 4, pokedex_id=0)
        ok, msg = PickValidator.validate_species_available(state, unknown, state.coaches[0])
        assert ok

    def test_species_conflict_blocks_pick_when_other_form_in_bank(self):
        from models.pick_bank import PickBank

        state = make_state()
        coach = state.coaches[0]
        deoxys_def = make_pokemon("Deoxys-Defense", 16, pokedex_id=386)
        deoxys_spe = make_pokemon("Deoxys-Speed", 16, pokedex_id=386)
        state.pokemon_pool = {
            deoxys_def.name.lower(): deoxys_def,
            deoxys_spe.name.lower(): deoxys_spe,
        }
        bank = PickBank(coach_discord_id=coach.discord_id, division_name=state.division_name)
        bank.set_plan(4, ["Deoxys-Defense"])
        state.pick_banks[coach.discord_id] = bank

        ok, taken, rnd = PickValidator.find_species_conflict_on_bank(state, coach, deoxys_spe)
        assert not ok
        assert taken == "Deoxys-Defense"
        assert rnd == 4

        ok, msg = PickValidator.validate_species_available(state, deoxys_spe, coach)
        assert not ok
        assert "386" in msg
        assert "Deoxys-Defense" in msg
        assert "pick bank" in msg.lower()

        err = PickValidator.species_conflict_for_pick(state, deoxys_spe, coach)
        assert err is not None
        key, kwargs = err
        assert key == "pick.species_conflict_bank"
        assert kwargs["round"] == 4

    def test_species_conflict_allows_picking_same_name_already_in_bank(self):
        from models.pick_bank import PickBank

        state = make_state()
        coach = state.coaches[0]
        deoxys_spe = make_pokemon("Deoxys-Speed", 16, pokedex_id=386)
        state.pokemon_pool = {deoxys_spe.name.lower(): deoxys_spe}
        bank = PickBank(coach_discord_id=coach.discord_id, division_name=state.division_name)
        bank.set_plan(6, ["Deoxys-Speed"])
        state.pick_banks[coach.discord_id] = bank

        ok, msg = PickValidator.validate_species_available(state, deoxys_spe, coach)
        assert ok
        assert msg is None
