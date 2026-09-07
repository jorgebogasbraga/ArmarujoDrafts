"""
Minimal Discord ApplicationContext stand-in for draft dry-runs.

Slash commands expect defer/respond/followup and response.is_done().
Without these, /pick, /makeup_pick, /replace, and admin acks raise at runtime.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from config import Config

if TYPE_CHECKING:
    pass


class MockResponse:
    def __init__(self) -> None:
        self._done = False

    def is_done(self) -> bool:
        return self._done


class MockFollowup:
    def __init__(self, channel: discord.TextChannel, *, log_ephemeral: bool = False) -> None:
        self._channel = channel
        self._log_ephemeral = log_ephemeral

    async def send(
        self,
        content: str | None = None,
        *,
        embed: discord.Embed | None = None,
        view: discord.ui.View | None = None,
        ephemeral: bool = False,
        delete_after: float | None = None,
        **kwargs,
    ) -> None:
        if content == "✅" and ephemeral and delete_after is not None:
            return
        if ephemeral and not self._log_ephemeral:
            return
        payload: dict = {}
        if content:
            payload["content"] = content
        if embed:
            payload["embed"] = embed
        if view:
            payload["view"] = view
        if payload:
            await self._channel.send(**payload)


class MockAuthor:
    def __init__(
        self,
        discord_id: str,
        display_name: str,
        guild: discord.Guild,
        *,
        admin: bool = False,
    ) -> None:
        self.id = int(discord_id) if discord_id.isdigit() else 0
        self.display_name = display_name
        self._guild = guild
        self.roles: list = []
        if admin:
            admin_role = guild.get_role(Config.ADMIN_ROLE_ID)
            if admin_role:
                self.roles = [admin_role]

    @property
    def guild(self) -> discord.Guild:
        return self._guild


class MockContext:
    """Drop-in for discord.ApplicationContext during automated dry-runs."""

    def __init__(
        self,
        channel: discord.TextChannel,
        guild: discord.Guild,
        discord_id: str,
        display_name: str = "SimBot",
        *,
        admin: bool = False,
        log_ephemeral: bool = False,
    ) -> None:
        self._channel = channel
        self.channel = channel
        self.channel_id = channel.id
        self.guild = guild
        self.author = MockAuthor(discord_id, display_name, guild, admin=admin)
        self.response = MockResponse()
        self.followup = MockFollowup(channel, log_ephemeral=log_ephemeral)

    async def respond(
        self,
        content: str | None = None,
        *,
        embed: discord.Embed | None = None,
        view: discord.ui.View | None = None,
        ephemeral: bool = False,
        delete_after: float | None = None,
        **kwargs,
    ) -> None:
        self.response._done = True
        if content == "✅" and ephemeral and delete_after is not None:
            return
        if ephemeral and not self.followup._log_ephemeral:
            return
        payload: dict = {}
        if content:
            payload["content"] = content
        if embed:
            payload["embed"] = embed
        if view:
            payload["view"] = view
        if payload:
            await self._channel.send(**payload)

    async def defer(self, *, ephemeral: bool = False) -> None:
        self.response._done = True

    async def delete(self, *, delay: float | None = None) -> None:
        # Real Discord uses this to dismiss the deferred "thinking…" placeholder.
        return
