"""Paginator for the per-division admin draft board."""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Optional

import discord

from services.admin_draft_board import board_total_pages

if TYPE_CHECKING:
    from models.draft_state import DraftState


class AdminDraftBoardView(discord.ui.View):
    def __init__(
        self,
        division_name: str,
        get_state: Callable[[str], Optional["DraftState"]],
        refresh_page: Callable,
        initial_page: int = 0,
    ) -> None:
        super().__init__(timeout=None)
        self.division_name = division_name
        self.get_state = get_state
        self.refresh_page = refresh_page
        self.page = initial_page
        slug = division_name.lower().replace(" ", "_")

        prev_btn = discord.ui.Button(
            label="◀",
            style=discord.ButtonStyle.secondary,
            custom_id=f"admin_board:prev:{slug}",
        )
        prev_btn.callback = self._prev_page
        self.add_item(prev_btn)

        latest_btn = discord.ui.Button(
            label="↻ Latest",
            style=discord.ButtonStyle.primary,
            custom_id=f"admin_board:latest:{slug}",
        )
        latest_btn.callback = self._latest_page
        self.add_item(latest_btn)

        next_btn = discord.ui.Button(
            label="▶",
            style=discord.ButtonStyle.secondary,
            custom_id=f"admin_board:next:{slug}",
        )
        next_btn.callback = self._next_page
        self.add_item(next_btn)

        self._sync_buttons()

    def _sync_buttons(self) -> None:
        state = self.get_state(self.division_name)
        total = board_total_pages(state) if state else 1
        buttons = [c for c in self.children if isinstance(c, discord.ui.Button)]
        if buttons:
            buttons[0].disabled = self.page <= 0
        if len(buttons) >= 3:
            buttons[2].disabled = self.page >= total - 1

    async def _prev_page(self, interaction: discord.Interaction) -> None:
        if self.page > 0:
            self.page -= 1
            await self.refresh_page(
                self.division_name, self.page, interaction=interaction
            )

    async def _next_page(self, interaction: discord.Interaction) -> None:
        state = self.get_state(self.division_name)
        if state and self.page < board_total_pages(state) - 1:
            self.page += 1
            await self.refresh_page(
                self.division_name, self.page, interaction=interaction
            )

    async def _latest_page(self, interaction: discord.Interaction) -> None:
        self.page = 0
        await self.refresh_page(
            self.division_name, self.page, interaction=interaction
        )
