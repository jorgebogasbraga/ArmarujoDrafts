"""Tests for pick bank resolution and snipe detection."""

from constants.draft_constants import DraftStatus
from models.pick_bank import BankMode, PickBank, PickBankEntry, PlanType, ConditionalBranch
from models.draft_state import PickRecord
from services.draft.bank_service import (
    find_pickable,
    get_coach_pick_in_round,
    get_primary_status,
    parse_conditional_branches,
    resolve_priority_list,
    resolve_bank_pokemon_name,
    resolve_bank_name_list,
    resolve_runtime_bank_names,
    validate_bank_species,
    validate_bank_pokemon_name,
    validate_bank_activation,
    diagnose_bank_failure,
    PrimaryStatus,
)
from utils.alias_manager import AliasManager
from tests.helpers import make_coach, make_pokemon, make_state


class TestBankService:
    def test_resolve_simple_plan(self):
        state = make_state()
        entry = PickBankEntry(round_number=3, priority_list=["Gengar", "Typhlosion"])
        result = resolve_priority_list(entry, state, "1")
        assert result == ["Gengar", "Typhlosion"]

    def test_resolve_conditional_plan(self):
        state = make_state(num_coaches=2, team_size=5, total_points=50)
        coach = state.coaches[0]
        state.pick_history.append(PickRecord(
            pick_number=1, round_number=4,
            coach_discord_id=coach.discord_id, coach_name=coach.name,
            pokemon_name="Tinkaton", points_cost=5,
        ))
        entry = PickBankEntry(
            round_number=5,
            plan_type=PlanType.CONDITIONAL,
            branches=[
                ConditionalBranch(if_round=4, if_picked="Tinkaton", then_list=["Hydreigon"]),
                ConditionalBranch(if_round=4, if_picked="Scream Tail", then_list=["Scizor"]),
            ],
            default_list=["Great Tusk"],
        )
        assert resolve_priority_list(entry, state, coach.discord_id) == ["Hydreigon"]

    def test_conditional_default_branch(self):
        state = make_state()
        entry = PickBankEntry(
            round_number=5,
            plan_type=PlanType.CONDITIONAL,
            branches=[
                ConditionalBranch(if_round=4, if_picked="Tinkaton", then_list=["Hydreigon"]),
            ],
            default_list=["Great Tusk"],
        )
        assert resolve_priority_list(entry, state, "1") == ["Great Tusk"]

    def test_primary_available(self):
        state = make_state()
        coach = state.coaches[0]
        p = make_pokemon("Gengar", 3)
        state.pokemon_pool["gengar"] = p
        assert get_primary_status(state, coach, "Gengar", state.team_size) == PrimaryStatus.AVAILABLE

    def test_primary_sniped(self):
        state = make_state()
        coach = state.coaches[0]
        p = make_pokemon("Gengar", 3)
        p.is_drafted = True
        p.drafted_by = "999"
        state.pokemon_pool["gengar"] = p
        assert get_primary_status(state, coach, "Gengar", state.team_size) == PrimaryStatus.SNIPE

    def test_find_pickable_immediate(self):
        state = make_state()
        coach = state.coaches[0]
        state.pokemon_pool["gengar"] = make_pokemon("Gengar", 3)
        pick, snipe, rest = find_pickable(
            state, coach, ["Gengar", "Typhlosion"], BankMode.FALLBACK, state.team_size
        )
        assert pick == "Gengar"
        assert snipe is None

    def test_find_pickable_snipe_triggers_pause(self):
        state = make_state()
        coach = state.coaches[0]
        g = make_pokemon("Gengar", 3)
        g.is_drafted = True
        g.drafted_by = "999"
        state.pokemon_pool["gengar"] = g
        state.pokemon_pool["typhlosion"] = make_pokemon("Typhlosion", 4)

        pick, snipe, rest = find_pickable(
            state, coach, ["Gengar", "Typhlosion"], BankMode.FALLBACK, state.team_size
        )
        assert pick is None
        assert snipe == "Gengar"
        assert rest == ["Typhlosion"]

    def test_find_pickable_snipe_on_later_option(self):
        state = make_state()
        coach = state.coaches[0]
        banned = make_pokemon("BannedMon", 3)
        banned.is_banned = True
        state.pokemon_pool["bannedmon"] = banned
        g = make_pokemon("Gengar", 3)
        g.is_drafted = True
        g.drafted_by = "999"
        state.pokemon_pool["gengar"] = g
        state.pokemon_pool["typhlosion"] = make_pokemon("Typhlosion", 4)

        pick, snipe, rest = find_pickable(
            state,
            coach,
            ["BannedMon", "Gengar", "Typhlosion"],
            BankMode.FALLBACK,
            state.team_size,
        )
        assert pick is None
        assert snipe == "Gengar"
        assert rest == ["Typhlosion"]

    def test_all_options_sniped(self):
        state = make_state()
        coach = state.coaches[0]
        for name in ("Gengar", "Typhlosion"):
            p = make_pokemon(name, 3)
            p.is_drafted = True
            p.drafted_by = "999"
            state.pokemon_pool[name.lower()] = p

        from services.draft.bank_service import all_options_sniped

        assert all_options_sniped(
            state, coach, ["Gengar", "Typhlosion"], state.team_size
        )
        assert all_options_sniped(state, coach, ["Gengar"], state.team_size)
        state.pokemon_pool["mew"] = make_pokemon("Mew", 3)
        assert not all_options_sniped(
            state, coach, ["Gengar", "Mew"], state.team_size
        )

    def test_first_available_skips_sniped(self):
        from services.draft.bank_service import first_available_in_list

        state = make_state()
        coach = state.coaches[0]
        for name in ("Gengar", "Typhlosion"):
            p = make_pokemon(name, 3)
            p.is_drafted = True
            p.drafted_by = "999"
            state.pokemon_pool[name.lower()] = p
        state.pokemon_pool["mew"] = make_pokemon("Mew", 3)

        assert first_available_in_list(
            state, coach, ["Gengar", "Typhlosion", "Mew"], state.team_size
        ) == "Mew"
        assert first_available_in_list(
            state, coach, ["Gengar", "Typhlosion"], state.team_size
        ) is None

    def test_find_pickable_strict_snipe_no_rest(self):
        state = make_state()
        coach = state.coaches[0]
        g = make_pokemon("Gengar", 3)
        g.is_drafted = True
        g.drafted_by = "999"
        state.pokemon_pool["gengar"] = g

        pick, snipe, rest = find_pickable(
            state, coach, ["Gengar"], BankMode.STRICT, state.team_size
        )
        assert pick is None
        assert snipe == "Gengar"
        assert rest == []

    def test_parse_conditional_branches(self):
        raw = "if:4=Tinkaton>then:Hydreigon,Kingambit|if:4=Scream Tail>then:Scizor|else:Great Tusk"
        branches, default, err = parse_conditional_branches(raw)
        assert err is None
        assert len(branches) == 2
        assert branches[0].if_picked == "Tinkaton"
        assert default == ["Great Tusk"]

    def test_get_coach_pick_in_round(self):
        state = make_state()
        coach = state.coaches[0]
        state.pick_history.append(PickRecord(
            pick_number=1, round_number=2,
            coach_discord_id=coach.discord_id, coach_name=coach.name,
            pokemon_name="Pikachu", points_cost=1, is_makeup=True,
        ))
        state.pick_history.append(PickRecord(
            pick_number=2, round_number=2,
            coach_discord_id=coach.discord_id, coach_name=coach.name,
            pokemon_name="Raichu", points_cost=2,
        ))
        assert get_coach_pick_in_round(state, coach.discord_id, 2) == "Raichu"

    def test_validate_bank_species_blocks_cross_round(self):
        state = make_state()
        urshifu_single = make_pokemon("Urshifu-Single-Strike", 8, pokedex_id=892)
        urshifu_rapid = make_pokemon("Urshifu-Rapid-Strike", 8, pokedex_id=892)
        state.pokemon_pool = {
            urshifu_single.name.lower(): urshifu_single,
            urshifu_rapid.name.lower(): urshifu_rapid,
        }
        bank = PickBank(coach_discord_id="1", division_name="Test")
        bank.set_plan(4, ["Urshifu-Single-Strike"])

        err = validate_bank_species(
            state,
            bank,
            round_number=6,
            priority_lists=[["Urshifu-Rapid-Strike"]],
            all_new_names=["Urshifu-Rapid-Strike"],
            coach_discord_id="1",
        )
        assert err is not None
        key, kwargs = err
        assert key == "bank.species_conflict_other_round"
        assert kwargs["round"] == 4

    def test_validate_bank_species_blocks_same_list(self):
        state = make_state()
        ogerpon_well = make_pokemon("Ogerpon-Wellspring", 6, pokedex_id=1017)
        ogerpon_heat = make_pokemon("Ogerpon-Hearthflame", 6, pokedex_id=1017)
        state.pokemon_pool = {
            ogerpon_well.name.lower(): ogerpon_well,
            ogerpon_heat.name.lower(): ogerpon_heat,
        }

        err = validate_bank_species(
            state,
            None,
            round_number=3,
            priority_lists=[["Ogerpon-Wellspring", "Ogerpon-Hearthflame"]],
            all_new_names=["Ogerpon-Wellspring", "Ogerpon-Hearthflame"],
            coach_discord_id="1",
        )
        assert err is not None
        assert err[0] == "bank.species_conflict_same_list"

    def test_validate_bank_species_allows_different_dex(self):
        state = make_state()
        gengar = make_pokemon("Gengar", 8, pokedex_id=94)
        typh = make_pokemon("Typhlosion", 7, pokedex_id=157)
        state.pokemon_pool = {
            gengar.name.lower(): gengar,
            typh.name.lower(): typh,
        }
        bank = PickBank(coach_discord_id="1", division_name="Test")
        bank.set_plan(2, ["Gengar"])

        err = validate_bank_species(
            state,
            bank,
            round_number=5,
            priority_lists=[["Typhlosion"]],
            all_new_names=["Typhlosion"],
            coach_discord_id="1",
        )
        assert err is None

    def test_validate_bank_pokemon_rejects_unknown_name(self):
        state = make_state()
        coach = state.coaches[0]
        err = validate_bank_pokemon_name(state, coach, "NotAPokemon", state.team_size)
        assert err is not None
        assert err[0] == "bank.invalid_name"

    def test_validate_bank_pokemon_rejects_points_budget(self):
        state = make_state(team_size=3, total_points=10)
        coach = make_coach("1", "Alice", remaining_points=4)
        coach.team = [make_pokemon("Mon1", 6, pokedex_id=1)]
        borderline = make_pokemon("Borderline", 4, pokedex_id=2)
        state.pokemon_pool["borderline"] = borderline
        err = validate_bank_pokemon_name(state, coach, "Borderline", state.team_size)
        assert err is not None
        assert err[0] == "bank.points_budget"

    def test_find_pickable_respects_points_budget(self):
        state = make_state(team_size=3, total_points=10)
        coach = make_coach("1", "Alice", remaining_points=4)
        coach.team = [make_pokemon("Mon1", 6, pokedex_id=1)]
        borderline = make_pokemon("Borderline", 4, pokedex_id=2)
        cheap = make_pokemon("Pikachu", 1, pokedex_id=25)
        state.pokemon_pool["borderline"] = borderline
        state.pokemon_pool["pikachu"] = cheap

        pick, snipe, _ = find_pickable(
            state, coach, ["Borderline", "Pikachu"], BankMode.FALLBACK, state.team_size
        )
        assert pick == "Pikachu"
        assert snipe is None

    def test_validate_bank_pokemon_rejects_team_species_conflict(self):
        state = make_state()
        coach = state.coaches[0]
        defense = make_pokemon("Deoxys-Defense", 16, pokedex_id=386)
        speed = make_pokemon("Deoxys-Speed", 16, pokedex_id=386)
        state.pokemon_pool = {
            defense.name.lower(): defense,
            speed.name.lower(): speed,
        }
        coach.add_pokemon(defense)
        err = validate_bank_pokemon_name(state, coach, "Deoxys-Speed", state.team_size)
        assert err is not None
        assert err[0] == "bank.species_conflict_team"
        assert err[1]["other"] == "Deoxys-Defense"

    def test_find_pickable_skips_team_species_conflict(self):
        state = make_state(team_size=3, total_points=50)
        coach = state.coaches[0]
        defense = make_pokemon("Deoxys-Defense", 8, pokedex_id=386)
        speed = make_pokemon("Deoxys-Speed", 8, pokedex_id=386)
        gengar = make_pokemon("Gengar", 8, pokedex_id=94)
        state.pokemon_pool = {
            defense.name.lower(): defense,
            speed.name.lower(): speed,
            gengar.name.lower(): gengar,
        }
        coach.add_pokemon(defense)
        pick, snipe, _ = find_pickable(
            state, coach, ["Deoxys-Speed", "Gengar"], BankMode.FALLBACK, state.team_size
        )
        assert pick == "Gengar"
        assert snipe is None

    def test_validate_bank_activation_full_bank(self):
        state = make_state()
        coach = state.coaches[0]
        gengar = make_pokemon("Gengar", 3, pokedex_id=94)
        state.pokemon_pool["gengar"] = gengar
        bank = PickBank(coach_discord_id="1", division_name="Test")
        bank.set_plan(3, ["Gengar"])
        assert validate_bank_activation(state, coach, bank) is None

    def test_diagnose_bank_failure_invalid_name(self):
        state = make_state()
        coach = state.coaches[0]
        key, _ = diagnose_bank_failure(
            state, coach, ["FakeMon"], BankMode.FALLBACK, state.team_size, round_number=2
        )
        assert key == "bank.invalid_name"

    def test_resolve_bank_alias_static(self):
        state = make_state()
        alias_mgr = AliasManager(data_dir="data")
        all_names = ["Ogerpon-Wellspring", "Ogerpon-Hearthflame", "Gengar"]
        canonical, err = resolve_bank_pokemon_name("waterpon", all_names, alias_mgr)
        assert err is None
        assert canonical == "Ogerpon-Wellspring"

    def test_resolve_bank_form_ambiguous(self):
        state = make_state()
        alias_mgr = AliasManager(data_dir="data")
        all_names = ["Ogerpon", "Ogerpon-Wellspring", "Ogerpon-Hearthflame"]
        _, err = resolve_bank_pokemon_name("Ogerpon", all_names, alias_mgr)
        assert err is not None
        assert err[0] == "bank.form_ambiguous"

    def test_resolve_bank_landorus_bare_name_is_incarnate(self):
        alias_mgr = AliasManager(data_dir="data")
        all_names = ["Landorus", "Landorus-T", "Dragapult"]
        canonical, err = resolve_bank_pokemon_name("Landorus", all_names, alias_mgr)
        assert err is None
        assert canonical == "Landorus"

    def test_resolve_bank_urshifu_bare_is_single_strike(self):
        alias_mgr = AliasManager(data_dir="data")
        all_names = ["Urshifu", "Urshifu-RS", "Dragapult"]
        canonical, err = resolve_bank_pokemon_name("Urshifu", all_names, alias_mgr)
        assert err is None
        assert canonical == "Urshifu"

    def test_resolve_bank_urshifu_rapid_aliases(self):
        alias_mgr = AliasManager(data_dir="data")
        all_names = ["Urshifu", "Urshifu-RS", "Dragapult"]
        for raw in ("Urshifu-RS", "Urshifu-RapidStrike", "Urshifu-Rapid-Strike", "watershifu"):
            canonical, err = resolve_bank_pokemon_name(raw, all_names, alias_mgr)
            assert err is None, raw
            assert canonical == "Urshifu-RS", raw

    def test_resolve_bank_urshifu_single_aliases(self):
        alias_mgr = AliasManager(data_dir="data")
        all_names = ["Urshifu", "Urshifu-RS", "Dragapult"]
        for raw in ("Urshifu-SS", "Urshifu-SingleStrike", "Urshifu-Single-Strike", "darkshifu"):
            canonical, err = resolve_bank_pokemon_name(raw, all_names, alias_mgr)
            assert err is None, raw
            assert canonical == "Urshifu", raw

    def test_resolve_bank_landorus_therian_aliases(self):
        alias_mgr = AliasManager(data_dir="data")
        all_names = ["Landorus", "Landorus-T", "Dragapult"]
        for raw in ("Landorus-T", "Landorus-Therian", "lando", "landot"):
            canonical, err = resolve_bank_pokemon_name(raw, all_names, alias_mgr)
            assert err is None, raw
            assert canonical == "Landorus-T", raw

    def test_resolve_bank_name_list_fuzzy(self):
        alias_mgr = AliasManager(data_dir="data")
        all_names = ["Scream-Tail", "Gengar"]
        resolved, err, norms = resolve_bank_name_list(
            ["Scream Tail", "Gengar"], all_names, alias_mgr
        )
        assert err is None
        assert resolved == ["Scream-Tail", "Gengar"]
        assert ("Scream Tail", "Scream-Tail") in norms

    def test_resolve_runtime_bank_names_legacy_typo(self):
        state = make_state()
        alias_mgr = AliasManager(data_dir="data")
        state.pokemon_pool["scream-tail"] = make_pokemon(
            "Scream-Tail", 5, pokedex_id=985
        )
        resolved = resolve_runtime_bank_names(
            state, ["Scream Tail"], alias_mgr
        )
        assert resolved == ["Scream-Tail"]
