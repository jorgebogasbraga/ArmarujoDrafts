from __future__ import annotations

"""
SheetsService — Google Sheets integration.

The bot is the source of truth; Sheets is a secondary mirror for the live board.

Draft-board picks share one global write lane with small, paced batches
(values batchUpdate). Near-simultaneous picks from several divisions are
merged into one API call so the live board stays close to Discord without
bursting the Sheets quota.
"""

import asyncio
import json
import logging
import os
import time
from datetime import datetime
from typing import Optional

import gspread
from google.oauth2.service_account import Credentials

from config import Config
from models.pokemon import Pokemon
from services.draft.sheet_geometry import compute_card_cell, compute_division_block_start_row
from services.sheets_write_batch import (
    SheetsWriteRateLimiter,
    board_pick_cell_ref,
    coalesce_board_pick_tasks,
)
from utils.sheet_layout import SheetLayout, get_sheet_layout

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.readonly",
]


def _is_sheets_quota_error(exc: BaseException) -> bool:
    text = str(exc)
    return "429" in text or "Quota exceeded" in text


def resolve_participant_restore_rows(
    snapshot_rows: list[dict],
    replacements: list[dict],
    live_id_to_row: dict[str, int],
) -> list[dict]:
    """
    Pick the Participants rows that /replace actually changed.

    Prefer the live row currently holding the replacement Discord ID, then the
    original ID, then the snapshotted row number. This still works after a
    partial rollback (some coaches already restored, others still replaced).
    """
    if not replacements:
        return list(snapshot_rows)

    by_old_id = {
        str(row.get("discord_id") or "").strip(): dict(row)
        for row in snapshot_rows
        if str(row.get("discord_id") or "").strip()
    }
    resolved: list[dict] = []
    missing: list[str] = []
    for entry in replacements:
        old = entry.get("old") or {}
        new = entry.get("new") or {}
        old_id = str(old.get("discord_id") or "").strip()
        new_id = str(new.get("discord_id") or "").strip()
        snap = by_old_id.get(old_id) or {
            "name": old.get("name", ""),
            "team_name": old.get("team_name", ""),
            "discord_id": old_id,
            "logo_url": old.get("logo_url", ""),
            "timezone": old.get("timezone", ""),
        }
        row_num = (
            live_id_to_row.get(new_id)
            or live_id_to_row.get(old_id)
            or int(snap.get("row") or 0)
        )
        if not row_num:
            missing.append(str(old.get("name") or old_id or "?"))
            continue
        restored = dict(snap)
        restored["row"] = int(row_num)
        restored.setdefault("name", old.get("name", ""))
        restored.setdefault("team_name", old.get("team_name", ""))
        restored.setdefault("discord_id", old_id)
        restored.setdefault("logo_url", old.get("logo_url", ""))
        restored.setdefault("timezone", old.get("timezone", ""))
        resolved.append(restored)
    if missing:
        raise RuntimeError(
            "Could not locate Participants row(s) to restore: " + ", ".join(missing)
        )
    return resolved


class _BoardWriter:
    """Single queue that batches draft-board cell updates across divisions."""

    def __init__(self, service: SheetsService) -> None:
        self._service = service
        self._queue: asyncio.Queue[dict] = asyncio.Queue()
        self._task: Optional[asyncio.Task] = None

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        if self._task is None:
            self._task = loop.create_task(self._worker())

    def enqueue(self, task: dict) -> None:
        self._queue.put_nowait(task)

    async def _worker(self) -> None:
        while True:
            try:
                batch = await self._collect_batch()
                if not batch:
                    continue
                await self._service._flush_board_batch(batch)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("[Sheets] Board writer error: %s", e)

    async def _collect_batch(self) -> list[dict]:
        first = await self._queue.get()
        batch = [first]
        self._queue.task_done()

        flush_seconds = Config.SHEETS_BATCH_FLUSH_SECONDS
        if flush_seconds > 0:
            deadline = asyncio.get_running_loop().time() + flush_seconds
            while len(batch) < Config.SHEETS_BATCH_MAX_SIZE:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    break
                try:
                    item = await asyncio.wait_for(self._queue.get(), timeout=remaining)
                    batch.append(item)
                    self._queue.task_done()
                except asyncio.TimeoutError:
                    break
        else:
            while len(batch) < Config.SHEETS_BATCH_MAX_SIZE:
                try:
                    batch.append(self._queue.get_nowait())
                    self._queue.task_done()
                except asyncio.QueueEmpty:
                    break

        while len(batch) < Config.SHEETS_BATCH_MAX_SIZE:
            try:
                batch.append(self._queue.get_nowait())
                self._queue.task_done()
            except asyncio.QueueEmpty:
                break

        return batch


