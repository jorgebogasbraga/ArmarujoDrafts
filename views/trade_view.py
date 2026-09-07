"""Trade approval interactive view."""

from __future__ import annotations

import discord

from models.trade import TradeStatus
from services.embed_service import EmbedService
from services.trade_service import TradeService
from utils.i18n import i18n
from utils.response_embeds import interaction_error, interaction_success


def build_trade_embed(trade, locale: str | None = None) -> discord.Embed:
    return EmbedService.trade_proposal(trade, locale=locale)


class TradeApprovalView(discord.ui.View):
    def __init__(self, trade_id: str, trade_service: TradeService) -> None:
        super().__init__(timeout=None)
        self.trade_id = trade_id
        self.trade_service = trade_service

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success)
    async def accept(self, button: discord.ui.Button, interaction: discord.Interaction) -> None:
        trade = self.trade_service.get(self.trade_id)
        loc = i18n.get_user_locale(interaction.user.id)
        if not trade:
            await interaction_error(
                interaction, i18n.t(interaction.user.id, "trade.not_found"), locale=loc
            )
            return
        if str(interaction.user.id) not in (trade.target_id, trade.proposer_id):
            if trade.trade_type.value == "direct" and str(interaction.user.id) != trade.target_id:
                await interaction_error(
                    interaction, i18n.t(interaction.user.id, "trade.not_target"), locale=loc
                )
                return
        ok, err = self.trade_service.accept_trade(self.trade_id, str(interaction.user.id))
        if not ok:
            await interaction_error(interaction, i18n.t(interaction.user.id, err), locale=loc)
            return
        trade = self.trade_service.get(self.trade_id)
        embed = build_trade_embed(trade)
        await interaction.response.edit_message(embed=embed, view=TradeModView(self.trade_id, self.trade_service))
        await interaction_success(
            interaction, i18n.t(interaction.user.id, "trade.accepted_pending_mod"), locale=loc
        )


class TradeModView(discord.ui.View):
    def __init__(self, trade_id: str, trade_service: TradeService) -> None:
        super().__init__(timeout=None)
        self.trade_id = trade_id
        self.trade_service = trade_service

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.primary)
    async def approve(self, button: discord.ui.Button, interaction: discord.Interaction) -> None:
        from config import Config

        loc = i18n.get_user_locale(interaction.user.id)
        if Config.ADMIN_ROLE_ID and not any(
            r.id == Config.ADMIN_ROLE_ID for r in interaction.user.roles
        ):
            await interaction_error(
                interaction, i18n.t(interaction.user.id, "errors.admin_only"), locale=loc
            )
            return
        ok, err = self.trade_service.approve_trade(self.trade_id, str(interaction.user.id))
        if not ok:
            await interaction_error(interaction, i18n.t(interaction.user.id, err), locale=loc)
            return
        trade = self.trade_service.get(self.trade_id)
        embed = build_trade_embed(trade)
        await interaction.response.edit_message(embed=embed, view=None)
        await interaction_success(
            interaction, i18n.t(interaction.user.id, "trade.approved"), locale=loc
        )

    @discord.ui.button(label="Reject", style=discord.ButtonStyle.danger)
    async def reject(self, button: discord.ui.Button, interaction: discord.Interaction) -> None:
        from config import Config

        loc = i18n.get_user_locale(interaction.user.id)
        if Config.ADMIN_ROLE_ID and not any(
            r.id == Config.ADMIN_ROLE_ID for r in interaction.user.roles
        ):
            await interaction_error(
                interaction, i18n.t(interaction.user.id, "errors.admin_only"), locale=loc
            )
            return
        self.trade_service.reject_trade(self.trade_id)
        trade = self.trade_service.get(self.trade_id)
        embed = build_trade_embed(trade)
        await interaction.response.edit_message(embed=embed, view=None)
