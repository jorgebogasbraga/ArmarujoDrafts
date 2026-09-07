"""Shared Pokémon pool search for autocomplete and bank wizard filtering."""

from __future__ import annotations

from typing import TYPE_CHECKING, Iterable

import discord

if TYPE_CHECKING:
    from models.pokemon import Pokemon

MIN_POKEMON_SEARCH_LEN = 3
BANK_MIN_POKEMON_SEARCH_LEN = 1
MAX_POKEMON_SUGGESTIONS = 25


def search_query_ready(query: str) -> bool:
    return len(query.strip()) >= MIN_POKEMON_SEARCH_LEN


def bank_search_query_ready(query: str) -> bool:
    return len(query.strip()) >= BANK_MIN_POKEMON_SEARCH_LEN


def filter_pokemon_pool(
    pool: Iterable["Pokemon"],
    query: str,
    *,
    require_min_length: bool = True,
) -> list["Pokemon"]:
    """Return pool entries whose name contains ``query`` (case-insensitive)."""
    items = list(pool)
    q = query.strip()
    if not q:
        return items
    if require_min_length and not search_query_ready(q):
        return items

    needle = q.lower()

    matched = [p for p in items if needle in p.name.lower()]

    def sort_key(p: "Pokemon") -> tuple[int, int, str]:
        name = p.name.lower()
        prefix_rank = 0 if name.startswith(needle) else 1
        return (prefix_rank, -p.points, name)

    matched.sort(key=sort_key)
    return matched


def pokemon_to_select_option(p: "Pokemon") -> discord.SelectOption:
    type_str = "/".join(t.capitalize() for t in p.types[:2]) if p.types else "—"
    return discord.SelectOption(
        label=p.name[:100],
        value=p.name,
        description=f"{p.points} pts · {type_str}"[:100],
    )


def pokemon_to_autocomplete_choice(p: "Pokemon") -> discord.OptionChoice:
    type_str = "/".join(t.capitalize() for t in p.types[:2]) if p.types else "—"
    return discord.OptionChoice(
        name=f"{p.name} ({p.points} pts · {type_str})"[:100],
        value=p.name[:100],
    )


def build_select_options(
    pool: Iterable["Pokemon"],
    *,
    query: str = "",
    page: int = 0,
    page_size: int = MAX_POKEMON_SUGGESTIONS,
    require_min_length: bool = True,
) -> tuple[list[discord.SelectOption], int]:
    filtered = filter_pokemon_pool(pool, query, require_min_length=require_min_length)
    if not query.strip():
        filtered.sort(key=lambda p: (-p.points, p.name.lower()))
    total_pages = max(1, (len(filtered) + page_size - 1) // page_size)
    page = max(0, min(page, total_pages - 1))
    start = page * page_size
    chunk = filtered[start : start + page_size]
    return [pokemon_to_select_option(p) for p in chunk], total_pages


def build_autocomplete_choices(
    pool: Iterable["Pokemon"],
    query: str,
) -> list[discord.OptionChoice]:
    if not search_query_ready(query):
        return []
    filtered = filter_pokemon_pool(pool, query, require_min_length=True)
    return [
        pokemon_to_autocomplete_choice(p)
        for p in filtered[:MAX_POKEMON_SUGGESTIONS]
    ]
