"""Unseen Pokémon should be preferred so repeated system tests walk the pool."""

from simulation.coverage import (
    mark_seen,
    prefer_unseen,
    remaining_unseen,
    remember_pool,
    reset_seen,
)
from tests.helpers import make_pokemon


def test_unseen_names_are_tried_first(tmp_path, monkeypatch):
    monkeypatch.setattr("simulation.coverage.Config.DATA_DIR", str(tmp_path))
    mark_seen(["Pikachu"])
    pool = [
        make_pokemon("Pikachu", 8, pokedex_id=25),
        make_pokemon("Mega Malamar", 12, pokedex_id=687),
    ]
    preferred = prefer_unseen(pool)
    assert [p.name for p in preferred] == ["Mega Malamar"]


def test_falls_back_to_everyone_once_the_pool_is_seen(tmp_path, monkeypatch):
    monkeypatch.setattr("simulation.coverage.Config.DATA_DIR", str(tmp_path))
    mark_seen(["Pikachu", "Mega Malamar"])
    pool = [
        make_pokemon("Pikachu", 8, pokedex_id=25),
        make_pokemon("Mega Malamar", 12, pokedex_id=687),
    ]
    assert prefer_unseen(pool) == pool


def test_reset_clears_seen_but_keeps_the_pool(tmp_path, monkeypatch):
    monkeypatch.setattr("simulation.coverage.Config.DATA_DIR", str(tmp_path))
    remember_pool(["Pikachu", "Mega Malamar", "Sylveon"])
    mark_seen(["Pikachu"])
    reset_seen()
    leftovers = remaining_unseen()
    assert {name.lower() for name in leftovers} == {
        "pikachu",
        "mega malamar",
        "sylveon",
    }
