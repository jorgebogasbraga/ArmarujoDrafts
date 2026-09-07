"""Tests for draft state rebuild helpers."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from constants.draft_constants import SnakeDirection
from models.draft_state import PickRecord
from services.draft.state_rebuild import compute_cursor_after_picks, rebuild_rosters_from_history
from tests.helpers import make_coach, make_pokemon, make_state


def test_compute_cursor_snake_turnaround():
    idx, rnd, direction = compute_cursor_after_picks(4, 4)
    assert idx == 3
    assert rnd == 2
    assert direction == SnakeDirection.BACKWARD

    idx, rnd, direction = compute_cursor_after_picks(4, 8)
    assert idx == 0
    assert rnd == 3
    assert direction == SnakeDirection.FORWARD


def test_rebuild_rosters_from_history():
    state = make_state(num_coaches=2, team_size=2, total_points=20)
    mon_a = make_pokemon("MonA", 3)
    mon_b = make_pokemon("MonB", 4)
    state.pokemon_pool = {mon_a.name.lower(): mon_a, mon_b.name.lower(): mon_b}

    history = [
        PickRecord(1, 1, "1", "Coach1", "MonA", 3),
        PickRecord(2, 1, "2", "Coach2", "MonB", 4),
    ]
    rebuild_rosters_from_history(state, history)

    assert state.global_pick_counter == 2
    assert len(state.coaches[0].team) == 1
    assert len(state.coaches[1].team) == 1
    assert state.current_coach_index == 1
    assert state.current_round == 2
    assert state.snake_direction == SnakeDirection.BACKWARD


if __name__ == "__main__":
    test_compute_cursor_snake_turnaround()
    test_rebuild_rosters_from_history()
    print("OK")
