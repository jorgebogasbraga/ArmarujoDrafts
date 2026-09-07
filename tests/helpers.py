"""Test helpers — no pytest dependency required."""

from constants.draft_constants import DraftStatus
from models.coach import Coach
from models.draft_state import DraftState
from models.pokemon import Pokemon


def make_pokemon(name: str, points: int, **kwargs) -> Pokemon:
    defaults = {"pokedex_id": 1, "types": ["normal"], "sprite_url": ""}
    defaults.update(kwargs)
    return Pokemon(name=name, points=points, **defaults)


def make_coach(
    discord_id: str,
    name: str,
    remaining_points: int = 100,
) -> Coach:
    return Coach(
        discord_id=discord_id,
        name=name,
        team_name=f"{name} FC",
        team_logo_url="",
        timezone="GMT+0",
        division_name="Test",
        remaining_points=remaining_points,
    )


def make_state(
    num_coaches: int = 4,
    team_size: int = 3,
    total_points: int = 20,
) -> DraftState:
    coaches = [
        make_coach(str(i + 1), f"Coach{i + 1}", remaining_points=total_points)
        for i in range(num_coaches)
    ]
    pool = {
        f"mon{j}": make_pokemon(f"Mon{j}", points=1 + (j % 5))
        for j in range(1, 21)
    }
    return DraftState(
        division_name="TestDivision",
        channel_id=123,
        team_size=team_size,
        total_points=total_points,
        tera_captain_points=10,
        pick_time_initial=3600,
        pick_time_second=1800,
        pick_time_final=900,
        sheet_name="Test",
        coaches=coaches,
        pokemon_pool=pool,
        status=DraftStatus.ACTIVE,
    )
