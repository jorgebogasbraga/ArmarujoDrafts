"""Match scheduling slash commands."""

from __future__ import annotations

import logging

import discord
import pytz
from discord import option
from discord.ext import commands

from config import Config
from services.embed_service import EmbedService
from services.match_service import MatchService
from utils.division_helper import get_division_name_by_channel, get_coaches_for_division
from utils.i18n import i18n
from utils.match_scheduling import analyze_match_time
from utils.response_embeds import list_embed, respond_embed, respond_error, respond_info
from utils.timezone_helper import parse_local_datetime, resolve_user_timezone
from views.match_confirm_view import MatchProposalConfirmView

logger = logging.getLogger(__name__)


class MatchCog(commands.Cog):
    def __init__(self, bot: discord.Bot, match_service: MatchService) -> None:
        self.bot = bot
        self.match_service = match_service

    def _get_division(self, channel_id: int, user_id: str) -> tuple[str | None, str | None]:
        division = get_division_name_by_channel(channel_id)
        if not division:
            return None, i18n.t(user_id, "errors.channel_not_registered")
        return division, None

    @discord.slash_command(
        name="propose_match",
        description="Propose a date and time for this week's match.",
    )
    @option("opponent", description="Opponent coach", type=discord.Member)
    @option("week", description="League week number", type=int)
    @option("date", description="Date in your timezone (YYYY-MM-DD)", type=str)
    @option("time", description="Time in your timezone (HH:MM)", type=str)
    async def propose_match(
        self,
        ctx: discord.ApplicationContext,
        opponent: discord.Member,
        week: int,
        date: str,
        time: str,
    ) -> None:
        uid = str(ctx.author.id)
        loc = i18n.get_user_locale(uid)
        division, err = self._get_division(ctx.channel_id, uid)
        if err:
            await respond_error(ctx, err, locale=loc)
            return

        coaches = get_coaches_for_division(division)
        coach_ids = {str(c.get("discord_id", "")) for c in coaches}
        if uid not in coach_ids:
            await respond_error(ctx, i18n.t(uid, "errors.not_coach"), locale=loc)
            return
        if str(opponent.id) not in coach_ids:
            await respond_error(ctx, i18n.t(uid, "match.opponent_not_coach"), locale=loc)
            return
        if str(opponent.id) == uid:
            await respond_error(ctx, i18n.t(uid, "match.self_opponent"), locale=loc)
            return

        try:
            proposer_tz = resolve_user_timezone(
                uid,
                discord_locale=ctx.locale,
                guild_locale=ctx.guild_locale,
                bot_language=loc,
            )
            opponent_tz = resolve_user_timezone(
                opponent.id,
                discord_locale=getattr(opponent, "locale", None),
                guild_locale=ctx.guild_locale,
                bot_language=i18n.get_user_locale(opponent.id),
            )
            local_dt = parse_local_datetime(date, time, proposer_tz.tz)
            scheduled_utc = local_dt.astimezone(pytz.UTC).timestamp()
        except ValueError:
            await respond_error(ctx, i18n.t(uid, "match.invalid_datetime"), locale=loc)
            return

        analysis = analyze_match_time(
            local_dt.astimezone(pytz.UTC),
            proposer_tz,
            opponent_tz,
        )
        embed = EmbedService.match_proposal_preview(
            division=division,
            week=week,
            opponent=opponent,
            analysis=analysis,
            scheduled_utc=scheduled_utc,
            locale=loc,
        )
        view = MatchProposalConfirmView(
            proposer_id=uid,
            division=division,
            opponent_id=str(opponent.id),
            scheduled_utc=scheduled_utc,
            week=week,
            match_service=self.match_service,
            bot=self.bot,
            channel_id=ctx.channel_id,
        )
        await ctx.respond(embed=embed, view=view, ephemeral=True)

    @discord.slash_command(
        name="match_schedule",
        description="View scheduled and played matches for a league week.",
    )
    @option("week", description="League week number", type=int)
    @option("division", description="Division (defaults to this channel)", required=False, type=str)
    async def match_schedule(
        self,
        ctx: discord.ApplicationContext,
        week: int,
        division: str = None,
    ) -> None:
        uid = str(ctx.author.id)
        if division:
            div = division
            err = None
        else:
            div, err = self._get_division(ctx.channel_id, uid)
        if err or not div:
            await respond_error(
                ctx, err or i18n.t(uid, "errors.division_not_found"), locale=i18n.get_user_locale(uid)
            )
            return

        proposals = self.match_service.list_for_week(div, week)
        results = []
        if self.match_service.sheets and self.match_service.sheets.is_connected:
            results = self.match_service.sheets.read_match_results(div, week)

        coaches = get_coaches_for_division(div)
        coach_names = {str(c.get("discord_id", "")): c.get("name", "?") for c in coaches}

        embed = EmbedService.match_week_schedule(
            div,
            week,
            proposals,
            results,
            coach_names,
            locale=i18n.get_user_locale(uid),
        )
        await ctx.respond(embed=embed)

    @discord.slash_command(
        name="match_status",
        description="[ADMIN] List pending match proposals for this division.",
    )
    async def match_status(self, ctx: discord.ApplicationContext) -> None:
        uid = str(ctx.author.id)
        loc = i18n.get_user_locale(uid)
        if Config.ADMIN_ROLE_ID:
            member = ctx.author
            if not any(r.id == Config.ADMIN_ROLE_ID for r in member.roles):
                await respond_error(ctx, i18n.t(uid, "errors.admin_only"), locale=loc)
                return

        division, err = self._get_division(ctx.channel_id, uid)
        if err:
            await respond_error(ctx, err, locale=loc)
            return

        pending = self.match_service.list_pending(division)
        if not pending:
            await respond_info(ctx, i18n.t(uid, "match.no_pending"), locale=loc)
            return

        lines = []
        for p in pending:
            ts = int(p.scheduled_utc)
            week_label = i18n.t(uid, "match.status_week", week=p.week) if p.week else ""
            lines.append(
                i18n.t(
                    uid,
                    "match.status_line",
                    week=week_label,
                    proposer=f"<@{p.proposer_id}>",
                    opponent=f"<@{p.opponent_id}>",
                    time=ts,
                )
            )
        await respond_embed(ctx, list_embed(lines, locale=loc, title=i18n.t(uid, "match.status_title")), ephemeral=True)
