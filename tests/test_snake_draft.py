"""Tests for snake draft cursor navigation."""

from constants.draft_constants import SnakeDirection
from tests.helpers import make_state


class TestSnakeDraft:
    def test_forward_through_round(self):
        state = make_state(num_coaches=4)
        assert state.current_coach.name == "Coach1"

        state.advance_snake()
        assert state.current_coach.name == "Coach2"
        assert state.current_round == 1

        state.advance_snake()
        assert state.current_coach.name == "Coach3"

    def test_round_reversal_at_end(self):
        state = make_state(num_coaches=4)
        for _ in range(3):
            state.advance_snake()

        assert state.current_coach.name == "Coach4"
        state.advance_snake()

        assert state.snake_direction == SnakeDirection.BACKWARD
        assert state.current_round == 2
        assert state.current_coach.name == "Coach4"

    def test_snake_full_round_trip(self):
        state = make_state(num_coaches=3)
        order = ["Coach1", "Coach2", "Coach3", "Coach3", "Coach2", "Coach1"]
        for expected in order:
            assert state.current_coach.name == expected
            state.advance_snake()

        assert state.current_round == 3

    def test_next_coach_property(self):
        state = make_state(num_coaches=4)
        assert state.next_coach.name == "Coach2"

        state.current_coach_index = 3
        assert state.next_coach.name == "Coach4"

    def test_is_complete(self):
        state = make_state(num_coaches=2, team_size=2, total_points=10)
        assert not state.is_complete()

        mon_idx = 1
        for coach in state.coaches:
            for _ in range(state.team_size):
                mon = state.get_available_pokemon(f"mon{mon_idx}")
                assert mon is not None, f"Expected mon{mon_idx} to be available"
                coach.add_pokemon(mon)
                mon_idx += 1

        assert state.is_complete()
