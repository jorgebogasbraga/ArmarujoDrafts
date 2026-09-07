"""Trade system slash commands."""

from __future__ import annotations

import logging

import discord
from discord import option
from discord.commands import AutocompleteContext
from discord.ext import commands

from config import Config
from models.trade import TradeStatus, TradeType
from services.trade_service import TradeService
from utils.division_helper import get_division_name_by_channel, get_coaches_for_division
from utils.i18n import i18n
from utils.pokemon_autocomplete import (
    POKEMON_OPTION_DESCRIPTION,
    trade_offering_autocomplete,
    trade_receiving_autocomplete,
)
from utils.response_embeds import list_embed, respond_embed, respond_error, respond_info, respond_success
from views.trade_view import TradeApprovalView, build_trade_embed

logger = logging.getLogger(__name__)


class TradeCog(commands.Cog):
    def __init__(self, bot: discord.Bot, trade_service: TradeService) -> None:
        self.bot = bot
        self.trade_service = trade_service

    def _get_division(self, channel_id: int, user_id: str) -> tuple[str | None, str | None]:
        division = get_division_name_by_channel(channel_id)
        if not division:
            return None, i18n.t(user_id, "errors.channel_not_registered")
        return division, None

    async def _trade_receiving_autocomplete(
        self, ctx: AutocompleteContext
    ) -> list[discord.OptionChoice]:
        draft = self.trade_service.draft_service
        if not draft:
            return []
        options = ctx.options or {}
        return trade_receiving_autocomplete(
            draft,
            ctx.interaction.channel_id,
            ctx.value or "",
            proposer_id=str(ctx.interaction.user.id),
            target_option=options.get("target"),
        )

    async def _trade_offering_autocomplete(
        self, ctx: AutocompleteContext
    ) -> list[discord.OptionChoice]:
        draft = self.trade_service.draft_service
        if not draft:
            return []
        return trade_offering_autocomplete(
            draft,
            ctx.interaction.channel_id,
            str(ctx.interaction.user.id),
            ctx.value or "",
        )

    @discord.slash_command(name="trade_offer", description="Propose a Pokémon trade.")
    @option(
        "receiving",
        description=POKEMON_OPTION_DESCRIPTION,
        autocomplete=_trade_receiving_autocomplete,
    )
    @option(
        "offering",
        description=POKEMON_OPTION_DESCRIPTION,
        required=False,
        autocomplete=_trade_offering_autocomplete,
    )
    @option("target", description="Target coach (direct trade)", required=False, type=discord.Member)
    async def trade_offer(
        self,
        ctx: discord.ApplicationContext,
        receiving: str,
        offering: str = "",
        target: discord.Member = None,
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

        trade_type = TradeType.DIRECT if target else TradeType.POOL
        target_id = str(target.id) if target else uid

        if trade_type == TradeType.DIRECT and target:
            if str(target.id) not in coach_ids:
                await respond_error(ctx, i18n.t(uid, "trade.target_not_coach"), locale=loc)
                return
            if str(target.id) == uid:
                await respond_error(ctx, i18n.t(uid, "trade.self_target"), locale=loc)
                return

        if trade_type == TradeType.DIRECT and not offering:
            await respond_error(ctx, i18n.t(uid, "trade.offering_required"), locale=loc)
            return

        trade, err_key = self.trade_service.create_trade(
            division=division,
            proposer_id=uid,
            target_id=target_id,
            offering=offering,
            receiving=receiving,
            trade_type=trade_type,
        )
        if not trade:
            await respond_error(ctx, i18n.t(uid, err_key), locale=loc)
            return

        channel_id = self.trade_service.get_trades_channel(division) or ctx.channel_id
        channel = self.bot.get_channel(channel_id) or ctx.channel
        embed = build_trade_embed(trade, locale=loc)
        view = TradeApprovalView(trade.id, self.trade_service)
        if trade.status == TradeStatus.AWAITING_MOD:
            from views.trade_view import TradeModView
            view = TradeModView(trade.id, self.trade_service)

        msg = await channel.send(embed=embed, view=view)
        trade.message_id = msg.id
        trade.channel_id = channel.id
        self.trade_service._trades[trade.id] = trade
        self.trade_service._save()

        await respond_success(ctx, i18n.t(uid, "trade.proposed"), locale=loc)

    @discord.slash_command(name="trade_queue", description="[ADMIN] Pending trades awaiting mod approval.")
    async def trade_queue(self, ctx: discord.ApplicationContext) -> None:
        uid = str(ctx.author.id)
        loc = i18n.get_user_locale(uid)
        if Config.ADMIN_ROLE_ID and not any(
            r.id == Config.ADMIN_ROLE_ID for r in ctx.author.roles
        ):
            await respond_error(ctx, i18n.t(uid, "errors.admin_only"), locale=loc)
            return

        division, err = self._get_division(ctx.channel_id, uid)
        if err:
            await respond_error(ctx, err, locale=loc)
            return

        pending = self.trade_service.pending_mod_queue(division)
        if not pending:
            await respond_info(ctx, i18n.t(uid, "trade.no_pending"), locale=loc)
            return

        lines = [
            i18n.t(uid, "trade.queue_line", id=t.id, proposer=f"<@{t.proposer_id}>", receiving=t.receiving)
            for t in pending
        ]
        await respond_embed(
            ctx,
            list_embed(lines, locale=loc, title=i18n.t(uid, "trade.queue_title")),
            ephemeral=True,
        )

    @discord.slash_command(name="my_trades", description="List your trade proposals.")
    async def my_trades(self, ctx: discord.ApplicationContext) -> None:
        uid = str(ctx.author.id)
        loc = i18n.get_user_locale(uid)
        division, err = self._get_division(ctx.channel_id, uid)
        if err:
            await respond_error(ctx, err, locale=loc)
            return

        mine = [
            t for t in self.trade_service._trades.values()
            if t.division.lower() == division.lower()
            and uid in (t.proposer_id, t.target_id)
        ]
        if not mine:
            await respond_info(ctx, i18n.t(uid, "trade.none"), locale=loc)
            return

        lines = [
            i18n.t(uid, "trade.my_line", id=t.id, status=t.status.value, receiving=t.receiving)
            for t in sorted(mine, key=lambda x: x.created_at, reverse=True)
        ]
        await respond_embed(
            ctx,
            list_embed(lines, locale=loc, title=i18n.t(uid, "trade.my_title")),
            ephemeral=True,
        )
