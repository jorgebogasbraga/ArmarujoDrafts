"""Tests for trade validation — roster ownership and points on both sides."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from models.coach import Coach
from models.pokemon import Pokemon
from models.trade import TradeType
from services.trade_service import TradeService
from tests.helpers import make_coach, make_pokemon, make_state


class StubDraftService:
    def __init__(self, state) -> None:
        self.state = state
        self.alias_manager = None
        self.persistence = None

    def _get_state(self, division: str):
        if self.state.division_name.lower() == division.lower():
            return self.state
        return None


def _service_with_teams(
    proposer_team: list[Pokemon],
    target_team: list[Pokemon],
    *,
    proposer_remaining: int | None = None,
    target_remaining: int | None = None,
) -> TradeService:
    state = make_state(num_coaches=2, total_points=20)
    proposer = state.coaches[0]
    target = state.coaches[1]
    proposer.team = proposer_team
    target.team = target_team
    if proposer_remaining is not None:
        proposer.remaining_points = proposer_remaining
    else:
        proposer.recalculate_points(state.total_points)
    if target_remaining is not None:
        target.remaining_points = target_remaining
    else:
        target.recalculate_points(state.total_points)

    svc = TradeService(draft_service=StubDraftService(state))
    return svc


def test_target_must_own_receiving_pokemon():
    mon_a = make_pokemon("MonA", 5)
    mon_b = make_pokemon("MonB", 8)
    mon_c = make_pokemon("MonC", 6)
    svc = _service_with_teams([mon_a], [mon_c])

    ok, err = svc.validate_trade(
        "TestDivision",
        "1",
        "2",
        "MonA",
        "MonB",
        TradeType.DIRECT,
    )
    assert not ok
    assert err == "trade.target_missing"


def test_target_cannot_afford_trade():
    mon_a = make_pokemon("MonA", 18)
    mon_b = make_pokemon("MonB", 15)
    mon_c = make_pokemon("MonC", 3)
    svc = _service_with_teams([mon_a], [mon_b, mon_c], target_remaining=2)

    ok, err = svc.validate_trade(
        "TestDivision",
        "1",
        "2",
        "MonA",
        "MonC",
        TradeType.DIRECT,
    )
    assert not ok
    assert err == "trade.target_insufficient_points"


def test_valid_direct_trade():
    mon_a = make_pokemon("MonA", 5)
    mon_b = make_pokemon("MonB", 8)
    mon_c = make_pokemon("MonC", 6)
    svc = _service_with_teams([mon_a], [mon_b])

    ok, err = svc.validate_trade(
        "TestDivision",
        "1",
        "2",
        "MonA",
        "MonB",
        TradeType.DIRECT,
    )
    assert ok
    assert err == ""


def test_proposer_cannot_afford_trade():
    mon_a = make_pokemon("MonA", 3)
    mon_x = make_pokemon("MonX", 14)
    mon_b = make_pokemon("MonB", 18)
    svc = _service_with_teams([mon_a, mon_x], [mon_b])

    ok, err = svc.validate_trade(
        "TestDivision",
        "1",
        "2",
        "MonA",
        "MonB",
        TradeType.DIRECT,
    )
    assert not ok
    assert err == "trade.insufficient_points"


if __name__ == "__main__":
    test_target_must_own_receiving_pokemon()
    test_target_cannot_afford_trade()
    test_valid_direct_trade()
    test_proposer_cannot_afford_trade()
    print("OK")
