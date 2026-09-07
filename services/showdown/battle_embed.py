"""Discord embed builder for Showdown battle summaries."""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

import discord

from services.showdown.battle_summary import BattleSummary, MonSummary, PlayerSummary
from utils.i18n import i18n

if TYPE_CHECKING:
    from services.pokemon_service import PokemonService

WIN_GOLD = 0xF1C40F
LOSE_MUTED = 0x5865F2
TIE_COLOUR = 0x95A5A6


def _L(loc: str, key: str, **kwargs) -> str:
    return i18n.t_locale(loc, key, **kwargs)


def _mon_line(loc: str, mon: MonSummary, *, highlight: bool = False) -> str:
    if mon.fainted:
        status = _L(loc, "embed.battle.status_fainted")
    else:
        status = _L(loc, "embed.battle.status_alive")
    kills = (
        _L(loc, "embed.battle.kills_badge", count=mon.kills)
        if mon.kills
        else ""
    )
    prefix = "▸ " if highlight else "   "
    return f"{prefix}{status} **{mon.display_name}**{kills}"


def _team_block(loc: str, player: PlayerSummary, *, won: bool) -> str:
    mvp = player.mvp()
    lines = [
        _L(
            loc,
            "embed.battle.team_header",
            name=player.name,
            kills=player.kills,
            deaths=player.deaths,
        )
    ]
    for mon in player.team:
        lines.append(
            _mon_line(loc, mon, highlight=mvp is not None and mon is mvp)
        )
    if not player.team:
        lines.append(_L(loc, "embed.battle.team_empty"))
    if won and mvp and mvp.kills:
        lines.append(
            _L(loc, "embed.battle.mvp_line", pokemon=mvp.display_name, kills=mvp.kills)
        )
    return "\n".join(lines)


async def build_battle_summary_message(
    summary: BattleSummary,
    pokemon_service: PokemonService,
    locale: str | None = None,
) -> tuple[list[discord.Embed], discord.ui.View]:
    loc = locale or i18n.default_locale
    sprites = await _resolve_sprites(summary, pokemon_service)

    wp = summary.winner_player()
    p1, p2 = summary.player1, summary.player2
    p1_won = wp is p1
    p2_won = wp is p2

    if summary.is_tie:
        header_colour = TIE_COLOUR
        title = _L(loc, "embed.battle.title_tie")
    elif wp:
        header_colour = WIN_GOLD
        title = _L(loc, "embed.battle.title_win", winner=summary.winner)
    else:
        header_colour = WIN_GOLD
        title = _L(loc, "embed.battle.title_win", winner=summary.winner)

    score_line = _L(
        loc,
        "embed.battle.score",
        p1=p1.name,
        k1=p1.kills,
        k2=p2.kills,
        p2=p2.name,
    )
    meta = _L(
        loc,
        "embed.battle.meta",
        format=summary.format,
        turns=summary.turns,
    )

    header = discord.Embed(
        title=title,
        description=f"{score_line}\n{meta}",
        colour=header_colour,
        url=summary.replay_url or None,
    )

    mvp_all = wp.mvp() if wp else (p1.mvp() or p2.mvp())
    if mvp_all and mvp_all.species in sprites and sprites[mvp_all.species]:
        header.set_thumbnail(url=sprites[mvp_all.species])
        header.set_image(url=sprites[mvp_all.species])

    header.add_field(
        name=p1.name + (" 🏆" if p1_won else ""),
        value=_team_block(loc, p1, won=p1_won),
        inline=True,
    )
    header.add_field(
        name="⚔️",
        value=_L(loc, "embed.battle.vs"),
        inline=True,
    )
    header.add_field(
        name=p2.name + (" 🏆" if p2_won else ""),
        value=_team_block(loc, p2, won=p2_won),
        inline=True,
    )

    if summary.replay_url:
        header.set_footer(text=_L(loc, "embed.battle.footer_replay"))
    else:
        header.set_footer(text=_L(loc, "embed.battle.footer"))

    view = discord.ui.View(timeout=None)
    if summary.replay_url:
        view.add_item(
            discord.ui.Button(
                label=_L(loc, "embed.battle.button_replay"),
                url=summary.replay_url,
                emoji="🔗",
            )
        )

    return [header], view


async def _resolve_sprites(
    summary: BattleSummary,
    pokemon_service: PokemonService,
) -> dict[str, str]:
    species_set: set[str] = set()
    for player in (summary.player1, summary.player2):
        for mon in player.team:
            species_set.add(mon.species)

    sprites: dict[str, str] = {}
    for species in species_set:
        url = await pokemon_service.resolve_sprite_url(species)
        if url:
            sprites[species] = url
    return sprites
