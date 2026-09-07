"""
AdminLogService — one live draft board embed per division in the admin log channel.

Instead of spamming a new message on every pick/skip, events append to a paginated
feed (newest first) on a single message that is edited in place.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Awaitable, Callable, Optional

import discord

from config import Config
from models.draft_event import DraftEvent, DraftEventType
from services.admin_draft_board import build_admin_draft_board_embed
from views.admin_draft_board_view import AdminDraftBoardView

if TYPE_CHECKING:
    from models.draft_state import DraftState

logger = logging.getLogger(__name__)

GetStateFn = Callable[[str], Optional["DraftState"]]


SaveStateFn = Callable[["DraftState"], None]


class AdminLogService:
    def __init__(self) -> None:
        self._bot: Optional[discord.Bot] = None
        self._get_state: Optional[GetStateFn] = None
        self._save_state: Optional[SaveStateFn] = None
        self._views: dict[str, AdminDraftBoardView] = {}

    def bind(
        self,
        bot: discord.Bot,
        get_state: GetStateFn,
        save_state: Optional[SaveStateFn] = None,
    ) -> None:
        self._bot = bot
        self._get_state = get_state
        self._save_state = save_state

    @property
    def channel_id(self) -> int:
        return Config.ADMIN_LOG_CHANNEL_ID

    def is_enabled(self) -> bool:
        return bool(self.channel_id and self._bot and self._get_state)

    def _resolve_state(self, division: str) -> Optional["DraftState"]:
        if not self._get_state:
            return None
        return self._get_state(division)

    async def _append_event(self, state: "DraftState", event: DraftEvent) -> None:
        state.draft_events.append(event)
        await self.refresh_board(state, page=0)

    async def refresh_board(
        self,
        state: "DraftState",
        page: int = 0,
        *,
        interaction: Optional[discord.Interaction] = None,
    ) -> None:
        if not self.is_enabled():
            return

        channel = self._bot.get_channel(self.channel_id)
        if not channel:
            logger.warning("[AdminLog] Admin channel %s not found", self.channel_id)
            return

        view = self._views.get(state.division_name)
        if not view:
            view = AdminDraftBoardView(
                state.division_name,
                self._get_state,
                self._refresh_from_view,
                initial_page=page,
            )
            self._views[state.division_name] = view
        else:
            view.page = page
            view._sync_buttons()

        embed = build_admin_draft_board_embed(state, page)

        try:
            if state.admin_log_message_id:
                try:
                    message = await channel.fetch_message(state.admin_log_message_id)
                    if interaction:
                        await interaction.response.edit_message(embed=embed, view=view)
                    else:
                        await message.edit(embed=embed, view=view)
                    self._bot.add_view(view, message_id=message.id)
                    if self._save_state:
                        self._save_state(state)
                    return
                except discord.NotFound:
                    state.admin_log_message_id = None

            message = await channel.send(embed=embed, view=view)
            state.admin_log_message_id = message.id
            self._bot.add_view(view, message_id=message.id)
            if self._save_state:
                self._save_state(state)
        except discord.HTTPException as e:
            logger.error("[AdminLog] Failed to refresh board for %s: %s", state.division_name, e)

    async def _refresh_from_view(
        self,
        division_name: str,
        page: int,
        *,
        interaction: Optional[discord.Interaction] = None,
    ) -> None:
        state = self._resolve_state(division_name)
        if not state:
            if interaction and not interaction.response.is_done():
                await interaction.response.send_message(
                    "Division state not found.", ephemeral=True
                )
            return
        view = self._views.get(division_name)
        if view:
            view.page = page
            view._sync_buttons()
        await self.refresh_board(state, page=page, interaction=interaction)

    async def restore_boards(self, states: dict[str, "DraftState"]) -> None:
        """Re-attach paginator views after bot restart."""
        if not self.is_enabled():
            return
        for state in states.values():
            if not state.admin_log_message_id:
                continue
            view = AdminDraftBoardView(
                state.division_name,
                self._get_state,
                self._refresh_from_view,
                initial_page=0,
            )
            self._views[state.division_name] = view
            self._bot.add_view(view, message_id=state.admin_log_message_id)
            try:
                await self.refresh_board(state, page=0)
            except Exception as e:
                logger.warning(
                    "[AdminLog] Could not restore board for %s: %s",
                    state.division_name,
                    e,
                )

    async def draft_initialized(
        self, state: "DraftState", coach_count: int, pool_size: int
    ) -> None:
        state.draft_events = []
        state.admin_log_message_id = None
        await self._append_event(
            state,
            DraftEvent(
                event_type=DraftEventType.INITIALIZED,
                pick_number=coach_count,
                points=pool_size,
            ),
        )

    async def draft_started(self, state: "DraftState", first_coach: str) -> None:
        await self._append_event(
            state,
            DraftEvent(event_type=DraftEventType.STARTED, coach_name=first_coach),
        )

    async def pick_made(
        self,
        state: "DraftState",
        coach: str,
        pokemon: str,
        points: int,
        pick_number: int,
        is_makeup: bool = False,
        is_bank: bool = False,
    ) -> None:
        await self._append_event(
            state,
            DraftEvent(
                event_type=DraftEventType.PICK,
                coach_name=coach,
                detail=pokemon,
                pick_number=pick_number,
                points=points,
                is_makeup=is_makeup,
                is_bank=is_bank,
                timestamp=time.time(),
            ),
        )

    async def skip(self, state: "DraftState", coach: str, skip_count: int) -> None:
        await self._append_event(
            state,
            DraftEvent(
                event_type=DraftEventType.SKIP,
                coach_name=coach,
                skip_count=skip_count,
            ),
        )

    async def replacement_needed(self, state: "DraftState", coach: str) -> None:
        await self._append_event(
            state,
            DraftEvent(
                event_type=DraftEventType.REPLACEMENT_NEEDED,
                coach_name=coach,
            ),
        )

    async def replacement_applied(
        self, state: "DraftState", old_coach: str, new_coach: str
    ) -> None:
        await self._append_event(
            state,
            DraftEvent(
                event_type=DraftEventType.REPLACEMENT,
                coach_name=new_coach,
                secondary=old_coach,
            ),
        )

    async def draft_paused(self, state: "DraftState", reason: str) -> None:
        await self._append_event(
            state,
            DraftEvent(event_type=DraftEventType.PAUSE, detail=reason),
        )

    async def draft_resumed(self, state: "DraftState", coach: str) -> None:
        await self._append_event(
            state,
            DraftEvent(event_type=DraftEventType.RESUME, coach_name=coach),
        )

    async def draft_completed(self, state: "DraftState") -> None:
        await self._append_event(
            state, DraftEvent(event_type=DraftEventType.COMPLETED)
        )

    async def force_skip(self, state: "DraftState", coach: str) -> None:
        await self._append_event(
            state,
            DraftEvent(event_type=DraftEventType.FORCE_SKIP, coach_name=coach),
        )

    async def pick_correction(
        self,
        state: "DraftState",
        action: str,
        coach: str,
        detail: str,
        pick_number: int,
    ) -> None:
        event_map = {
            "undo": DraftEventType.UNDO,
            "goto": DraftEventType.GOTO,
            "edit": DraftEventType.EDIT,
        }
        event_type = event_map.get(action, DraftEventType.EDIT)
        await self._append_event(
            state,
            DraftEvent(
                event_type=event_type,
                coach_name=coach,
                detail=detail,
                pick_number=pick_number,
            ),
        )

    async def division_reset(self, state: "DraftState") -> None:
        if not self._bot:
            return

        division = state.division_name
        if state.admin_log_message_id:
            channel = self._bot.get_channel(self.channel_id)
            if channel:
                try:
                    message = await channel.fetch_message(state.admin_log_message_id)
                    await message.delete()
                except (discord.NotFound, discord.HTTPException):
                    pass

        self._views.pop(division, None)
        state.admin_log_message_id = None
        state.draft_events = []
