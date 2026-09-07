"""Interactive wizard for /replace."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from services.embed_service import EmbedService
from services.public_messages import replacement_welcome_message
from services.replace_wizard import build_replace_wizard_embed
from utils.i18n import i18n
from utils.response_embeds import interaction_embed, interaction_error, success_embed

if TYPE_CHECKING:
    from cogs.admin_cog import AdminCog

WIZARD_TIMEOUT = 900


def _L(locale: str, key: str, **kwargs) -> str:
    return i18n.t_locale(locale, key, **kwargs)


class ReplaceDetailsModal(discord.ui.Modal):
    def __init__(
        self,
        admin_cog: "AdminCog",
        division_name: str,
        *,
        locale: str,
        default_timezone: str = "GMT+0",
    ) -> None:
        super().__init__(title=_L(locale, "replace.wizard.modal.title"))
        self.admin_cog = admin_cog
        self.division_name = division_name
        self.locale = locale

        self.team_name = discord.ui.InputText(
            label=_L(locale, "replace.wizard.modal.team"),
            placeholder=_L(locale, "replace.wizard.modal.team_placeholder"),
            max_length=60,
        )
        self.timezone = discord.ui.InputText(
            label=_L(locale, "replace.wizard.modal.timezone"),
            placeholder="GMT+1",
            value=default_timezone,
            required=False,
            max_length=40,
        )
        self.logo_url = discord.ui.InputText(
            label=_L(locale, "replace.wizard.modal.logo"),
            placeholder="https://…",
            required=False,
            max_length=300,
        )
        self.add_item(self.team_name)
        self.add_item(self.timezone)
        self.add_item(self.logo_url)

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.admin_cog.complete_replace_from_wizard(
            interaction,
            division_name=self.division_name,
            team_name=self.team_name.value.strip(),
            timezone=(self.timezone.value or "GMT+0").strip(),
            logo_url=(self.logo_url.value or "").strip(),
            locale=self.locale,
        )


class ReplaceWizardView(discord.ui.View):
    def __init__(
        self,
        admin_cog: "AdminCog",
        division_name: str,
        *,
        locale: str,
        default_timezone: str = "GMT+0",
    ) -> None:
        super().__init__(timeout=WIZARD_TIMEOUT)
        self.admin_cog = admin_cog
        self.division_name = division_name
        self.locale = locale
        self.default_timezone = default_timezone

    @discord.ui.button(
        label="Fill details",
        style=discord.ButtonStyle.primary,
        emoji="📝",
        custom_id="replace_wizard:open",
    )
    async def open_modal(
        self,
        button: discord.ui.Button,
        interaction: discord.Interaction,
    ) -> None:
        _ = button
        modal = ReplaceDetailsModal(
            self.admin_cog,
            self.division_name,
            locale=self.locale,
            default_timezone=self.default_timezone,
        )
        await interaction.response.send_modal(modal)


def localized_replace_wizard_view(
    admin_cog: "AdminCog",
    division_name: str,
    *,
    locale: str,
    default_timezone: str = "GMT+0",
) -> ReplaceWizardView:
    view = ReplaceWizardView(
        admin_cog,
        division_name,
        locale=locale,
        default_timezone=default_timezone,
    )
    for child in view.children:
        if isinstance(child, discord.ui.Button) and child.custom_id == "replace_wizard:open":
            child.label = _L(locale, "replace.wizard.button.open")
    return view
