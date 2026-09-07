"""
Pick bank resolution and snipe detection — pure logic, no I/O.
"""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING, Optional

from models.coach import Coach
from models.pick_bank import BankMode, PickBank, PickBankEntry, PlanType
from services.draft.pick_validator import PickValidator

if TYPE_CHECKING:
    from models.draft_state import DraftState
    from utils.alias_manager import AliasManager


class PrimaryStatus(str, Enum):
    AVAILABLE = "available"
    SNIPE = "snipe"
    UNAVAILABLE = "unavailable"


def get_coach_pick_in_round(state: DraftState, coach_discord_id: str, round_number: int) -> Optional[str]:
    """Return the Pokémon this coach drafted in a given round (normal picks only)."""
    for record in reversed(state.pick_history):
        if (
            record.coach_discord_id == coach_discord_id
            and record.round_number == round_number
            and not record.is_makeup
        ):
            return record.pokemon_name
    return None


def resolve_priority_list(
    entry: PickBankEntry,
    state: DraftState,
    coach_discord_id: str,
) -> list[str]:
    if entry.plan_type == PlanType.SIMPLE:
        return list(entry.priority_list)

    for branch in entry.branches:
        picked = get_coach_pick_in_round(state, coach_discord_id, branch.if_round)
        if picked and picked.lower() == branch.if_picked.lower():
            return list(branch.then_list)

    return list(entry.default_list)


def pool_pokemon_names(state: "DraftState") -> list[str]:
    return [p.name for p in state.pokemon_pool.values()]


def lookup_pool_pokemon(state: "DraftState", name: str):
    """Case-insensitive pool lookup by stored or canonical name."""
    return state.pokemon_pool.get(name.lower())


def _is_base_form_input(raw: str, canonical: str) -> bool:
    """True when the user typed only the species base name (e.g. 'Ogerpon' not a form)."""
    cleaned = raw.strip().lower().replace(" ", "-")
    base = canonical.split("-")[0].split(" ")[0].lower()
    return cleaned == base


def resolve_bank_pokemon_name(
    raw: str,
    all_names: list[str],
    alias_manager: "AliasManager",
) -> tuple[Optional[str], Optional[tuple[str, dict]]]:
    """
    Resolve a user-provided bank Pokémon name to the canonical sheet name.

    Uses the same AliasManager as /pick (static aliases, learned aliases, fuzzy match).
    Returns (canonical_name, error) where error is an i18n (key, kwargs) tuple.
    """
    stripped = raw.strip()
    if not stripped:
        return None, ("bank.invalid_name", {"pokemon": raw})

    canonical, _was_fuzzy = alias_manager.resolve(stripped, all_names)
    if not canonical:
        return None, ("bank.invalid_name", {"pokemon": raw})

    if _is_base_form_input(stripped, canonical):
        related = alias_manager.find_related_forms(canonical, all_names)
        # Two-form sheet pairs: unsuffixed name is the default pick
        # (Landorus = Incarnate, Urshifu = Single Strike).
        if len(related) > 1 and not alias_manager.is_default_form_pair(related):
            return None, (
                "bank.form_ambiguous",
                {
                    "input": stripped,
                    "forms": ", ".join(related),
                },
            )

    return canonical, None


def resolve_bank_name_list(
    raw_names: list[str],
    all_names: list[str],
    alias_manager: "AliasManager",
) -> tuple[Optional[list[str]], Optional[tuple[str, dict]], list[tuple[str, str]]]:
    """
    Resolve every name in a bank priority list.

    Returns:
        (resolved_names, error, normalizations)
        normalizations — [(raw, canonical), ...] for names that changed
    """
    resolved: list[str] = []
    normalizations: list[tuple[str, str]] = []

    for raw in raw_names:
        canonical, err = resolve_bank_pokemon_name(raw, all_names, alias_manager)
        if err:
            return None, err, []
        resolved.append(canonical)
        if canonical.lower() != raw.strip().lower():
            normalizations.append((raw.strip(), canonical))

    return resolved, None, normalizations


