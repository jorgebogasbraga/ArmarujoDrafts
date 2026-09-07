"""
LanguageCog — per-user locale preference.

Commands:
  /language [locale]  — Show or set your preferred bot language
  /timezone [tz]      — Optional override for auto-detected timezone
"""

import logging

import discord
from discord import option
from discord.ext import commands

from utils.i18n import i18n
from utils.response_embeds import error_embed, info_embed, respond_embed, respond_error, respond_success, success_embed
from utils.timezone_helper import (
    format_timezone_source,
    get_user_timezone_preference,
    resolve_invoker_timezone,
    set_user_timezone_preference,
)

logger = logging.getLogger(__name__)


class LanguageCog(commands.Cog):
    def __init__(self, bot: discord.Bot) -> None:
        self.bot = bot

    @discord.slash_command(
        name="language",
        description="Set your preferred bot language / Define o teu idioma / Elige tu idioma",
    )
    @option(
        "locale",
        description="Language code (en, pt, es)",
        required=False,
        choices=[discord.OptionChoice(name=n, value=c) for c, n in [
            ("en", "English"),
            ("pt", "Português"),
            ("es", "Español"),
        ]],
    )
    async def language(
        self,
        ctx: discord.ApplicationContext,
        locale: str = None,
    ) -> None:
        user_id = str(ctx.author.id)
        loc = i18n.get_user_locale(user_id)

        if locale is None:
            current = i18n.get_user_locale(user_id)
            names = i18n.locale_display_names()
            await respond_embed(
                ctx,
                info_embed(
                    i18n.t(user_id, "language.current",
                           locale=current,
                           locale_name=names.get(current, current)),
                    locale=loc,
                ),
                ephemeral=True,
            )
            return

        success, message = i18n.set_user_locale(user_id, locale)
        embed = success_embed(message, locale=loc) if success else error_embed(message, locale=loc)
        await respond_embed(ctx, embed, ephemeral=True)

    @discord.slash_command(
        name="timezone",
        description="[Optional] Override auto-detected timezone for scheduling",
    )
    @option(
        "timezone",
        description='Leave empty to see auto-detection; set e.g. Europe/Lisbon to override',
        required=False,
    )
    async def timezone(
        self,
        ctx: discord.ApplicationContext,
        timezone: str = None,
    ) -> None:
        user_id = str(ctx.author.id)
        loc = i18n.get_user_locale(user_id)

        if timezone is None:
            pref = get_user_timezone_preference(user_id)
            detected = resolve_invoker_timezone(
                user_id,
                discord_locale=ctx.locale,
                guild_locale=ctx.guild_locale,
                bot_language=loc,
            )
            if pref:
                await respond_embed(
                    ctx,
                    info_embed(i18n.t(user_id, "timezone.current_saved", timezone=pref), locale=loc),
                    ephemeral=True,
                )
            else:
                source = format_timezone_source(detected, loc)
                await respond_embed(
                    ctx,
                    info_embed(
                        i18n.t(
                            user_id,
                            "timezone.current_detected",
                            timezone=detected.name,
                            source=source,
                        ),
                        locale=loc,
                    ),
                    ephemeral=True,
                )
            return

        ok, result = set_user_timezone_preference(user_id, timezone)
        if not ok:
            await respond_error(
                ctx, i18n.t(user_id, "timezone.invalid", timezone=timezone), locale=loc
            )
            return
        await respond_success(
            ctx, i18n.t(user_id, "timezone.updated", timezone=timezone), locale=loc
        )
