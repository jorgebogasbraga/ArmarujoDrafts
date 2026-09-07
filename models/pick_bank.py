from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class BankMode(str, Enum):
    """How the pick bank handles a unavailable primary target."""
    FALLBACK = "fallback"   # Try next options; snipe pause only on primary snipe
    STRICT = "strict"       # Only pick primary; snipe pause if primary sniped


class PlanType(str, Enum):
    SIMPLE = "simple"
    CONDITIONAL = "conditional"


@dataclass
class ConditionalBranch:
    """If coach picked `if_picked` in `if_round`, use `then_list` for this round."""
    if_round: int
    if_picked: str
    then_list: list[str]

    def to_dict(self) -> dict:
        return {
            "if_round": self.if_round,
            "if_picked": self.if_picked,
            "then_list": self.then_list,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ConditionalBranch":
        return cls(
            if_round=data["if_round"],
            if_picked=data["if_picked"],
            then_list=list(data["then_list"]),
        )


@dataclass
class PickBankEntry:
    """
    A single round's pick plan.

    Simple: priority_list tried in order (fallback mode skips sniped primaries
    with a pause; strict mode only picks the first entry).

    Conditional: branches checked against prior-round picks; default_list used
    when no branch matches.
    """
    round_number: int
    plan_type: PlanType = PlanType.SIMPLE
    priority_list: list[str] = field(default_factory=list)
    branches: list[ConditionalBranch] = field(default_factory=list)
    default_list: list[str] = field(default_factory=list)

    def all_names(self) -> list[str]:
        """Every Pokémon name referenced in this plan (simple or conditional)."""
        if self.plan_type == PlanType.SIMPLE:
            return list(self.priority_list)
        names: list[str] = []
        for branch in self.branches:
            names.extend(branch.then_list)
        names.extend(self.default_list)
        return names

    def to_dict(self) -> dict:
        return {
            "round_number": self.round_number,
            "plan_type": self.plan_type.value,
            "priority_list": self.priority_list,
            "branches": [b.to_dict() for b in self.branches],
            "default_list": self.default_list,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PickBankEntry":
        plan_type_raw = data.get("plan_type", "simple")
        return cls(
            round_number=data["round_number"],
            plan_type=PlanType(plan_type_raw),
            priority_list=list(data.get("priority_list", [])),
            branches=[ConditionalBranch.from_dict(b) for b in data.get("branches", [])],
            default_list=list(data.get("default_list", [])),
        )


@dataclass
class BankSnipePending:
    """Active snipe pause — waiting before executing fallback picks."""
    coach_discord_id: str
    round_number: int
    sniped_primary: str
    remaining_seconds: float
    deadline: float = 0.0  # Display-only Unix timestamp for embeds
    remaining_priority: list[str] = field(default_factory=list)

    def sync_deadline(self) -> None:
        from utils.quiet_hours import deadline_from_remaining

        if self.remaining_seconds > 0:
            self.deadline = deadline_from_remaining(self.remaining_seconds)
        else:
            self.deadline = 0.0

    def to_dict(self) -> dict:
        return {
            "coach_discord_id": self.coach_discord_id,
            "round_number": self.round_number,
            "sniped_primary": self.sniped_primary,
            "remaining_seconds": self.remaining_seconds,
            "deadline": self.deadline,
            "remaining_priority": self.remaining_priority,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "BankSnipePending":
        remaining = data.get("remaining_seconds")
        deadline = data.get("deadline", 0.0)
        if remaining is None and deadline:
            import time
            remaining = max(0.0, deadline - time.time())
        snipe = cls(
            coach_discord_id=data["coach_discord_id"],
            round_number=data["round_number"],
            sniped_primary=data["sniped_primary"],
            remaining_seconds=float(remaining or 0),
            deadline=deadline,
            remaining_priority=list(data.get("remaining_priority", [])),
        )
        snipe.sync_deadline()
        return snipe


@dataclass
class PickBank:
    coach_discord_id: str
    division_name: str
    is_active: bool = False
    mode: BankMode = BankMode.FALLBACK
    entries: list[PickBankEntry] = field(default_factory=list)

    def get_plan_for_round(self, round_number: int) -> Optional[PickBankEntry]:
        for entry in self.entries:
            if entry.round_number == round_number:
                return entry
        return None

    def has_plan_for_round(self, round_number: int) -> bool:
        return self.get_plan_for_round(round_number) is not None

    def set_plan(self, round_number: int, priority_list: list[str]) -> None:
        self.entries = [e for e in self.entries if e.round_number != round_number]
        self.entries.append(PickBankEntry(
            round_number=round_number,
            plan_type=PlanType.SIMPLE,
            priority_list=priority_list,
        ))
        self.entries.sort(key=lambda e: e.round_number)

    def set_conditional_plan(
        self,
        round_number: int,
        branches: list[ConditionalBranch],
        default_list: list[str],
    ) -> None:
        self.entries = [e for e in self.entries if e.round_number != round_number]
        self.entries.append(PickBankEntry(
            round_number=round_number,
            plan_type=PlanType.CONDITIONAL,
            branches=branches,
            default_list=default_list,
        ))
        self.entries.sort(key=lambda e: e.round_number)

    def remove_plan(self, round_number: int) -> None:
        self.entries = [e for e in self.entries if e.round_number != round_number]

    def activate(self) -> None:
        self.is_active = True

    def deactivate(self) -> None:
        self.is_active = False

    def to_dict(self) -> dict:
        return {
            "coach_discord_id": self.coach_discord_id,
            "division_name": self.division_name,
            "is_active": self.is_active,
            "mode": self.mode.value,
            "entries": [e.to_dict() for e in self.entries],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PickBank":
        bank = cls(
            coach_discord_id=data["coach_discord_id"],
            division_name=data["division_name"],
            is_active=data.get("is_active", False),
            mode=BankMode(data.get("mode", "fallback")),
        )
        bank.entries = [PickBankEntry.from_dict(e) for e in data.get("entries", [])]
        return bank

    def __repr__(self) -> str:
        status = "active" if self.is_active else "inactive"
        return (
            f"PickBank({self.coach_discord_id}, {len(self.entries)} plans, "
            f"{status}, mode={self.mode.value})"
        )
