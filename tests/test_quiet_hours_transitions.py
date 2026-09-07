"""
Crossing into and out of the nightly window.

At the pause boundary every running clock is banked and the channel is told;
at the resume boundary the same clocks pick up where they stopped. The draft
never leaves ACTIVE, so picking stays open all night.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from constants.draft_constants import DraftStatus
from models.pick_bank import BankSnipePending
from services.draft_service import DraftService
from tests.helpers import make_state
from utils.quiet_hours import QuietHours

NIGHT_CONFIG = {
    "enabled": True,
    "timezone": "Europe/Lisbon",
    "pause_at": "23:00",
    "resume_at": "09:00",
}


class _Clock(QuietHours):
    """Quiet hours whose current time is set by the test."""

    def __init__(self) -> None:
        super().__init__(NIGHT_CONFIG)
        self.quiet_now = False

    def is_quiet(self, at=None) -> bool:
        return self.quiet_now


@pytest.fixture
def service(monkeypatch):
    monkeypatch.setattr(
        "utils.division_helper.load_division_config",
        lambda: [{"name": "TestDivision"}],
    )
    persistence = MagicMock()
    persistence.load_all.return_value = {}

    svc = DraftService(persistence, MagicMock(), MagicMock())
    svc.admin_log = MagicMock()
    svc.quiet_hours = _Clock()
    svc.timers.quiet_hours = svc.quiet_hours
    svc.send_callback = AsyncMock()

    state = make_state()
    svc.states[state.division_name] = state
    return svc, state


def _sent_embeds(service: DraftService) -> list:
    return [
        call.args[1]["embed"]
        for call in service.send_callback.await_args_list
        if "embed" in call.args[1]
    ]


class TestEnteringTheWindow:
    @pytest.mark.asyncio
    async def test_running_clock_is_banked_not_lost(self, service):
        svc, state = service
        svc.timers.start_timer(state)
        remaining_before = state.current_coach.pick_timer_remaining

        svc.quiet_hours.quiet_now = True
        await svc.enter_quiet_hours()

        assert state.current_coach.pick_timer_remaining == pytest.approx(
            remaining_before, abs=5
        )
        assert svc.timers._timer_tasks.get(state.division_name) is None

    @pytest.mark.asyncio
    async def test_draft_stays_active_so_picks_still_work(self, service):
        svc, state = service
        svc.timers.start_timer(state)
        svc.quiet_hours.quiet_now = True
        await svc.enter_quiet_hours()
        assert state.status == DraftStatus.ACTIVE

    @pytest.mark.asyncio
    async def test_channel_is_told_once(self, service):
        svc, state = service
        svc.timers.start_timer(state)
        svc.quiet_hours.quiet_now = True
        await svc.enter_quiet_hours()
        assert len(_sent_embeds(svc)) == 1

    @pytest.mark.asyncio
    async def test_paused_divisions_are_left_alone(self, service):
        svc, state = service
        state.status = DraftStatus.PAUSED
        svc.quiet_hours.quiet_now = True
        await svc.enter_quiet_hours()
        svc.send_callback.assert_not_awaited()


class TestLeavingTheWindow:
    @pytest.mark.asyncio
    async def test_clock_resumes_from_where_it_stopped(self, service):
        svc, state = service
        state.current_coach.set_pick_timer(1500.0)

        svc.quiet_hours.quiet_now = False
        await svc.exit_quiet_hours()

        assert svc.timers._timer_tasks.get(state.division_name) is not None
        assert state.current_coach.pick_timer_remaining == pytest.approx(1500.0)
        svc.timers.cancel_timer(state)

    @pytest.mark.asyncio
    async def test_channel_gets_the_all_clear(self, service):
        svc, state = service
        state.current_coach.set_pick_timer(1500.0)
        svc.quiet_hours.quiet_now = False
        await svc.exit_quiet_hours()
        assert len(_sent_embeds(svc)) == 1
        svc.timers.cancel_timer(state)

    @pytest.mark.asyncio
    async def test_a_coach_who_arrived_overnight_gets_a_full_clock(self, service):
        svc, state = service
        svc.quiet_hours.quiet_now = True
        # A pick lands at 02:00: the next coach is on the clock but nothing runs.
        svc.timers.start_timer(state)
        assert svc.timers._timer_tasks.get(state.division_name) is None
        assert state.current_coach.pick_timer_remaining == state.pick_time_initial

        svc.quiet_hours.quiet_now = False
        await svc.exit_quiet_hours()

        assert svc.timers._timer_tasks.get(state.division_name) is not None
        assert state.current_coach.pick_timer_remaining == pytest.approx(
            state.pick_time_initial
        )
        svc.timers.cancel_timer(state)


class TestBankSnipeAcrossTheWindow:
    @pytest.mark.asyncio
    async def test_fallback_window_does_not_burn_overnight(self, service):
        svc, state = service
        svc.quiet_hours.quiet_now = True
        state.bank_snipe_pending = BankSnipePending(
            coach_discord_id=state.current_coach.discord_id,
            round_number=1,
            sniped_primary="Landorus-Therian",
            remaining_seconds=1800.0,
        )

        svc._schedule_bank_snipe_timer(state, 1800.0)
        assert state.bank_snipe_pending.remaining_seconds == pytest.approx(1800.0)

        svc.quiet_hours.quiet_now = False
        await svc.exit_quiet_hours()
        assert state.bank_snipe_pending.remaining_seconds == pytest.approx(1800.0)

    @pytest.mark.asyncio
    async def test_partly_spent_window_keeps_only_what_was_left(self, service):
        svc, state = service
        state.bank_snipe_pending = BankSnipePending(
            coach_discord_id=state.current_coach.discord_id,
            round_number=1,
            sniped_primary="Landorus-Therian",
            remaining_seconds=1200.0,
        )
        svc.quiet_hours.quiet_now = True
        await svc.enter_quiet_hours()
        assert state.bank_snipe_pending.remaining_seconds == pytest.approx(1200.0, abs=5)
