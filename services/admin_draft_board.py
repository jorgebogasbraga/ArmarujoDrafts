"""Admin channel draft board embed builder."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import discord

from constants.draft_constants import DraftStatus
from models.draft_event import DraftEvent, DraftEventType
from utils.i18n import i18n

if TYPE_CHECKING:
    from models.draft_state import DraftState

EVENTS_PER_PAGE = 12

_STATUS_COLOURS = {
    DraftStatus.PENDING: 0x5865F2,
    DraftStatus.ACTIVE: 0x57F287,
    DraftStatus.PAUSED: 0xFEE75C,
    DraftStatus.WAITING_REPLACE: 0xED4245,
    DraftStatus.REPLACEMENT_PENDING: 0xFEE75C,
    DraftStatus.COMPLETED: 0x57F287,
}


def _L(key: str, **kwargs) -> str:
    return i18n.t_locale(i18n.default_locale, key, **kwargs)


def backfill_events_from_history(state: DraftState) -> None:
    """Migrate older saves that only have pick_history."""
    if state.draft_events:
        return
    for record in state.pick_history:
        state.draft_events.append(DraftEvent.from_pick_record(record))


def _format_event_line(event: DraftEvent) -> str:
    ts = int(event.timestamp) if event.timestamp else 0
    time_part = f"<t:{ts}:t>" if ts else ""

    if event.event_type == DraftEventType.PICK:
        tags = []
        if event.is_makeup:
            tags.append(_L("admin.board.tag_makeup"))
        if event.is_bank:
            tags.append(_L("admin.board.tag_bank"))
        tag_str = f" {' '.join(tags)}" if tags else ""
        return _L(
            "admin.board.line_pick",
            number=event.pick_number,
            coach=event.coach_name,
            pokemon=event.detail,
            points=event.points,
            tags=tag_str,
            time=time_part,
        )

    if event.event_type == DraftEventType.SKIP:
        return _L(
            "admin.board.line_skip",
            coach=event.coach_name,
            count=event.skip_count,
            time=time_part,
        )

    if event.event_type == DraftEventType.FORCE_SKIP:
        return _L(
            "admin.board.line_force_skip",
            coach=event.coach_name,
            time=time_part,
        )

    if event.event_type == DraftEventType.PAUSE:
        return _L(
            "admin.board.line_pause",
            detail=event.detail or _L("admin.board.no_reason"),
            time=time_part,
        )

    if event.event_type == DraftEventType.RESUME:
        return _L(
            "admin.board.line_resume",
            coach=event.coach_name,
            time=time_part,
        )

    if event.event_type == DraftEventType.STARTED:
        return _L(
            "admin.board.line_started",
            coach=event.coach_name,
            time=time_part,
        )

    if event.event_type == DraftEventType.INITIALIZED:
        return _L(
            "admin.board.line_initialized",
            coaches=event.pick_number,
            pool=event.points,
            time=time_part,
        )

    if event.event_type == DraftEventType.REPLACEMENT_NEEDED:
        return _L(
            "admin.board.line_replacement_needed",
            coach=event.coach_name,
            time=time_part,
        )

    if event.event_type == DraftEventType.REPLACEMENT:
        return _L(
            "admin.board.line_replacement",
            new_coach=event.coach_name,
            old_coach=event.secondary,
            time=time_part,
        )

    if event.event_type == DraftEventType.COMPLETED:
        return _L("admin.board.line_completed", time=time_part)

    if event.event_type == DraftEventType.RESET:
        return _L("admin.board.line_reset", time=time_part)

    if event.event_type == DraftEventType.UNDO:
        return _L(
            "admin.board.line_undo",
            pick=event.pick_number,
            coach=event.coach_name,
            pokemon=event.detail,
            time=time_part,
        )

    if event.event_type == DraftEventType.GOTO:
        return _L(
            "admin.board.line_goto",
            pick=event.pick_number,
            detail=event.detail,
            time=time_part,
        )

    if event.event_type == DraftEventType.EDIT:
        return _L(
            "admin.board.line_edit",
            pick=event.pick_number,
            coach=event.coach_name,
            detail=event.detail,
            time=time_part,
        )

    if event.event_type == DraftEventType.COACH_VOTE_PAUSE:
        return _L(
            "admin.board.line_coach_vote_pause",
            detail=event.detail or _L("admin.board.no_reason"),
            time=time_part,
        )

    return event.detail or event.event_type.value


def build_admin_draft_board_embed(state: DraftState, page: int) -> discord.Embed:
    backfill_events_from_history(state)

    events = list(reversed(state.draft_events))
    total_pages = max(1, math.ceil(len(events) / EVENTS_PER_PAGE))
    page = max(0, min(page, total_pages - 1))

    start = page * EVENTS_PER_PAGE
    page_events = events[start : start + EVENTS_PER_PAGE]

    status_key = f"admin.board.status.{state.status.value}"
    status_label = _L(status_key)

    current = state.current_coach.name if state.current_coach else "—"
    colour = _STATUS_COLOURS.get(state.status, 0x5865F2)

    embed = discord.Embed(
        title=_L("admin.board.title", division=state.division_name),
        colour=colour,
    )
    embed.add_field(
        name=_L("admin.board.field_status"),
        value=_L(
            "admin.board.summary",
            status=status_label,
            current=state.global_pick_counter,
            total=state.total_picks_expected,
            coach=current,
            round=state.current_round,
        ),
        inline=False,
    )

    if page_events:
        body = "\n".join(_format_event_line(ev) for ev in page_events)
    else:
        body = _L("admin.board.empty")

    embed.add_field(
        name=_L("admin.board.field_feed"),
        value=body,
        inline=False,
    )
    embed.set_footer(
        text=_L(
            "admin.board.footer",
            page=page + 1,
            total=total_pages,
            hint=_L("admin.board.footer_hint"),
        )
    )
    return embed


def board_total_pages(state: DraftState) -> int:
    backfill_events_from_history(state)
    return max(1, math.ceil(len(state.draft_events) / EVENTS_PER_PAGE))
