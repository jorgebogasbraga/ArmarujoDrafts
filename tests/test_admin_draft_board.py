"""Tests for the admin draft board embed builder."""

import math

from models.draft_event import DraftEvent, DraftEventType
from models.draft_state import PickRecord
from services.admin_draft_board import (
    EVENTS_PER_PAGE,
    board_total_pages,
    build_admin_draft_board_embed,
)
from tests.helpers import make_state


def test_board_total_pages_empty():
    state = make_state()
    assert board_total_pages(state) == 1


def test_board_total_pages_with_events():
    state = make_state()
    state.draft_events = [
        DraftEvent(event_type=DraftEventType.PICK, pick_number=i)
        for i in range(1, 14)
    ]
    assert board_total_pages(state) == math.ceil(13 / EVENTS_PER_PAGE)


def test_feed_newest_first_on_page_zero():
    state = make_state()
    state.draft_events = [
        DraftEvent(
            event_type=DraftEventType.PICK,
            pick_number=1,
            coach_name="Alice",
            detail="Bulbasaur",
            points=5,
        ),
        DraftEvent(
            event_type=DraftEventType.PICK,
            pick_number=2,
            coach_name="Bob",
            detail="Charmander",
            points=8,
        ),
    ]
    embed = build_admin_draft_board_embed(state, page=0)
    feed = embed.fields[1].value
    assert "Bob" in feed
    assert "Charmander" in feed
    assert feed.index("Bob") < feed.index("Alice")


def test_backfill_from_pick_history():
    state = make_state()
    state.pick_history = [
        PickRecord(
            pick_number=1,
            round_number=1,
            coach_discord_id="1",
            coach_name="Coach A",
            pokemon_name="Pikachu",
            points_cost=10,
        )
    ]
    embed = build_admin_draft_board_embed(state, page=0)
    assert "Pikachu" in embed.fields[1].value
    assert len(state.draft_events) == 1


if __name__ == "__main__":
    test_board_total_pages_empty()
    test_board_total_pages_with_events()
    test_feed_newest_first_on_page_zero()
    test_backfill_from_pick_history()
    print("OK")