def resolve_runtime_bank_names(
    state: "DraftState",
    names: list[str],
    alias_manager: "AliasManager",
) -> list[str]:
    """
    Best-effort resolve for stored bank plans at execution time.
    Falls back to the stored name when resolution fails (legacy plans).
    """
    all_names = pool_pokemon_names(state)
    resolved: list[str] = []
    for name in names:
        if lookup_pool_pokemon(state, name):
            resolved.append(
                next(n for n in all_names if n.lower() == name.lower())
            )
            continue
        canonical, err = resolve_bank_pokemon_name(name, all_names, alias_manager)
        resolved.append(canonical if canonical and not err else name)
    return resolved


def get_primary_status(
    state: DraftState,
    coach: Coach,
    pokemon_name: str,
    team_size: int,
) -> PrimaryStatus:
    p = lookup_pool_pokemon(state, pokemon_name)
    if not p or p.is_banned:
        return PrimaryStatus.UNAVAILABLE
    if p.is_drafted:
        if p.drafted_by == coach.discord_id:
            return PrimaryStatus.UNAVAILABLE
        return PrimaryStatus.SNIPE
    if not coach.can_afford(p):
        return PrimaryStatus.UNAVAILABLE
    ok, _ = PickValidator.validate_points_budget(coach, p, team_size)
    if not ok:
        return PrimaryStatus.UNAVAILABLE
    ok, _, _ = PickValidator.find_species_conflict_on_team(coach, p)
    if not ok:
        return PrimaryStatus.UNAVAILABLE
    return PrimaryStatus.AVAILABLE


def find_pickable(
    state: DraftState,
    coach: Coach,
    priority_list: list[str],
    mode: BankMode,
    team_size: int,
) -> tuple[Optional[str], Optional[str], list[str]]:
    """
    Decide what to do with a priority list.

    Returns:
        (pokemon_to_pick_immediately, sniped_primary_for_pause, remaining_after_snipe)

    - If pokemon_to_pick is set → pick now.
    - If sniped_primary is set → enter snipe pause; remaining_after_snipe is fallback list.
    - If both None → nothing pickable (exhausted).
    """
    if not priority_list:
        return None, None, []

    primary = priority_list[0]
    rest = priority_list[1:]
    status = get_primary_status(state, coach, primary, team_size)

    if status == PrimaryStatus.AVAILABLE:
        return primary, None, []

    if status == PrimaryStatus.SNIPE:
        if mode == BankMode.STRICT:
            return None, primary, []
        return None, primary, rest

    # Primary unavailable (banned, unaffordable, etc.)
    if mode == BankMode.STRICT:
        return None, None, []

    for i, name in enumerate(rest):
        s = get_primary_status(state, coach, name, team_size)
        if s == PrimaryStatus.AVAILABLE:
            return name, None, []
        if s == PrimaryStatus.SNIPE:
            return None, name, rest[i + 1 :]

    return None, None, []


def all_options_sniped(
    state: DraftState,
    coach: Coach,
    priority_list: list[str],
    team_size: int,
) -> bool:
    """True when every name in the list was taken by another coach."""
    if not priority_list:
        return False
    return all(
        get_primary_status(state, coach, name, team_size) == PrimaryStatus.SNIPE
        for name in priority_list
    )


def first_available_in_list(
    state: DraftState,
    coach: Coach,
    priority_list: list[str],
    team_size: int,
) -> Optional[str]:
    """First name in the list that can be drafted right now."""
    for name in priority_list:
        if get_primary_status(state, coach, name, team_size) == PrimaryStatus.AVAILABLE:
            return name
    return None


def parse_conditional_branches(branches_str: str) -> tuple[list, list[str], Optional[str]]:
    """
    Parse conditional plan syntax:

        if:4=Tinkaton>then:Hydreigon,Kingambit|if:4=Scream Tail>then:Scizor|else:Great Tusk

    Returns (branches, default_list, error_message).
    """
    from models.pick_bank import ConditionalBranch

    branches: list[ConditionalBranch] = []
    default_list: list[str] = []

    parts = [p.strip() for p in branches_str.split("|") if p.strip()]
    if not parts:
        return [], [], "No branches provided."

    for part in parts:
        if part.lower().startswith("else:"):
            default_list = [p.strip() for p in part[5:].split(",") if p.strip()]
            continue

        if not part.lower().startswith("if:"):
            return [], [], f"Invalid segment (expected if: or else:): `{part}`"

        try:
            condition, then_part = part.split(">then:", 1)
        except ValueError:
            return [], [], f"Invalid branch format: `{part}`. Use if:R=N>then:A,B"

        cond = condition[3:]  # strip "if:"
        if "=" not in cond:
            return [], [], f"Invalid condition: `{condition}`"
        round_str, picked = cond.split("=", 1)
        try:
            if_round = int(round_str.strip())
        except ValueError:
            return [], [], f"Invalid round number in `{condition}`"

        then_list = [p.strip() for p in then_part.split(",") if p.strip()]
        if not then_list:
            return [], [], f"Empty then-list in `{part}`"

        branches.append(ConditionalBranch(
            if_round=if_round,
            if_picked=picked.strip(),
            then_list=then_list,
        ))

    if not branches:
        return [], [], "At least one if-branch is required (use else: for default)."

    return branches, default_list, None


