"""
Multi-division end-to-end system test — runs in parallel across all league divisions.

Exercises: initialise + start, /pick, pick bank, /picking, pause, /makeup_pick
while paused, resume, triple-skip + /replace with real replacement profiles,
then /pick (and leftover makeups) until every team is full.

Always rolls back coaches.json and division state files when finished (or on error).
Sheets rollback only clears tracked Drafting Pool pick cells and restores
Participants coach cells changed by /replace — formulas are never rewritten.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field

import discord

from config import Config
from constants.draft_constants import DraftStatus
from services.draft_service import DraftService
from services.public_messages import default_public_text, draft_start_message
from simulation.bank_helpers import select_valid_bank_priority_lists, wait_for_bank_round_resolution
from simulation.harness import DraftCommandHarness
from simulation.coverage import (
    coverage_summary,
    mark_seen,
    remaining_unseen,
    remember_pool,
    reset_seen,
)
from simulation.replacements import (
    replacement_for,
    system_test_channel_id,
    system_test_divisions,
    system_test_max_attempts,
)
from simulation.test_logger import SystemTestLogger
from simulation.timer_helpers import wait_for_natural_timer_skip
from utils.quiet_hours import quiet_hours_suspended
from simulation.transaction import SystemTestTransaction
from utils.division_helper import get_division_config, load_division_config

logger = logging.getLogger(__name__)

SKIP3_COACH_INDEX = 5
BANK_COACH_INDEX = 3
INITIAL_PICKS = 4
BANK_TARGET_ROUND = 4
TIMER_TEST_SECONDS = 5
TIMER_WAIT_BUFFER = 3
PICK_DELAY = 1.5
BANK_RESOLVE_TIMEOUT = 180.0
MAX_NAVIGATION_STEPS = 400
MAX_FINISH_STEPS = 500


@dataclass
class DivisionRun:
    name: str
    channel_id: int
    channel: discord.TextChannel | None = None
    logs: list[str] = field(default_factory=list)

    def log(self, msg: str) -> None:
        self.logs.append(msg)


@dataclass
class SystemTestReport:
    divisions: dict[str, DivisionRun] = field(default_factory=dict)
    success: bool = False
    error: str | None = None
    rolled_back: bool = False
    snapshot_taken: bool = False
    log_paths: dict[str, str] = field(default_factory=dict)

    def get_division(self, name: str) -> DivisionRun:
        if name not in self.divisions:
            self.divisions[name] = DivisionRun(name=name, channel_id=0)
        return self.divisions[name]


class MultiDivisionSystemTest:
    def __init__(
        self,
        bot: discord.Bot,
        draft: DraftService,
        guild: discord.Guild,
        admin_id: str,
        *,
        status_channel: discord.TextChannel | None = None,
    ) -> None:
        self.bot = bot
        self.draft = draft
        self.guild = guild
        self.admin_id = admin_id
        self.status_channel = status_channel
        self.harness = DraftCommandHarness(bot, draft, guild, admin_id)
        self.report = SystemTestReport()
        self._divisions: list[DivisionRun] = []
        self._test_log: SystemTestLogger | None = None

    async def _status(self, msg: str, *, level: str = "info") -> None:
        logger.info("[SystemTest] %s", msg)
        if self._test_log:
            if level == "phase":
                self._test_log.phase(msg)
            elif level == "success":
                self._test_log.success_msg(msg)
            elif level == "error":
                self._test_log.error(msg)
            elif level == "warn":
                self._test_log.warn(msg)
            else:
                self._test_log.log(msg)
        if self.status_channel:
            await self._safe_send(
                self.status_channel, f"```\n🧪 [SYSTEM TEST] {msg}\n```"
            )

    async def _division_status(self, div: DivisionRun, msg: str, *, level: str = "info") -> None:
        div.log(msg)
        if self._test_log:
            log_fn = {
                "phase": self._test_log.phase,
                "success": self._test_log.success_msg,
                "error": self._test_log.error,
                "warn": self._test_log.warn,
            }.get(level, self._test_log.log)
            log_fn(msg, division=div.name)
        if div.channel:
            await self._safe_send(div.channel, f"```\n🧪 [{div.name}] {msg}\n```")
        await asyncio.sleep(0.3)

    @staticmethod
    async def _safe_send(channel, content: str) -> None:
        """Status lines must not abort the test if the channel is locked."""
        try:
            await channel.send(content)
        except discord.Forbidden:
            logger.warning(
                "[SystemTest] Cannot send to #%s — missing Send Messages.",
                getattr(channel, "id", "?"),
            )
        except discord.HTTPException as exc:
            logger.warning("[SystemTest] Failed to send status: %s", exc)

    async def _unmute_test_channels(self) -> None:
        """A leftover pause lock must not mute the bot before Phase 1."""
        lock = self.draft.channel_lock
        if not lock:
            return
        for div in self._divisions:
            existing = self.draft._get_state(div.name)
            if existing:
                await lock.release(existing)
            elif div.channel:
                await lock.ensure_bot_can_send(div.channel)

    def _resolve_divisions(self) -> list[DivisionRun]:
        configured = load_division_config()
        if not configured:
            raise RuntimeError(
                "division_config.json is missing or empty. "
                "That file maps divisions to Discord channels — it is not a draft state file. "
                "Do not delete it when resetting data/state_*.json."
            )
        runs: list[DivisionRun] = []
        for name in system_test_divisions():
            cfg = get_division_config(name)
            if not cfg:
                raise RuntimeError(f"Division '{name}' not found in division_config.json")
            channel_id = system_test_channel_id() or cfg.get("channel_id", 0)
            channel = self.bot.get_channel(channel_id)
            run = DivisionRun(name=name, channel_id=channel_id, channel=channel)
            if channel is None:
                raise RuntimeError(
                    f"Cannot access channel {channel_id} for division {name}. "
                    "Invite the bot and check the system_test.json channel_id."
                )
            runs.append(run)
            self.report.get_division(name).channel_id = channel_id
            self.report.get_division(name).channel = channel
        return runs

    async def _init_and_start(self, div: DivisionRun) -> None:
        await self._division_status(div, "Phase 1 — initialise + start draft", level="phase")

        existing = self.draft._get_state(div.name)
        if existing:
            await self.draft.reset_division(div.name, self.admin_id)
        elif self.draft.channel_lock and div.channel:
            # A previous pause may have locked the channel and then the
            # division was reset — Discord overwrites survive without a snapshot.
            await self.draft.channel_lock.ensure_bot_can_send(div.channel)

        ok, msg = self.draft.initialise_division(div.name, self.admin_id)
        if not ok:
            raise RuntimeError(f"{div.name} init failed: {msg}")

        ok, msg = self.draft.start_draft(div.name, self.admin_id)
        if not ok:
            raise RuntimeError(f"{div.name} start failed: {msg}")

        state = self.draft._get_state(div.name)
        if state and system_test_channel_id():
            state.channel_id = div.channel_id
            self.draft.persistence.save(state)
        if state:
            remember_pool(p.name for p in state.pokemon_pool.values())
        if state and state.current_coach and div.channel:
            embed, view = draft_start_message(state)
            await div.channel.send(
                content=default_public_text(
                    "admin.start_first_pick",
                    mention=state.current_coach.mention(),
                ),
                embed=embed,
                view=view,
            )
        await self._division_status(div, "✓ Draft started", level="success")

    async def _normal_picks(self, div: DivisionRun, count: int) -> None:
        await self._division_status(div, f"Phase 2 — {count} normal /pick(s)", level="phase")
        for i in range(count):
            state = self.draft._get_state(div.name)
            if not state or state.status != DraftStatus.ACTIVE:
                break
            if not await self.harness.pick_for_current_coach(div.channel):
                raise RuntimeError(f"{div.name} pick {i + 1} failed")
            await asyncio.sleep(PICK_DELAY)

    async def _setup_bank(self, div: DivisionRun) -> None:
        state = self.draft._get_state(div.name)
        if not state or len(state.coaches) <= BANK_COACH_INDEX:
            await self._division_status(div, "⏭ Bank setup skipped (not enough coaches)")
            return

        coach = state.coaches[BANK_COACH_INDEX]
        r4, r6 = select_valid_bank_priority_lists(state, coach)
        if not r4 and not r6:
            await self._division_status(div, "⏭ Bank setup skipped (no valid bank targets)")
            return

        await self._division_status(
            div,
            f"Phase 3 — pick bank for {coach.name} (mirrors /bank save)\n"
            f"  R4: {', '.join(r4)}  |  R6: {', '.join(r6)}",
            level="phase",
        )
        if r4:
            ok, msg = self.draft.set_bank_plan(div.name, coach.discord_id, 4, r4)
            if not ok:
                raise RuntimeError(f"{div.name} bank R4 failed: {msg}")
        if r6:
            ok, msg = self.draft.set_bank_plan(div.name, coach.discord_id, 6, r6)
            if not ok:
                raise RuntimeError(f"{div.name} bank R6 failed: {msg}")

        ok, msg = self.draft.activate_bank(div.name, coach.discord_id)
        if not ok:
            raise RuntimeError(f"{div.name} bank activation failed: {msg}")

    async def _wait_for_bank_auto_pick(self, div: DivisionRun) -> None:
        state = self.draft._get_state(div.name)
        if not state or len(state.coaches) <= BANK_COACH_INDEX:
            await self._division_status(div, "⏭ Bank auto-pick skipped (not enough coaches)")
            return

        bank_coach = state.coaches[BANK_COACH_INDEX]
        await self._division_status(
            div,
            f"Phase 3b — advance to Round {BANK_TARGET_ROUND} bank pick for {bank_coach.name} "
            f"(auto-pick, snipe fallback, or manual if all sniped)",
            level="phase",
        )

        ok = await wait_for_bank_round_resolution(
            self.draft,
            self.harness,
            div.name,
            div.channel,
            bank_coach.discord_id,
            BANK_TARGET_ROUND,
            timeout_seconds=BANK_RESOLVE_TIMEOUT,
            pick_delay=PICK_DELAY,
        )
        if not ok:
            raise RuntimeError(
                f"{div.name} Round {BANK_TARGET_ROUND} did not resolve for {bank_coach.name} "
                f"within {int(BANK_RESOLVE_TIMEOUT)}s (bank/snipe/fallback/manual)"
            )

        await self._division_status(
            div,
            f"✓ Round {BANK_TARGET_ROUND} pick completed for {bank_coach.name}",
            level="success",
        )

    async def _create_makeup_via_skip(self, div: DivisionRun) -> None:
        await self._division_status(div, "Phase 5 — timer skip to create makeup queue", level="phase")
        state = self.draft._get_state(div.name)
        if not state or not state.current_coach:
            return

        coach_id = state.current_coach.discord_id
        if not await wait_for_natural_timer_skip(
            self.draft,
            div.name,
            coach_id,
            timer_seconds=TIMER_TEST_SECONDS,
            wait_buffer=TIMER_WAIT_BUFFER,
        ):
            raise RuntimeError(f"{div.name} timer skip did not fire naturally")

        state = self.draft._get_state(div.name)
        if not state or not state.makeup_queue:
            raise RuntimeError(f"{div.name} failed to create makeup entry")

    async def _picking_check(self, div: DivisionRun) -> None:
        await self._division_status(div, "Phase 4 — /picking", level="phase")
        await self.harness.invoke_picking(div.channel)

    async def _pause(self, div: DivisionRun) -> None:
        await self._division_status(div, "Phase 6 — /pause_draft", level="phase")
        await self.harness.invoke_pause(div.channel, f"System test pause — {div.name}")
        await asyncio.sleep(1)
        state = self.draft._get_state(div.name)
        if not state or state.status != DraftStatus.PAUSED:
            raise RuntimeError(f"{div.name} not paused")

    async def _makeup_while_paused(self, div: DivisionRun) -> None:
        await self._division_status(div, "Phase 7 — /makeup_pick while paused (staff)", level="phase")
        state = self.draft._get_state(div.name)
        if not state or not state.makeup_queue:
            raise RuntimeError(f"{div.name} no makeup to test")

        coach_id, _rnd = state.makeup_queue[0]
        coach = state.get_coach_by_id(coach_id)
        if not coach:
            raise RuntimeError(f"{div.name} makeup coach missing")

        pick = self.harness.weighted_pick(state, coach) or self.harness.cheapest_valid_pick(
            state, coach, set()
        )
        if not pick:
            raise RuntimeError(f"{div.name} no pick for makeup")

        await self.harness.invoke_makeup(
            div.channel,
            coach,
            pick.name,
            as_staff=True,
        )
        await asyncio.sleep(PICK_DELAY)

    async def _resume(self, div: DivisionRun) -> None:
        await self._division_status(div, "Phase 8 — /resume_draft", level="phase")
        await self.harness.invoke_resume(div.channel)
        await asyncio.sleep(1)
        state = self.draft._get_state(div.name)
        if not state or state.status != DraftStatus.ACTIVE:
            raise RuntimeError(f"{div.name} not resumed")

    async def _skips_without_replacement(self, div: DivisionRun) -> None:
        """
        Walk a coach up the skip ladder but stop one short of the replacement
        threshold. Leagues with no stand-in coaches still get their timers,
        skip counters and shrinking pick windows exercised.
        """
        state = self.draft._get_state(div.name)
        if not state or len(state.coaches) <= SKIP3_COACH_INDEX:
            return

        target_id = state.coaches[SKIP3_COACH_INDEX].discord_id
        target_skips = max(1, Config.MAX_SKIPS - 1)

        await self._division_status(
            div,
            f"Phase 9 — {target_skips} timer skip(s) on "
            f"{state.coaches[SKIP3_COACH_INDEX].name}, no replacement configured",
            level="phase",
        )

        steps = 0
        while steps < MAX_NAVIGATION_STEPS:
            steps += 1
            state = self.draft._get_state(div.name)
            if not state or state.status != DraftStatus.ACTIVE:
                break

            target = state.get_coach_by_id(target_id)
            if not target or target.skip_count >= target_skips:
                break

            current = state.current_coach
            if not current:
                raise RuntimeError(f"{div.name} no current coach during skip phase")

            if current.discord_id == target_id:
                pre_skip = target.skip_count
                if not await wait_for_natural_timer_skip(
                    self.draft,
                    div.name,
                    target_id,
                    timer_seconds=TIMER_TEST_SECONDS,
                    wait_buffer=TIMER_WAIT_BUFFER,
                ):
                    raise RuntimeError(
                        f"{div.name} natural timer skip did not fire for {target.name} "
                        f"(skip_count still {pre_skip})"
                    )
            else:
                if not await self.harness.advance_draft_pick(div.channel):
                    raise RuntimeError(
                        f"{div.name} pick failed while waiting for {target.name}'s turn"
                    )
                await asyncio.sleep(PICK_DELAY)

        state = self.draft._get_state(div.name)
        target = state.get_coach_by_id(target_id) if state else None
        skips = target.skip_count if target else 0
        if skips < target_skips:
            raise RuntimeError(
                f"{div.name} only reached {skips}/{target_skips} skips"
            )
        if state and state.status == DraftStatus.WAITING_REPLACE:
            raise RuntimeError(
                f"{div.name} escalated to a replacement — the skip ladder went "
                f"one step too far ({skips} skips)"
            )

        await self._division_status(
            div,
            f"✓ {skips} skip(s) taken, makeups queued, no replacement triggered",
            level="success",
        )

    async def _triple_skip_and_replace(self, div: DivisionRun) -> None:
        state = self.draft._get_state(div.name)
        if not state or len(state.coaches) <= SKIP3_COACH_INDEX:
            return

        replacement = replacement_for(div.name)
        if replacement is None:
            await self._skips_without_replacement(div)
            return

        target = state.coaches[SKIP3_COACH_INDEX]
        target_id = target.discord_id

        await self._division_status(
            div,
            f"Phase 9 — 3 natural timer skips on {target.name} → /replace with {replacement.name}",
            level="phase",
        )

        steps = 0
        while steps < MAX_NAVIGATION_STEPS:
            steps += 1
            state = self.draft._get_state(div.name)
            if not state:
                break

            if state.status == DraftStatus.WAITING_REPLACE:
                break

            if state.status != DraftStatus.ACTIVE:
                if state.status == DraftStatus.REPLACEMENT_PENDING:
                    if not await self.harness.advance_draft_pick(div.channel):
                        raise RuntimeError(
                            f"{div.name} replacement makeup pick failed during skip phase"
                        )
                    await asyncio.sleep(PICK_DELAY)
                    continue
                raise RuntimeError(f"{div.name} unexpected status {state.status}")

            target = state.get_coach_by_id(target_id)
            if not target:
                raise RuntimeError(f"{div.name} skip target coach missing")

            if target.skip_count >= 3:
                break

            current = state.current_coach
            if not current:
                raise RuntimeError(f"{div.name} no current coach during skip phase")

            if current.discord_id == target_id:
                pre_skip = target.skip_count
                if not await wait_for_natural_timer_skip(
                    self.draft,
                    div.name,
                    target_id,
                    timer_seconds=TIMER_TEST_SECONDS,
                    wait_buffer=TIMER_WAIT_BUFFER,
                ):
                    raise RuntimeError(
                        f"{div.name} natural timer skip did not fire for {target.name} "
                        f"(skip_count still {pre_skip})"
                    )
                state = self.draft._get_state(div.name)
                if state and state.status == DraftStatus.WAITING_REPLACE:
                    break
            else:
                if not await self.harness.advance_draft_pick(div.channel):
                    current = state.current_coach
                    name = current.name if current else "?"
                    raise RuntimeError(
                        f"{div.name} pick failed while waiting for {target.name}'s turn "
                        f"(current: {name}, status={state.status.value})"
                    )
                await asyncio.sleep(PICK_DELAY)

        state = self.draft._get_state(div.name)
        if not state or state.status != DraftStatus.WAITING_REPLACE:
            target = state.get_coach_by_id(target_id) if state else None
            skips = target.skip_count if target else "?"
            raise RuntimeError(
                f"{div.name} replacement not triggered (skips={skips}, "
                f"status={getattr(state, 'status', None)})"
            )

        await self.harness.invoke_replace(
            div.channel,
            div.name,
            team_name=replacement.team_name,
            coach_name=replacement.name,
            discord_id=replacement.discord_id,
            logo_url=replacement.logo_url,
            timezone=replacement.timezone,
        )
        await asyncio.sleep(2)

        if not await self.harness.resolve_replacement_picks(div.channel):
            raise RuntimeError(
                f"{div.name} replacement coach did not complete makeup/on-clock picks"
            )

        if Config.ANNOUNCEMENTS_CHANNEL_ID:
            await self._division_status(
                div,
                f"✓ Replacement posted — check <#{Config.ANNOUNCEMENTS_CHANNEL_ID}>",
                level="success",
            )

        await self._division_status(div, "✓ Replace flow complete", level="success")

    def _snapshot_discord_ids(self) -> list[str]:
        ids: list[str] = []
        coaches_path = Config.COACHES_CONFIG_FILE
        if not os.path.isfile(coaches_path):
            return ids
        try:
            with open(coaches_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            return ids
        under_test = set(system_test_divisions())
        for coach in data.get("coaches", []):
            if coach.get("division") not in under_test:
                continue
            discord_id = str(coach.get("discord_id") or "").strip()
            if discord_id:
                ids.append(discord_id)
        return ids

    def _team_fill_summary(self, state) -> str:
        filled = sum(len(c.team) for c in state.coaches)
        total = len(state.coaches) * state.team_size
        lines = [
            f"{c.name}: {len(c.team)}/{state.team_size} ({c.remaining_points} pts left)"
            for c in state.coaches
        ]
        return f"{filled}/{total} roster slots\n" + "\n".join(lines)

    def _current_bank_round(self, state) -> tuple[str, int] | None:
        current = state.current_coach
        if not current:
            return None
        bank = state.pick_banks.get(current.discord_id)
        if not bank or not bank.is_active:
            return None
        if current.bank_all_sniped_exhausted:
            return None
        if not bank.get_plan_for_round(state.current_round):
            return None
        return current.discord_id, state.current_round

    async def _finish_draft(self, div: DivisionRun) -> None:
        await self._division_status(
            div,
            "Phase 10 — drain makeups and /pick until all teams are full",
            level="phase",
        )
        last_logged_round = None
        steps = 0
        while steps < MAX_FINISH_STEPS:
            steps += 1
            state = self.draft._get_state(div.name)
            if not state:
                raise RuntimeError(f"{div.name} state missing while finishing draft")

            if state.status == DraftStatus.COMPLETED or state.is_complete():
                break

            if state.status == DraftStatus.REPLACEMENT_PENDING:
                if not await self.harness.resolve_replacement_picks(div.channel):
                    raise RuntimeError(
                        f"{div.name} replacement picks stalled while finishing draft"
                    )
                await asyncio.sleep(PICK_DELAY)
                continue

            if state.status != DraftStatus.ACTIVE:
                raise RuntimeError(
                    f"{div.name} unexpected status {state.status} while finishing draft"
                )

            if state.current_round != last_logged_round:
                last_logged_round = state.current_round
                filled = sum(len(c.team) for c in state.coaches)
                total = len(state.coaches) * state.team_size
                await self._division_status(
                    div,
                    f"Finishing — round {state.current_round} ({filled}/{total} roster slots)",
                )

            if state.makeup_queue:
                if not await self.harness.drain_queued_makeup(div.channel):
                    raise RuntimeError(
                        f"{div.name} leftover makeup pick failed "
                        f"(queue={len(state.makeup_queue)})"
                    )
                await asyncio.sleep(PICK_DELAY)
                continue

            bank_round = self._current_bank_round(state)
            if bank_round:
                coach_id, target_round = bank_round
                ok = await wait_for_bank_round_resolution(
                    self.draft,
                    self.harness,
                    div.name,
                    div.channel,
                    coach_id,
                    target_round,
                    timeout_seconds=BANK_RESOLVE_TIMEOUT,
                    pick_delay=PICK_DELAY,
                )
                if not ok:
                    raise RuntimeError(
                        f"{div.name} bank round {target_round} did not resolve while finishing"
                    )
                await asyncio.sleep(PICK_DELAY)
                continue

            if not await self.harness.advance_draft_pick(div.channel):
                current = state.current_coach
                name = current.name if current else "?"
                raise RuntimeError(
                    f"{div.name} /pick failed while finishing "
                    f"(round={state.current_round}, coach={name}, "
                    f"{sum(len(c.team) for c in state.coaches)}/"
                    f"{len(state.coaches) * state.team_size} slots)"
                )
            await asyncio.sleep(PICK_DELAY)

        state = self.draft._get_state(div.name)
        if not state or not (state.status == DraftStatus.COMPLETED or state.is_complete()):
            summary = self._team_fill_summary(state) if state else "no state"
            raise RuntimeError(
                f"{div.name} draft did not complete after {MAX_FINISH_STEPS} steps:\n{summary}"
            )

        short = [
            c for c in state.coaches if len(c.team) < state.team_size
        ]
        if short:
            detail = ", ".join(f"{c.name} {len(c.team)}/{state.team_size}" for c in short)
            raise RuntimeError(f"{div.name} teams not full after complete: {detail}")

        filled = sum(len(c.team) for c in state.coaches)
        total = len(state.coaches) * state.team_size
        await self._division_status(
            div,
            f"✓ Draft complete — {filled}/{total} roster slots, "
            f"status={state.status.value}",
            level="success",
        )
        if self._test_log:
            self._test_log.log(self._team_fill_summary(state), division=div.name)
        remember_pool(p.name for p in state.pokemon_pool.values())
        mark_seen(record.pokemon_name for record in state.pick_history)
        missing: list[str] = []
        for record in state.pick_history:
            if (record.gif_url or "").strip():
                continue
            mon = state.pokemon_pool.get(record.pokemon_name.lower())
            if not mon or not mon.sprite_url:
                missing.append(record.pokemon_name)
        if missing and self._test_log:
            self._test_log.warn(
                "Picks with no sprite/GIF: " + ", ".join(missing),
                division=div.name,
            )
        pool_size = len(state.pokemon_pool) if state else 0
        await self._division_status(div, coverage_summary(pool_size), level="info")

    async def _run_division_phases(self, div: DivisionRun) -> None:
        await self._normal_picks(div, INITIAL_PICKS)
        await self._setup_bank(div)
        await self._wait_for_bank_auto_pick(div)
        await self._picking_check(div)
        await self._create_makeup_via_skip(div)
        await self._pause(div)
        await self._makeup_while_paused(div)
        await self._resume(div)
        await self._triple_skip_and_replace(div)
        await self._finish_draft(div)

    async def run(self) -> SystemTestReport:
        # Pick timers get compressed to seconds here, so the nightly window has
        # to stand aside — otherwise a test started near 23:00 hangs till 09:00.
        with quiet_hours_suspended():
            attempts = system_test_max_attempts()
            reset_seen()
            passed = 0
            failed = 0
            last = self.report
            for attempt in range(1, attempts + 1):
                self.report = SystemTestReport()
                self._tx = None
                try:
                    last = await self._run(attempt=attempt, of=attempts)
                    if last.success:
                        passed += 1
                    else:
                        failed += 1
                except Exception as exc:
                    self.report.success = False
                    self.report.error = str(exc) or type(exc).__name__
                    last = self.report
                    failed += 1
                    logger.exception("[SystemTest] Attempt %s/%s failed", attempt, attempts)
                    await self._status(
                        f"Attempt {attempt}/{attempts} FAILED: {last.error}",
                        level="error",
                    )
                await self._rollback_attempt()
                if attempt < attempts:
                    await self._status(
                        f"Attempt {attempt}/{attempts} done "
                        f"({'ok' if last.success else 'failed'}) — "
                        f"rollback, starting attempt {attempt + 1}.",
                        level="warn" if not last.success else "info",
                    )
            last.success = failed == 0
            if failed:
                last.error = (
                    f"{failed}/{attempts} attempt(s) failed; {passed} passed. "
                    f"Last error: {last.error or 'unknown'}"
                )
            leftovers = remaining_unseen()
            if leftovers:
                preview = ", ".join(leftovers[:40])
                extra = f" (+{len(leftovers) - 40} more)" if len(leftovers) > 40 else ""
                await self._status(
                    f"Coverage leftover: {len(leftovers)} Pokémon never drafted "
                    f"across {attempts} run(s): {preview}{extra}",
                    level="warn",
                )
            else:
                await self._status(
                    f"Coverage complete — every pool Pokémon was drafted at least "
                    f"once across {attempts} run(s).",
                    level="success",
                )
            await self._status(
                f"Finished {attempts} attempt(s): {passed} passed, {failed} failed.",
                level="success" if failed == 0 else "warn",
            )
            return last

    async def _rollback_attempt(self) -> None:
        tx = getattr(self, "_tx", None)
        if not tx:
            return
        try:
            await tx.rollback(
                self.draft,
                self.draft.persistence,
                self.draft.channel_lock,
                self.draft.sheets,
            )
            if self._test_log:
                self._test_log.warn("Rolled back after a failed attempt")
        except Exception:
            logger.exception("[SystemTest] Rollback between attempts failed")

    async def _run(self, *, attempt: int = 1, of: int = 1) -> SystemTestReport:
        tx: SystemTestTransaction | None = None
        try:
            self._divisions = self._resolve_divisions()
            await self._unmute_test_channels()
            division_names = [div.name for div in self._divisions]
            tx = SystemTestTransaction(division_names)
            self._tx = tx
            tx.capture(self.draft.persistence)
            await tx.capture_sheets(self.draft.sheets, self._snapshot_discord_ids())
            self.draft.set_system_test_session(tx.session_dir)
            self.report.snapshot_taken = True

            self._test_log = SystemTestLogger.for_session(
                session_id=tx.session_id,
                session_dir=tx.session_dir,
                admin_id=self.admin_id,
                divisions=division_names,
            )
            self._test_log.phase(f"System test started (attempt {attempt}/{of})")

            await self._status(
                f"Starting system test across {len(self._divisions)} divisions: "
                + ", ".join(d.name for d in self._divisions),
                level="phase",
            )

            await self._status("Phase 0 — sequential init (coaches.json safety)", level="phase")
            for div in self._divisions:
                await self._init_and_start(div)

            results = await asyncio.gather(
                *[self._run_division_phases(div) for div in self._divisions],
                return_exceptions=True,
            )
            errors: list[Exception] = []
            for div, result in zip(self._divisions, results):
                if isinstance(result, Exception):
                    errors.append(result)
                    if self._test_log:
                        self._test_log.mark_division_failed(div.name, result)
                elif self._test_log:
                    self._test_log.mark_division_ok(div.name)

            if errors:
                raise errors[0]

            self.report.success = True
            await self._status(
                "All divisions passed — review results, then run `/rollback_system_test` "
                "to restore coaches, state, Participants coach rows, and tracked pick cells.",
                level="success",
            )
        except Exception as exc:
            self.report.success = False
            self.report.error = str(exc) or type(exc).__name__
            logger.exception("[SystemTest] Failed")
            await self._status(
                f"⛔ FAILED: {self.report.error} — review logs, then run `/rollback_system_test` "
                "when ready to restore."
                if self.report.snapshot_taken
                else f"⛔ FAILED: {self.report.error}",
                level="error",
            )
            raise
        finally:
            if self._test_log:
                report_data = self._test_log.finalize(
                    success=self.report.success,
                    error=self.report.error,
                    rolled_back=False,
                )
                self.report.log_paths = {
                    "log": report_data.get("report_log", ""),
                    "json": report_data.get("report_json", ""),
                    "session_dir": report_data.get(
                        "session_dir", tx.session_dir if tx else ""
                    ),
                }

        return self.report

    async def build_summary_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="🧪 Multi-Division System Test",
            colour=0x57F287 if self.report.success else 0xED4245,
        )
        embed.add_field(
            name="Result",
            value=(
                "✅ Passed"
                if self.report.success
                else f"❌ Failed: {self.report.error or 'unknown error'}"
            ),
            inline=False,
        )
        embed.add_field(
            name="Rollback",
            value=(
                "✅ Restored"
                if self.report.rolled_back
                else (
                    "⏳ Pending — run `/rollback_system_test` when done reviewing"
                    if self.report.snapshot_taken
                    else "— No snapshot this run"
                )
            ),
            inline=True,
        )
        if self.report.log_paths:
            embed.add_field(
                name="Report files",
                value=(
                    f"📄 `{self.report.log_paths.get('log', '—')}`\n"
                    f"📋 `{self.report.log_paths.get('json', '—')}`\n"
                    f"Use `/system_test_logs` for a summary."
                ),
                inline=False,
            )
        for name, div in self.report.divisions.items():
            summary = "\n".join(div.logs[-4:]) if div.logs else "—"
            if len(summary) > 900:
                summary = summary[:897] + "..."
            embed.add_field(name=name, value=summary or "—", inline=False)
        embed.set_footer(
            text=(
                "Snapshot saved — /rollback_system_test restores coaches, state, "
                "Participants coach rows, and tracked Drafting Pool pick cells only."
                if self.report.snapshot_taken
                else "No snapshot was taken for this run."
            )
        )
        return embed
