"""Tests for random marosca pick filtering."""

from services.marosca_service import filter_available_pool, random_marosca_pick
from tests.helpers import make_coach, make_pokemon, make_state


class TestMaroscaService:
    def test_respects_point_budget(self):
        state = make_state(total_points=10, team_size=3)
        coach = state.coaches[0]
        coach.team = [make_pokemon("Mon1", 1)]

        pool = filter_available_pool(state, coach)
        assert pool
        assert all(p.points <= coach.remaining_points for p in pool)
        for pokemon in pool:
            picks_left = state.team_size - len(coach.team) - 1
            assert coach.remaining_points - pokemon.points >= picks_left

    def test_min_max_points_filter(self):
        state = make_state(total_points=20, team_size=3)
        coach = state.coaches[0]
        pool = filter_available_pool(state, coach, min_points=3, max_points=4)
        assert pool
        assert all(3 <= p.points <= 4 for p in pool)

    def test_type_filter(self):
        state = make_state(total_points=20, team_size=3)
        state.pokemon_pool["fire"] = make_pokemon("Charizard", 3, types=["fire", "flying"])
        coach = state.coaches[0]
        pool = filter_available_pool(state, coach, type_filter="fire")
        assert any(p.name == "Charizard" for p in pool)
        assert all("fire" in [t.lower() for t in p.types] for p in pool)

    def test_random_pick_returns_match(self):
        state = make_state(total_points=20, team_size=3)
        coach = state.coaches[0]
        pick = random_marosca_pick(state, coach, min_points=1, max_points=5)
        assert pick is not None
        assert 1 <= pick.points <= 5

    def test_no_matches_returns_none(self):
        state = make_state(total_points=5, team_size=3)
        coach = state.coaches[0]
        pick = random_marosca_pick(state, coach, min_points=100)
        assert pick is None
