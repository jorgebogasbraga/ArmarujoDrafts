"""Standardised ephemeral (and simple public) response embeds."""

from __future__ import annotations

from typing import Optional

import discord

from constants.embed_colours import (
    EMBED_ERROR,
    EMBED_INFO,
    EMBED_NEUTRAL,
    EMBED_SUCCESS,
    EMBED_WARNING,
)
from utils.i18n import i18n


def _L(locale: str, key: str, **kwargs) -> str:
    return i18n.t_locale(locale, key, **kwargs)


def build_response_embed(
    kind: str,
    description: str,
    *,
    title: Optional[str] = None,
    locale: str | None = None,
    fields: list[tuple[str, str, bool]] | None = None,
) -> discord.Embed:
    colours = {
        "error": EMBED_ERROR,
        "warning": EMBED_WARNING,
        "success": EMBED_SUCCESS,
        "info": EMBED_INFO,
        "neutral": EMBED_NEUTRAL,
    }
    loc = locale or i18n.default_locale
    if title is None:
        title = _L(loc, f"response.{kind}.title")
    embed = discord.Embed(
        title=title,
        description=description,
        colour=colours.get(kind, EMBED_NEUTRAL),
    )
    if fields:
        for name, value, inline in fields:
            embed.add_field(name=name, value=value, inline=inline)
    return embed


def error_embed(description: str, *, title: str | None = None, locale: str | None = None) -> discord.Embed:
    return build_response_embed("error", description, title=title, locale=locale)


def warning_embed(description: str, *, title: str | None = None, locale: str | None = None) -> discord.Embed:
    return build_response_embed("warning", description, title=title, locale=locale)


def success_embed(description: str, *, title: str | None = None, locale: str | None = None) -> discord.Embed:
    return build_response_embed("success", description, title=title, locale=locale)


def info_embed(description: str, *, title: str | None = None, locale: str | None = None) -> discord.Embed:
    return build_response_embed("info", description, title=title, locale=locale)


async def dismiss_thinking(ctx: discord.ApplicationContext) -> None:
    """
    Clear the deferred 'thinking…' placeholder after a public reply.

    `/pick` defers so Discord does not time out while the pick is applied, then
    posts the announcement in the channel. Without this, Discord leaves the
    private thinking message up until the user dismisses it.
    """
    if not ctx.response.is_done():
        return
    delete = getattr(ctx, "delete", None)
    if not callable(delete):
        return
    try:
        await delete()
    except (discord.HTTPException, discord.NotFound):
        return


async def respond_embed(
    ctx: discord.ApplicationContext,
    embed: discord.Embed,
    *,
    ephemeral: bool = True,
    view: discord.ui.View | None = None,
) -> None:
    kwargs: dict = {"embed": embed, "ephemeral": ephemeral}
    if view:
        kwargs["view"] = view
    try:
        if ctx.response.is_done():
            await ctx.followup.send(**kwargs)
        else:
            await ctx.respond(**kwargs)
    except discord.NotFound:
        try:
            await ctx.followup.send(**kwargs)
        except discord.HTTPException:
            return
    except discord.HTTPException:
        return


async def respond_error(
    ctx: discord.ApplicationContext,
    description: str,
    *,
    locale: str | None = None,
    title: str | None = None,
) -> None:
    loc = locale or i18n.get_user_locale(ctx.author.id)
    await respond_embed(ctx, error_embed(description, title=title, locale=loc), ephemeral=True)


async def respond_success(
    ctx: discord.ApplicationContext,
    description: str,
    *,
    locale: str | None = None,
    title: str | None = None,
    ephemeral: bool = True,
) -> None:
    loc = locale or i18n.get_user_locale(ctx.author.id)
    await respond_embed(
        ctx, success_embed(description, title=title, locale=loc), ephemeral=ephemeral
    )


async def respond_warning(
    ctx: discord.ApplicationContext,
    description: str,
    *,
    locale: str | None = None,
    title: str | None = None,
) -> None:
    loc = locale or i18n.get_user_locale(ctx.author.id)
    await respond_embed(ctx, warning_embed(description, title=title, locale=loc), ephemeral=True)


async def respond_result(
    ctx: discord.ApplicationContext,
    success: bool,
    description: str,
    *,
    locale: str | None = None,
    ephemeral: bool = True,
) -> None:
    if success:
        await respond_success(ctx, description, locale=locale, ephemeral=ephemeral)
    else:
        await respond_error(ctx, description, locale=locale)


async def respond_info(
    ctx: discord.ApplicationContext,
    description: str,
    *,
    locale: str | None = None,
    title: str | None = None,
    ephemeral: bool = True,
) -> None:
    loc = locale or i18n.get_user_locale(ctx.author.id)
    await respond_embed(
        ctx, info_embed(description, title=title, locale=loc), ephemeral=ephemeral
    )


def list_embed(
    lines: list[str],
    *,
    locale: str | None = None,
    title: str | None = None,
) -> discord.Embed:
    loc = locale or i18n.default_locale
    body = "\n".join(lines) if lines else "—"
    return info_embed(body, title=title, locale=loc)


async def interaction_result(
    interaction: discord.Interaction,
    success: bool,
    description: str,
    *,
    locale: str | None = None,
    edit: bool = False,
    view: discord.ui.View | None = None,
) -> None:
    loc = locale or i18n.get_user_locale(interaction.user.id)
    embed = success_embed(description, locale=loc) if success else error_embed(description, locale=loc)
    await interaction_embed(interaction, embed, ephemeral=True, edit=edit, view=view)


async def interaction_error(
    interaction: discord.Interaction,
    description: str,
    *,
    locale: str | None = None,
    edit: bool = False,
) -> None:
    loc = locale or i18n.get_user_locale(interaction.user.id)
    await interaction_embed(
        interaction, error_embed(description, locale=loc), ephemeral=True, edit=edit
    )


async def interaction_warning(
    interaction: discord.Interaction,
    description: str,
    *,
    locale: str | None = None,
    edit: bool = False,
    view: discord.ui.View | None = None,
) -> None:
    loc = locale or i18n.get_user_locale(interaction.user.id)
    await interaction_embed(
        interaction,
        warning_embed(description, locale=loc),
        ephemeral=True,
        edit=edit,
        view=view,
    )


async def interaction_success(
    interaction: discord.Interaction,
    description: str,
    *,
    locale: str | None = None,
    edit: bool = False,
) -> None:
    loc = locale or i18n.get_user_locale(interaction.user.id)
    await interaction_embed(
        interaction, success_embed(description, locale=loc), ephemeral=True, edit=edit
    )


async def interaction_info(
    interaction: discord.Interaction,
    description: str,
    *,
    locale: str | None = None,
    title: str | None = None,
    edit: bool = False,
    view: discord.ui.View | None = None,
) -> None:
    loc = locale or i18n.get_user_locale(interaction.user.id)
    await interaction_embed(
        interaction,
        info_embed(description, title=title, locale=loc),
        ephemeral=True,
        edit=edit,
        view=view,
    )


async def interaction_embed(
    interaction: discord.Interaction,
    embed: discord.Embed,
    *,
    ephemeral: bool = True,
    edit: bool = False,
    view: discord.ui.View | None = None,
) -> None:
    kwargs: dict = {"embed": embed, "ephemeral": ephemeral}
    if view is not None:
        kwargs["view"] = view
    if edit:
        await interaction.response.edit_message(**kwargs)
    elif interaction.response.is_done():
        await interaction.followup.send(**kwargs)
    else:
        await interaction.response.send_message(**kwargs)
