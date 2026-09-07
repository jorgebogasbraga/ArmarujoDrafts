"""
SearchCog — /search command for browsing the Pokémon draft pool.
"""

from __future__ import annotations

import discord
from discord import option
from discord.commands import AutocompleteContext
from discord.ext import commands

from services.draft_service import DraftService
from constants.type_chart import ALL_TYPES
from utils.i18n import i18n
from utils.response_embeds import error_embed, respond_error, respond_info
from utils.pokemon_autocomplete import (
    POKEMON_NAME_FILTER_DESCRIPTION,
    pool_name_autocomplete,
)

PAGE_SIZE = 12


class PoolBrowserView(discord.ui.View):
    """Paginated pool view — holds filtered results, rebuilds embed on navigation."""

    def __init__(
        self,
        items: list,
        title: str,
        division_name: str,
        locale: str,
        timeout: int = 300,
    ) -> None:
        super().__init__(timeout=timeout)
        self.items         = items
        self.title         = title
        self.division_name = division_name
        self.locale        = locale
        self.page          = 0
        self._refresh_buttons()

    def total_pages(self) -> int:
        return max(1, (len(self.items) + PAGE_SIZE - 1) // PAGE_SIZE)

    def _refresh_buttons(self) -> None:
        self.prev_btn.disabled = self.page == 0
        self.next_btn.disabled = self.page >= self.total_pages() - 1

    def build_embed(self) -> discord.Embed:
        start = self.page * PAGE_SIZE
        page_items = self.items[start : start + PAGE_SIZE]
        pages      = self.total_pages()
        total      = len(self.items)
        loc        = self.locale

        embed = discord.Embed(title=self.title, colour=0x5865F2)
        lines = []

        for p in page_items:
            type_str = (
                "/".join(t.capitalize() for t in p.types)
                if p.types and p.types != ["unknown"]
                else "—"
            )
            if p.is_banned:
                status = i18n.t_locale(loc, "search.status_banned")
            elif p.is_drafted:
                drafter_info = getattr(p, "_drafter_display", "Unknown")
                status = i18n.t_locale(
                    loc, "search.status_drafted", drafter=drafter_info
                )
            else:
                status = i18n.t_locale(loc, "search.status_available")

            lines.append(
                i18n.t_locale(
                    loc,
                    "search.line",
                    name=p.name,
                    types=type_str,
                    points=p.points,
                    status=status,
                )
            )

        embed.description = (
            "\n".join(lines)
            if lines
            else i18n.t_locale(loc, "search.empty_page")
        )
        embed.set_footer(
            text=i18n.t_locale(
                loc,
                "search.footer",
                page=self.page + 1,
                pages=pages,
                total=total,
            )
        )
        return embed

    @discord.ui.button(label="◀ Prev", style=discord.ButtonStyle.secondary)
    async def prev_btn(
        self, button: discord.ui.Button, interaction: discord.Interaction
    ) -> None:
        self.page -= 1
        self._refresh_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.secondary)
    async def next_btn(
        self, button: discord.ui.Button, interaction: discord.Interaction
    ) -> None:
        self.page += 1
        self._refresh_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True


class SearchCog(commands.Cog):
    def __init__(self, bot: discord.Bot, draft_service: DraftService) -> None:
        self.bot   = bot
        self.draft = draft_service

    def _get_division(self, channel_id: int, user_id: str):
        name = self.draft.get_division_name_for_channel(channel_id)
        if not name:
            return None, i18n.t(user_id, "errors.channel_not_registered")
        return name, None

    async def _search_name_autocomplete(
        self, ctx: AutocompleteContext
    ) -> list[discord.OptionChoice]:
        return pool_name_autocomplete(
            self.draft,
            ctx.interaction.channel_id,
            ctx.value or "",
            include_drafted=True,
            include_banned=True,
        )

    @discord.slash_command(
        name="search",
        description="Browse the Pokémon draft pool with optional filters.",
    )
    @option(
        "name",
        description=POKEMON_NAME_FILTER_DESCRIPTION,
        required=False,
        autocomplete=_search_name_autocomplete,
    )
    @option("type",    description="Filter by type (e.g. fire, water)", required=False)
    @option("min_pts", description="Minimum point cost", type=int, required=False)
    @option("max_pts", description="Maximum point cost", type=int, required=False)
    @option(
        "status",
        description="Show only available, drafted, or all Pokémon",
        required=False,
        choices=["available", "drafted", "all"],
    )
    async def search(
        self,
        ctx: discord.ApplicationContext,
        name:    str = None,
        type:    str = None,
        min_pts: int = None,
        max_pts: int = None,
        status:  str = "all",
    ) -> None:
        uid = str(ctx.author.id)
        loc = i18n.get_user_locale(uid)
        division_name, err = self._get_division(ctx.channel_id, uid)
        if err:
            await respond_error(ctx, err, locale=loc)
            return

        state = self.draft._get_state(division_name)
        if not state:
            await respond_error(ctx, i18n.t(uid, "search.not_initialised"), locale=loc)
            return

        type_filter = type.lower().strip() if type else None
        if type_filter and type_filter not in ALL_TYPES:
            await respond_error(
                ctx,
                i18n.t(
                    uid,
                    "search.unknown_type",
                    type=type_filter,
                    valid=", ".join(ALL_TYPES),
                ),
                locale=loc,
            )
            return

        coach_lookup: dict[str, str] = {
            c.discord_id: f"{c.name} ({c.team_name})"
            for c in state.coaches
        }

        pool = list(state.pokemon_pool.values())
        filtered = []

        for p in pool:
            if status == "available" and (p.is_drafted or p.is_banned):
                continue
            if status == "drafted" and not p.is_drafted:
                continue
            if name and name.lower() not in p.name.lower():
                continue
            if type_filter and type_filter not in [t.lower() for t in p.types]:
                continue
            if min_pts is not None and p.points < min_pts:
                continue
            if max_pts is not None and p.points > max_pts:
                continue

            if p.is_drafted and p.drafted_by:
                p._drafter_display = coach_lookup.get(p.drafted_by, "Unknown")
            filtered.append(p)

        filtered.sort(key=lambda p: (p.is_drafted or p.is_banned, p.name.lower()))

        filter_parts = []
        if name:
            filter_parts.append(f"name={name}")
        if type_filter:
            filter_parts.append(f"type={type_filter}")
        if min_pts is not None:
            filter_parts.append(f"pts≥{min_pts}")
        if max_pts is not None:
            filter_parts.append(f"pts≤{max_pts}")
        if status != "all":
            filter_parts.append(status)
        filter_str = (
            " · ".join(filter_parts)
            if filter_parts
            else i18n.t_locale(loc, "search.all")
        )
        title = i18n.t_locale(
            loc, "search.title", division=division_name, filter=filter_str
        )

        view  = PoolBrowserView(filtered, title, division_name, locale=loc)
        embed = view.build_embed()
        await ctx.respond(embed=embed, view=view, ephemeral=True)
