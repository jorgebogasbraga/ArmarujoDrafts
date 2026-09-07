import time
from dataclasses import dataclass, field
from typing import Optional
from models.pokemon import Pokemon
from utils.quiet_hours import deadline_from_remaining


@dataclass
class Coach:
    """
    Represents a draft participant (coach) within a division.
    """
    discord_id: str           # Discord user snowflake as string
    name: str                 # Display name
    team_name: str
    team_logo_url: str
    timezone: str             # e.g. "Europe/Lisbon"
    division_name: str

    # Draft state — mutated during the draft
    remaining_points: int = 100
    tera_points_remaining: int = 30
    team: list[Pokemon] = field(default_factory=list)
    skip_count: int = 0
    makeup_picks_owed: int = 0   # Number of makeup picks this coach still needs to make
    is_replaced: bool = False    # Coach was replaced mid-draft
    bank_all_sniped_exhausted: bool = False  # Bank tried but every option was sniped this turn

    # Pick timer — remaining seconds is the source of truth (frozen during bot downtime)
    pick_timer_remaining: Optional[float] = None
    pick_deadline: Optional[float] = None  # Unix timestamp for Discord embeds only

    def sync_pick_deadline(self) -> None:
        """
        Recalculate pick_deadline from remaining seconds (for <t:…> timestamps).

        The deadline skips over the nightly quiet window, so a coach frozen at
        23:00 with two hours left is shown expiring at 11:00, not at 01:00.
        """
        if self.pick_timer_remaining is not None and self.pick_timer_remaining > 0:
            self.pick_deadline = deadline_from_remaining(self.pick_timer_remaining)
        else:
            self.pick_deadline = None

    def set_pick_timer(self, remaining_seconds: float) -> None:
        self.pick_timer_remaining = max(0.0, remaining_seconds)
        self.sync_pick_deadline()

    def clear_pick_timer(self) -> None:
        self.pick_timer_remaining = None
        self.pick_deadline = None

    def to_dict(self) -> dict:
        return {
            "discord_id": self.discord_id,
            "name": self.name,
            "team_name": self.team_name,
            "team_logo_url": self.team_logo_url,
            "timezone": self.timezone,
            "division_name": self.division_name,
            "remaining_points": self.remaining_points,
            "tera_points_remaining": self.tera_points_remaining,
            "team": [p.to_dict() for p in self.team],
            "skip_count": self.skip_count,
            "makeup_picks_owed": self.makeup_picks_owed,
            "is_replaced": self.is_replaced,
            "bank_all_sniped_exhausted": self.bank_all_sniped_exhausted,
            "pick_timer_remaining": self.pick_timer_remaining,
            "pick_deadline": self.pick_deadline,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Coach":
        coach = cls(
            discord_id=data["discord_id"],
            name=data["name"],
            team_name=data["team_name"],
            team_logo_url=data.get("team_logo_url", ""),
            timezone=data.get("timezone", "UTC"),
            division_name=data["division_name"],
            remaining_points=data.get("remaining_points", 100),
            tera_points_remaining=data.get("tera_points_remaining", 30),
            skip_count=data.get("skip_count", 0),
            makeup_picks_owed=data.get("makeup_picks_owed", 0),
            is_replaced=data.get("is_replaced", False),
            bank_all_sniped_exhausted=data.get("bank_all_sniped_exhausted", False),
            pick_timer_remaining=data.get("pick_timer_remaining"),
            pick_deadline=data.get("pick_deadline"),
        )
        if coach.pick_timer_remaining is None and coach.pick_deadline is not None:
            coach.pick_timer_remaining = max(0.0, coach.pick_deadline - time.time())
        coach.sync_pick_deadline()
        coach.team = [Pokemon.from_dict(p) for p in data.get("team", [])]
        return coach

    def add_pokemon(self, pokemon: Pokemon) -> None:
        """Add a drafted Pokémon to this coach's team and deduct points."""
        pokemon.is_drafted = True
        pokemon.drafted_by = self.discord_id
        self.team.append(pokemon)
        self.remaining_points -= pokemon.points

    def recalculate_points(self, total_points: int) -> None:
        """
        Recompute remaining_points from scratch based on the actual team.
        Call this defensively whenever points might have desynced
        (e.g. after a replacement, or before critical validations).
        """
        spent = sum(p.points for p in self.team)
        self.remaining_points = max(0, total_points - spent)

    def can_afford(self, pokemon: Pokemon) -> bool:
        return self.remaining_points >= pokemon.points

    def has_slot(self, team_size: int) -> bool:
        return len(self.team) < team_size

    def team_size(self) -> int:
        return len(self.team)

    def mention(self) -> str:
        return f"<@{self.discord_id}>"

    def __repr__(self) -> str:
        return f"Coach({self.name}, {self.division_name}, {self.remaining_points}pts)"
