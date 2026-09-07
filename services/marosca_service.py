"""Random pick ('marosca') with optional pool filters."""

from __future__ import annotations

import random
from typing import Optional

from models.coach import Coach
from models.draft_state import DraftState
from models.pokemon import Pokemon
from services.draft.pick_validator import PickValidator
from services.pokemon_service import PokemonService, normalise_slug


def _pokemon_types(pokemon: Pokemon) -> list[str]:
    return [t.lower() for t in (pokemon.types or []) if t and t != "unknown"]


def _pokemon_abilities(pokemon: Pokemon, pokemon_service: PokemonService | None) -> list[str]:
    if not pokemon_service:
        return []
    slug = pokemon.api_slug or normalise_slug(pokemon.name)
    if slug in pokemon_service._slug_map:
        slug = pokemon_service._slug_map[slug]
    data = pokemon_service._cache.get(slug, {})
    names: list[str] = []
    for entry in data.get("abilities", []):
        ability = entry.get("ability", {})
        name = ability.get("name", "")
        if name:
            names.append(name.lower())
    return names


def filter_available_pool(
    state: DraftState,
    coach: Coach,
    pokemon_service: PokemonService | None = None,
    *,
    min_points: int | None = None,
    max_points: int | None = None,
    type_filter: str | None = None,
    ability_filter: str | None = None,
) -> list[Pokemon]:
    type_q = (type_filter or "").strip().lower()
    ability_q = (ability_filter or "").strip().lower().replace(" ", "-")
    out: list[Pokemon] = []

    for pokemon in state.pokemon_pool.values():
        if pokemon.is_drafted or pokemon.is_banned:
            continue
        ok, _ = PickValidator.validate_pick(coach, pokemon, state.team_size)
        if not ok:
            continue
        if min_points is not None and pokemon.points < min_points:
            continue
        if max_points is not None and pokemon.points > max_points:
            continue
        if type_q and type_q not in _pokemon_types(pokemon):
            continue
        if ability_q:
            abilities = _pokemon_abilities(pokemon, pokemon_service)
            if not any(ability_q in ab or ab in ability_q for ab in abilities):
                continue
        out.append(pokemon)

    return out


def random_marosca_pick(
    state: DraftState,
    coach: Coach,
    pokemon_service: PokemonService | None = None,
    *,
    min_points: int | None = None,
    max_points: int | None = None,
    type_filter: str | None = None,
    ability_filter: str | None = None,
) -> Optional[Pokemon]:
    pool = filter_available_pool(
        state,
        coach,
        pokemon_service,
        min_points=min_points,
        max_points=max_points,
        type_filter=type_filter,
        ability_filter=ability_filter,
    )
    if not pool:
        return None
    return random.choice(pool)


def distinct_types_in_pool(state: DraftState) -> list[str]:
    seen: set[str] = set()
    for pokemon in state.pokemon_pool.values():
        if pokemon.is_drafted or pokemon.is_banned:
            continue
        for t in _pokemon_types(pokemon):
            seen.add(t)
    return sorted(seen)


def distinct_abilities_in_pool(
    state: DraftState,
    pokemon_service: PokemonService | None,
) -> list[str]:
    seen: set[str] = set()
    for pokemon in state.pokemon_pool.values():
        if pokemon.is_drafted or pokemon.is_banned:
            continue
        for ab in _pokemon_abilities(pokemon, pokemon_service):
            seen.add(ab)
    return sorted(seen)
