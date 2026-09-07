"""Coach reaction votes for pause/resume when admins are unavailable."""

from __future__ import annotations

PAUSE_EMOJI = "⏸️"
RESUME_EMOJI = "▶️"


class CoachVoteTracker:
    """Tracks reaction votes on coach pause/resume embeds."""

    def __init__(self) -> None:
        self._pending: dict[int, dict] = {}

    def register(self, message_id: int, division: str, vote_type: str) -> None:
        self._pending[message_id] = {
            "division": division,
            "vote_type": vote_type,
            "voters": set(),
        }

    def get(self, message_id: int) -> dict | None:
        return self._pending.get(message_id)

    def add_voter(self, message_id: int, user_id: str) -> tuple[int, dict | None]:
        entry = self._pending.get(message_id)
        if not entry:
            return 0, None
        entry["voters"].add(user_id)
        return len(entry["voters"]), entry

    def clear(self, message_id: int) -> None:
        self._pending.pop(message_id, None)
