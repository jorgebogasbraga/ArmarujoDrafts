"""
Channel lock — restrict division draft channels to staff while paused.

On pause: snapshot existing permission overwrites, deny @everyone (and
draft-participant role) from sending messages, allow admin/mod roles.
On resume: restore saved overwrites exactly.
"""

from __future__ import annotations

import logging
from typing import Optional

import discord

from config import Config
from constants.draft_constants import DraftStatus
from models.draft_state import DraftState
from services.persistence_service import PersistenceService

logger = logging.getLogger(__name__)


def serialize_overwrite(ow: discord.PermissionOverwrite) -> dict:
    allow, deny = ow.pair()
    return {
        "allow": allow.value,
        "deny": deny.value,
        "is_empty": ow.is_empty(),
    }


def overwrite_from_snapshot(data: dict) -> discord.PermissionOverwrite | None:
    if data.get("is_empty"):
        return None
    return discord.PermissionOverwrite.from_pair(
        discord.Permissions(data["allow"]),
        discord.Permissions(data["deny"]),
    )


class ChannelLockService:
    def __init__(self, persistence: PersistenceService | None = None) -> None:
        self._bot: discord.Bot | None = None
        self._persistence = persistence

    def bind(self, bot: discord.Bot) -> None:
        self._bot = bot

    @staticmethod
    def _staff_role_ids() -> list[int]:
        ids: list[int] = []
        if Config.ADMIN_ROLE_ID:
            ids.append(Config.ADMIN_ROLE_ID)
        if Config.MOD_ROLE_ID:
            ids.append(Config.MOD_ROLE_ID)
        return ids

    async def _get_channel(self, state: DraftState) -> discord.abc.GuildChannel | None:
        if not self._bot:
            logger.warning("[ChannelLock] Bot not bound — cannot lock channel.")
            return None
        channel = self._bot.get_channel(state.channel_id)
        if channel is None:
            try:
                channel = await self._bot.fetch_channel(state.channel_id)
            except discord.HTTPException as e:
                logger.error(
                    "[ChannelLock] Could not fetch channel %s: %s",
                    state.channel_id,
                    e,
                )
                return None
        if not hasattr(channel, "set_permissions"):
            logger.error("[ChannelLock] Channel %s is not a guild channel.", state.channel_id)
            return None
        return channel

    def _resolve_target(self, guild: discord.Guild, target_id: int):
        if target_id == guild.id:
            return guild.default_role
        role = guild.get_role(target_id)
        if role:
            return role
        member = guild.get_member(target_id)
        if member:
            return member
        if self._bot and self._bot.user and self._bot.user.id == target_id:
            return guild.me
        return None

    async def ensure_bot_can_send(self, channel) -> bool:
        """
        Give the bot an explicit Send Messages overwrite.

        Needed when a previous pause locked @everyone and then the division
        was reset without unlocking — Discord keeps the overwrites, the
        snapshot is gone, and the bot can no longer talk in its own channel.
        """
        guild = getattr(channel, "guild", None)
        me = getattr(guild, "me", None) if guild else None
        if not me or not hasattr(channel, "set_permissions"):
            return False
        try:
            await channel.set_permissions(
                me,
                send_messages=True,
                reason="ArmaDraft: bot must keep Send Messages in the draft channel",
            )
            return True
        except discord.Forbidden:
            logger.error(
                "[ChannelLock] Cannot grant the bot Send Messages in channel %s",
                getattr(channel, "id", "?"),
            )
            return False
        except discord.HTTPException as e:
            logger.error(
                "[ChannelLock] Failed to grant the bot Send Messages in %s: %s",
                getattr(channel, "id", "?"),
                e,
            )
            return False

    async def lock(self, state: DraftState) -> bool:
        """Apply channel lock and persist snapshot on state."""
        if not Config.LOCK_CHANNEL_ON_PAUSE:
            return True
        if state.channel_lock_snapshot:
            return True

        guild = self._bot.get_guild(Config.GUILD_ID) if self._bot else None
        if not guild:
            logger.error("[ChannelLock] Guild %s not found.", Config.GUILD_ID)
            return False

        channel = await self._get_channel(state)
        if channel is None:
            return False

        snapshot: dict[str, dict] = {"targets": {}}

        try:
            everyone = guild.default_role
            snapshot["targets"][str(everyone.id)] = serialize_overwrite(
                channel.overwrites_for(everyone)
            )
            await channel.set_permissions(
                everyone,
                send_messages=False,
                reason=f"ArmaDraft: {state.division_name} draft paused",
            )

            if Config.DRAFT_PARTICIPANT_ROLE_ID:
                participant = guild.get_role(Config.DRAFT_PARTICIPANT_ROLE_ID)
                if participant:
                    snapshot["targets"][str(participant.id)] = serialize_overwrite(
                        channel.overwrites_for(participant)
                    )
                    await channel.set_permissions(
                        participant,
                        send_messages=False,
                        reason=f"ArmaDraft: {state.division_name} draft paused",
                    )

            for role_id in self._staff_role_ids():
                role = guild.get_role(role_id)
                if not role:
                    continue
                snapshot["targets"][str(role.id)] = serialize_overwrite(
                    channel.overwrites_for(role)
                )
                await channel.set_permissions(
                    role,
                    send_messages=True,
                    reason=f"ArmaDraft: {state.division_name} draft paused (staff)",
                )

            # @everyone just lost Send Messages. Without an explicit allow the
            # bot locks itself out of its own draft channel — announcements,
            # makeups and the system-test log all 403.
            me = guild.me
            if me:
                snapshot["targets"][str(me.id)] = serialize_overwrite(
                    channel.overwrites_for(me)
                )
                await channel.set_permissions(
                    me,
                    send_messages=True,
                    reason=f"ArmaDraft: {state.division_name} draft paused (bot)",
                )

            state.channel_lock_snapshot = snapshot
            if self._persistence:
                self._persistence.save(state)
            logger.info(
                "[ChannelLock] Locked channel %s for %s",
                state.channel_id,
                state.division_name,
            )
            return True
        except discord.Forbidden:
            logger.error(
                "[ChannelLock] Missing Manage Channels permission for channel %s",
                state.channel_id,
            )
            return False
        except discord.HTTPException as e:
            logger.error("[ChannelLock] Failed to lock channel %s: %s", state.channel_id, e)
            return False

    async def release(self, state: DraftState) -> bool:
        """Undo a pause lock, or at least give the bot its voice back."""
        if state.channel_lock_snapshot:
            return await self.unlock(state)
        channel = await self._get_channel(state)
        if channel is None:
            return False
        return await self.ensure_bot_can_send(channel)

    async def unlock(self, state: DraftState) -> bool:
        """Restore permission overwrites from snapshot."""
        snapshot = state.channel_lock_snapshot
        if not snapshot:
            return True

        guild = self._bot.get_guild(Config.GUILD_ID) if self._bot else None
        if not guild:
            logger.error("[ChannelLock] Guild %s not found.", Config.GUILD_ID)
            return False

        channel = await self._get_channel(state)
        if channel is None:
            return False

        try:
            for target_id_str, data in snapshot.get("targets", {}).items():
                target = self._resolve_target(guild, int(target_id_str))
                if target is None:
                    continue
                restored = overwrite_from_snapshot(data)
                await channel.set_permissions(
                    target,
                    overwrite=restored,
                    reason=f"ArmaDraft: {state.division_name} draft resumed",
                )

            state.channel_lock_snapshot = None
            if self._persistence:
                self._persistence.save(state)
            logger.info(
                "[ChannelLock] Unlocked channel %s for %s",
                state.channel_id,
                state.division_name,
            )
            return True
        except discord.Forbidden:
            logger.error(
                "[ChannelLock] Missing Manage Channels permission for channel %s",
                state.channel_id,
            )
            return False
        except discord.HTTPException as e:
            logger.error(
                "[ChannelLock] Failed to unlock channel %s: %s", state.channel_id, e
            )
            return False

    async def reconcile_on_startup(self, states: dict[str, DraftState]) -> None:
        """
        Fix inconsistent lock state after bot restart.

        - PAUSED without snapshot → apply lock (legacy saves).
        - ACTIVE with snapshot → unlock (resume did not finish).
        """
        if not Config.LOCK_CHANNEL_ON_PAUSE:
            for state in states.values():
                if state.channel_lock_snapshot:
                    await self.unlock(state)
            return

        for state in states.values():
            if state.status == DraftStatus.PAUSED:
                if not state.channel_lock_snapshot:
                    await self.lock(state)
            elif state.channel_lock_snapshot:
                logger.warning(
                    "[ChannelLock] Orphan lock on active division %s — restoring.",
                    state.division_name,
                )
                await self.unlock(state)
