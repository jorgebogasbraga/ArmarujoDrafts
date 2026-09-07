"""Shared admin / moderator role checks."""

from __future__ import annotations

import discord

from config import Config


def member_is_staff(member: discord.Member) -> bool:
    """True if the member has the configured admin or moderator role."""
    role_ids = [rid for rid in (Config.ADMIN_ROLE_ID, Config.MOD_ROLE_ID) if rid]
    member_role_ids = {r.id for r in member.roles}
    return any(rid in member_role_ids for rid in role_ids)
