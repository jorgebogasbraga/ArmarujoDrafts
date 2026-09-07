"""Trade proposal model."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional
import uuid


class TradeType(str, Enum):
    POOL = "pool"
    DIRECT = "direct"


class TradeStatus(str, Enum):
    AWAITING_ACCEPT = "awaiting_accept"
    AWAITING_MOD = "awaiting_mod"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


@dataclass
class TradeProposal:
    id: str
    division: str
    trade_type: TradeType
    proposer_id: str
    target_id: str
    offering: str
    receiving: str
    points_delta_proposer: int = 0
    points_delta_target: int = 0
    status: TradeStatus = TradeStatus.AWAITING_ACCEPT
    message_id: Optional[int] = None
    channel_id: Optional[int] = None
    created_at: float = 0.0
    approved_by: Optional[str] = None

    @staticmethod
    def new_id() -> str:
        return uuid.uuid4().hex[:12]

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "division": self.division,
            "type": self.trade_type.value,
            "proposer_id": self.proposer_id,
            "target_id": self.target_id,
            "offering": self.offering,
            "receiving": self.receiving,
            "points_delta_a": self.points_delta_proposer,
            "points_delta_b": self.points_delta_target,
            "status": self.status.value,
            "message_id": self.message_id,
            "channel_id": self.channel_id,
            "created_at": self.created_at,
            "approved_by": self.approved_by,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TradeProposal":
        return cls(
            id=data["id"],
            division=data["division"],
            trade_type=TradeType(data.get("type", "direct")),
            proposer_id=data["proposer_id"],
            target_id=data["target_id"],
            offering=data.get("offering", ""),
            receiving=data.get("receiving", ""),
            points_delta_proposer=int(data.get("points_delta_a", 0)),
            points_delta_target=int(data.get("points_delta_b", 0)),
            status=TradeStatus(data.get("status", "awaiting_accept")),
            message_id=data.get("message_id"),
            channel_id=data.get("channel_id"),
            created_at=float(data.get("created_at", 0)),
            approved_by=data.get("approved_by"),
        )
