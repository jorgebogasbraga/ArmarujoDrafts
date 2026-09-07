"""Factories for public channel messages with language tab views."""

from __future__ import annotations

from typing import Callable, Optional

import discord

from models.coach import Coach
from models.draft_state import DraftState, PickRecord
from models.pokemon import Pokemon
from services.embed_service import EmbedService
from views.replacement_views import ReplacementGlobalView
from utils.i18n import i18n
from views.locale_embed_view import public_embed_message


def _default_loc() -> str:
    return i18n.default_locale


def pick_lead_in(record: PickRecord) -> str:
    """Plain channel line above makeup/bank pick embeds."""
    loc = _default_loc()
    if record.is_makeup:
        return i18n.t_locale(loc, "embed.pick.lead_makeup")
    if record.is_bank:
        return i18n.t_locale(loc, "embed.pick.lead_bank")
    return ""


def pick_send_kwargs(
    embed: discord.Embed,
    view: discord.ui.View,
    record: PickRecord | None,
) -> dict:
    payload: dict = {"embed": embed, "view": view}
    if record:
        lead = pick_lead_in(record)
        if lead:
            payload["content"] = lead
    return payload


def pick_announcement(
    state: DraftState,
    coach: Coach,
    pokemon: Pokemon,
    record: PickRecord,
    next_coach: Optional[Coach],
) -> tuple[discord.Embed, discord.ui.View]:
    def build(loc: str) -> discord.Embed:
        return EmbedService.pick_card(
            state, coach, pokemon, record, next_coach, locale=loc
        )
    return public_embed_message(build, _default_loc())


def skip_announcement(
    coach: Coach,
    skips_used: int,
    next_deadline: int,
    deadline_coach: Coach | None = None,
) -> tuple[discord.Embed, discord.ui.View]:
    def build(loc: str) -> discord.Embed:
        return EmbedService.skip_warning(
            coach,
            skips_used,
            next_deadline,
            deadline_coach=deadline_coach or coach,
            locale=loc,
        )
    return public_embed_message(build, _default_loc())


def makeup_reminder_message(
    state: DraftState,
) -> tuple[Optional[discord.Embed], Optional[discord.ui.View]]:
    if not state.makeup_queue:
        return None, None

    def build(loc: str) -> discord.Embed:
        return EmbedService.makeup_reminder(state, locale=loc)

    embed, view = public_embed_message(build, _default_loc())
    return embed, view


def draft_start_message(
    state: DraftState,
) -> tuple[discord.Embed, discord.ui.View]:
    def build(loc: str) -> discord.Embed:
        return EmbedService.draft_start_announcement(state, locale=loc)
    return public_embed_message(build, _default_loc())


def bank_snipe_message(
    coach: Coach,
    division_name: str,
    deadline_ts: int,
    resume_ts: Optional[int] = None,
) -> tuple[discord.Embed, discord.ui.View]:
    def build(loc: str) -> discord.Embed:
        return EmbedService.bank_snipe_public(
            coach, division_name, deadline_ts, locale=loc, resume_ts=resume_ts
        )
    return public_embed_message(build, _default_loc())


def quiet_hours_start_message(
    state: DraftState,
    coach: Optional[Coach],
    resume_ts: int,
    elapsed_seconds: float,
) -> tuple[discord.Embed, discord.ui.View]:
    def build(loc: str) -> discord.Embed:
        return EmbedService.quiet_hours_start(
            state, coach, resume_ts, elapsed_seconds, locale=loc
        )
    return public_embed_message(build, _default_loc())


def quiet_hours_end_message(
    state: DraftState,
    coach: Optional[Coach],
    deadline_ts: Optional[int],
) -> tuple[discord.Embed, discord.ui.View]:
    def build(loc: str) -> discord.Embed:
        return EmbedService.quiet_hours_end(state, coach, deadline_ts, locale=loc)
    return public_embed_message(build, _default_loc())


def replacement_division_message(
    coach: Coach,
    state: DraftState,
) -> tuple[discord.Embed, discord.ui.View]:
    def build(loc: str) -> discord.Embed:
        return EmbedService.replacement_announcement_division(coach, state, locale=loc)
    return public_embed_message(build, _default_loc())


def replacement_global_message(
    coach: Coach,
    state: DraftState,
) -> tuple[discord.Embed, discord.ui.View]:
    def build(loc: str) -> discord.Embed:
        embed = EmbedService.replacement_announcement_global(coach, state, locale=loc)
        channel_ref = f"<#{state.channel_id}>" if state.channel_id else state.division_name
        embed.description = EmbedService._L(
            loc,
            "embed.replace.global.desc_link",
            division=state.division_name,
            channel=channel_ref,
        )
        return embed

    embed, tab_view = public_embed_message(build, _default_loc())
    link_view = ReplacementGlobalView(state.channel_id, locale=_default_loc())
    if tab_view and link_view.children:
        for button in link_view.children:
            tab_view.add_item(button)
        return embed, tab_view
    return embed, link_view


def replacement_welcome_message(
    new_coach: Coach,
    old_coach_name: str,
    state: DraftState,
) -> tuple[discord.Embed, discord.ui.View]:
    def build(loc: str) -> discord.Embed:
        return EmbedService.replacement_welcome(new_coach, old_coach_name, state, locale=loc)
    return public_embed_message(build, _default_loc())


def default_public_text(key: str, **kwargs) -> str:
    """Single-locale plain text for public channel (default locale only)."""
    return i18n.t_locale(_default_loc(), key, **kwargs)


def channel_ping(coach: Coach) -> str:
    """Single-locale ping line for public channel (not an embed)."""
    coach.sync_pick_deadline()
    if not coach.pick_deadline:
        return default_public_text("pick.next_ping_no_deadline", mention=coach.mention())
    ts = int(coach.pick_deadline)
    return default_public_text(
        "pick.next_ping",
        mention=coach.mention(),
        relative=ts,
        absolute=ts,
    )


def draft_resume_message(
    state: DraftState,
    coach: Coach,
    downtime_seconds: int,
) -> tuple[discord.Embed, discord.ui.View]:
    def build(loc: str) -> discord.Embed:
        return EmbedService.draft_resume_announcement(
            state, coach, downtime_seconds, locale=loc
        )
    return public_embed_message(build, _default_loc())


def draft_pause_message(
    state: DraftState,
    reason: str,
) -> tuple[discord.Embed, discord.ui.View]:
    def build(loc: str) -> discord.Embed:
        return EmbedService.draft_pause_announcement(state, reason, locale=loc)
    return public_embed_message(build, _default_loc())


def draft_manual_resume_message(
    state: DraftState,
) -> tuple[discord.Embed, discord.ui.View]:
    def build(loc: str) -> discord.Embed:
        return EmbedService.draft_manual_resume_announcement(state, locale=loc)
    return public_embed_message(build, _default_loc())


def edit_pick_message(
    state: DraftState,
    *,
    pick_number: int,
    edited_coach: Coach,
    old_name: str,
    new_name: str,
) -> tuple[discord.Embed, discord.ui.View]:
    def build(loc: str) -> discord.Embed:
        return EmbedService.edit_pick_announcement(
            state,
            pick_number=pick_number,
            edited_coach=edited_coach,
            old_name=old_name,
            new_name=new_name,
            locale=loc,
        )
    return public_embed_message(build, _default_loc())
