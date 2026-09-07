"""Tests for Pokémon pool search helpers."""

from utils.pokemon_search import (
    BANK_MIN_POKEMON_SEARCH_LEN,
    MIN_POKEMON_SEARCH_LEN,
    bank_search_query_ready,
    build_autocomplete_choices,
    filter_pokemon_pool,
    search_query_ready,
)


class _Pokemon:
    def __init__(self, name: str, points: int = 10, types=None):
        self.name = name
        self.points = points
        self.types = types or ["normal"]


POOL = [
    _Pokemon("Gengar", 14, ["ghost", "poison"]),
    _Pokemon("Scream Tail", 12, ["fairy", "psychic"]),
    _Pokemon("Enamorus", 16, ["fairy", "flying"]),
    _Pokemon("Tinkaton", 11, ["fairy", "steel"]),
]


class TestPokemonSearch:
    def test_search_query_ready(self):
        assert not search_query_ready("")
        assert not search_query_ready("ge")
        assert search_query_ready("gen")

    def test_bank_search_query_ready(self):
        assert not bank_search_query_ready("")
        assert bank_search_query_ready("g")
        assert bank_search_query_ready("ge")

    def test_filter_requires_min_length_by_default(self):
        assert len(filter_pokemon_pool(POOL, "ge")) == len(POOL)

    def test_filter_substring(self):
        matched = filter_pokemon_pool(POOL, "tail", require_min_length=False)
        assert [p.name for p in matched] == ["Scream Tail"]

    def test_filter_prefers_prefix_then_points(self):
        matched = filter_pokemon_pool(POOL, "e", require_min_length=False)
        assert matched[0].name == "Enamorus"

    def test_sort_by_points_in_select_options(self):
        from utils.pokemon_search import build_select_options

        options, _ = build_select_options(POOL, query="", page=0)
        assert options[0].label == "Enamorus"
        assert options[1].label == "Gengar"

    def test_autocomplete_empty_before_min_len(self):
        assert build_autocomplete_choices(POOL, "ge") == []

    def test_autocomplete_returns_choices(self):
        choices = build_autocomplete_choices(POOL, "gen")
        assert len(choices) == 1
        assert choices[0].value == "Gengar"
        assert "14 pts" in choices[0].name

    def test_min_len_constants(self):
        assert MIN_POKEMON_SEARCH_LEN == 3
        assert BANK_MIN_POKEMON_SEARCH_LEN == 1
