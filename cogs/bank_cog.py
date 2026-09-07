"""
BankCog — slash commands for the pick bank system.

Primary entry point: /bank (interactive wizard)
"""

import logging

import discord
from discord.ext import commands

from services.bank_wizard import BankWizardSession, build_dashboard_embed
from services.draft_service import DraftService
from utils.i18n import i18n
from utils.response_embeds import error_embed, respond_embed
from views.bank_wizard_view import BankDashboardView

logger = logging.getLogger(__name__)


class BankCog(commands.Cog):
    def __init__(self, bot: discord.Bot, draft_service: DraftService) -> None:
        self.bot = bot
        self.draft = draft_service
        self._wizard_sessions: dict[str, BankWizardSession] = {}

    def _session_key(self, division_name: str, coach_id: str) -> str:
        return f"{coach_id}:{division_name.lower()}"

    def get_wizard_session(
        self, division_name: str, coach_id: str, locale: str
    ) -> BankWizardSession:
        key = self._session_key(division_name, coach_id)
        if key not in self._wizard_sessions:
            self._wizard_sessions[key] = BankWizardSession(
                division_name=division_name,
                coach_id=coach_id,
                locale=locale,
            )
        else:
            self._wizard_sessions[key].locale = locale
        return self._wizard_sessions[key]

    def _get_division(
        self, channel_id: int, user_id: str
    ) -> tuple[str | None, str | None]:
        division_name = self.draft.get_division_name_for_channel(channel_id)
        if not division_name:
            return None, i18n.t(user_id, "errors.channel_not_registered")
        return division_name, None

    @discord.slash_command(
        name="bank",
        description="Open the pick bank wizard — configure auto-picks visually.",
    )
    async def bank(self, ctx: discord.ApplicationContext) -> None:
        uid = str(ctx.author.id)
        loc = i18n.get_user_locale(uid)
        division_name, err = self._get_division(ctx.channel_id, uid)
        if err:
            await respond_embed(ctx, error_embed(err, locale=loc), ephemeral=True)
            return

        state = self.draft._get_state(division_name)
        if not state:
            await respond_embed(
                ctx,
                error_embed(i18n.t(uid, "errors.division_not_found"), locale=loc),
                ephemeral=True,
            )
            return

        coach = state.get_coach_by_id(uid)
        if not coach:
            await respond_embed(
                ctx,
                error_embed(i18n.t(uid, "errors.not_coach"), locale=loc),
                ephemeral=True,
            )
            return

        self.get_wizard_session(division_name, uid, loc)
        embed = build_dashboard_embed(coach, state, loc)
        view = BankDashboardView(self, division_name, uid, loc)
        await ctx.respond(embed=embed, view=view, ephemeral=True)
