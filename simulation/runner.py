"""
Automated draft dry-run — exercises real slash-command handlers end-to-end.

Each scenario maps to a user-facing command where possible:
  /pick, /makeup_pick, /picking, /undo_pick, /bank (via set_bank_plan),
  /pause_draft, /resume_draft, /force_skip, /replace
Timer auto-skip uses DraftTimerService naturally (shortened timer, no forced _handle_timeout).
"""

from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass, field
from typing import Optional

import discord

from constants.draft_constants import DraftStatus
from models.draft_state import DraftState
from models.pokemon import Pokemon
from services.draft_service import DraftService
from simulation.mock_context import MockContext
from simulation.bank_helpers import select_valid_bank_priority_lists, wait_for_bank_round_resolution
from simulation.coverage import prefer_unseen, using_unseen_only
from simulation.timer_helpers import wait_for_natural_timer_skip

logger = logging.getLogger(__name__)

SIM_REPLACEMENT_ID = "999000001"
SIM_REPLACEMENT_NAME = "Sim Replacement"
SIM_REPLACEMENT_TEAM = "Sim Team FC"

POINT_WEIGHTS: dict[int, int] = {
    1: 1, 2: 1, 3: 2, 4: 2, 5: 3, 6: 4, 7: 5, 8: 6, 9: 8, 10: 10,
    11: 12, 12: 14, 13: 16, 14: 18, 15: 20, 16: 18, 17: 16, 18: 12, 19: 8, 20: 4,
}


@dataclass
class SimulationConfig:
    max_rounds: int = 10
    timer_test_seconds: int = 5
    timer_wait_buffer: int = 3
    pick_delay: float = 2.0
    makeup_after_picks: int = 12


@dataclass
class SimulationRoles:
    """Coach index assignments for scripted scenarios (0-based)."""

    bank: int
    error: int
    timer_skip: int
    force_skip: int
    skip3: int
    pause_round: int = 2

    @classmethod
    def for_coach_count(cls, n: int) -> SimulationRoles:
        if n < 2:
            return cls(bank=0, error=0, timer_skip=0, force_skip=0, skip3=0)
        return cls(
            bank=min(3, n - 1),
            error=min(5, n - 1),
            timer_skip=min(7, n - 1),
            force_skip=min(9, n - 1),
            skip3=min(11, n - 1) if n > 11 else min(5, n - 1),
        )


@dataclass
class ScenarioFlags:
    bank_configured: bool = False
    picking_checked: bool = False
    pause_resume_done: bool = False
    error_tests_done: bool = False
    timer_skip_done: bool = False
    force_skip_done: bool = False
    over_budget_done: bool = False
    undo_done: bool = False
    replace_done: bool = False
    skip_events: list[tuple[int, bool]] = field(default_factory=list)


