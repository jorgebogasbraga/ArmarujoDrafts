"""Match scheduling — proposals with JSON + optional Sheets mirror."""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Optional

from models.match_proposal import MatchProposal, MatchProposalStatus

logger = logging.getLogger(__name__)


class MatchService:
    def __init__(self, data_dir: str = "data", sheets=None) -> None:
        self.data_dir = data_dir
        self.sheets = sheets
        os.makedirs(data_dir, exist_ok=True)
        self._path = os.path.join(data_dir, "match_proposals.json")
        self._proposals: dict[str, MatchProposal] = {}
        self._load()

    def _load(self) -> None:
        if not os.path.exists(self._path):
            return
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            for item in raw.get("proposals", []):
                p = MatchProposal.from_dict(item)
                self._proposals[p.id] = p
        except (json.JSONDecodeError, OSError, KeyError) as e:
            logger.error("[MatchService] Failed to load proposals: %s", e)

    def _save(self) -> None:
        data = {"proposals": [p.to_dict() for p in self._proposals.values()]}
        tmp = self._path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, self._path)

    def create_proposal(
        self,
        division: str,
        proposer_id: str,
        opponent_id: str,
        scheduled_utc: float,
        parent_id: Optional[str] = None,
        week: Optional[int] = None,
    ) -> MatchProposal:
        if parent_id and parent_id in self._proposals:
            self._proposals[parent_id].status = MatchProposalStatus.COUNTERED
        proposal = MatchProposal(
            id=MatchProposal.new_id(),
            division=division,
            proposer_id=proposer_id,
            opponent_id=opponent_id,
            scheduled_utc=scheduled_utc,
            parent_id=parent_id,
            created_at=time.time(),
            week=week,
        )
        self._proposals[proposal.id] = proposal
        self._save()
        if self.sheets and self.sheets.is_connected:
            self.sheets.queue_match_proposal_write(proposal)
        return proposal

    def get(self, proposal_id: str) -> Optional[MatchProposal]:
        return self._proposals.get(proposal_id)

    def update(self, proposal: MatchProposal) -> None:
        self._proposals[proposal.id] = proposal
        self._save()
        if self.sheets and self.sheets.is_connected:
            self.sheets.queue_match_proposal_write(proposal)

    def list_pending(self, division: Optional[str] = None) -> list[MatchProposal]:
        out = [
            p for p in self._proposals.values()
            if p.status == MatchProposalStatus.PENDING
        ]
        if division:
            out = [p for p in out if p.division.lower() == division.lower()]
        return sorted(out, key=lambda p: p.created_at)

    def list_for_week(
        self,
        division: str,
        week: int,
        *,
        include_statuses: Optional[set[MatchProposalStatus]] = None,
    ) -> list[MatchProposal]:
        if include_statuses is None:
            include_statuses = {
                MatchProposalStatus.PENDING,
                MatchProposalStatus.CONFIRMED,
            }
        out = [
            p for p in self._proposals.values()
            if p.division.lower() == division.lower()
            and p.week == week
            and p.status in include_statuses
        ]
        return sorted(out, key=lambda p: p.scheduled_utc)

    def set_status(self, proposal_id: str, status: MatchProposalStatus) -> Optional[MatchProposal]:
        p = self.get(proposal_id)
        if not p:
            return None
        p.status = status
        self.update(p)
        return p