def entry_all_names(entry: PickBankEntry) -> list[str]:
    """Every Pokémon name referenced in a bank entry."""
    return entry.all_names()


def _species_dex_for_name(state: "DraftState", name: str) -> int:
    pokemon = lookup_pool_pokemon(state, name)
    return pokemon.species_dex() if pokemon else 0


def _duplicate_dex_in_list(
    state: "DraftState", names: list[str]
) -> Optional[tuple[str, str, int]]:
    """Return (name_a, name_b, dex) if two names in the same list share a dex #."""
    seen: dict[int, str] = {}
    for name in names:
        dex = _species_dex_for_name(state, name)
        if dex <= 0:
            continue
        if dex in seen:
            return name, seen[dex], dex
        seen[dex] = name
    return None


def validate_bank_pokemon_name(
    state: "DraftState",
    coach: Coach,
    name: str,
    team_size: int,
) -> Optional[tuple[str, dict]]:
    """Validate a single Pokémon name for bank plans (pool, ban, points, species)."""
    pokemon = lookup_pool_pokemon(state, name)
    if not pokemon:
        return ("bank.invalid_name", {"pokemon": name})

    if pokemon.is_banned:
        return ("bank.pokemon_banned", {"pokemon": name})

    if pokemon.is_drafted:
        if pokemon.drafted_by == coach.discord_id:
            return ("bank.already_owned", {"pokemon": name})
        return ("bank.pokemon_taken", {"pokemon": name})

    for team_mon in coach.team:
        if (
            team_mon.species_dex() > 0
            and team_mon.species_dex() == pokemon.species_dex()
            and team_mon.name.lower() != pokemon.name.lower()
        ):
            return (
                "bank.species_conflict_team",
                {
                    "pokemon": name,
                    "other": team_mon.name,
                    "dex": pokemon.species_dex(),
                },
            )

    ok, taken_name, _ = PickValidator.find_species_conflict_on_team(coach, pokemon)
    if not ok and taken_name:
        return (
            "bank.species_conflict_team",
            {
                "pokemon": name,
                "other": taken_name,
                "dex": pokemon.species_dex(),
            },
        )

    if not coach.can_afford(pokemon):
        return (
            "bank.cannot_afford",
            {
                "pokemon": name,
                "points": pokemon.points,
                "remaining": coach.remaining_points,
            },
        )

    ok, _ = PickValidator.validate_points_budget(coach, pokemon, team_size)
    if not ok:
        picks_left = team_size - len(coach.team) - 1
        return (
            "bank.points_budget",
            {
                "pokemon": name,
                "points": pokemon.points,
                "remaining": coach.remaining_points,
                "picks_left": picks_left,
            },
        )

    return None


