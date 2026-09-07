"""Confirmation step before sending a match proposal."""

from __future__ import annotations

import discord

from services.match_service import MatchService
from utils.i18n import i18n
from utils.response_embeds import interaction_error, success_embed
from views.match_proposal_view import build_match_proposal_view


class MatchProposalConfirmView(discord.ui.View):
    def __init__(
        self,
        *,
        proposer_id: str,
        division: str,
        opponent_id: str,
        scheduled_utc: float,
        week: int,
        match_service: MatchService,
        bot: discord.Bot,
        channel_id: int,
    ) -> None:
        super().__init__(timeout=180)
        self.proposer_id = proposer_id
        self.division = division
        self.opponent_id = opponent_id
        self.scheduled_utc = scheduled_utc
        self.week = week
        self.match_service = match_service
        self.bot = bot
        self.channel_id = channel_id

    async def _guard(self, interaction: discord.Interaction) -> bool:
        if str(interaction.user.id) != self.proposer_id:
            await interaction_error(
                interaction,
                i18n.t(interaction.user.id, "match.confirm_not_yours"),
                locale=i18n.get_user_locale(interaction.user.id),
            )
            return False
        return True

    @discord.ui.button(label="Send proposal", style=discord.ButtonStyle.success)
    async def confirm(self, button: discord.ui.Button, interaction: discord.Interaction) -> None:
        if not await self._guard(interaction):
            return

        loc = i18n.get_user_locale(self.proposer_id)
        proposal = self.match_service.create_proposal(
            division=self.division,
            proposer_id=self.proposer_id,
            opponent_id=self.opponent_id,
            scheduled_utc=self.scheduled_utc,
            week=self.week,
        )
        embed, view = build_match_proposal_view(proposal.id, self.match_service)

        channel = self.bot.get_channel(self.channel_id) or interaction.channel
        await interaction.response.edit_message(
            embed=success_embed(i18n.t(self.proposer_id, "match.proposal_sent"), locale=loc),
            view=None,
        )
        if channel:
            msg = await channel.send(embed=embed, view=view)
            proposal.message_id = msg.id
            proposal.channel_id = channel.id
            self.match_service.update(proposal)

        opponent = await self.bot.fetch_user(int(self.opponent_id))
        if opponent:
            from services.embed_service import EmbedService

            try:
                dm_embed = EmbedService.match_proposal(
                    proposal, locale=i18n.get_user_locale(opponent.id)
                )
                await opponent.send(
                    content=i18n.t(opponent.id, "match.dm_notify", division=self.division),
                    embed=dm_embed,
                )
            except discord.HTTPException:
                pass

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, button: discord.ui.Button, interaction: discord.Interaction) -> None:
        if not await self._guard(interaction):
            return
        loc = i18n.get_user_locale(self.proposer_id)
        await interaction.response.edit_message(
            embed=success_embed(
                i18n.t(self.proposer_id, "match.proposal_cancelled"), locale=loc
            ),
            view=None,
        )
