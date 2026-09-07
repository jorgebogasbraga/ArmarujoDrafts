"""League standings, kill leaderboard, and replay submission."""

from __future__ import annotations

import logging
from datetime import datetime

import discord
from discord import option
from discord.ext import commands

from services.embed_service import EmbedService
from services.league.kill_leaderboard_service import KillLeaderboardService
from services.league.standings_service import StandingsService
from services.pokemon_service import PokemonService
from services.showdown.battle_embed import build_battle_summary_message
from services.showdown.replay_parser import parse_battle_summary_url, parse_replay_url
from utils.division_helper import get_division_name_by_channel
from utils.i18n import i18n
from utils.response_embeds import error_embed, info_embed, interaction_error, interaction_success, respond_error, success_embed
from views.paginator_view import PaginatorView
from views.locale_embed_view import public_embed_message

logger = logging.getLogger(__name__)


class LeagueCog(commands.Cog):
    def __init__(
        self,
        bot: discord.Bot,
        standings: StandingsService,
        kills: KillLeaderboardService,
        sheets,
        pokemon_service: PokemonService,
    ) -> None:
        self.bot = bot
        self.standings = standings
        self.kills = kills
        self.sheets = sheets
        self.pokemon = pokemon_service

    def _resolve_division(self, ctx, division: str | None) -> tuple[str | None, str | None]:
        uid = str(ctx.author.id)
        if division:
            return division, None
        div = get_division_name_by_channel(ctx.channel_id)
        if not div:
            return None, i18n.t(uid, "errors.channel_not_registered")
        return div, None

    @discord.slash_command(
        name="battle_summary",
        description="Parse a Showdown replay and show a battle report with sprites.",
    )
    @option(
        "replay_url",
        description="Replay URL (replay.pokemonshowdown.com or psim.us)",
        type=str,
    )
    async def battle_summary(
        self,
        ctx: discord.ApplicationContext,
        replay_url: str,
    ) -> None:
        uid = str(ctx.author.id)
        loc = i18n.get_user_locale(uid)
        await ctx.defer()

        try:
            summary = await parse_battle_summary_url(replay_url.strip())
        except ValueError as e:
            await ctx.followup.send(
                embed=error_embed(
                    i18n.t(uid, "league.replay_invalid", error=str(e)),
                    locale=loc,
                ),
                ephemeral=True,
            )
            return

        embeds, view = await build_battle_summary_message(
            summary, self.pokemon, locale=loc
        )
        await ctx.followup.send(embeds=embeds, view=view)

    @discord.slash_command(name="standings", description="Show division standings.")
    @option("division", description="Division name (defaults to this channel)", required=False, type=str)
    async def standings_cmd(
        self, ctx: discord.ApplicationContext, division: str = None
    ) -> None:
        div, err = self._resolve_division(ctx, division)
        if err:
            await respond_error(ctx, err, locale=i18n.get_user_locale(ctx.author.id))
            return

        rows = self.standings.get_standings(div, refresh=True)

        def build(loc: str) -> discord.Embed:
            return EmbedService.standings(div, rows, locale=loc)

        embed, view = public_embed_message(build, i18n.get_user_locale(ctx.author.id))
        await ctx.respond(embed=embed, view=view)

    @discord.slash_command(name="killboard", description="Kill leaderboard for a division.")
    @option("division", description="Division name (defaults to this channel)", required=False, type=str)
    async def killboard(
        self, ctx: discord.ApplicationContext, division: str = None
    ) -> None:
        div, err = self._resolve_division(ctx, division)
        if err:
            await respond_error(ctx, err, locale=i18n.get_user_locale(ctx.author.id))
            return

        page_rows, total_pages = self.kills.page(div, 0, refresh=True)
        loc = i18n.get_user_locale(ctx.author.id)

        def build_embed(page: int) -> discord.Embed:
            rows, pages = self.kills.page(div, page)
            return EmbedService.kill_leaderboard(div, rows, page, pages, locale=loc)

        embed = build_embed(0)
        view = PaginatorView(build_embed, total_pages)
        await ctx.respond(embed=embed, view=view)

    @discord.slash_command(name="submit_replay", description="Submit a Showdown replay URL.")
    @option("replay_url", description="Replay URL from replay.pokemonshowdown.com", type=str)
    @option("division", description="Division", required=False, type=str)
    @option("week", description="Week number", required=False, type=int)
    async def submit_replay(
        self,
        ctx: discord.ApplicationContext,
        replay_url: str,
        division: str = None,
        week: int = None,
    ) -> None:
        uid = str(ctx.author.id)
        div, err = self._resolve_division(ctx, division)
        if err:
            await respond_error(ctx, err, locale=i18n.get_user_locale(ctx.author.id))
            return

        await ctx.defer(ephemeral=True)
        try:
            result = await parse_replay_url(replay_url)
        except ValueError as e:
            await ctx.followup.send(
                embed=error_embed(i18n.t(uid, "league.replay_invalid", error=str(e)), locale=loc)
            )
            return

        embed = EmbedService.replay_preview(result, locale=i18n.get_user_locale(uid))

        class ConfirmView(discord.ui.View):
            @discord.ui.button(label="Confirm", style=discord.ButtonStyle.success)
            async def confirm(self, button, interaction):
                if str(interaction.user.id) != uid:
                    await interaction_error(
                        interaction,
                        i18n.t(interaction.user.id, "league.replay_not_yours"),
                        locale=i18n.get_user_locale(interaction.user.id),
                    )
                    return
                record = {
                    "division": div,
                    "week": week or "",
                    "coach_a": result.player_a,
                    "coach_b": result.player_b,
                    "winner": result.winner,
                    "replay_url": replay_url,
                    "kills_a": result.kills_a,
                    "kills_b": result.kills_b,
                    "submitted_by": uid,
                    "timestamp": datetime.utcnow().isoformat(),
                }
                if self_outer.sheets.is_connected:
                    self_outer.sheets.queue_match_result_write(record)
                self_outer.standings.invalidate(div)
                self_outer.kills.invalidate(div)
                await interaction.response.edit_message(
                    embed=success_embed(
                        i18n.t(uid, "league.replay_saved"),
                        locale=loc,
                    ),
                    view=None,
                )

        self_outer = self
        view = ConfirmView(timeout=120)
        await ctx.followup.send(embed=embed, view=view)
