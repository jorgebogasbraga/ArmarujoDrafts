"""Rebuild draft cursor and rosters from pick history."""

from __future__ import annotations

from constants.draft_constants import SnakeDirection
from models.draft_state import DraftState, PickRecord


def compute_cursor_after_picks(num_coaches: int, completed_normal_picks: int) -> tuple[int, int, SnakeDirection]:
    """Return (coach_index, round, direction) after `completed_normal_picks` snake advances."""
    if num_coaches <= 0:
        return 0, 1, SnakeDirection.FORWARD
    if completed_normal_picks <= 0:
        return 0, 1, SnakeDirection.FORWARD

    coach_index = 0
    current_round = 1
    direction = SnakeDirection.FORWARD

    for _ in range(completed_normal_picks):
        next_idx = coach_index + int(direction)
        if 0 <= next_idx < num_coaches:
            coach_index = next_idx
        else:
            direction = SnakeDirection(int(direction) * -1)
            current_round += 1

    return coach_index, current_round, direction


def rebuild_rosters_from_history(state: DraftState, history: list[PickRecord]) -> None:
    """Reset teams and pool flags, then replay `history` onto coaches."""
    for coach in state.coaches:
        coach.team = []
        coach.recalculate_points(state.total_points)

    for pokemon in state.pokemon_pool.values():
        pokemon.is_drafted = False
        pokemon.drafted_by = None

    for record in history:
        coach = state.get_coach_by_id(record.coach_discord_id)
        pokemon = state.pokemon_pool.get(record.pokemon_name.lower())
        if not coach or not pokemon:
            continue
        coach.add_pokemon(pokemon)

    state.pick_history = list(history)
    state.global_pick_counter = len(history)

    normal_picks = sum(1 for record in history if not record.is_makeup)
    idx, rnd, direction = compute_cursor_after_picks(len(state.coaches), normal_picks)
    state.current_coach_index = idx
    state.current_round = rnd
    state.snake_direction = direction
