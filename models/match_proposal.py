"""Match scheduling proposal model."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import uuid


class MatchProposalStatus(str, Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    DECLINED = "declined"
    COUNTERED = "countered"
    EXPIRED = "expired"


@dataclass
class MatchProposal:
    id: str
    division: str
    proposer_id: str
    opponent_id: str
    scheduled_utc: float
    status: MatchProposalStatus = MatchProposalStatus.PENDING
    message_id: Optional[int] = None
    channel_id: Optional[int] = None
    parent_id: Optional[str] = None
    created_at: float = 0.0
    week: Optional[int] = None

    @staticmethod
    def new_id() -> str:
        return uuid.uuid4().hex[:12]

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "division": self.division,
            "proposer_id": self.proposer_id,
            "opponent_id": self.opponent_id,
            "scheduled_utc": self.scheduled_utc,
            "status": self.status.value,
            "message_id": self.message_id,
            "channel_id": self.channel_id,
            "parent_id": self.parent_id,
            "created_at": self.created_at,
            "week": self.week,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "MatchProposal":
        return cls(
            id=data["id"],
            division=data["division"],
            proposer_id=data["proposer_id"],
            opponent_id=data["opponent_id"],
            scheduled_utc=float(data["scheduled_utc"]),
            status=MatchProposalStatus(data.get("status", "pending")),
            message_id=data.get("message_id"),
            channel_id=data.get("channel_id"),
            parent_id=data.get("parent_id"),
            created_at=float(data.get("created_at", 0)),
            week=data.get("week"),
        )
