"""
Pure validation logic for draft picks — no I/O, easy to unit test.
"""

from __future__ import annotations

from typing import Optional

from constants.draft_constants import DraftStatus
from models.coach import Coach
from models.draft_state import DraftState
from models.pokemon import Pokemon


class PickValidator:
    """Validates pick eligibility and point budget constraints."""

    @staticmethod
    def validate_draft_status(
        state: DraftState,
        is_makeup: bool,
        coach_discord_id: str,
        *,
        requester_is_staff: bool = False,
    ) -> tuple[bool, Optional[str]]:
        if state.status == DraftStatus.REPLACEMENT_PENDING:
            replacement = state.get_coach_by_id(state.replacement_coach_discord_id or "")
            r_name = replacement.name if replacement else "The replacement coach"

            is_replacement_doing_makeup = (
                is_makeup and coach_discord_id == state.replacement_coach_discord_id
            )
            if not is_replacement_doing_makeup:
                deadline_str = ""
                if replacement and replacement.pick_deadline:
                    ts = int(replacement.pick_deadline)
                    deadline_str = f" They have until <t:{ts}:R>."
                return (
                    False,
                    f"⏸️ The draft is paused while **{r_name}** completes "
                    f"their makeup picks. Please wait.{deadline_str}",
                )
        elif state.status == DraftStatus.PAUSED:
            if not requester_is_staff:
                return False, "__PAUSED_STAFF_ONLY__"
            return True, None
        elif state.status != DraftStatus.ACTIVE:
            return False, f"Draft is not active (status: {state.status.value})."
        return True, None

    @staticmethod
    def validate_team_not_full(coach: Coach, team_size: int) -> tuple[bool, Optional[str]]:
        if len(coach.team) >= team_size:
            return (
                False,
                f"**{coach.name}** already has a full team "
                f"({len(coach.team)}/{team_size} Pokémon).",
            )
        return True, None

    @staticmethod
    def validate_can_afford(coach: Coach, pokemon: Pokemon) -> tuple[bool, Optional[str]]:
        if not coach.can_afford(pokemon):
            return (
                False,
                f"You cannot afford **{pokemon.name}** ({pokemon.points} pts). "
                f"{coach.name} has {coach.remaining_points} pts remaining.",
            )
        return True, None

    @staticmethod
    def validate_points_budget(
        coach: Coach,
        pokemon: Pokemon,
        team_size: int,
    ) -> tuple[bool, Optional[str]]:
        """Ensure the coach can still fill remaining roster slots after this pick."""
        picks_remaining = team_size - len(coach.team) - 1
        points_after = coach.remaining_points - pokemon.points
        if points_after < picks_remaining:
            return (
                False,
                f"You cannot draft **{pokemon.name}** ({pokemon.points} pts) — "
                f"you would only have **{points_after} pts** left for "
                f"**{picks_remaining}** remaining pick(s). "
                f"You need at least **{picks_remaining} pts** to complete your team.",
            )
        return True, None

    @staticmethod
    def validate_pick(
        coach: Coach,
        pokemon: Pokemon,
        team_size: int,
    ) -> tuple[bool, Optional[str]]:
        ok, msg = PickValidator.validate_team_not_full(coach, team_size)
        if not ok:
            return ok, msg
        ok, msg = PickValidator.validate_can_afford(coach, pokemon)
        if not ok:
            return ok, msg
        return PickValidator.validate_points_budget(coach, pokemon, team_size)

    @staticmethod
    def find_species_conflict_on_team(
        coach: Coach,
        pokemon: Pokemon,
    ) -> tuple[bool, Optional[str], Optional[Pokemon]]:
        """
        Block picks that share a national Pokédex # with another Pokémon on the
        same coach's team (e.g. Rotom-Heat + Rotom-Mow for one coach).
        Different coaches may draft different forms of the same species.
        """
        dex = pokemon.species_dex()
        if dex <= 0:
            return True, None, None

        for team_mon in coach.team:
            if team_mon.name.lower() == pokemon.name.lower():
                continue
            if team_mon.species_dex() != dex:
                continue
            return False, team_mon.name, team_mon

        return True, None, None

    @staticmethod
    def find_species_conflict_on_bank(
        state: DraftState,
        coach: Coach,
        pokemon: Pokemon,
    ) -> tuple[bool, Optional[str], Optional[int]]:
        """
        Block picks that share a national Pokédex # with a different form
        already saved in this coach's pick bank.
        Returns (ok, conflicting_bank_name, bank_round).
        """
        dex = pokemon.species_dex()
        if dex <= 0:
            return True, None, None

        bank = state.pick_banks.get(coach.discord_id)
        if not bank:
            return True, None, None

        for entry in bank.entries:
            for name in entry.all_names():
                if name.lower() == pokemon.name.lower():
                    continue
                other = state.pokemon_pool.get(name.lower())
                if other is None or other.species_dex() != dex:
                    continue
                return False, other.name, entry.round_number

        return True, None, None

    @staticmethod
    def find_species_conflict(
        state: DraftState,
        pokemon: Pokemon,
        coach: Coach | None = None,
    ) -> tuple[bool, Optional[str], Optional[Pokemon]]:
        """Backward-compatible wrapper — species dupes are per-coach only."""
        if coach is None:
            return True, None, None
        return PickValidator.find_species_conflict_on_team(coach, pokemon)

    @staticmethod
    def species_conflict_for_pick(
        state: DraftState,
        pokemon: Pokemon,
        coach: Coach,
    ) -> Optional[tuple[str, dict]]:
        """i18n key + kwargs if this pick shares a dex # with the team or pick bank."""
        ok, taken_name, _ = PickValidator.find_species_conflict_on_team(coach, pokemon)
        if not ok:
            return (
                "pick.species_conflict_team",
                {
                    "pokemon": pokemon.name,
                    "other": taken_name,
                    "dex": pokemon.species_dex(),
                },
            )

        ok, taken_name, bank_round = PickValidator.find_species_conflict_on_bank(
            state, coach, pokemon
        )
        if not ok:
            return (
                "pick.species_conflict_bank",
                {
                    "pokemon": pokemon.name,
                    "other": taken_name,
                    "dex": pokemon.species_dex(),
                    "round": bank_round,
                },
            )
        return None

    @staticmethod
    def validate_species_available(
        state: DraftState,
        pokemon: Pokemon,
        coach: Coach,
    ) -> tuple[bool, Optional[str]]:
        err = PickValidator.species_conflict_for_pick(state, pokemon, coach)
        if not err:
            return True, None

        key, kwargs = err
        if key == "pick.species_conflict_bank":
            return (
                False,
                f"**{kwargs['pokemon']}** shares Pokédex #{kwargs['dex']} with "
                f"**{kwargs['other']}** in your Round {kwargs['round']} pick bank. "
                f"Only one form per species is allowed per coach — "
                f"remove that plan from your bank first, or pick a different Pokémon.",
            )
        return (
            False,
            f"**{kwargs['pokemon']}** shares Pokédex #{kwargs['dex']} with "
            f"**{kwargs['other']}**, already on **{coach.name}**'s team. "
            f"Only one form per species is allowed per coach.",
        )

    @staticmethod
    def is_unambiguous_input(raw_input: str, canonical: str) -> bool:
        cleaned_input = raw_input.strip().lower().replace(" ", "-")
        cleaned_canon = canonical.strip().lower().replace(" ", "-")
        return cleaned_input == cleaned_canon
