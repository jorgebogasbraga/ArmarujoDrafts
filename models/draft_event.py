"""Structured draft feed events for the admin log board."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class DraftEventType(str, Enum):
    INITIALIZED = "initialized"
    STARTED = "started"
    PICK = "pick"
    SKIP = "skip"
    FORCE_SKIP = "force_skip"
    PAUSE = "pause"
    RESUME = "resume"
    REPLACEMENT_NEEDED = "replacement_needed"
    REPLACEMENT = "replacement"
    COMPLETED = "completed"
    UNDO = "undo"
    GOTO = "goto"
    EDIT = "edit"
    COACH_VOTE_PAUSE = "coach_vote_pause"


@dataclass
class DraftEvent:
    event_type: DraftEventType
    timestamp: float = field(default_factory=time.time)
    coach_name: str = ""
    detail: str = ""
    pick_number: int = 0
    points: int = 0
    is_makeup: bool = False
    is_bank: bool = False
    skip_count: int = 0
    secondary: str = ""

    def to_dict(self) -> dict:
        return {
            "event_type": self.event_type.value,
            "timestamp": self.timestamp,
            "coach_name": self.coach_name,
            "detail": self.detail,
            "pick_number": self.pick_number,
            "points": self.points,
            "is_makeup": self.is_makeup,
            "is_bank": self.is_bank,
            "skip_count": self.skip_count,
            "secondary": self.secondary,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "DraftEvent":
        return cls(
            event_type=DraftEventType(data["event_type"]),
            timestamp=data.get("timestamp", 0.0),
            coach_name=data.get("coach_name", ""),
            detail=data.get("detail", ""),
            pick_number=data.get("pick_number", 0),
            points=data.get("points", 0),
            is_makeup=data.get("is_makeup", False),
            is_bank=data.get("is_bank", False),
            skip_count=data.get("skip_count", 0),
            secondary=data.get("secondary", ""),
        )

    @classmethod
    def from_pick_record(cls, record) -> "DraftEvent":
        return cls(
            event_type=DraftEventType.PICK,
            timestamp=record.timestamp or time.time(),
            coach_name=record.coach_name,
            detail=record.pokemon_name,
            pick_number=record.pick_number,
            points=record.points_cost,
            is_makeup=record.is_makeup,
            is_bank=record.is_bank,
        )
