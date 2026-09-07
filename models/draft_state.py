import asyncio
from dataclasses import dataclass, field
from typing import Optional

from constants.draft_constants import DraftStatus, SnakeDirection
from models.coach import Coach
from models.draft_event import DraftEvent
from models.pokemon import Pokemon
from models.pick_bank import PickBank, BankSnipePending


@dataclass
class PickRecord:
    """Immutable record of a completed pick."""
    pick_number: int         # Global pick number within the division draft
    round_number: int
    coach_discord_id: str
    coach_name: str
    pokemon_name: str
    points_cost: int
    is_makeup: bool = False
    is_bank: bool = False
    timestamp: float = 0.0   # Unix timestamp
    picked_by_discord_id: Optional[str] = None  # Discord user who ran /pick
    gif_url: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "pick_number": self.pick_number,
            "round_number": self.round_number,
            "coach_discord_id": self.coach_discord_id,
            "coach_name": self.coach_name,
            "pokemon_name": self.pokemon_name,
            "points_cost": self.points_cost,
            "is_makeup": self.is_makeup,
            "is_bank": self.is_bank,
            "timestamp": self.timestamp,
            "picked_by_discord_id": self.picked_by_discord_id,
            "gif_url": self.gif_url,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PickRecord":
        return cls(
            pick_number=data["pick_number"],
            round_number=data["round_number"],
            coach_discord_id=data["coach_discord_id"],
            coach_name=data["coach_name"],
            pokemon_name=data["pokemon_name"],
            points_cost=data["points_cost"],
            is_makeup=data.get("is_makeup", False),
            is_bank=data.get("is_bank", False),
            timestamp=data.get("timestamp", 0.0),
            picked_by_discord_id=data.get("picked_by_discord_id"),
            gif_url=data.get("gif_url"),
        )


