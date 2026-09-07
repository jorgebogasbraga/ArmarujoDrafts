"""Tests for channel lock snapshot helpers."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from services.channel_lock_service import (
    ChannelLockService,
    overwrite_from_snapshot,
    serialize_overwrite,
)


def test_serialize_empty_overwrite():
    ow = discord.PermissionOverwrite()
    data = serialize_overwrite(ow)
    assert data["is_empty"] is True
    assert overwrite_from_snapshot(data) is None


def test_serialize_roundtrip():
    ow = discord.PermissionOverwrite(send_messages=False, read_messages=True)
    data = serialize_overwrite(ow)
    assert data["is_empty"] is False
    restored = overwrite_from_snapshot(data)
    assert restored is not None
    allow, deny = restored.pair()
    assert allow.read_messages is True
    assert allow.send_messages is False


def test_restore_deletes_when_empty():
    assert overwrite_from_snapshot({"allow": 0, "deny": 0, "is_empty": True}) is None


def test_resolve_target_finds_the_bot_member():
    bot_member = SimpleNamespace(id=99, name="ArmarujoDrafts")
    guild = SimpleNamespace(
        id=1,
        me=bot_member,
        get_role=lambda _id: None,
        get_member=lambda _id: bot_member if _id == 99 else None,
    )
    service = ChannelLockService()
    assert service._resolve_target(guild, 99) is bot_member


@pytest.mark.asyncio
async def test_lock_keeps_the_bot_able_to_talk(monkeypatch):
    monkeypatch.setattr(
        "services.channel_lock_service.Config.LOCK_CHANNEL_ON_PAUSE", True
    )
    monkeypatch.setattr("services.channel_lock_service.Config.GUILD_ID", 1)
    monkeypatch.setattr("services.channel_lock_service.Config.ADMIN_ROLE_ID", 0)
    monkeypatch.setattr("services.channel_lock_service.Config.MOD_ROLE_ID", 0)
    monkeypatch.setattr(
        "services.channel_lock_service.Config.DRAFT_PARTICIPANT_ROLE_ID", 0
    )

    everyone = SimpleNamespace(id=1)
    bot_member = SimpleNamespace(id=99)
    channel = MagicMock()
    channel.overwrites_for.return_value = discord.PermissionOverwrite()
    channel.set_permissions = AsyncMock()

    guild = SimpleNamespace(
        id=1,
        default_role=everyone,
        me=bot_member,
        get_role=lambda _id: None,
        get_member=lambda _id: bot_member if _id == 99 else None,
    )

    bot = MagicMock()
    bot.get_guild.return_value = guild

    state = SimpleNamespace(
        channel_id=10,
        division_name="Champions",
        channel_lock_snapshot=None,
    )

    service = ChannelLockService()
    service.bind(bot)
    service._get_channel = AsyncMock(return_value=channel)

    assert await service.lock(state) is True
    allowed = [
        call.kwargs.get("send_messages")
        for call in channel.set_permissions.await_args_list
        if call.args and call.args[0] is bot_member
    ]
    assert allowed == [True]
    assert "99" in state.channel_lock_snapshot["targets"]


@pytest.mark.asyncio
async def test_ensure_bot_can_send_grants_an_overwrite():
    bot_member = SimpleNamespace(id=99)
    guild = SimpleNamespace(me=bot_member)
    channel = MagicMock()
    channel.guild = guild
    channel.set_permissions = AsyncMock()

    service = ChannelLockService()
    assert await service.ensure_bot_can_send(channel) is True
    channel.set_permissions.assert_awaited_once()
    assert channel.set_permissions.await_args.args[0] is bot_member
    assert channel.set_permissions.await_args.kwargs["send_messages"] is True


@pytest.mark.asyncio
async def test_release_without_snapshot_still_unmutes_the_bot():
    bot_member = SimpleNamespace(id=99)
    channel = MagicMock()
    channel.guild = SimpleNamespace(me=bot_member)
    channel.set_permissions = AsyncMock()

    service = ChannelLockService()
    service._get_channel = AsyncMock(return_value=channel)
    state = SimpleNamespace(channel_lock_snapshot=None, channel_id=10)

    assert await service.release(state) is True
    assert channel.set_permissions.await_args.kwargs["send_messages"] is True