class SheetsService:
    def __init__(self, layout: Optional[SheetLayout] = None) -> None:
        self._layout = layout or get_sheet_layout()
        self._client: Optional[gspread.Client] = None
        self._spreadsheet: Optional[gspread.Spreadsheet] = None
        self._misc_queue: asyncio.Queue = asyncio.Queue()
        self._misc_worker_task: Optional[asyncio.Task] = None
        self._board_writer: Optional[_BoardWriter] = None
        self._worksheet_cache: dict[str, gspread.Worksheet] = {}
        self._rate_limiter = SheetsWriteRateLimiter(Config.SHEETS_MAX_WRITES_PER_MINUTE)
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._api_lock = asyncio.Lock()
        self._suppress_misc_writes = False
        self._status_log_next_row: Optional[int] = None
        self._board_cell_log_path: Optional[str] = None
        self._tracked_board_cells: list[str] = []

    # ── Initialisation ───────────────────────────────────────────────────────

    def connect(self) -> None:
        try:
            creds = Credentials.from_service_account_file(
                Config.GOOGLE_CREDENTIALS_FILE, scopes=SCOPES
            )
            self._client = gspread.authorize(creds)
            self._spreadsheet = self._client.open_by_key(Config.SPREADSHEET_ID)
            logger.info("[Sheets] Connected to Google Sheets.")
        except FileNotFoundError:
            logger.error("[Sheets] credentials.json not found. Sheets integration disabled.")
        except Exception as e:
            logger.error(f"[Sheets] Connection failed: {e}")

    def start_worker(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._board_writer = _BoardWriter(self)
        self._board_writer.start(loop)
        self._misc_worker_task = loop.create_task(self._misc_write_worker())
        logger.info("[Sheets] Write workers started (shared board lane + misc).")

    @property
    def is_connected(self) -> bool:
        return self._client is not None and self._spreadsheet is not None

    def _get_worksheet(self, sheet_name: str) -> gspread.Worksheet:
        if sheet_name not in self._worksheet_cache:
            self._worksheet_cache[sheet_name] = self._spreadsheet.worksheet(sheet_name)
        return self._worksheet_cache[sheet_name]

    def _ensure_board_writer(self) -> _BoardWriter:
        if self._board_writer is None:
            if self._loop is None:
                raise RuntimeError("SheetsService.start_worker() must run before queueing writes.")
            self._board_writer = _BoardWriter(self)
            self._board_writer.start(self._loop)
        return self._board_writer

    def _sheet_range(self, sheet_name: str, cell_a1: str) -> str:
        if " " in sheet_name or "'" in sheet_name:
            safe = sheet_name.replace("'", "''")
            return f"'{safe}'!{cell_a1}"
        return f"{sheet_name}!{cell_a1}"

    # ── Reading: master Pokémon pool ───────────────────────────────────────────

    def read_master_pool(self, sheet_name: str | None = None) -> list[Pokemon]:
        pool_cfg = self._layout.master_pool
        cols = pool_cfg.get("columns", {})
        header_rows = pool_cfg.get("header_rows", 1)
        banned_value = pool_cfg.get("banned_value", "banned").lower()
        sheet_name = sheet_name or self._layout.master_pool_sheet

        require_points = self._layout.master_pool_requires_points()
        read_range = self._layout.master_pool_read_range()
        name_idx = self._layout.master_pool_column("name", 2)
        points_idx = self._layout.master_pool_column("points", 3)
        tera_idx = self._layout.master_pool_column("tera_tax", None)
        dex_idx = self._layout.master_pool_column("pokedex_id", 1)

        if not self.is_connected:
            logger.warning("[Sheets] Not connected — cannot read master pool.")
            return []
        try:
            sheet = self._get_worksheet(sheet_name)
            rows = sheet.get(read_range) if read_range else sheet.get_all_values()
            pool: list[Pokemon] = []
            skipped = 0

            for row in rows[header_rows:]:
                if len(row) <= name_idx or not row[name_idx].strip():
                    continue

                name = row[name_idx].strip()

                points_raw = row[points_idx].strip() if len(row) > points_idx else ""
                is_banned = points_raw.lower() == banned_value

                try:
                    pokedex_id = int(row[dex_idx].strip()) if len(row) > dex_idx and row[dex_idx].strip() else 0
                except ValueError:
                    pokedex_id = 0

                try:
                    points = 0 if is_banned else int(points_raw)
                except ValueError:
                    # Leagues that mark draftable Pokémon by cost treat anything
                    # that is neither a number nor the ban marker as "not in this draft".
                    if require_points:
                        skipped += 1
                        continue
                    points = 0

                tera_tax_raw = ""
                if tera_idx is not None and len(row) > tera_idx:
                    tera_tax_raw = row[tera_idx].strip()
                try:
                    tera_tax_cost = 0 if is_banned else int(tera_tax_raw)
                except ValueError:
                    tera_tax_cost = 0

                pool.append(Pokemon(
                    name=name,
                    points=points,
                    pokedex_id=pokedex_id,
                    types=["unknown"],
                    sprite_url="",
                    is_banned=is_banned,
                    tera_tax_cost=tera_tax_cost,
                ))

            logger.info(
                "[Sheets] Read %d Pokémon from '%s' (%d rows not in this draft).",
                len(pool),
                sheet_name,
                skipped,
            )
            return pool
        except gspread.exceptions.WorksheetNotFound:
            logger.error(f"[Sheets] Sheet '{sheet_name}' not found.")
            return []
        except Exception as e:
            logger.error(f"[Sheets] Error reading master pool: {e}")
            return []

    # ── Reading: participants ──────────────────────────────────────────────────

    def read_participants_for_division(
        self, division_name: str, all_divisions_config: list[dict]
    ) -> list[dict]:
        if not self.is_connected:
            logger.warning("[Sheets] Not connected — cannot read participants.")
            return []

        div_cfg = next(
            (d for d in all_divisions_config if d["name"].lower() == division_name.lower()),
            None,
        )
        if not div_cfg:
            return []

        num_coaches = div_cfg.get("num_coaches", 0)
        first_coach = div_cfg.get("first_coach", "").strip()
        if num_coaches <= 0:
            logger.error(f"[Sheets] '{division_name}' has no num_coaches configured.")
            return []

        p_cfg = self._layout.participants
        col_map = p_cfg.get("columns", {})

        try:
            sheet = self._get_worksheet(self._layout.participants_sheet)
            all_values = sheet.get(self._layout.participants_read_range())
        except Exception as e:
            logger.error(f"[Sheets] Error reading participants: {e}")
            return []

        order_cfg = self._layout.draft_order()
        if order_cfg:
            return self._participants_by_draft_order(
                all_values, col_map, order_cfg, num_coaches
            )

        start_idx = 0
        if first_coach:
            name_col = col_map.get("name", 0)
            found = False
            for i, row in enumerate(all_values):
                if row and row[name_col].strip().lower() == first_coach.lower():
                    start_idx = i
                    found = True
                    break
            if not found:
                logger.error(f"[Sheets] first_coach '{first_coach}' not found in Participants.")
                return []

        participants = []
        for row in all_values[start_idx : start_idx + num_coaches]:
            entry = self._participant_from_row(row, col_map)
            if entry:
                participants.append(entry)
        return participants

    @staticmethod
    def _participant_from_row(row: list[str], col_map: dict) -> Optional[dict]:
        if not row:
            return None

        def cell(key: str, default_idx: int, fallback: str = "") -> str:
            idx = col_map.get(key, default_idx)
            if idx is None or len(row) <= idx:
                return fallback
            return row[idx].strip() or fallback

        name = cell("name", 0)
        if not name:
            return None
        return {
            "name": name,
            "team_name": cell("team_name", 1),
            "discord_id": cell("discord_id", 2),
            "logo_url": cell("logo_url", 3),
            "timezone": cell("timezone", 5, "GMT+0"),
        }

    def _participants_by_draft_order(
        self,
        all_values: list[list[str]],
        col_map: dict,
        order_cfg: dict,
        num_coaches: int,
    ) -> list[dict]:
        """
        Build the coach list from an explicit draft-order range of coach names.

        The order range is the source of truth for who drafts and in what
        position; the Participants tab only supplies each coach's details.
        """
        order_sheet = order_cfg.get("sheet") or self._layout.participants_sheet
        order_range = order_cfg["range"]
        try:
            raw_order = self._get_worksheet(order_sheet).get(order_range)
        except Exception as e:
            logger.error(
                "[Sheets] Error reading draft order '%s'!%s: %s",
                order_sheet,
                order_range,
                e,
            )
            return []

        ordered_names = [
            row[0].strip() for row in raw_order if row and row[0].strip()
        ][:num_coaches]
        if not ordered_names:
            logger.error(
                "[Sheets] Draft order '%s'!%s is empty.", order_sheet, order_range
            )
            return []

        by_name: dict[str, dict] = {}
        for row in all_values:
            entry = self._participant_from_row(row, col_map)
            if entry:
                by_name.setdefault(entry["name"].strip().lower(), entry)

        participants: list[dict] = []
        missing: list[str] = []
        for name in ordered_names:
            entry = by_name.get(name.lower())
            if entry is None:
                missing.append(name)
                continue
            participants.append(entry)

        if missing:
            logger.error(
                "[Sheets] Draft order names not found in Participants: %s",
                ", ".join(missing),
            )
            return []
        return participants

    def compute_division_block_start_row(
        self, division_name: str, all_divisions_config: list[dict]
    ) -> int:
        return compute_division_block_start_row(
            division_name, all_divisions_config, self._layout
        )

    # ── Writing ────────────────────────────────────────────────────────────────

    def queue_pick_write(
        self,
        division_name: str,
        position_in_division: int,
        pick_index: int,
        pokemon_name: str,
        division_block_start_row: int,
        num_coaches: int,
    ) -> None:
        task = {
            "type": "board_pick",
            "position_in_division": position_in_division,
            "pick_index": pick_index,
            "pokemon_name": pokemon_name,
            "division_block_start_row": division_block_start_row,
            "num_coaches": num_coaches,
        }
        self._record_board_pick_cell(board_pick_cell_ref(task, self._layout))
        self._ensure_board_writer().enqueue(task)

    def suppress_misc_writes(self, enabled: bool) -> None:
        self._suppress_misc_writes = enabled

    def queue_status_write(self, division_name: str, message: str) -> None:
        if self._suppress_misc_writes:
            return
        self._misc_queue.put_nowait({
            "type": "status",
            "division_name": division_name,
            "message": message,
        })

    def queue_match_proposal_write(self, proposal) -> None:
        self._misc_queue.put_nowait({"type": "match_proposal", "proposal": proposal.to_dict()})

    def queue_match_result_write(self, result: dict) -> None:
        self._misc_queue.put_nowait({"type": "match_result", "result": result})

    def queue_trade_write(self, trade: dict) -> None:
        self._misc_queue.put_nowait({"type": "trade", "trade": trade})

    def queue_roster_trade_update(self, task: dict) -> None:
        self._misc_queue.put_nowait({"type": "roster_trade", **task})

    async def _flush_board_batch(self, batch: list[dict]) -> None:
        if not batch or not self.is_connected:
            return

        merged = coalesce_board_pick_tasks(batch, self._layout)
        sheet_name = self._layout.draft_board_sheet
        attempt = 0

        while True:
            try:
                async with self._api_lock:
                    await self._rate_limiter.acquire()
                    await asyncio.get_running_loop().run_in_executor(
                        None,
                        self._execute_board_batch,
                        sheet_name,
                        merged,
                    )
                if len(merged) > 1:
                    logger.debug(
                        "[Sheets] Batched %d picks into one API call.",
                        len(merged),
                    )
                return
            except Exception as e:
                is_quota = _is_sheets_quota_error(e)
                if is_quota:
                    self._rate_limiter.note_rate_limit(60.0)
                    logger.warning(
                        "[Sheets] Rate limited writing board (%d cells); waiting to retry.",
                        len(merged),
                    )
                    continue
                attempt += 1
                logger.warning(
                    "[Sheets] Board batch failed (attempt %d/4): %s",
                    attempt,
                    e,
                )
                if attempt >= 4:
                    logger.error(
                        "[Sheets] Re-queuing board batch (%d picks) after repeated failures.",
                        len(merged),
                    )
                    if self._board_writer is not None:
                        for task in batch:
                            self._board_writer.enqueue(task)
                    return
                await asyncio.sleep(2.0 * attempt)

    BATCH_RANGE_CHUNK = 80

    def _execute_board_batch(
        self,
        sheet_name: str,
        merged: list[tuple[str, str]],
    ) -> None:
        self._batch_set_cells(sheet_name, merged)

    def _batch_set_cells(
        self,
        sheet_name: str,
        pairs: list[tuple[str, str]],
    ) -> None:
        if not pairs or not self.is_connected:
            return
        for offset in range(0, len(pairs), self.BATCH_RANGE_CHUNK):
            chunk = pairs[offset:offset + self.BATCH_RANGE_CHUNK]
            body = {
                "valueInputOption": "RAW",
                "data": [
                    {
                        "range": self._sheet_range(sheet_name, cell),
                        "values": [[value]],
                    }
                    for cell, value in chunk
                ],
            }
            self._values_batch_update_with_retry(body)

    def _values_batch_update_with_retry(self, body: dict, *, attempts: int = 8) -> None:
        last_err: BaseException | None = None
        for attempt in range(1, attempts + 1):
            try:
                self._spreadsheet.values_batch_update(body)
                return
            except Exception as e:
                last_err = e
                if not _is_sheets_quota_error(e) or attempt >= attempts:
                    raise
                wait = min(60.0, 5.0 * (2 ** (attempt - 1)))
                logger.warning(
                    "[Sheets] batchUpdate quota hit (attempt %d/%d); retrying in %.0fs.",
                    attempt,
                    attempts,
                    wait,
                )
                time.sleep(wait)
        if last_err:
            raise last_err

    async def _misc_write_worker(self) -> None:
        while True:
            try:
                task = await self._misc_queue.get()
                for attempt in range(8):
                    try:
                        async with self._api_lock:
                            await self._rate_limiter.acquire()
                            await asyncio.get_running_loop().run_in_executor(
                                None, self._execute_write, task
                            )
                        break
                    except Exception as e:
                        is_quota = _is_sheets_quota_error(e)
                        if is_quota:
                            self._rate_limiter.note_rate_limit(60.0)
                        logger.warning(
                            "[Sheets] Misc write attempt %d/8 failed: %s",
                            attempt + 1,
                            e,
                        )
                        if attempt < 7:
                            await asyncio.sleep(5.0 if is_quota else 2.0)
                        else:
                            logger.error("[Sheets] Dropping misc write task: %s", task.get("type"))
                self._misc_queue.task_done()
            except asyncio.CancelledError:
                logger.info("[Sheets] Misc write worker stopped.")
                break
            except Exception as e:
                logger.error(f"[Sheets] Unexpected misc worker error: {e}")

    def _execute_write(self, task: dict) -> None:
        if not self.is_connected:
            return
        if task["type"] == "board_pick":
            self._write_board_pick(task)
        elif task["type"] == "status":
            self._write_status(task)
        elif task["type"] == "match_proposal":
            self._write_match_proposal(task)
        elif task["type"] == "match_result":
            self._write_match_result(task)
        elif task["type"] == "trade":
            self._write_trade(task)
        elif task["type"] == "roster_trade":
            self._write_roster_trade(task)

    def _write_board_pick(self, task: dict) -> None:
        try:
            cell_ref = compute_card_cell(
                task["position_in_division"],
                task["division_block_start_row"],
                task["pick_index"],
                task["num_coaches"],
                self._layout,
            )
            sheet = self._get_worksheet(self._layout.draft_board_sheet)
            sheet.update(cell_ref, [[task["pokemon_name"]]])
        except Exception as e:
            raise RuntimeError(f"_write_board_pick failed: {e}") from e

    def _write_status(self, task: dict) -> None:
        try:
            sheet = self._get_worksheet(self._layout.status_log_sheet)
            if self._status_log_next_row is None:
                col_a = sheet.col_values(1)
                self._status_log_next_row = len(col_a) + 1
            row = self._status_log_next_row
            self._status_log_next_row = row + 1
            sheet.update(
                f"A{row}:C{row}",
                [[datetime.utcnow().isoformat(), task["division_name"], task["message"]]],
            )
        except Exception as e:
            self._status_log_next_row = None
            raise RuntimeError(f"_write_status failed: {e}") from e

    def _write_match_proposal(self, task: dict) -> None:
        try:
            sheet_name = self._layout.sheet_names.get("match_proposals", "Match Proposals")
            sheet = self._get_worksheet(sheet_name)
            p = task["proposal"]
            col_a = sheet.col_values(1)
            row = len(col_a) + 1 if col_a else 2
            sheet.update(
                f"A{row}:J{row}",
                [[
                    p["id"], p["division"], p["proposer_id"], p["opponent_id"],
                    datetime.utcfromtimestamp(p["scheduled_utc"]).isoformat(),
                    p["status"], str(p.get("message_id") or ""), str(p.get("channel_id") or ""),
                    datetime.utcfromtimestamp(p.get("created_at", 0)).isoformat(),
                    str(p.get("week") or ""),
                ]],
            )
        except Exception as e:
            raise RuntimeError(f"_write_match_proposal failed: {e}") from e

    def _write_match_result(self, task: dict) -> None:
        try:
            sheet_name = self._layout.sheet_names.get("match_results", "Match Results")
            sheet = self._get_worksheet(sheet_name)
            r = task["result"]
            col_a = sheet.col_values(1)
            row = len(col_a) + 1 if col_a else 2
            sheet.update(
                f"A{row}:J{row}",
                [[
                    r.get("division", ""), r.get("week", ""), r.get("coach_a", ""),
                    r.get("coach_b", ""), r.get("winner", ""), r.get("replay_url", ""),
                    r.get("kills_a", 0), r.get("kills_b", 0),
                    r.get("submitted_by", ""), r.get("timestamp", ""),
                ]],
            )
        except Exception as e:
            raise RuntimeError(f"_write_match_result failed: {e}") from e

    def _write_trade(self, task: dict) -> None:
        try:
            sheet_name = self._layout.sheet_names.get("trades", "Trades")
            sheet = self._get_worksheet(sheet_name)
            t = task["trade"]
            col_a = sheet.col_values(1)
            row = len(col_a) + 1 if col_a else 2
            sheet.update(
                f"A{row}:L{row}",
                [[
                    t.get("id", ""), t.get("division", ""), t.get("type", ""),
                    t.get("proposer_id", ""), t.get("target_id", ""),
                    t.get("offering", ""), t.get("receiving", ""),
                    t.get("points_delta_a", 0), t.get("points_delta_b", 0),
                    t.get("status", ""), t.get("created_at", ""), t.get("approved_by", ""),
                ]],
            )
        except Exception as e:
            raise RuntimeError(f"_write_trade failed: {e}") from e

    def _write_roster_trade(self, task: dict) -> None:
        try:
            sheet = self._get_worksheet(self._layout.draft_board_sheet)
            sheet.update(task["cell"], [[task["pokemon_name"]]])
        except Exception as e:
            raise RuntimeError(f"_write_roster_trade failed: {e}") from e

    def snapshot_tabs(self) -> dict[str, list[list[str]]]:
        """Deprecated — full-tab snapshots wipe formulas on restore. Use snapshot_for_system_test."""
        logger.warning(
            "[Sheets] snapshot_tabs() is deprecated and no longer dumps full tabs."
        )
        return {}

    def restore_tabs(self, snapshot: dict) -> None:
        """Deprecated — never clear/rewrite a full tab (that destroys formulas)."""
        logger.warning(
            "[Sheets] restore_tabs() refused: full-tab restore would overwrite formulas."
        )
        self.restore_system_test_edits(snapshot or {})

    def start_board_pick_tracking(self, log_path: str) -> None:
        self._board_cell_log_path = log_path
        self._tracked_board_cells = []
        if os.path.isfile(log_path):
            try:
                with open(log_path, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                if isinstance(saved, list):
                    self._tracked_board_cells = [str(c) for c in saved]
            except (json.JSONDecodeError, OSError):
                pass

    def stop_board_pick_tracking(self) -> None:
        self._flush_board_cell_log()
        self._board_cell_log_path = None

    def _record_board_pick_cell(self, cell: str) -> None:
        if not self._board_cell_log_path or not cell:
            return
        if cell not in self._tracked_board_cells:
            self._tracked_board_cells.append(cell)
            self._flush_board_cell_log()

    def _flush_board_cell_log(self) -> None:
        if not self._board_cell_log_path:
            return
        try:
            os.makedirs(os.path.dirname(self._board_cell_log_path) or ".", exist_ok=True)
            with open(self._board_cell_log_path, "w", encoding="utf-8") as f:
                json.dump(self._tracked_board_cells, f)
        except OSError as e:
            logger.warning("[Sheets] Could not persist board cell log: %s", e)

    def snapshot_for_system_test(self, discord_ids: list[str]) -> dict:
        """Snapshot only Participants cells we may overwrite on /replace. Never the Drafting Pool tab."""
        return {
            "participants_rows": self.snapshot_participant_rows(discord_ids),
            "board_pick_cells": list(self._tracked_board_cells),
        }

    def snapshot_participant_rows(self, discord_ids: list[str]) -> list[dict]:
        if not self.is_connected or not discord_ids:
            return []
        wanted = {str(i).strip() for i in discord_ids if str(i).strip()}
        sheet_cols = self._layout.participants.get("sheet_columns", {})
        start_row = int(self._layout.participants.get("start_row", 5))
        name_col = sheet_cols.get("name", "D")
        try:
            sheet = self._get_worksheet(self._layout.participants_sheet)
            block = sheet.get(f"{name_col}{start_row}:I1000")
            rows: list[dict] = []
            for offset, row in enumerate(block):
                # D=0 name, E=1 team, F=2 discord, G=3 logo, H=4 unused, I=5 tz
                discord_id = row[2].strip() if len(row) > 2 else ""
                if discord_id not in wanted:
                    continue
                rows.append({
                    "row": start_row + offset,
                    "name": row[0].strip() if len(row) > 0 else "",
                    "team_name": row[1].strip() if len(row) > 1 else "",
                    "discord_id": discord_id,
                    "logo_url": row[3].strip() if len(row) > 3 else "",
                    "timezone": row[5].strip() if len(row) > 5 else "",
                })
            logger.info("[Sheets] Snapshotted %d Participants row(s) for rollback.", len(rows))
            return rows
        except Exception as e:
            logger.error("[Sheets] snapshot_participant_rows failed: %s", e)
            return []

    def restore_system_test_edits(self, snapshot: dict) -> None:
        """
        Undo system-test sheet writes without touching formulas:
        - Drafting Pool: clear Pokémon names only in card cells we wrote
        - Participants: restore the coach cells we changed on /replace
        """
        if not self.is_connected:
            return
        cells = list(snapshot.get("board_pick_cells") or [])
        if self._board_cell_log_path and os.path.isfile(self._board_cell_log_path):
            try:
                with open(self._board_cell_log_path, "r", encoding="utf-8") as f:
                    logged = json.load(f)
                if isinstance(logged, list):
                    for cell in logged:
                        if cell not in cells:
                            cells.append(str(cell))
            except (json.JSONDecodeError, OSError):
                pass
        for cell in self._tracked_board_cells:
            if cell not in cells:
                cells.append(cell)
        cells_path = snapshot.get("board_pick_cells_path")
        if cells_path and os.path.isfile(cells_path):
            try:
                with open(cells_path, "r", encoding="utf-8") as f:
                    logged = json.load(f)
                if isinstance(logged, list):
                    for cell in logged:
                        if cell not in cells:
                            cells.append(str(cell))
            except (json.JSONDecodeError, OSError):
                pass
        self._restore_participant_row_cells(
            self._participant_rows_for_restore(snapshot)
        )
        self._clear_board_pick_cells(cells)
        self.stop_board_pick_tracking()
        self._tracked_board_cells = []

    def _map_participant_discord_rows(self) -> dict[str, int]:
        if not self.is_connected:
            return {}
        sheet_cols = self._layout.participants.get("sheet_columns", {})
        discord_col = sheet_cols.get("discord_id", "F")
        sheet = self._get_worksheet(self._layout.participants_sheet)
        col_index = ord(discord_col.upper()) - ord("A") + 1
        mapping: dict[str, int] = {}
        for i, val in enumerate(sheet.col_values(col_index)):
            discord_id = str(val).strip()
            if discord_id:
                mapping[discord_id] = i + 1
        return mapping

    def _participant_rows_for_restore(self, snapshot: dict) -> list[dict]:
        snapshot_rows = list(snapshot.get("participants_rows") or [])
        replacements = list(snapshot.get("replacements") or [])
        if not replacements:
            return snapshot_rows
        try:
            live_map = self._map_participant_discord_rows()
        except Exception as e:
            logger.warning(
                "[Sheets] Could not read live Participants IDs for restore lookup: %s",
                e,
            )
            live_map = {}
        return resolve_participant_restore_rows(snapshot_rows, replacements, live_map)

    def _clear_board_pick_cells(self, cells: list[str]) -> None:
        if not cells:
            return
        sheet_name = self._layout.draft_board_sheet
        unique = list(dict.fromkeys(cells))
        merged = [(cell, "") for cell in unique]
        self._batch_set_cells(sheet_name, merged)
        logger.info(
            "[Sheets] Cleared %d Drafting Pool pick cell(s); formulas elsewhere untouched.",
            len(unique),
        )

    def _restore_participant_row_cells(self, rows: list[dict]) -> None:
        if not rows:
            return
        sheet_cols = self._layout.participants.get("sheet_columns", {})
        name_col = sheet_cols.get("name", "D")
        team_col = sheet_cols.get("team_name", "E")
        discord_col = sheet_cols.get("discord_id", "F")
        logo_col = sheet_cols.get("logo_url", "G")
        tz_col = sheet_cols.get("timezone", "I")
        pairs: list[tuple[str, str]] = []
        for item in rows:
            row_num = int(item["row"])
            pairs.extend([
                (f"{name_col}{row_num}", item.get("name", "")),
                (f"{team_col}{row_num}", item.get("team_name", "")),
                (f"{discord_col}{row_num}", item.get("discord_id", "")),
                (f"{logo_col}{row_num}", item.get("logo_url", "")),
                (f"{tz_col}{row_num}", item.get("timezone", "")),
            ])
        self._batch_set_cells(self._layout.participants_sheet, pairs)
        names = ", ".join(
            f"{item.get('name', '?')} (row {item.get('row')})" for item in rows
        )
        logger.info(
            "[Sheets] Restored %d Participants coach row(s): %s",
            len(rows),
            names,
        )

    # ── Reading: league data ───────────────────────────────────────────────────

    def read_standings(self, division: str) -> list[dict]:
        if not self.is_connected:
            return []
        try:
            sheet_name = self._layout.sheet_names.get("standings", "Standings")
            sheet = self._get_worksheet(sheet_name)
            rows = sheet.get_all_values()
            if len(rows) < 2:
                return []
            out = []
            for row in rows[1:]:
                if len(row) < 6 or not row[0].strip():
                    continue
                if row[0].strip().lower() != division.lower():
                    continue
                out.append({
                    "division": row[0].strip(),
                    "coach": row[1].strip(),
                    "team": row[2].strip(),
                    "wins": int(row[3] or 0),
                    "losses": int(row[4] or 0),
                    "kill_diff": int(row[5] or 0),
                    "tiebreak_winner": row[6].strip() if len(row) > 6 else "",
                })
            return out
        except Exception as e:
            logger.error("[Sheets] read_standings failed: %s", e)
            return []

    def read_match_results(
        self, division: str, week: int | None = None
    ) -> list[dict]:
        if not self.is_connected:
            return []
        try:
            sheet_name = self._layout.sheet_names.get("match_results", "Match Results")
            sheet = self._get_worksheet(sheet_name)
            rows = sheet.get_all_values()
            if len(rows) < 2:
                return []
            out = []
            for row in rows[1:]:
                if len(row) < 6 or not row[0].strip():
                    continue
                if row[0].strip().lower() != division.lower():
                    continue
                row_week = (row[1] or "").strip()
                if week is not None and str(row_week) != str(week):
                    continue
                out.append({
                    "division": row[0].strip(),
                    "week": row_week,
                    "coach_a": row[2].strip() if len(row) > 2 else "",
                    "coach_b": row[3].strip() if len(row) > 3 else "",
                    "winner": row[4].strip() if len(row) > 4 else "",
                    "replay_url": row[5].strip() if len(row) > 5 else "",
                    "kills_a": int(row[6] or 0) if len(row) > 6 else 0,
                    "kills_b": int(row[7] or 0) if len(row) > 7 else 0,
                })
            return out
        except Exception as e:
            logger.error("[Sheets] read_match_results failed: %s", e)
            return []

    def read_kill_stats(self, division: str) -> list[dict]:
        if not self.is_connected:
            return []
        try:
            sheet_name = self._layout.sheet_names.get("kill_stats", "Kill Stats")
            sheet = self._get_worksheet(sheet_name)
            rows = sheet.get_all_values()
            if len(rows) < 2:
                return []
            out = []
            for row in rows[1:]:
                if len(row) < 5 or not row[0].strip():
                    continue
                if row[0].strip().lower() != division.lower():
                    continue
                out.append({
                    "division": row[0].strip(),
                    "coach": row[1].strip(),
                    "pokemon": row[2].strip(),
                    "kills": int(row[3] or 0),
                    "deaths": int(row[4] or 0),
                    "battles": int(row[5] or 0) if len(row) > 5 else 0,
                })
            return out
        except Exception as e:
            logger.error("[Sheets] read_kill_stats failed: %s", e)
            return []

    @staticmethod
    def _tz_to_gmt(tz_name: str) -> str:
        try:
            import pytz
            tz = pytz.timezone(tz_name)
            now = datetime.now(tz)
            offset_seconds = int(now.utcoffset().total_seconds())
            hours = offset_seconds // 3600
            minutes = (abs(offset_seconds) % 3600) // 60
            sign = "+" if hours >= 0 else "-"
            if minutes:
                return f"GMT{sign}{abs(hours)}:{minutes:02d}"
            return f"GMT{sign}{abs(hours)}"
        except Exception:
            return tz_name

    def update_participant_row(
        self,
        old_discord_id: str,
        new_discord_id: str,
        new_name: str,
        new_team_name: str,
        new_timezone: str,
        new_logo_url: str = "",
    ) -> bool:
        if not self.is_connected:
            logger.warning("[Sheets] Not connected — cannot update Participants.")
            return False

        sheet_cols = self._layout.participants.get("sheet_columns", {})
        try:
            sheet = self._get_worksheet(self._layout.participants_sheet)
            discord_col = sheet_cols.get("discord_id", "F")
            col_index = ord(discord_col.upper()) - ord("A") + 1
            col_f = sheet.col_values(col_index)

            target_row = None
            for i, val in enumerate(col_f):
                if val.strip() == str(old_discord_id):
                    target_row = i + 1
                    break

            if not target_row:
                logger.error(
                    f"[Sheets] Could not find discord_id '{old_discord_id}' in Participants."
                )
                return False

            gmt_tz = self._tz_to_gmt(new_timezone)
            name_col = sheet_cols.get("name", "D")
            team_col = sheet_cols.get("team_name", "E")
            logo_col = sheet_cols.get("logo_url", "G")
            tz_col = sheet_cols.get("timezone", "I")

            self._batch_set_cells(
                self._layout.participants_sheet,
                [
                    (f"{name_col}{target_row}", new_name),
                    (f"{team_col}{target_row}", new_team_name),
                    (f"{discord_col}{target_row}", new_discord_id),
                    (f"{logo_col}{target_row}", new_logo_url),
                    (f"{tz_col}{target_row}", gmt_tz),
                ],
            )

            logger.info(
                f"[Sheets] Participants updated: row {target_row} "
                f"'{old_discord_id}' → '{new_discord_id}' ({new_name})"
            )
            return True
        except Exception as e:
            logger.error(f"[Sheets] Failed to update Participants: {e}")
            return False