@dataclass
class DraftState:
    """
    Full mutable state of a single division's draft.
    One instance per division, stored in DraftService.states[division_name].
    
    This is the single source of truth. Google Sheets is a secondary mirror.
    """
    division_name: str
    channel_id: int
    team_size: int
    total_points: int
    tera_captain_points: int
    pick_time_initial: int
    pick_time_second: int
    pick_time_final: int
    sheet_name: str
    card_block_start_row: int = 3   # ← NOVO: linha onde começa o 1º cartão desta divisão
    replacement_coach_discord_id: Optional[str] = None  # new coach waiting to pick
    replacement_picks_owed: int = 0   # how many picks (makeups + current) remain

    # Ordered list of coaches — defines snake draft order
    coaches: list[Coach] = field(default_factory=list)

    # Draft pool — all Pokémon available for this division
    pokemon_pool: dict[str, Pokemon] = field(default_factory=dict)  # name.lower() → Pokemon

    # Snake draft cursor
    current_coach_index: int = 0
    current_round: int = 1
    snake_direction: SnakeDirection = SnakeDirection.FORWARD
    global_pick_counter: int = 0   # Total picks made so far

    status: DraftStatus = DraftStatus.PENDING

    # All completed picks, in order
    pick_history: list[PickRecord] = field(default_factory=list)

    # Admin log feed (picks, skips, pauses, etc.) — newest appended last
    draft_events: list[DraftEvent] = field(default_factory=list)
    admin_log_message_id: Optional[int] = None

    # Pick banks keyed by coach discord_id
    pick_banks: dict[str, PickBank] = field(default_factory=dict)

    # Snipe pause — current coach's bank primary was taken; waiting before fallback
    bank_snipe_pending: Optional[BankSnipePending] = None

    # Set when the bot shuts down with an active draft (for resume announcements)
    last_shutdown_at: Optional[float] = None

    # Permission overwrites saved when the draft channel is locked on pause
    channel_lock_snapshot: Optional[dict] = None

    # Asyncio timer task handle — cancelled when a pick is made
    _timer_task: Optional[asyncio.Task] = field(default=None, repr=False, compare=False)

    # Asyncio lock — prevents two simultaneous pick operations
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False, compare=False)

    # ── Replace system ──────────────────────────────────────────────────────
    replacement_message_id: Optional[int] = None  # Discord message seeking replacement
    pending_replace_coach_id: Optional[str] = None  # Who is being replaced

    # ── Makeup tracking ─────────────────────────────────────────────────────
    # Makeup picks are processed AFTER each normal pick round for the coach.
    # We track them as a queue: list of (coach_discord_id, round_they_were_skipped)
    makeup_queue: list[tuple[str, int]] = field(default_factory=list)

    # ── Properties ──────────────────────────────────────────────────────────

    @property
    def current_coach(self) -> Optional[Coach]:
        if not self.coaches or self.current_coach_index >= len(self.coaches):
            return None
        return self.coaches[self.current_coach_index]

    @property
    def next_coach(self) -> Optional[Coach]:
        """Return who picks next after the current coach (accounting for snake)."""
        if not self.coaches:
            return None
        next_idx = self.current_coach_index + self.snake_direction
        if 0 <= next_idx < len(self.coaches):
            return self.coaches[next_idx]
        # At the end/start of a round — next round reversal
        if self.snake_direction == SnakeDirection.FORWARD:
            return self.coaches[-1]
        return self.coaches[0]

    @property
    def is_active(self) -> bool:
        return self.status == DraftStatus.ACTIVE

    @property
    def total_picks_expected(self) -> int:
        return len(self.coaches) * self.team_size

    @property
    def picks_remaining(self) -> int:
        return self.total_picks_expected - self.global_pick_counter

    def get_coach_by_id(self, discord_id: str) -> Optional[Coach]:
        for c in self.coaches:
            if c.discord_id == discord_id:
                return c
        return None

    def get_available_pokemon(self, name_lower: str) -> Optional[Pokemon]:
        """Return a Pokémon from the pool if it exists, is not drafted and not banned."""
        p = self.pokemon_pool.get(name_lower)
        if p and not p.is_drafted and not p.is_banned:
            return p
        return None

    def is_complete(self) -> bool:
        return all(
            len(c.team) >= self.team_size for c in self.coaches
        )

    # ── Snake navigation ─────────────────────────────────────────────────────

    def advance_snake(self) -> None:
        """
        Move the snake cursor forward by one slot, handling round reversals.
        Call this AFTER a successful pick is recorded.
        """
        num = len(self.coaches)
        next_idx = self.current_coach_index + int(self.snake_direction)

        if 0 <= next_idx < num:
            self.current_coach_index = next_idx
        else:
            # End of a round — reverse direction, stay on same coach (they pick twice)
            self.snake_direction = SnakeDirection(int(self.snake_direction) * -1)
            self.current_round += 1
            # Current coach doesn't change on the turnaround pick
            # The NEXT call to advance_snake will move them normally

    # ── Serialisation ────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        return {
            "division_name": self.division_name,
            "channel_id": self.channel_id,
            "team_size": self.team_size,
            "total_points": self.total_points,
            "tera_captain_points": self.tera_captain_points,
            "pick_time_initial": self.pick_time_initial,
            "pick_time_second": self.pick_time_second,
            "pick_time_final": self.pick_time_final,
            "sheet_name": self.sheet_name,
            "coaches": [c.to_dict() for c in self.coaches],
            "pokemon_pool": {k: v.to_dict() for k, v in self.pokemon_pool.items()},
            "current_coach_index": self.current_coach_index,
            "current_round": self.current_round,
            "snake_direction": int(self.snake_direction),
            "global_pick_counter": self.global_pick_counter,
            "status": self.status.value,
            "pick_history": [p.to_dict() for p in self.pick_history],
            "draft_events": [e.to_dict() for e in self.draft_events],
            "admin_log_message_id": self.admin_log_message_id,
            "pick_banks": {k: v.to_dict() for k, v in self.pick_banks.items()},
            "bank_snipe_pending": (
                self.bank_snipe_pending.to_dict() if self.bank_snipe_pending else None
            ),
            "replacement_message_id": self.replacement_message_id,
            "pending_replace_coach_id": self.pending_replace_coach_id,
            "makeup_queue": self.makeup_queue,
            "card_block_start_row": self.card_block_start_row,
            "replacement_coach_discord_id": self.replacement_coach_discord_id,
            "replacement_picks_owed": self.replacement_picks_owed,
            "last_shutdown_at": self.last_shutdown_at,
            "channel_lock_snapshot": self.channel_lock_snapshot,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "DraftState":
        state = cls(
            division_name=data["division_name"],
            channel_id=data["channel_id"],
            team_size=data["team_size"],
            total_points=data["total_points"],
            tera_captain_points=data.get("tera_captain_points", 30),
            pick_time_initial=data["pick_time_initial"],
            pick_time_second=data["pick_time_second"],
            pick_time_final=data["pick_time_final"],
            sheet_name=data["sheet_name"],
            current_coach_index=data.get("current_coach_index", 0),
            current_round=data.get("current_round", 1),
            snake_direction=SnakeDirection(data.get("snake_direction", 1)),
            global_pick_counter=data.get("global_pick_counter", 0),
            status=DraftStatus(data.get("status", "pending")),
            replacement_message_id=data.get("replacement_message_id"),
            pending_replace_coach_id=data.get("pending_replace_coach_id"),
            makeup_queue=data.get("makeup_queue", []),
            card_block_start_row=data.get("card_block_start_row", 3),
            replacement_coach_discord_id=data.get("replacement_coach_discord_id"),
            replacement_picks_owed=data.get("replacement_picks_owed", 0),
            last_shutdown_at=data.get("last_shutdown_at"),
            channel_lock_snapshot=data.get("channel_lock_snapshot"),
        )
        snipe_raw = data.get("bank_snipe_pending")
        if snipe_raw:
            state.bank_snipe_pending = BankSnipePending.from_dict(snipe_raw)
        state.coaches = [Coach.from_dict(c) for c in data.get("coaches", [])]
        state.pokemon_pool = {
            k: Pokemon.from_dict(v) for k, v in data.get("pokemon_pool", {}).items()
        }
        state.pick_history = [PickRecord.from_dict(p) for p in data.get("pick_history", [])]
        from models.draft_event import DraftEvent

        state.draft_events = [DraftEvent.from_dict(e) for e in data.get("draft_events", [])]
        state.admin_log_message_id = data.get("admin_log_message_id")
        state.pick_banks = {
            k: PickBank.from_dict(v) for k, v in data.get("pick_banks", {}).items()
        }
        return state
