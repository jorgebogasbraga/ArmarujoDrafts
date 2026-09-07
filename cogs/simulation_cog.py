"""
SimulationCog — draft dry-run and multi-division system test commands.
"""

from __future__ import annotations

import logging
import os

import discord
from discord import option
from discord.ext import commands

from config import Config
from constants.draft_constants import DraftStatus
from services.draft_service import DraftService
from simulation.runner import DraftSimulationRunner, SimulationConfig
from simulation.replacements import system_test_divisions
from simulation.system_test import MultiDivisionSystemTest
from simulation.test_logger import LATEST_REPORT_LOG, SystemTestLogger
from simulation.transaction import SystemTestTransaction
from utils.response_embeds import respond_error, respond_success

logger = logging.getLogger(__name__)


def admin_only():
    async def predicate(ctx: discord.ApplicationContext) -> bool:
        role = ctx.guild.get_role(Config.ADMIN_ROLE_ID)
        if role and role in ctx.author.roles:
            return True
        await respond_error(ctx, "You need the Admin role to use this command.")
        return False

    return commands.check(predicate)


class SimulationCog(commands.Cog):
    def __init__(self, bot: discord.Bot, draft_service: DraftService) -> None:
        self.bot = bot
        self.draft = draft_service
        self._running_channels: set[int] = set()
        self._system_test_running = False

    @staticmethod
    async def _post_system_test_summary(
        ctx: discord.ApplicationContext, embed: discord.Embed
    ) -> None:
        try:
            await ctx.channel.send(embed=embed)
        except discord.Forbidden:
            logger.warning(
                "[SimulationCog] Cannot post system-test summary in #%s — "
                "channel is locked or missing Send Messages.",
                ctx.channel_id,
            )

    @discord.slash_command(
        name="simulate_draft",
        description="[ADMIN] Automated end-to-end draft dry-run in this division's channel.",
    )
    @option(
        "max_rounds",
        description="Stop after this many rounds (default 10)",
        type=int,
        required=False,
        default=10,
    )
    @option(
        "timer_seconds",
        description="Pick timer duration for the auto-skip test (default 5)",
        type=int,
        required=False,
        default=5,
    )
    @admin_only()
    async def simulate_draft(
        self,
        ctx: discord.ApplicationContext,
        max_rounds: int = 10,
        timer_seconds: int = 5,
    ) -> None:
        if ctx.channel_id in self._running_channels:
            await respond_error(ctx, "A dry-run is already running in this channel.")
            return

        division_name = self.draft.get_division_name_for_channel(ctx.channel_id)
        if not division_name:
            await respond_error(ctx, "Channel not registered to any division.")
            return

        state = self.draft._get_state(division_name)
        if not state:
            await respond_error(ctx, "Division not initialised.")
            return

        if state.status != DraftStatus.ACTIVE:
            await respond_error(
                ctx, f"Draft is not active (status: {state.status.value})."
            )
            return

        max_rounds = max(1, min(max_rounds, 50))
        timer_seconds = max(3, min(timer_seconds, 30))

        self._running_channels.add(ctx.channel_id)
        self.draft.enter_simulation_mode()
        try:
            await respond_success(
                ctx,
                f"Dry-run started — up to **{max_rounds}** rounds, "
                f"timer test **{timer_seconds}s**. Watch this channel.",
                ephemeral=True,
            )

            runner = DraftSimulationRunner(
                bot=self.bot,
                draft_service=self.draft,
                channel=ctx.channel,
                guild=ctx.guild,
                division_name=division_name,
                admin_discord_id=str(ctx.author.id),
                config=SimulationConfig(
                    max_rounds=max_rounds,
                    timer_test_seconds=timer_seconds,
                ),
            )
            await runner.run()
        except Exception:
            logger.exception("[SimulationCog] Dry-run failed in %s", division_name)
            await ctx.channel.send(
                "```\n🔬 [DRY-RUN] ⛔ Unexpected error — check logs.\n```"
            )
        finally:
            self._running_channels.discard(ctx.channel_id)
            self.draft.exit_simulation_mode()

    @discord.slash_command(
        name="simulate_system",
        description="[ADMIN] Full end-to-end system test. Rollback with /rollback_system_test.",
    )
    @admin_only()
    async def simulate_system(self, ctx: discord.ApplicationContext) -> None:
        if self._system_test_running:
            await respond_error(ctx, "A system test is already running.")
            return

        self._system_test_running = True
        self.draft.enter_simulation_mode()
        try:
            divisions = system_test_divisions()
            from simulation.replacements import system_test_channel_id, system_test_max_attempts

            dest = system_test_channel_id()
            dest_note = f"<#{dest}>" if dest else "each division channel"
            await respond_success(
                ctx,
                f"🧪 **System test started** across {len(divisions)} division(s): "
                f"{', '.join(divisions)} — posting in {dest_note} "
                f"({system_test_max_attempts()} attempt(s)). "
                "When finished reviewing, run `/rollback_system_test` to restore "
                "coaches, state, Participants coach rows, and tracked pick cells.",
                ephemeral=True,
            )

            runner = MultiDivisionSystemTest(
                bot=self.bot,
                draft=self.draft,
                guild=ctx.guild,
                admin_id=str(ctx.author.id),
                status_channel=ctx.channel,
            )
            try:
                report = await runner.run()
                embed = await runner.build_summary_embed()
                await self._post_system_test_summary(ctx, embed)
                if not report.success:
                    await respond_error(
                        ctx, f"System test failed: {report.error or 'unknown error'}"
                    )
            except Exception as exc:
                embed = await runner.build_summary_embed()
                await self._post_system_test_summary(ctx, embed)
                await respond_error(ctx, f"System test failed: {exc}")
        finally:
            self._system_test_running = False
            self.draft.exit_simulation_mode()

    @discord.slash_command(
        name="system_test_logs",
        description="[ADMIN] Show summary and files from the last /simulate_system run.",
    )
    @admin_only()
    async def system_test_logs(self, ctx: discord.ApplicationContext) -> None:
        report = SystemTestLogger.load_latest_report()
        if not report:
            await respond_error(
                ctx,
                "No system test report found. Run `/simulate_system` first.",
                ephemeral=True,
            )
            return

        ok = report.get("success", False)
        colour = 0x57F287 if ok else 0xED4245
        embed = discord.Embed(
            title="🧪 Last System Test Report",
            colour=colour,
        )
        embed.add_field(
            name="Result",
            value="✅ Passed" if ok else f"❌ Failed: {report.get('error', '?')}",
            inline=False,
        )
        embed.add_field(
            name="Session",
            value=f"`{report.get('session_id', '?')}`",
            inline=True,
        )
        embed.add_field(
            name="Duration",
            value=f"{report.get('duration_seconds', '?')}s",
            inline=True,
        )
        embed.add_field(
            name="Rollback",
            value=(
                "⏳ Run `/rollback_system_test` to restore"
                if not report.get("rolled_back")
                else "✅ Restored"
            ),
            inline=True,
        )
        session_dir = report.get("session_dir") or report.get("log_paths", {}).get("session_dir")
        if session_dir:
            repl = SystemTestTransaction.format_replacement_summary(session_dir)
            if repl and "No replacements" not in repl:
                embed.add_field(name="Replacements during test", value=repl[:1020], inline=False)

        div_lines = []
        for div, passed in (report.get("division_results") or {}).items():
            div_lines.append(f"{'✅' if passed else '❌'} **{div}**")
        if div_lines:
            embed.add_field(
                name="Divisions",
                value="\n".join(div_lines),
                inline=False,
            )

        log_path = report.get("report_log", LATEST_REPORT_LOG)
        json_path = report.get("report_json", "")
        embed.add_field(
            name="Files",
            value=f"Log: `{log_path}`\nJSON: `{json_path}`",
            inline=False,
        )
        embed.set_footer(text=f"Finished: {report.get('finished_at_iso', '?')}")

        files: list[discord.File] = []
        if log_path and os.path.isfile(log_path):
            try:
                size = os.path.getsize(log_path)
                if size <= 7_500_000:
                    files.append(discord.File(log_path, filename="system_test_report.log"))
            except OSError:
                pass

        await ctx.respond(embed=embed, files=files or None, ephemeral=True)

    @discord.slash_command(
        name="rollback_system_test",
        description="[ADMIN] Restore coaches, state, Participants rows, and tracked pick cells.",
    )
    @admin_only()
    async def rollback_system_test(self, ctx: discord.ApplicationContext) -> None:
        await ctx.defer(ephemeral=True)
        manifest = SystemTestTransaction.load_latest_manifest()
        if not manifest:
            await respond_error(ctx, "No system test snapshot found.")
            return

        session_dir = manifest.get("session_dir", "")
        try:
            await SystemTestTransaction.rollback_from_manifest(
                manifest,
                self.draft,
                self.draft.persistence,
                self.draft.channel_lock,
                self.draft.sheets,
            )
            replacement_summary = SystemTestTransaction.format_replacement_summary(
                session_dir
            )
            embed = discord.Embed(
                title="✅ System Test Rollback Complete",
                description=(
                    f"Restored snapshot **`{manifest['session_id']}`**:\n"
                    "• `coaches.json`\n"
                    "• Division state files\n"
                    "• Participants coach cells changed by /replace\n"
                    "• Tracked Drafting Pool Pokémon name cells (formulas untouched)"
                ),
                colour=0x57F287,
            )
            if replacement_summary:
                embed.add_field(
                    name="Replacements reverted",
                    value=replacement_summary[:1020],
                    inline=False,
                )
            await ctx.followup.send(embed=embed, ephemeral=True)
        except Exception as exc:
            logger.exception("[SimulationCog] Manual rollback failed")
            await respond_error(ctx, f"Rollback failed: {exc}")


def setup(bot: discord.Bot) -> None:
    pass
