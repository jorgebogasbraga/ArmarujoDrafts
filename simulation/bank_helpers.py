"""Shared helpers for waiting on pick-bank resolution during simulations."""

from __future__ import annotations

import asyncio
import time

from constants.draft_constants import DraftStatus
from models.coach import Coach
from models.draft_state import DraftState
from services.draft.bank_service import validate_bank_pokemon_name
from services.draft_service import DraftService
from simulation.harness import DraftCommandHarness


def select_valid_bank_priority_lists(
    state: DraftState,
    coach: Coach,
    *,
    names_per_round: int = 3,
) -> tuple[list[str], list[str]]:
    """
    Choose expensive remaining Pokémon that this coach can legally bank.

    Skips names that share a Pokédex # with the coach's team, with each other,
    or that fail points / availability checks — the same rules as `/bank save`.
    """
    candidates = sorted(
        [
            pokemon
            for pokemon in state.pokemon_pool.values()
            if not pokemon.is_drafted and not pokemon.is_banned
        ],
        key=lambda pokemon: pokemon.points,
        reverse=True,
    )
    selected: list[str] = []
    used_dex: set[int] = set()
    needed = names_per_round * 2
    for pokemon in candidates:
        dex = pokemon.species_dex()
        if dex > 0 and dex in used_dex:
            continue
        if validate_bank_pokemon_name(state, coach, pokemon.name, state.team_size):
            continue
        selected.append(pokemon.name)
        if dex > 0:
            used_dex.add(dex)
        if len(selected) >= needed:
            break
    return selected[:names_per_round], selected[names_per_round:needed]


def coach_completed_round_pick(
    state: DraftState,
    coach_discord_id: str,
    round_number: int,
) -> bool:
    """True when the coach has a normal (non-makeup) pick recorded for the round."""
    return any(
        record.coach_discord_id == coach_discord_id
        and record.round_number == round_number
        and not record.is_makeup
        for record in state.pick_history
    )


async def wait_for_bank_round_resolution(
    draft: DraftService,
    harness: DraftCommandHarness,
    division_name: str,
    channel,
    bank_coach_discord_id: str,
    target_round: int,
    *,
    timeout_seconds: float = 180.0,
    pick_delay: float = 1.5,
) -> bool:
    """
    Advance the draft until ``bank_coach`` completes ``target_round``, tolerating
    snipe pauses, fallback auto-picks, and manual `/pick` after all options sniped.
    """
    deadline = time.time() + timeout_seconds

    while time.time() < deadline:
        state = draft._get_state(division_name)
        if not state or state.status != DraftStatus.ACTIVE:
            return False

        if coach_completed_round_pick(state, bank_coach_discord_id, target_round):
            return True

        current = state.current_coach
        if not current:
            return False

        if (
            current.discord_id != bank_coach_discord_id
            or state.current_round < target_round
        ):
            if not await harness.pick_for_current_coach(channel):
                return False
            await asyncio.sleep(pick_delay)
            continue

        if state.bank_snipe_pending:
            await asyncio.sleep(1.0)
            continue

        bank = state.pick_banks.get(bank_coach_discord_id)
        bank_active = bool(bank and bank.is_active)
        bank_coach = state.get_coach_by_id(bank_coach_discord_id)
        all_sniped = bool(bank_coach and bank_coach.bank_all_sniped_exhausted)

        if all_sniped or not bank_active:
            if await harness.pick_for_current_coach(channel):
                await asyncio.sleep(pick_delay)
                continue
            await asyncio.sleep(1.0)
            continue

        await asyncio.sleep(2.0)

    return coach_completed_round_pick(
        draft._get_state(division_name) or state,
        bank_coach_discord_id,
        target_round,
    )
