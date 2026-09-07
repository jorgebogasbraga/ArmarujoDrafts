"""User-facing slash command error formatting and admin diagnostics."""

from __future__ import annotations

import logging
import secrets
import traceback
from typing import Optional

import discord
from aiohttp import ClientError

from config import Config
from utils.i18n import i18n

logger = logging.getLogger(__name__)


class UserFacingError(Exception):
    """Raise from command handlers when the user should see a specific message."""

    def __init__(self, message_key: str, *, ephemeral: bool = True, **kwargs) -> None:
        super().__init__(message_key)
        self.message_key = message_key
        self.message_kwargs = kwargs
        self.ephemeral = ephemeral


def make_error_ref() -> str:
    return secrets.token_hex(3).upper()


def _unwrap(error: BaseException) -> BaseException:
    if isinstance(error, discord.ApplicationCommandInvokeError) and error.original:
        return error.original
    return error


def format_command_error(
    user_id: str | int,
    command_name: str,
    error_ref: str,
    error: BaseException,
) -> str:
    uid = str(user_id)
    original = _unwrap(error)

    if isinstance(original, UserFacingError):
        return i18n.t(uid, original.message_key, **original.message_kwargs)

    if isinstance(original, ValueError):
        return i18n.t(
            uid,
            "errors.invalid_input",
            command=command_name,
            detail=str(original),
            ref=error_ref,
        )

    if isinstance(original, (ClientError, TimeoutError, OSError)):
        return i18n.t(
            uid,
            "errors.network",
            command=command_name,
            ref=error_ref,
        )

    if isinstance(original, discord.HTTPException):
        return i18n.t(
            uid,
            "errors.discord_api",
            command=command_name,
            ref=error_ref,
        )

    if isinstance(original, discord.NotFound):
        return i18n.t(
            uid,
            "errors.not_found",
            command=command_name,
            ref=error_ref,
        )

    if isinstance(original, PermissionError):
        return i18n.t(
            uid,
            "errors.permission_denied",
            command=command_name,
            ref=error_ref,
        )

    return i18n.t(
        uid,
        "errors.command_failed",
        command=command_name,
        ref=error_ref,
        error_type=type(original).__name__,
    )


async def _send_user_message(
    ctx: discord.ApplicationContext,
    content: str,
    *,
    ephemeral: bool = True,
) -> None:
    from utils.response_embeds import error_embed

    embed = error_embed(content, locale=i18n.get_user_locale(ctx.author.id))
    try:
        if ctx.response.is_done():
            await ctx.followup.send(embed=embed, ephemeral=ephemeral)
        else:
            await ctx.respond(embed=embed, ephemeral=ephemeral)
    except discord.HTTPException:
        try:
            await ctx.followup.send(embed=embed, ephemeral=ephemeral)
        except discord.HTTPException:
            pass


async def _notify_admin_channel(
    bot: discord.Bot,
    *,
    error_ref: str,
    command_name: str,
    user: discord.User | discord.Member,
    error: BaseException,
) -> None:
    channel_id = Config.ADMIN_LOG_CHANNEL_ID
    if not channel_id:
        return
    channel = bot.get_channel(channel_id)
    if not channel:
        return

    original = _unwrap(error)
    tb = "".join(traceback.format_exception(type(original), original, original.__traceback__))
    field_limit = 1024
    wrapped_budget = field_limit - len("```\n\n```")
    if len(tb) > wrapped_budget:
        tb = tb[-wrapped_budget:]

    embed = discord.Embed(
        title=f"Command error [{error_ref}]",
        description=f"**/{command_name}** used by {user.mention} (`{user.id}`)",
        colour=0xED4245,
    )
    embed.add_field(name="Type", value=f"`{type(original).__name__}`", inline=True)
    embed.add_field(
        name="Message",
        value=(str(original) or "—")[:field_limit],
        inline=False,
    )
    embed.add_field(
        name="Traceback",
        value=f"```\n{tb}\n```"[:field_limit],
        inline=False,
    )

    try:
        await channel.send(embed=embed)
    except discord.HTTPException as exc:
        logger.warning("[Errors] Could not post to admin log channel: %s", exc)


async def handle_application_command_error(
    ctx: discord.ApplicationContext,
    error: discord.DiscordException,
    bot: Optional[discord.Bot] = None,
) -> None:
    if isinstance(error, discord.CheckFailure):
        return

    original = _unwrap(error)
    if isinstance(original, UserFacingError) and not original.ephemeral:
        # Handler may want a public response — still use standard path.
        pass

    command_name = ctx.command.name if ctx.command else "unknown"
    error_ref = make_error_ref()
    user_message = format_command_error(ctx.author.id, command_name, error_ref, error)

    logger.error(
        "[%s] Unhandled error in /%s (user=%s): %s",
        error_ref,
        command_name,
        ctx.author.id,
        original,
        exc_info=original,
    )

    ephemeral = True
    if isinstance(original, UserFacingError):
        ephemeral = original.ephemeral

    await _send_user_message(ctx, user_message, ephemeral=ephemeral)

    if bot and not isinstance(original, UserFacingError):
        await _notify_admin_channel(
            bot,
            error_ref=error_ref,
            command_name=command_name,
            user=ctx.author,
            error=error,
        )
