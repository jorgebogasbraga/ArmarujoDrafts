"""Embed builders for the /replace wizard."""

from __future__ import annotations

import discord

from constants.embed_colours import EMBED_WARNING
from models.coach import Coach
from models.draft_state import DraftState
from utils.i18n import i18n


def _L(locale: str, key: str, **kwargs) -> str:
    return i18n.t_locale(locale, key, **kwargs)


def build_replace_wizard_embed(
    state: DraftState,
    old_coach: Coach,
    *,
    locale: str,
    invoker_name: str,
) -> discord.Embed:
    embed = discord.Embed(
        title=_L(locale, "replace.wizard.title"),
        description=_L(
            locale,
            "replace.wizard.desc",
            division=state.division_name,
            old=old_coach.name,
            team=old_coach.team_name,
        ),
        colour=EMBED_WARNING,
    )
    embed.add_field(
        name=_L(locale, "replace.wizard.field.you"),
        value=invoker_name,
        inline=True,
    )
    embed.add_field(
        name=_L(locale, "replace.wizard.field.division"),
        value=state.division_name,
        inline=True,
    )
    embed.add_field(
        name=_L(locale, "replace.wizard.field.round"),
        value=str(state.current_round),
        inline=True,
    )

    if old_coach.team:
        lines = [
            f"• **{p.name}** ({p.points} pts)"
            for p in old_coach.team[:8]
        ]
        if len(old_coach.team) > 8:
            lines.append(f"… +{len(old_coach.team) - 8}")
        embed.add_field(
            name=_L(
                locale,
                "replace.wizard.field.inherited_team",
                current=len(old_coach.team),
                max=state.team_size,
            ),
            value="\n".join(lines),
            inline=False,
        )

    makeups = sum(1 for cid, _ in state.makeup_queue if cid == old_coach.discord_id)
    embed.add_field(
        name=_L(locale, "replace.wizard.field.points_remaining"),
        value=str(old_coach.remaining_points),
        inline=True,
    )
    embed.add_field(
        name=_L(locale, "replace.wizard.field.makeups"),
        value=str(makeups),
        inline=True,
    )
    embed.set_footer(text=_L(locale, "replace.wizard.footer"))
    if old_coach.team_logo_url:
        embed.set_thumbnail(url=old_coach.team_logo_url)
    return embed
