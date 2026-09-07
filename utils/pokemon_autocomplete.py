"""Shared slash-command autocomplete for Pokémon name options."""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

import discord

from utils.pokemon_search import build_autocomplete_choices, search_query_ready

if TYPE_CHECKING:
    from models.draft_state import DraftState
    from services.draft_service import DraftService

POKEMON_OPTION_DESCRIPTION = "Pokémon name — type 3+ letters for suggestions"
POKEMON_NAME_FILTER_DESCRIPTION = "Filter by name — type 3+ letters for suggestions"


def _sorted_pool(
    state: "DraftState",
    *,
    available_only: bool = False,
    include_banned: bool = False,
) -> list:
    pool = sorted(state.pokemon_pool.values(), key=lambda p: p.name.lower())
    if not include_banned:
        pool = [p for p in pool if not p.is_banned]
    if available_only:
        pool = [p for p in pool if not p.is_drafted]
    return pool


def _sorted_roster(state: "DraftState", coach_id: str) -> list:
    coach = state.get_coach_by_id(coach_id)
    if not coach:
        return []
    return sorted(coach.team, key=lambda p: p.name.lower())


def _choices(pool: list, typed: str) -> list[discord.OptionChoice]:
    if not search_query_ready(typed):
        return []
    return build_autocomplete_choices(pool, typed)


def _state_for_channel(
    draft_service: "DraftService",
    channel_id: int,
) -> tuple[Optional[str], Optional["DraftState"]]:
    division = draft_service.get_division_name_for_channel(channel_id)
    if not division:
        return None, None
    return division, draft_service._get_state(division)


def available_pool_autocomplete(
    draft_service: "DraftService",
    channel_id: int,
    typed: str,
) -> list[discord.OptionChoice]:
    """Undrafted, non-banned pool — for /pick, /makeup_pick, pool trades."""
    _, state = _state_for_channel(draft_service, channel_id)
    if not state:
        return []
    return _choices(_sorted_pool(state, available_only=True), typed)


def pool_name_autocomplete(
    draft_service: "DraftService",
    channel_id: int,
    typed: str,
    *,
    include_drafted: bool = True,
    include_banned: bool = False,
) -> list[discord.OptionChoice]:
    """Division pool names — for /edit_pick, /alias_learn, /search."""
    _, state = _state_for_channel(draft_service, channel_id)
    if not state:
        return []
    return _choices(
        _sorted_pool(
            state,
            available_only=not include_drafted,
            include_banned=include_banned,
        ),
        typed,
    )


def roster_autocomplete(
    draft_service: "DraftService",
    channel_id: int,
    coach_id: str,
    typed: str,
) -> list[discord.OptionChoice]:
    """Coach roster — for trade offering / direct-trade receiving."""
    _, state = _state_for_channel(draft_service, channel_id)
    if not state:
        return []
    return _choices(_sorted_roster(state, coach_id), typed)


def _member_id(value) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, discord.Member):
        return str(value.id)
    if isinstance(value, discord.User):
        return str(value.id)
    text = str(value).strip()
    return text if text.isdigit() else None


def trade_receiving_autocomplete(
    draft_service: "DraftService",
    channel_id: int,
    typed: str,
    *,
    proposer_id: str,
    target_option=None,
) -> list[discord.OptionChoice]:
    """Pool pick when no target; target coach roster when direct trade."""
    target_id = _member_id(target_option)
    if target_id and target_id != proposer_id:
        return roster_autocomplete(draft_service, channel_id, target_id, typed)
    return available_pool_autocomplete(draft_service, channel_id, typed)


def trade_offering_autocomplete(
    draft_service: "DraftService",
    channel_id: int,
    proposer_id: str,
    typed: str,
) -> list[discord.OptionChoice]:
    return roster_autocomplete(draft_service, channel_id, proposer_id, typed)
