"""
Transactional snapshot / rollback for multi-division system tests.

Captures coaches.json, division state files, Participants coach rows, and
tracked Drafting Pool pick cells before a test run. Restore manually via
/rollback_system_test when you are done reviewing results.

Never snapshots or rewrites a full Google Sheet tab (that would wipe formulas).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import time
from typing import TYPE_CHECKING, Any

from config import Config
from simulation.replacement_audit import load_replacements, replacements_path

if TYPE_CHECKING:
    from services.channel_lock_service import ChannelLockService
    from services.draft_service import DraftService
    from services.persistence_service import PersistenceService
    from services.sheets_service import SheetsService


logger = logging.getLogger(__name__)

BACKUP_ROOT = os.path.join(Config.DATA_DIR, "system_test_backup")
MANIFEST_PATH = os.path.join(BACKUP_ROOT, "manifest.json")
BOARD_PICK_CELLS_FILENAME = "board_pick_cells.json"


class SystemTestTransaction:
    def __init__(self, divisions: list[str]) -> None:
        self.divisions = divisions
        self.session_id = str(int(time.time()))
        self.session_dir = os.path.join(BACKUP_ROOT, self.session_id)
        self._coaches_backup: str | None = None
        self._state_backups: dict[str, str | None] = {}
        self._sheets_snapshot_path: str | None = None

    def capture(
        self,
        persistence: PersistenceService,
        coaches_path: str = "coaches.json",
    ) -> None:
        os.makedirs(self.session_dir, exist_ok=True)

        if os.path.isfile(coaches_path):
            dest = os.path.join(self.session_dir, "coaches.json")
            shutil.copy2(coaches_path, dest)
            self._coaches_backup = dest
        else:
            self._coaches_backup = None

        for division in self.divisions:
            path = persistence._path(division)
            if os.path.isfile(path):
                safe = division.lower().replace(" ", "_")
                dest = os.path.join(self.session_dir, f"state_{safe}.json")
                shutil.copy2(path, dest)
                self._state_backups[division] = dest
            else:
                self._state_backups[division] = None

        self._write_manifest()
        logger.info("[SystemTest] Snapshot saved to %s", self.session_dir)

    async def capture_sheets(
        self,
        sheets: SheetsService,
        discord_ids: list[str] | None = None,
    ) -> None:
        if not sheets.is_connected:
            logger.warning("[SystemTest] Sheets not connected — skipping sheet snapshot.")
            return
        os.makedirs(self.session_dir, exist_ok=True)
        cells_path = os.path.join(self.session_dir, BOARD_PICK_CELLS_FILENAME)
        sheets.start_board_pick_tracking(cells_path)
        snapshot = await asyncio.get_running_loop().run_in_executor(
            None, sheets.snapshot_for_system_test, discord_ids or []
        )
        snapshot["board_pick_cells_path"] = cells_path
        path = os.path.join(self.session_dir, "sheets_snapshot.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(snapshot, f)
        self._sheets_snapshot_path = path
        self._write_manifest()
        logger.info(
            "[SystemTest] Sheets snapshot saved (%d participant rows; pick cells tracked live).",
            len(snapshot.get("participants_rows") or []),
        )

    def _write_manifest(self) -> None:
        manifest = {
            "session_id": self.session_id,
            "session_dir": self.session_dir,
            "divisions": self.divisions,
            "coaches_backup": self._coaches_backup,
            "state_backups": self._state_backups,
            "sheets_snapshot": self._sheets_snapshot_path,
            "replacements_log": replacements_path(self.session_dir),
            "created_at": time.time(),
        }
        os.makedirs(BACKUP_ROOT, exist_ok=True)
        with open(os.path.join(self.session_dir, "manifest.json"), "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)
        with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)

    @classmethod
    def load_latest_manifest(cls) -> dict[str, Any] | None:
        if not os.path.isfile(MANIFEST_PATH):
            return None
        try:
            with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return None

    async def rollback(
        self,
        draft: DraftService,
        persistence: PersistenceService,
        channel_lock: ChannelLockService | None = None,
        sheets: SheetsService | None = None,
        *,
        coaches_path: str | None = None,
    ) -> None:
        coaches_path = coaches_path or Config.COACHES_CONFIG_FILE
        logger.info("[SystemTest] Rolling back transaction %s", self.session_id)

        for division in self.divisions:
            state = draft._get_state(division)
            if state:
                draft.timers.cancel_timer(state)
                if channel_lock and state.channel_lock_snapshot:
                    await channel_lock.unlock(state)

        if self._coaches_backup and os.path.isfile(self._coaches_backup):
            shutil.copy2(self._coaches_backup, coaches_path)
            logger.info("[SystemTest] Restored coaches.json")

        for division, backup_path in self._state_backups.items():
            live_path = persistence._path(division)
            if backup_path and os.path.isfile(backup_path):
                shutil.copy2(backup_path, live_path)
                logger.info("[SystemTest] Restored state for %s", division)
            else:
                if os.path.isfile(live_path):
                    os.remove(live_path)
                    logger.info("[SystemTest] Removed state created during test: %s", division)

        sheets_path = self._sheets_snapshot_path
        if sheets and sheets_path and os.path.isfile(sheets_path):
            try:
                with open(sheets_path, "r", encoding="utf-8") as f:
                    snapshot = json.load(f)
                snapshot["replacements"] = load_replacements(self.session_dir)
                cells_path = snapshot.get("board_pick_cells_path") or os.path.join(
                    self.session_dir, BOARD_PICK_CELLS_FILENAME
                )
                if os.path.isfile(cells_path):
                    sheets.start_board_pick_tracking(cells_path)
                await asyncio.get_running_loop().run_in_executor(
                    None, sheets.restore_system_test_edits, snapshot
                )
                logger.info(
                    "[SystemTest] Restored Participants coach rows and cleared test pick cells "
                    "(Drafting Pool formulas were not rewritten)."
                )
            except Exception as e:
                logger.exception("[SystemTest] Sheets restore failed: %s", e)
                raise

        draft.states.clear()
        for name, state in persistence.load_all().items():
            draft.states[name] = state

        if channel_lock:
            await channel_lock.reconcile_on_startup(draft.get_all_states())
        await draft.restore_timers_after_startup()
        draft.set_system_test_session(None)
        logger.info("[SystemTest] Rollback complete.")

    @classmethod
    async def rollback_from_manifest(
        cls,
        manifest: dict[str, Any],
        draft: DraftService,
        persistence: PersistenceService,
        channel_lock: ChannelLockService | None = None,
        sheets: SheetsService | None = None,
    ) -> None:
        tx = cls(manifest["divisions"])
        tx.session_id = manifest["session_id"]
        tx.session_dir = manifest["session_dir"]
        tx._coaches_backup = manifest.get("coaches_backup")
        tx._state_backups = manifest.get("state_backups", {})
        tx._sheets_snapshot_path = manifest.get("sheets_snapshot")
        await tx.rollback(draft, persistence, channel_lock, sheets)

    @staticmethod
    def format_replacement_summary(session_dir: str) -> str:
        entries = load_replacements(session_dir)
        if not entries:
            return "No replacements were recorded during this test."
        lines = []
        for entry in entries:
            old = entry.get("old", {})
            new = entry.get("new", {})
            div = entry.get("division", "?")
            lines.append(
                f"**{div}:** {old.get('name', '?')} (`{old.get('discord_id', '?')}`) "
                f"→ {new.get('name', '?')} (`{new.get('discord_id', '?')}`)"
            )
        return "\n".join(lines)
