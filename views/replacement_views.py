"""Views for replacement announcements."""

from __future__ import annotations

import discord

from config import Config
from utils.i18n import i18n


class ReplacementGlobalView(discord.ui.View):
    """Link button on the global replacement announcement."""

    def __init__(self, channel_id: int, *, locale: str | None = None) -> None:
        super().__init__(timeout=None)
        loc = locale or i18n.default_locale
        if Config.GUILD_ID and channel_id:
            url = f"https://discord.com/channels/{Config.GUILD_ID}/{channel_id}"
            self.add_item(
                discord.ui.Button(
                    label=i18n.t_locale(loc, "embed.replace.button.goto_channel"),
                    style=discord.ButtonStyle.link,
                    url=url,
                )
            )
