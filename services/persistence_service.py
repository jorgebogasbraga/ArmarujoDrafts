"""
PersistenceService — saves and restores DraftState from JSON files.

One file per division: data/state_<division_name>.json
Written after every pick and every status change.
On bot restart, DraftService loads any existing state files automatically.

This layer is intentionally simple — see the DB migration guide at the end
of the project for how to replace this with SQLite/PostgreSQL.
"""

import json
import os
import logging
from typing import Optional
from models.draft_state import DraftState

logger = logging.getLogger(__name__)


class PersistenceService:
    def __init__(self, data_dir: str = "data") -> None:
        self.data_dir = data_dir
        os.makedirs(data_dir, exist_ok=True)

    def _path(self, division_name: str) -> str:
        safe = division_name.lower().replace(" ", "_")
        return os.path.join(self.data_dir, f"state_{safe}.json")

    def save(self, state: DraftState) -> None:
        """Serialise and write state to disk. Called after every pick."""
        path = self._path(state.division_name)
        try:
            data = state.to_dict()
            # Write to a temp file first, then rename — prevents corruption on crash
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(tmp, path)
            logger.debug(f"[Persistence] Saved state for {state.division_name}")
        except OSError as e:
            logger.error(f"[Persistence] Failed to save {state.division_name}: {e}")

    def load(self, division_name: str) -> Optional[DraftState]:
        """Load and deserialise state from disk. Returns None if no file exists."""
        path = self._path(division_name)
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            state = DraftState.from_dict(data)
            logger.info(
                f"[Persistence] Restored state for {division_name} "
                f"(status={state.status}, picks={state.global_pick_counter})"
            )
            return state
        except (json.JSONDecodeError, KeyError, OSError) as e:
            logger.error(f"[Persistence] Failed to load {division_name}: {e}")
            return None

    def load_all(self) -> dict[str, DraftState]:
        """Load all saved division states found in data_dir."""
        states: dict[str, DraftState] = {}
        if not os.path.isdir(self.data_dir):
            return states
        for filename in os.listdir(self.data_dir):
            if filename.startswith("state_") and filename.endswith(".json"):
                division_name = filename[6:-5].replace("_", " ").title()
                state = self.load(division_name)
                if state:
                    states[state.division_name] = state
        return states

    def delete(self, division_name: str) -> None:
        """Remove the saved state file for a division (e.g. when draft completes)."""
        path = self._path(division_name)
        if os.path.exists(path):
            os.remove(path)
            logger.info(f"[Persistence] Deleted state for {division_name}")
