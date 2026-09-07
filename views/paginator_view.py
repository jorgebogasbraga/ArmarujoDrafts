"""Generic paginator for Discord embeds."""

from __future__ import annotations

from typing import Callable

import discord

EmbedBuilder = Callable[[int], discord.Embed]


class PaginatorView(discord.ui.View):
    def __init__(
        self,
        build_embed: EmbedBuilder,
        total_pages: int,
        initial_page: int = 0,
        timeout: float = 300,
    ) -> None:
        super().__init__(timeout=timeout)
        self.build_embed = build_embed
        self.total_pages = max(1, total_pages)
        self.page = max(0, min(initial_page, self.total_pages - 1))
        self._update_buttons()

    def _update_buttons(self) -> None:
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                if child.custom_id == "prev":
                    child.disabled = self.page <= 0
                elif child.custom_id == "next":
                    child.disabled = self.page >= self.total_pages - 1

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary, custom_id="prev")
    async def prev_page(self, button: discord.ui.Button, interaction: discord.Interaction) -> None:
        if self.page > 0:
            self.page -= 1
            self._update_buttons()
            await interaction.response.edit_message(
                embed=self.build_embed(self.page), view=self
            )

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary, custom_id="next")
    async def next_page(self, button: discord.ui.Button, interaction: discord.Interaction) -> None:
        if self.page < self.total_pages - 1:
            self.page += 1
            self._update_buttons()
            await interaction.response.edit_message(
                embed=self.build_embed(self.page), view=self
            )
