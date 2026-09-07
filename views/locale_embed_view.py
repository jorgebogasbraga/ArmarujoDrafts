"""
Language buttons for public channel embeds.

The posted message stays in the league's own language. Clicking a flag replies
with a private (ephemeral) copy in that language, so one coach reading in
Spanish never changes what the rest of the channel sees — Discord cannot render
a single message differently per viewer, so a private copy is the way to do it.
"""

from __future__ import annotations

from typing import Callable

import discord

from utils.i18n import i18n

EmbedBuilder = Callable[[str], discord.Embed]

_TAB_LABELS = {
    "en": "🇬🇧 EN",
    "pt": "🇵🇹 PT",
    "es": "🇪🇸 ES",
}


def tab_order(default_locale: str) -> list[str]:
    """League language first, English next, then whatever is left."""
    order: list[str] = []
    for loc in (default_locale, "en", *_TAB_LABELS):
        if loc in _TAB_LABELS and loc not in order:
            order.append(loc)
    return order


class LocaleEmbedView(discord.ui.View):
    """Language buttons attached to public announcement embeds."""

    def __init__(
        self,
        build_embed: EmbedBuilder,
        initial_locale: str = "en",
        timeout: float | None = None,
    ) -> None:
        super().__init__(timeout=timeout)
        self.build_embed = build_embed
        self.locale = (
            initial_locale if initial_locale in _TAB_LABELS else "en"
        )
        self._build_buttons()

    def _build_buttons(self) -> None:
        for loc in tab_order(self.locale):
            # The posted language is highlighted; the others offer a translation.
            style = (
                discord.ButtonStyle.primary
                if loc == self.locale
                else discord.ButtonStyle.secondary
            )
            btn = discord.ui.Button(
                label=_TAB_LABELS[loc],
                style=style,
                custom_id=f"locale_tab:{loc}",
            )

            async def callback(interaction: discord.Interaction, target=loc) -> None:
                await interaction.response.send_message(
                    embed=self.build_embed(target),
                    ephemeral=True,
                )

            btn.callback = callback
            self.add_item(btn)

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True


def public_embed_message(
    build_embed: EmbedBuilder,
    locale: str | None = None,
) -> tuple[discord.Embed, LocaleEmbedView]:
    """Return an embed in the league's language plus its translation buttons."""
    loc = locale if locale in _TAB_LABELS else i18n.default_locale
    view = LocaleEmbedView(build_embed, initial_locale=loc)
    return build_embed(loc), view
