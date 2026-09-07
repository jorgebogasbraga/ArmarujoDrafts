"""
Coach replacement logic — extracted from DraftService for clarity and testing.
"""

from __future__ import annotations

import logging
from typing import Optional

from constants.draft_constants import DraftStatus
from models.coach import Coach
from models.draft_state import DraftState
from services.draft.bank_service import audit_bank_for_coach

logger = logging.getLogger(__name__)


class ReplacementHandler:
    @staticmethod
    def apply(
        state: DraftState,
        new_coach_discord_id: str,
        new_coach_name: str,
        new_team_name: str = "",
        new_logo_url: str = "",
        new_timezone: str = "GMT+0",
    ) -> tuple[bool, str]:
        if state.status != DraftStatus.WAITING_REPLACE:
            return False, "This division is not awaiting a replacement."

        old_coach = state.get_coach_by_id(state.pending_replace_coach_id or "")
        if not old_coach:
            return False, "Could not find the coach being replaced."

        total_spent = sum(p.points for p in old_coach.team)
        verified_remaining = max(0, state.total_points - total_spent)
        if verified_remaining != old_coach.remaining_points:
            logger.warning(
                "[Replacement] Points mismatch for %s: stored=%d, calculated=%d",
                old_coach.name,
                old_coach.remaining_points,
                verified_remaining,
            )

        new_coach = Coach(
            discord_id=new_coach_discord_id,
            name=new_coach_name,
            team_name=new_team_name or old_coach.team_name,
            team_logo_url=new_logo_url or old_coach.team_logo_url,
            timezone=new_timezone,
            division_name=state.division_name,
            remaining_points=verified_remaining,
            tera_points_remaining=old_coach.tera_points_remaining,
            skip_count=0,
            makeup_picks_owed=0,
        )
        new_coach.team = list(old_coach.team)

        idx = state.coaches.index(old_coach)
        state.coaches[idx] = new_coach
        old_coach.is_replaced = True

        state.makeup_queue = [
            (new_coach_discord_id if cid == old_coach.discord_id else cid, rnd)
            for cid, rnd in state.makeup_queue
        ]

        this_coach_rounds = [
            rnd for cid, rnd in state.makeup_queue if cid == new_coach_discord_id
        ]
        if this_coach_rounds:
            latest_round = max(this_coach_rounds)
            state.makeup_queue = [
                (cid, rnd)
                for cid, rnd in state.makeup_queue
                if not (cid == new_coach_discord_id and rnd == latest_round)
            ]

        if old_coach.discord_id in state.pick_banks:
            bank = state.pick_banks.pop(old_coach.discord_id)
            bank.coach_discord_id = new_coach_discord_id
            state.pick_banks[new_coach_discord_id] = bank

        bank_deactivated_note = ""
        if new_coach_discord_id in state.pick_banks:
            bank = state.pick_banks[new_coach_discord_id]
            if audit_bank_for_coach(state, new_coach, bank):
                bank.deactivate()
                bank_deactivated_note = (
                    " Their inherited pick bank was deactivated because "
                    "the saved plans are no longer valid."
                )

        replacement_makeups = sum(
            1 for cid, _ in state.makeup_queue if cid == new_coach_discord_id
        )
        new_coach.makeup_picks_owed = replacement_makeups
        state.replacement_coach_discord_id = new_coach_discord_id
        state.replacement_picks_owed = replacement_makeups
        state.pending_replace_coach_id = None

        if replacement_makeups > 0:
            state.status = DraftStatus.REPLACEMENT_PENDING
        else:
            state.replacement_coach_discord_id = None
            state.status = DraftStatus.ACTIVE

        if replacement_makeups > 0:
            action_str = (
                f" They must first complete **{replacement_makeups}** makeup pick(s), "
                f"then make their pick for the current round."
            )
        else:
            action_str = (
                " They have no makeups pending — "
                "they just need to make their pick for the current round."
            )

        return True, (
            f"✅ **{new_coach_name}** ({new_coach.team_name}) has joined as replacement "
            f"for **{old_coach.name}**. Draft is paused until replacement completes "
            f"their picks.{action_str}{bank_deactivated_note}"
        )

    @staticmethod
    def get_old_coach(state: DraftState) -> Optional[Coach]:
        return state.get_coach_by_id(state.pending_replace_coach_id or "")
