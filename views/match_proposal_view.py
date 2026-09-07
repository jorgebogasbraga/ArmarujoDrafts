"""Interactive buttons for match scheduling proposals."""

from __future__ import annotations

import discord

from models.match_proposal import MatchProposalStatus
from services.match_service import MatchService
from services.embed_service import EmbedService
from utils.i18n import i18n
from utils.response_embeds import interaction_error, interaction_success, success_embed


class CounterProposalModal(discord.ui.Modal):
    def __init__(self, proposal_id: str, match_service: MatchService, division: str) -> None:
        super().__init__(title="Counter-propose match time")
        self.proposal_id = proposal_id
        self.match_service = match_service
        self.division = division
        self.date_input = discord.ui.InputText(
            label="Date (YYYY-MM-DD)",
            placeholder="2026-03-15",
            max_length=10,
        )
        self.time_input = discord.ui.InputText(
            label="Time (HH:MM, your timezone)",
            placeholder="20:00",
            max_length=5,
        )
        self.add_item(self.date_input)
        self.add_item(self.time_input)

    async def callback(self, interaction: discord.Interaction) -> None:
        from utils.timezone_helper import parse_local_datetime, resolve_user_timezone
        import pytz

        old = self.match_service.get(self.proposal_id)
        if not old or old.status != MatchProposalStatus.PENDING:
            await interaction_error(
                interaction,
                i18n.t(interaction.user.id, "match.proposal_not_found"),
                locale=i18n.get_user_locale(interaction.user.id),
            )
            return
        if str(interaction.user.id) != old.opponent_id:
            await interaction_error(
                interaction,
                i18n.t(interaction.user.id, "match.not_opponent"),
                locale=i18n.get_user_locale(interaction.user.id),
            )
            return
        tz_res = resolve_user_timezone(
            interaction.user.id,
            discord_locale=interaction.locale,
            guild_locale=getattr(interaction, "guild_locale", None),
            bot_language=i18n.get_user_locale(interaction.user.id),
        )
        try:
            local_dt = parse_local_datetime(
                self.date_input.value, self.time_input.value, tz_res.tz
            )
            scheduled_utc = local_dt.astimezone(pytz.UTC).timestamp()
        except ValueError:
            await interaction_error(
                interaction,
                i18n.t(interaction.user.id, "match.invalid_datetime"),
                locale=i18n.get_user_locale(interaction.user.id),
            )
            return

        new_proposal = self.match_service.create_proposal(
            division=old.division,
            proposer_id=str(interaction.user.id),
            opponent_id=old.proposer_id,
            scheduled_utc=scheduled_utc,
            parent_id=old.id,
            week=old.week,
        )
        embed, view = build_match_proposal_view(new_proposal.id, self.match_service)
        await interaction_success(
            interaction,
            i18n.t(interaction.user.id, "match.counter_sent"),
            locale=i18n.get_user_locale(interaction.user.id),
        )
        channel = interaction.channel
        if channel:
            msg = await channel.send(embed=embed, view=view)
            new_proposal.message_id = msg.id
            new_proposal.channel_id = channel.id
            self.match_service.update(new_proposal)
            if old.message_id:
                try:
                    old_msg = await channel.fetch_message(old.message_id)
                    old_embed = EmbedService.match_proposal(
                        old, status_override=MatchProposalStatus.COUNTERED
                    )
                    await old_msg.edit(embed=old_embed, view=None)
                except discord.HTTPException:
                    pass


async def _handle_action(
    interaction: discord.Interaction,
    proposal_id: str,
    match_service: MatchService,
    action: str,
) -> None:
    proposal = match_service.get(proposal_id)
    if not proposal or proposal.status != MatchProposalStatus.PENDING:
        await interaction_error(
            interaction,
            i18n.t(interaction.user.id, "match.proposal_not_found"),
            locale=i18n.get_user_locale(interaction.user.id),
        )
        return
    if str(interaction.user.id) != proposal.opponent_id:
        await interaction_error(
            interaction,
            i18n.t(interaction.user.id, "match.not_opponent"),
            locale=i18n.get_user_locale(interaction.user.id),
        )
        return

    if action == "confirm":
        proposal = match_service.set_status(proposal_id, MatchProposalStatus.CONFIRMED)
        key = "match.confirmed"
    else:
        proposal = match_service.set_status(proposal_id, MatchProposalStatus.DECLINED)
        key = "match.declined"

    embed = EmbedService.match_proposal(proposal)
    loc = i18n.get_user_locale(interaction.user.id)
    await interaction.response.edit_message(embed=embed, view=None)
    await interaction_success(interaction, i18n.t(interaction.user.id, key), locale=loc)


def build_match_proposal_view(proposal_id: str, match_service: MatchService):
    """Return (embed_builder, view) for a match proposal message."""

    class CombinedView(discord.ui.View):
        def __init__(self) -> None:
            super().__init__(timeout=None)
            self.proposal_id = proposal_id
            self.match_service = match_service
            self.locale = "en"
            self._build()

        def _build(self) -> None:
            self.clear_items()
            for loc, label in (("en", "🇬🇧 EN"), ("pt", "🇵🇹 PT"), ("es", "🇪🇸 ES")):
                style = (
                    discord.ButtonStyle.primary if loc == self.locale
                    else discord.ButtonStyle.secondary
                )
                btn = discord.ui.Button(label=label, style=style)

                async def locale_cb(inter, target=loc):
                    self.locale = target
                    p = self.match_service.get(self.proposal_id)
                    if p:
                        self._build()
                        await inter.response.edit_message(
                            embed=EmbedService.match_proposal(p, locale=target),
                            view=self,
                        )

                btn.callback = locale_cb
                self.add_item(btn)

            p = self.match_service.get(self.proposal_id)
            if p and p.status == MatchProposalStatus.PENDING:
                confirm = discord.ui.Button(
                    label="✅ Confirm", style=discord.ButtonStyle.success
                )
                decline = discord.ui.Button(
                    label="❌ Decline", style=discord.ButtonStyle.danger
                )
                counter = discord.ui.Button(
                    label="🔄 Counter", style=discord.ButtonStyle.secondary
                )

                async def confirm_cb(inter):
                    await _handle_action(inter, proposal_id, match_service, "confirm")

                async def decline_cb(inter):
                    await _handle_action(inter, proposal_id, match_service, "decline")

                async def counter_cb(inter):
                    prop = match_service.get(proposal_id)
                    if not prop:
                        return
                    if str(inter.user.id) != prop.opponent_id:
                        await interaction_error(
                            inter,
                            i18n.t(inter.user.id, "match.not_opponent"),
                            locale=i18n.get_user_locale(inter.user.id),
                        )
                        return
                    await inter.response.send_modal(
                        CounterProposalModal(proposal_id, match_service, prop.division)
                    )

                confirm.callback = confirm_cb
                decline.callback = decline_cb
                counter.callback = counter_cb
                self.add_item(confirm)
                self.add_item(decline)
                self.add_item(counter)

    p = match_service.get(proposal_id)
    embed = EmbedService.match_proposal(p) if p else discord.Embed(title="Match")
    return embed, CombinedView()
