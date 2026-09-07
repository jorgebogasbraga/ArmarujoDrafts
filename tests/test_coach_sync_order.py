"""
Pulling coaches out of the sheet at /start_division time.

Leagues either anchor on a `first_coach` row and read forward, or hand the bot
an explicit draft-order range. The second kind has no anchor row, and must not
be rejected for missing one.
"""

from unittest.mock import MagicMock

import pytest

from services.draft_service import DraftService
from utils.sheet_layout import SheetLayout

ORDERED_LAYOUT = SheetLayout(
    raw={
        "participants": {
            "draft_order": {"sheet": "Tabela do Draft", "range": "C4:C19"},
        }
    },
    path=None,
)

ANCHORED_LAYOUT = SheetLayout(raw={"participants": {}}, path=None)

COACHES = [
    {
        "name": f"Coach{i}",
        "team_name": f"Team{i}",
        "discord_id": str(i),
        "logo_url": "",
        "timezone": "Europe/Lisbon",
    }
    for i in range(1, 4)
]


@pytest.fixture
def service(monkeypatch):
    def build(division: dict, layout: SheetLayout, participants=COACHES):
        monkeypatch.setattr(
            "utils.division_helper.load_division_config", lambda *a, **k: [division]
        )
        monkeypatch.setattr(
            "utils.division_helper.load_coaches_config", lambda *a, **k: []
        )
        monkeypatch.setattr(
            "utils.division_helper.save_coaches_config", lambda *a, **k: None
        )
        monkeypatch.setattr(
            "services.draft_service.get_sheet_layout", lambda: layout
        )

        persistence = MagicMock()
        persistence.load_all.return_value = {}
        sheets = MagicMock()
        sheets.read_participants_for_division.return_value = participants

        svc = DraftService(persistence, sheets, MagicMock())
        svc.admin_log = MagicMock()
        return svc

    return build


CHAMPIONS_DIVISION = {"name": "Champions", "num_coaches": 3, "first_coach": ""}
ARMA_DIVISION = {"name": "Acuity", "num_coaches": 3, "first_coach": "Coach1"}


class TestDraftOrderLeagues:
    def test_sync_works_without_a_first_coach(self, service):
        svc = service(CHAMPIONS_DIVISION, ORDERED_LAYOUT)
        ok, message, coaches = svc.sync_coaches_from_sheet("Champions")
        assert ok, message
        assert len(coaches) == 3

    def test_coaches_keep_the_order_the_sheet_gave(self, service):
        svc = service(CHAMPIONS_DIVISION, ORDERED_LAYOUT)
        reversed_order = list(reversed(COACHES))
        svc.sheets.read_participants_for_division.return_value = reversed_order
        _, _, coaches = svc.sync_coaches_from_sheet("Champions")
        assert [c["name"] for c in coaches] == ["Coach3", "Coach2", "Coach1"]

    def test_short_sheet_still_fails_loudly(self, service):
        svc = service(CHAMPIONS_DIVISION, ORDERED_LAYOUT, participants=COACHES[:2])
        ok, _, coaches = svc.sync_coaches_from_sheet("Champions")
        assert ok is False
        assert coaches == []


class TestAnchoredLeagues:
    def test_first_coach_still_works(self, service):
        svc = service(ARMA_DIVISION, ANCHORED_LAYOUT)
        ok, message, coaches = svc.sync_coaches_from_sheet("Acuity")
        assert ok, message
        assert len(coaches) == 3

    def test_missing_first_coach_is_still_rejected(self, service):
        division = {**ARMA_DIVISION, "first_coach": ""}
        svc = service(division, ANCHORED_LAYOUT)
        ok, _, coaches = svc.sync_coaches_from_sheet("Acuity")
        assert ok is False
        assert coaches == []

    def test_zero_coaches_is_rejected_either_way(self, service):
        division = {"name": "Champions", "num_coaches": 0, "first_coach": ""}
        svc = service(division, ORDERED_LAYOUT)
        ok, _, _ = svc.sync_coaches_from_sheet("Champions")
        assert ok is False