class DraftSimulationRunner:
    def __init__(
        self,
        bot: discord.Bot,
        draft_service: DraftService,
        channel: discord.TextChannel,
        guild: discord.Guild,
        division_name: str,
        admin_discord_id: str,
        config: SimulationConfig | None = None,
    ) -> None:
        self.bot = bot
        self.draft = draft_service
        self.channel = channel
        self.guild = guild
        self.division_name = division_name
        self.admin_id = admin_discord_id
        self.config = config or SimulationConfig()
        self.flags = ScenarioFlags()

    # ── Cog accessors ─────────────────────────────────────────────────────────

    def _draft_cog(self):
        return self.bot.cogs.get("DraftCog")

    def _admin_cog(self):
        return self.bot.cogs.get("AdminCog")

    def _state(self) -> DraftState | None:
        return self.draft._get_state(self.division_name)

    def _ctx(
        self,
        discord_id: str,
        display_name: str = "SimBot",
        *,
        admin: bool = False,
    ) -> MockContext:
        return MockContext(
            self.channel,
            self.guild,
            discord_id,
            display_name,
            admin=admin,
        )

    # ── Logging ───────────────────────────────────────────────────────────────

    async def _note(self, msg: str) -> None:
        await self.channel.send(f"```\n🔬 [DRY-RUN] {msg}\n```")
        await asyncio.sleep(0.4)

    # ── Pick helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _weighted_pick(state: DraftState, coach) -> Optional[Pokemon]:
        picks_remaining = state.team_size - len(coach.team) - 1
        safe_budget = max(1, coach.remaining_points - picks_remaining)

        available = [
            p for p in state.pokemon_pool.values()
            if not p.is_drafted and not p.is_banned and p.points <= safe_budget
        ]
        if not available:
            available = [
                p for p in state.pokemon_pool.values()
                if not p.is_drafted and not p.is_banned
                and p.points <= coach.remaining_points
            ]
        if not available:
            return None

        available = prefer_unseen(available)
        by_points: dict[int, list[Pokemon]] = {}
        for p in available:
            by_points.setdefault(p.points, []).append(p)

        costs = list(by_points.keys())
        if using_unseen_only(available):
            weights = [max(1, c * c) for c in costs]
        else:
            weights = [POINT_WEIGHTS.get(c, 1) for c in costs]
        chosen = random.choices(costs, weights=weights, k=1)[0]
        return random.choice(by_points[chosen])

    @staticmethod
    def _get_expensive(state: DraftState, coach) -> Optional[Pokemon]:
        candidates = [
            p for p in state.pokemon_pool.values()
            if not p.is_drafted and not p.is_banned and p.points > coach.remaining_points
        ]
        return min(candidates, key=lambda p: p.points) if candidates else None

    @staticmethod
    def _cheapest_available(state: DraftState, budget: int) -> Optional[Pokemon]:
        candidates = [
            p for p in state.pokemon_pool.values()
            if not p.is_drafted and not p.is_banned and p.points <= budget
        ]
        return min(candidates, key=lambda p: p.points) if candidates else None

    def _record_skip(self, state: DraftState) -> None:
        self.flags.skip_events.append((state.global_pick_counter, False))

    def _due_makeups(self, state: DraftState) -> bool:
        threshold = self.config.makeup_after_picks
        for pick_at_skip, processed in self.flags.skip_events:
            if not processed and state.global_pick_counter >= pick_at_skip + threshold:
                return True
        return False

    def _mark_makeups_processed(self) -> None:
        self.flags.skip_events = [(p, True) for p, _ in self.flags.skip_events]

    # ── Command invocations (real cog handlers) ───────────────────────────────

    async def _invoke_pick(
        self, coach, pokemon_name: str
    ) -> bool:
        draft_cog = self._draft_cog()
        if not draft_cog:
            await self._note("DraftCog not loaded — cannot invoke /pick.")
            return False
        ctx = self._ctx(coach.discord_id, coach.name)
        before = self._state()
        before_count = before.global_pick_counter if before else 0
        await draft_cog.pick.callback(
            draft_cog, ctx, pokemon=pokemon_name
        )
        after = self._state()
        return after is not None and after.global_pick_counter > before_count

    async def _invoke_makeup(
        self, coach, pokemon_name: str
    ) -> None:
        draft_cog = self._draft_cog()
        if not draft_cog:
            return
        ctx = self._ctx(coach.discord_id, coach.name)
        await draft_cog.makeup_pick.callback(
            draft_cog,
            ctx,
            pokemon=pokemon_name,
            coach_mention=None,
            coach_name=coach.name,
        )

    async def _invoke_picking(self) -> None:
        draft_cog = self._draft_cog()
        if not draft_cog:
            return
        ctx = self._ctx(self.admin_id, "Admin", admin=True)
        await draft_cog.picking.callback(draft_cog, ctx)

    async def _invoke_undo(self, coach) -> None:
        draft_cog = self._draft_cog()
        if not draft_cog:
            return
        ctx = self._ctx(coach.discord_id, coach.name)
        await draft_cog.undo_pick.callback(draft_cog, ctx)

    async def _invoke_force_skip(self) -> None:
        admin_cog = self._admin_cog()
        if not admin_cog:
            return
        ctx = self._ctx(self.admin_id, "Admin", admin=True)
        await admin_cog.force_skip.callback(admin_cog, ctx)

    async def _invoke_pause(self, reason: str = "Dry-run pause test") -> None:
        admin_cog = self._admin_cog()
        if not admin_cog:
            return
        ctx = self._ctx(self.admin_id, "Admin", admin=True)
        await admin_cog.pause_draft.callback(admin_cog, ctx, reason=reason)

    async def _invoke_resume(self) -> None:
        admin_cog = self._admin_cog()
        if not admin_cog:
            return
        ctx = self._ctx(self.admin_id, "Admin", admin=True)
        await admin_cog.resume_draft.callback(admin_cog, ctx)

    async def _invoke_replace(self) -> None:
        admin_cog = self._admin_cog()
        if not admin_cog:
            return
        ctx = self._ctx(SIM_REPLACEMENT_ID, SIM_REPLACEMENT_NAME)
        await admin_cog.replace.callback(
            admin_cog,
            ctx,
            team_name=SIM_REPLACEMENT_TEAM,
            coach_mention=None,
            coach_name_str=SIM_REPLACEMENT_NAME,
            discord_id_str=SIM_REPLACEMENT_ID,
            division=self.division_name,
            logo_url=None,
            timezone="Europe/Lisbon",
        )

    # ── Scenario blocks ───────────────────────────────────────────────────────

    async def _setup_bank(self, coach, roles: SimulationRoles) -> None:
        state = self._state()
        if not state:
            return

        r4, r6 = select_valid_bank_priority_lists(state, coach)

        await self._note(
            f"Pre-draft: configuring pick bank for {coach.name}\n"
            f"  (mirrors /bank wizard save via set_bank_plan)\n"
            f"  Round 4: {', '.join(r4) or '—'}\n"
            f"  Round 6: {', '.join(r6) or '—'}"
        )

        if r4:
            ok, msg = self.draft.set_bank_plan(
                self.division_name, coach.discord_id, 4, r4
            )
            if not ok:
                await self._note(f"Bank round 4 failed: {msg}")
        if r6:
            ok, msg = self.draft.set_bank_plan(
                self.division_name, coach.discord_id, 6, r6
            )
            if not ok:
                await self._note(f"Bank round 6 failed: {msg}")

        ok, msg = self.draft.activate_bank(self.division_name, coach.discord_id)
        if not ok:
            await self._note(f"Bank activation failed: {msg}")
        else:
            self.flags.bank_configured = True
            await self._note(f"✓ Pick bank active for {coach.name} (rounds 4 and 6).")

    async def _do_error_tests(self, coach) -> None:
        state = self._state()
        if not state:
            return

        await self._note(f"=== /pick ERROR TESTS — {coach.name} (Round 3) ===")

        await self._note("Test 1/2 — Invalid name: 'Fakémon99'")
        await self._invoke_pick(coach, "Fakémon99")
        await asyncio.sleep(1)

        if state.pick_history:
            drafted_name = state.pick_history[0].pokemon_name
            by_coach = state.pick_history[0].coach_name
            await self._note(
                f"Test 2/2 — Already drafted: '{drafted_name}' (by {by_coach})"
            )
            await self._invoke_pick(coach, drafted_name)
            await asyncio.sleep(1)

        pick = self._weighted_pick(state, coach)
        if pick:
            await self._note(f"Valid /pick for {coach.name}: {pick.name}")
            await self._invoke_pick(coach, pick.name)

        self.flags.error_tests_done = True

    async def _do_timer_skip(self, coach) -> None:
        secs = self.config.timer_test_seconds
        await self._note(
            f"=== TIMER AUTO-SKIP — {coach.name} (Round 4) ===\n"
            f"Overriding timer to {secs}s and waiting for natural expiry."
        )

        pre_skip = coach.skip_count
        ok = await wait_for_natural_timer_skip(
            self.draft,
            self.division_name,
            coach.discord_id,
            timer_seconds=secs,
            wait_buffer=self.config.timer_wait_buffer,
        )

        state = self._state()
        updated = state.get_coach_by_id(coach.discord_id) if state else None
        if ok and updated and updated.skip_count > pre_skip:
            await self._note(
                f"✓ Timer skip confirmed — {coach.name} skip_count={updated.skip_count}"
            )
        else:
            await self._note(
                f"⚠ Timer skip did not fire for {coach.name} "
                f"(skip_count={updated.skip_count if updated else pre_skip})"
            )

        self.flags.timer_skip_done = True

    async def _do_force_skip(self, coach) -> None:
        await self._note(f"=== /force_skip — {coach.name} (Round 4) ===")
        await self._invoke_force_skip()
        await asyncio.sleep(2)
        await self._note(f"✓ Force skip — {coach.name} skip_count={coach.skip_count}")
        self.flags.force_skip_done = True

    async def _do_over_budget(self, coach, expensive: Pokemon) -> None:
        await self._note(
            f"=== OVER-BUDGET /pick — {coach.name} ===\n"
            f"Budget: {coach.remaining_points} pts  |  "
            f"Attempting: {expensive.name} ({expensive.points} pts)"
        )
        await self._invoke_pick(coach, expensive.name)
        await asyncio.sleep(1)
        self.flags.over_budget_done = True

    async def _do_makeups(self) -> None:
        state = self._state()
        if not state or not state.makeup_queue:
            return

        pending = sorted(list(state.makeup_queue), key=lambda x: x[1])
        for coach_id, skipped_round in pending:
            if not any(
                cid == coach_id and rnd == skipped_round
                for cid, rnd in state.makeup_queue
            ):
                continue

            coach = state.get_coach_by_id(coach_id)
            if not coach:
                continue

            pick = self._weighted_pick(state, coach) or self._cheapest_available(
                state, coach.remaining_points
            )
            if not pick:
                await self._note(
                    f"No affordable pick for {coach.name}'s makeup (Round {skipped_round})."
                )
                continue

            await self._note(
                f"/makeup_pick — {coach.name} (Round {skipped_round} slot) → {pick.name}"
            )
            await self._invoke_makeup(coach, pick.name)
            await asyncio.sleep(self.config.pick_delay)

    async def _do_replace(self) -> None:
        state = self._state()
        if not state:
            return

        old_coach = state.get_coach_by_id(state.pending_replace_coach_id or "")
        old_name = old_coach.name if old_coach else "Unknown"

        await self._note(
            f"=== /replace — {old_name} removed ===\n"
            f"Replacement: {SIM_REPLACEMENT_NAME} ({SIM_REPLACEMENT_TEAM})"
        )

        if old_coach:
            from services.embed_service import EmbedService

            team_embed = EmbedService.team_card(old_coach, state)
            await self.channel.send(
                content=f"📋 Final team of **{old_name}** before replacement:",
                embed=team_embed,
            )
            await asyncio.sleep(1)

        await self._invoke_replace()
        await asyncio.sleep(2)

        new_coach = state.get_coach_by_id(SIM_REPLACEMENT_ID)
        if new_coach and new_coach.makeup_picks_owed > 0:
            await self._note(
                f"Processing {new_coach.name}'s {new_coach.makeup_picks_owed} makeup pick(s)..."
            )
            await self._do_makeups()
            await asyncio.sleep(self.config.pick_delay)

        await self._note(
            f"✓ Replacement ready — draft status: {state.status.value}"
        )
        self.flags.replace_done = True

    async def _do_pause_resume(self) -> None:
        await self._note("=== /pause_draft + /resume_draft (admin) ===")
        await self._invoke_pause("Automated dry-run pause test")
        await asyncio.sleep(2)

        state = self._state()
        if state and state.status == DraftStatus.PAUSED:
            await self._note("Draft paused — invoking /resume_draft...")
            await self._invoke_resume()
            await asyncio.sleep(2)
            await self._note("✓ Pause/resume cycle complete.")
        else:
            await self._note("⚠ Draft did not enter paused state — skipping resume.")

        self.flags.pause_resume_done = True

    # ── Main loop ─────────────────────────────────────────────────────────────

    async def run(self) -> None:
        state = self._state()
        if not state:
            await self._note("Division state not found.")
            return

        coaches = state.coaches
        n = len(coaches)
        roles = SimulationRoles.for_coach_count(n)

        await self._note("=== ARMA DRAFT — DRY-RUN START ===")
        await self._note(
            f"Division: {self.division_name}  |  Coaches: {n}  |  "
            f"Pool: {len(state.pokemon_pool)}  |  Target: {self.config.max_rounds} rounds\n"
            f"Bank: {coaches[roles.bank].name}  |  "
            f"Errors: {coaches[roles.error].name} (R3)  |  "
            f"Timer skip: {coaches[roles.timer_skip].name} (R4)  |  "
            f"Force skip: {coaches[roles.force_skip].name} (R4)  |  "
            f"3-skip: {coaches[roles.skip3].name}"
        )

        await self._setup_bank(coaches[roles.bank], roles)
        await asyncio.sleep(1)

        if not self.flags.picking_checked:
            await self._note("Invoking /picking (status check)...")
            await self._invoke_picking()
            self.flags.picking_checked = True
            await asyncio.sleep(1)

        rounds_seen: set[int] = set()

        while True:
            state = self._state()
            if not state or state.status not in (
                DraftStatus.ACTIVE,
                DraftStatus.WAITING_REPLACE,
            ):
                break

            if state.status == DraftStatus.WAITING_REPLACE:
                if not self.flags.replace_done:
                    await self._do_replace()
                await asyncio.sleep(2)
                continue

            if state.status != DraftStatus.ACTIVE:
                break

            rnd = state.current_round
            if rnd > self.config.max_rounds:
                await self._note(f"Target of {self.config.max_rounds} rounds reached.")
                break

            current = state.current_coach
            if not current:
                await self._note("No current coach — stopping.")
                break

            if rnd not in rounds_seen:
                rounds_seen.add(rnd)
                direction = "Forward ▶" if int(state.snake_direction) == 1 else "◀ Backward"
                await self._note(f"— ROUND {rnd} [{direction}] —")
                await asyncio.sleep(0.5)

                if rnd == roles.pause_round and not self.flags.pause_resume_done:
                    await self._do_pause_resume()

            try:
                coach_idx = coaches.index(current)
            except ValueError:
                coach_idx = -1

            if self._due_makeups(state) and state.makeup_queue:
                await self._note(
                    f"Makeup trigger — processing {len(state.makeup_queue)} pending..."
                )
                await self._do_makeups()
                self._mark_makeups_processed()
                await asyncio.sleep(self.config.pick_delay)
                continue

            handled = False

            if (
                coach_idx == roles.bank
                and rnd in (4, 6)
                and self.flags.bank_configured
            ):
                from simulation.harness import DraftCommandHarness

                await self._note(
                    f"Pick bank round — waiting for resolution: {current.name} (Round {rnd})"
                )
                harness = DraftCommandHarness(self.bot, self.draft, self.admin_id)
                ok = await wait_for_bank_round_resolution(
                    self.draft,
                    harness,
                    self.division_name,
                    self.channel,
                    current.discord_id,
                    rnd,
                    timeout_seconds=180.0,
                    pick_delay=self.config.pick_delay,
                )
                if ok:
                    await self._note(f"✓ Pick bank round completed for {current.name}")
                else:
                    await self._note(
                        f"Bank round timed out — manual /pick fallback for {current.name}"
                    )
                    pick = self._weighted_pick(self._state(), current)
                    if pick:
                        await self._invoke_pick(current, pick.name)
                handled = True

            elif rnd == 3 and coach_idx == roles.error and not self.flags.error_tests_done:
                await self._do_error_tests(current)
                handled = True

            elif rnd == 4 and coach_idx == roles.timer_skip and not self.flags.timer_skip_done:
                pre_skip = current.skip_count
                await self._do_timer_skip(current)
                if current.skip_count > pre_skip:
                    self._record_skip(state)
                handled = True

            elif rnd == 4 and coach_idx == roles.force_skip and not self.flags.force_skip_done:
                pre_skip = current.skip_count
                await self._do_force_skip(current)
                if current.skip_count > pre_skip:
                    self._record_skip(state)
                handled = True

            elif (
                coach_idx == roles.skip3
                and not self.flags.replace_done
                and current.skip_count < 3
                and coach_idx not in (roles.timer_skip, roles.force_skip)
            ):
                pre_skip = current.skip_count
                await self._note(
                    f"=== NATURAL TIMER SKIP {pre_skip + 1}/3 — "
                    f"{current.name} (Round {rnd}) ==="
                )
                ok = await wait_for_natural_timer_skip(
                    self.draft,
                    self.division_name,
                    current.discord_id,
                    timer_seconds=self.config.timer_test_seconds,
                    wait_buffer=self.config.timer_wait_buffer,
                )
                state = self._state()
                updated = (
                    state.get_coach_by_id(current.discord_id) if state else None
                )
                if ok and updated and updated.skip_count > pre_skip:
                    self._record_skip(state)
                    if state.status == DraftStatus.WAITING_REPLACE:
                        await self._note(
                            f"✓ 3 skips — replacement triggered for {current.name}"
                        )
                    else:
                        await self._note(
                            f"Skip confirmed — {current.name} "
                            f"total skips: {updated.skip_count}"
                        )
                else:
                    await self._note(
                        f"⚠ Timer skip did not fire for {current.name} this turn"
                    )
                handled = True

            if rnd >= 5 and not self.flags.over_budget_done and not handled:
                expensive = self._get_expensive(state, current)
                if expensive:
                    await self._do_over_budget(current, expensive)

            if not handled:
                pick = self._weighted_pick(state, current)
                if not pick:
                    pick = self._cheapest_available(state, current.remaining_points)
                if not pick:
                    await self._note(
                        f"⚠ {current.name} has no affordable picks "
                        f"({current.remaining_points} pts) — invoking /force_skip."
                    )
                    await self._invoke_force_skip()
                    await asyncio.sleep(self.config.pick_delay)
                    continue

                success = await self._invoke_pick(current, pick.name)

                state = self._state()
                if (
                    success
                    and state
                    and rnd >= 5
                    and not self.flags.undo_done
                    and state.pick_history
                    and state.pick_history[-1].coach_discord_id == current.discord_id
                ):
                    last = state.pick_history[-1]
                    await self._note(
                        f"=== /undo_pick — reverting {last.pokemon_name} by {current.name} ==="
                    )
                    await self._invoke_undo(current)
                    await asyncio.sleep(1)
                    redo = self._weighted_pick(state, current) or pick
                    await self._note(f"Re-picking after undo: {redo.name}")
                    await self._invoke_pick(current, redo.name)
                    self.flags.undo_done = True

            await asyncio.sleep(self.config.pick_delay)

        state = self._state()
        if state and state.makeup_queue:
            await self._note(
                f"=== POST-LOOP MAKEUPS — {len(state.makeup_queue)} remaining ==="
            )
            await self._do_makeups()

        if state:
            await self._print_summary(state)

    async def _print_summary(self, state: DraftState) -> None:
        await self._note("=== DRY-RUN COMPLETE ===")

        cfg = self.config
        embed = discord.Embed(
            title=f"🔬 Draft Dry-Run — {self.division_name}",
            colour=0x9B59B6,
        )
        embed.add_field(
            name="Total Picks", value=str(state.global_pick_counter), inline=True
        )
        embed.add_field(
            name="Current Round", value=str(state.current_round), inline=True
        )
        embed.add_field(
            name="Pending Makeups", value=str(len(state.makeup_queue)), inline=True
        )

        scenarios = [
            f"{'✅' if self.flags.picking_checked else '⏭'} /picking status check",
            f"{'✅' if self.flags.bank_configured else '⏭'} Pick bank (rounds 4 + 6)",
            f"{'✅' if self.flags.pause_resume_done else '⏭'} /pause_draft + /resume_draft",
            f"{'✅' if self.flags.error_tests_done else '⏭'} /pick error tests",
            f"{'✅' if self.flags.timer_skip_done else '⏭'} Timer auto-skip ({cfg.timer_test_seconds}s)",
            f"{'✅' if self.flags.force_skip_done else '⏭'} /force_skip",
            f"{'✅' if self.flags.over_budget_done else '⏭'} Over-budget /pick rejection",
            f"{'✅' if self.flags.undo_done else '⏭'} /undo_pick",
            f"{'✅' if self.flags.replace_done else '⏭'} 3-skip + /replace",
        ]
        embed.add_field(name="Commands Exercised", value="\n".join(scenarios), inline=False)

        skipped = [c for c in state.coaches if c.skip_count > 0]
        if skipped:
            embed.add_field(
                name="Coaches with Skips",
                value="\n".join(
                    f"**{c.name}**: {c.skip_count} skip(s), "
                    f"{c.makeup_picks_owed} makeup(s) owed"
                    for c in skipped
                ),
                inline=False,
            )

        embed.add_field(
            name="Team Status",
            value="\n".join(
                f"**{c.name}**: {len(c.team)}/{state.team_size} picks, "
                f"{c.remaining_points} pts left"
                for c in state.coaches
            ),
            inline=False,
        )
        embed.add_field(
            name="⚠️ To Reset",
            value=(
                f"Stop bot → delete `data/state_{state.division_name.lower().replace(' ', '_')}.json` "
                "→ restart → `/start_division` → `/start_draft`"
            ),
            inline=False,
        )
        await self.channel.send(embed=embed)