def validate_bank_species(
    state: "DraftState",
    bank: Optional[PickBank],
    round_number: int,
    priority_lists: list[list[str]],
    all_new_names: list[str],
    coach_discord_id: str,
) -> Optional[tuple[str, dict]]:
    """
    Block bank plans that reference the same national Pokédex # twice.

    Checks:
    - duplicate dex within each priority sub-list (same round)
    - duplicate dex vs other rounds in the bank
    - duplicate dex vs Pokémon already on the coach's team
    - duplicate dex vs any already-drafted species in the pool
    """
    for names in priority_lists:
        if not names:
            continue
        dup = _duplicate_dex_in_list(state, names)
        if dup:
            return (
                "bank.species_conflict_same_list",
                {"pokemon": dup[0], "other": dup[1], "dex": dup[2]},
            )

    if bank:
        for entry in bank.entries:
            if entry.round_number == round_number:
                continue
            for other_name in entry_all_names(entry):
                other_dex = _species_dex_for_name(state, other_name)
                if other_dex <= 0:
                    continue
                for new_name in all_new_names:
                    new_dex = _species_dex_for_name(state, new_name)
                    if (
                        new_dex > 0
                        and new_dex == other_dex
                        and new_name.lower() != other_name.lower()
                    ):
                        return (
                            "bank.species_conflict_other_round",
                            {
                                "pokemon": new_name,
                                "other": other_name,
                                "dex": new_dex,
                                "round": entry.round_number,
                            },
                        )

    coach = state.get_coach_by_id(coach_discord_id)
    if coach:
        for team_mon in coach.team:
            team_dex = team_mon.species_dex()
            if team_dex <= 0:
                continue
            for new_name in all_new_names:
                new_dex = _species_dex_for_name(state, new_name)
                if new_dex > 0 and new_dex == team_dex:
                    return (
                        "bank.species_conflict_team",
                        {
                            "pokemon": new_name,
                            "other": team_mon.name,
                            "dex": new_dex,
                        },
                    )

    return None


def validate_bank_plan_entry(
    state: "DraftState",
    coach: Coach,
    bank: Optional[PickBank],
    round_number: int,
    priority_lists: list[list[str]],
    all_new_names: list[str],
) -> Optional[tuple[str, dict]]:
    """Full validation when saving a bank plan for one round."""
    species_err = validate_bank_species(
        state,
        bank,
        round_number,
        priority_lists,
        all_new_names,
        coach.discord_id,
    )
    if species_err:
        return species_err

    for name in all_new_names:
        err = validate_bank_pokemon_name(state, coach, name, state.team_size)
        if err:
            return err

    return None


def validate_bank_activation(
    state: "DraftState",
    coach: Coach,
    bank: PickBank,
) -> Optional[tuple[str, dict]]:
    """Validate every plan in an active bank against the current draft state."""
    seen_dex: dict[int, tuple[str, int]] = {}
    for entry in bank.entries:
        for name in entry_all_names(entry):
            dex = _species_dex_for_name(state, name)
            if dex <= 0:
                continue
            if dex in seen_dex:
                prev_name, prev_round = seen_dex[dex]
                if name.lower() != prev_name.lower():
                    return (
                        "bank.species_conflict_other_round",
                        {
                            "pokemon": name,
                            "other": prev_name,
                            "dex": dex,
                            "round": prev_round,
                        },
                    )
            else:
                seen_dex[dex] = (name, entry.round_number)

    for entry in bank.entries:
        for name in entry_all_names(entry):
            err = validate_bank_pokemon_name(state, coach, name, state.team_size)
            if err:
                return err

    return None


def diagnose_bank_failure(
    state: "DraftState",
    coach: Coach,
    priority_list: list[str],
    mode: BankMode,
    team_size: int,
    round_number: int,
) -> tuple[str, dict]:
    """Return the best i18n key + kwargs explaining why a bank plan could not execute."""
    if not priority_list:
        return "bank.failure_empty", {"round": round_number}

    pick, snipe, _ = find_pickable(state, coach, priority_list, mode, team_size)
    if pick or snipe:
        return "bank.exhausted", {"round": round_number}

    for name in priority_list:
        err = validate_bank_pokemon_name(state, coach, name, team_size)
        if err:
            key, kwargs = err
            kwargs.setdefault("round", round_number)
            return key, kwargs

    primary = priority_list[0]
    p = lookup_pool_pokemon(state, primary)
    if p and p.is_drafted and p.drafted_by != coach.discord_id:
        return "bank.failure_sniped", {"pokemon": primary, "round": round_number}

    if mode == BankMode.STRICT and len(priority_list) == 1:
        return "bank.failure_strict_primary", {"pokemon": primary, "round": round_number}

    return "bank.exhausted", {"round": round_number}


def audit_bank_for_coach(
    state: "DraftState",
    coach: Coach,
    bank: PickBank,
) -> list[tuple[str, dict]]:
    """Return all validation issues for a coach's bank (used after replacement)."""
    err = validate_bank_activation(state, coach, bank)
    return [err] if err else []
