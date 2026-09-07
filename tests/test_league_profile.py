"""
Config-driven league profiles.

Covers the pieces that let a second league run from JSON alone: hiding
commands, reading a Pokédex where cost marks membership, ordering coaches from
an explicit draft-order range, and the Champions profile files themselves.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from services.draft.sheet_geometry import compute_card_cell
from services.sheets_service import SheetsService
from utils.feature_gate import apply_command_gate
from utils.league_settings import LeagueSettings
from utils.sheet_layout import SheetLayout, load_sheet_layout
from views.locale_embed_view import tab_order

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CHAMPIONS = PROJECT_ROOT / "leagues" / "tuga-champions"
ARMA = PROJECT_ROOT / "leagues" / "arma"


class _Command:
    def __init__(self, name: str, parent=None) -> None:
        self.name = name
        self.parent = parent


class _Cog:
    def __init__(self, *names: str) -> None:
        self.__cog_commands__ = tuple(_Command(n) for n in names)


class TestCommandGate:
    def test_keeps_everything_when_nothing_is_disabled(self):
        cog = _Cog("pick", "team")
        assert apply_command_gate(cog, set()) is True
        assert len(cog.__cog_commands__) == 2

    def test_strips_only_the_named_commands(self):
        cog = _Cog("language", "timezone")
        assert apply_command_gate(cog, {"timezone"}) is True
        assert [c.name for c in cog.__cog_commands__] == ["language"]

    def test_reports_empty_cog_so_caller_can_skip_it(self):
        cog = _Cog("trade_offer", "trade_queue", "my_trades")
        disabled = {"trade_offer", "trade_queue", "my_trades"}
        assert apply_command_gate(cog, disabled) is False
        assert cog.__cog_commands__ == ()

    def test_subcommands_follow_their_parent(self):
        parent = _Command("standings")
        child = _Command("weekly", parent=parent)
        cog = _Cog()
        cog.__cog_commands__ = (parent, child)
        assert apply_command_gate(cog, {"standings"}) is False


class TestFeatureFlags:
    def test_optional_features_default_to_on(self):
        assert LeagueSettings({}).show_tera is True

    def test_tera_can_be_switched_off(self):
        assert LeagueSettings({"features": {"tera": False}}).show_tera is False


class TestBaseLanguage:
    def test_blank_means_english(self):
        assert LeagueSettings({}).default_locale == "en"
        assert LeagueSettings({"default_locale": ""}).default_locale == "en"

    def test_league_language_is_used(self):
        assert LeagueSettings({"default_locale": "pt"}).default_locale == "pt"

    def test_unknown_language_falls_back_rather_than_breaking_embeds(self):
        assert LeagueSettings({"default_locale": "fr"}).default_locale == "en"


class TestLanguageButtonOrder:
    def test_english_league_keeps_english_first(self):
        assert tab_order("en") == ["en", "pt", "es"]

    def test_portuguese_league_leads_with_portuguese(self):
        assert tab_order("pt") == ["pt", "en", "es"]

    def test_english_is_always_the_second_option(self):
        assert tab_order("es")[:2] == ["es", "en"]

    def test_every_language_is_still_offered(self):
        assert sorted(tab_order("pt")) == ["en", "es", "pt"]


class TestFooterBranding:
    def _footer(self, monkeypatch, name, existing=""):
        from services.embed_service import EmbedService

        monkeypatch.setattr("services.embed_service.league_name", lambda: name)
        return EmbedService._footer(existing)

    def test_unnamed_league_leaves_footers_untouched(self, monkeypatch):
        assert self._footer(monkeypatch, "", "Acuity") == "Acuity"
        assert self._footer(monkeypatch, "") == ""

    def test_named_league_appears_on_its_own(self, monkeypatch):
        assert self._footer(monkeypatch, "Tuga Champions League") == (
            "Tuga Champions League"
        )

    def test_named_league_is_appended_to_existing_text(self, monkeypatch):
        assert self._footer(monkeypatch, "Tuga Champions League", "Champions") == (
            "Champions · Tuga Champions League"
        )


class TestTeraVisibility:
    """Formats without Terastallisation should not advertise Tera points."""

    def _field_names(self, monkeypatch, tera: bool) -> list[str]:
        from services.embed_service import EmbedService
        from tests.helpers import make_state

        monkeypatch.setattr("services.embed_service.show_tera", lambda: tera)
        state = make_state()
        embed = EmbedService.team_card(state.coaches[0], state, locale="en")
        return [f.name for f in embed.fields]

    def test_tera_field_shown_when_the_format_uses_it(self, monkeypatch):
        assert any("Tera" in n for n in self._field_names(monkeypatch, True))

    def test_tera_field_hidden_when_the_format_does_not(self, monkeypatch):
        assert not any("Tera" in n for n in self._field_names(monkeypatch, False))

    def test_points_and_skips_survive_hiding_tera(self, monkeypatch):
        names = self._field_names(monkeypatch, False)
        assert any("Points" in n for n in names)
        assert any("Skips" in n for n in names)


def _sheets_with_layout(layout: SheetLayout, values: dict) -> SheetsService:
    """A SheetsService that reads canned values instead of calling Google."""
    svc = SheetsService(layout=layout)
    svc._client = object()
    svc._spreadsheet = object()

    def worksheet(name: str):
        sheet = MagicMock()
        sheet.get_all_values.return_value = values.get(name, [])
        sheet.get.side_effect = lambda rng: values.get(f"{name}!{rng}", values.get(name, []))
        return sheet

    svc._get_worksheet = worksheet
    return svc


POKEDEX_LAYOUT = SheetLayout(
    raw={
        "sheets": {"master_pool": "Pokédex"},
        "master_pool": {
            "header_rows": 0,
            "require_points": True,
            "columns": {"pokedex_id": 0, "name": 1, "points": 30},
            "banned_value": "BAN",
        },
    },
    path=None,
)


def _dex_row(dex: str, name: str, points: str) -> list[str]:
    row = [""] * 31
    row[0] = dex
    row[1] = name
    row[30] = points
    return row


class TestMasterPoolByCost:
    def _pool(self, rows):
        svc = _sheets_with_layout(POKEDEX_LAYOUT, {"Pokédex": rows})
        return svc.read_master_pool()

    def test_only_priced_pokemon_join_the_draft(self):
        pool = self._pool([
            _dex_row("6", "Charizard", "18"),
            _dex_row("10", "Caterpie", ""),
        ])
        assert [p.name for p in pool] == ["Charizard"]

    def test_cost_and_dex_are_read_from_their_columns(self):
        pool = self._pool([_dex_row("6", "Charizard-Mega-X", "20")])
        assert pool[0].points == 20
        assert pool[0].pokedex_id == 6

    def test_banned_pokemon_stay_visible_at_zero_points(self):
        pool = self._pool([_dex_row("150", "Mewtwo", "BAN")])
        assert pool[0].is_banned is True
        assert pool[0].points == 0

    def test_missing_tera_column_costs_nothing(self):
        pool = self._pool([_dex_row("6", "Charizard", "18")])
        assert pool[0].tera_tax_cost == 0


PARTICIPANTS_LAYOUT = SheetLayout(
    raw={
        "sheets": {"participants": "Participantes"},
        "participants": {
            "start_row": 4,
            "read_range": "D{start_row}:H35",
            "columns": {
                "name": 0,
                "team_name": 1,
                "discord_id": 2,
                "logo_url": 3,
                "timezone": 4,
            },
            "draft_order": {"sheet": "Tabela do Draft", "range": "C4:C19"},
        },
    },
    path=None,
)

DIVISIONS = [{"name": "Champions", "num_coaches": 3}]


class TestDraftOrderParticipants:
    def _read(self, order_rows, participant_rows, num_coaches=3):
        divisions = [{"name": "Champions", "num_coaches": num_coaches}]
        svc = _sheets_with_layout(
            PARTICIPANTS_LAYOUT,
            {
                "Participantes!D4:H35": participant_rows,
                "Tabela do Draft!C4:C19": order_rows,
            },
        )
        return svc.read_participants_for_division("Champions", divisions)

    ROWS = [
        ["Ana", "Team A", "1", "a.png", "Europe/Lisbon"],
        ["Bruno", "Team B", "2", "b.png", "Europe/Lisbon"],
        ["Carla", "Team C", "3", "c.png", "Europe/Lisbon"],
        ["Diogo", "Team D", "4", "d.png", "Europe/Lisbon"],
    ]

    def test_order_range_drives_the_snake_order(self):
        coaches = self._read([["Carla"], ["Ana"], ["Bruno"]], self.ROWS)
        assert [c["name"] for c in coaches] == ["Carla", "Ana", "Bruno"]

    def test_coaches_outside_the_order_are_left_out(self):
        coaches = self._read([["Carla"], ["Ana"], ["Bruno"]], self.ROWS)
        assert "Diogo" not in [c["name"] for c in coaches]

    def test_details_come_from_the_participants_tab(self):
        coaches = self._read([["Bruno"]], self.ROWS, num_coaches=1)
        assert coaches[0]["team_name"] == "Team B"
        assert coaches[0]["discord_id"] == "2"
        assert coaches[0]["logo_url"] == "b.png"

    def test_order_is_capped_at_the_configured_coach_count(self):
        order = [["Ana"], ["Bruno"], ["Carla"], ["Diogo"]]
        assert len(self._read(order, self.ROWS)) == 3

    def test_blank_order_cells_are_skipped(self):
        order = [["Ana"], [], [""], ["Bruno"], ["Carla"]]
        assert [c["name"] for c in self._read(order, self.ROWS)] == [
            "Ana", "Bruno", "Carla"
        ]

    def test_unknown_name_in_order_aborts_rather_than_shifting_picks(self):
        order = [["Ana"], ["Ghost"], ["Bruno"]]
        assert self._read(order, self.ROWS) == []


class TestChampionsProfile:
    """The shipped profile must describe the real spreadsheet."""

    @pytest.fixture(scope="class")
    def layout(self) -> SheetLayout:
        return load_sheet_layout(CHAMPIONS / "sheet_layout.json")

    @pytest.fixture(scope="class")
    def division(self) -> dict:
        data = json.loads((CHAMPIONS / "division_config.json").read_text("utf-8"))
        return data["divisions"][0]

    @pytest.fixture(scope="class")
    def league(self) -> LeagueSettings:
        return LeagueSettings(
            json.loads((CHAMPIONS / "league_config.json").read_text("utf-8"))
        )

    def test_reads_names_and_costs_from_the_pokedex(self, layout):
        assert layout.master_pool_sheet == "Pokédex"
        assert layout.master_pool_column("name") == 1     # column B
        assert layout.master_pool_column("points") == 30  # column AE
        assert layout.master_pool_column("pokedex_id") == 0
        assert layout.master_pool_requires_points() is True

    def test_draft_order_points_at_the_card_tab(self, layout):
        order = layout.draft_order()
        assert order == {"sheet": "Tabela do Draft", "range": "C4:C19"}

    def test_first_row_of_cards(self, layout):
        cells = [compute_card_cell(pos, 3, 0, 16, layout) for pos in range(4)]
        assert cells == ["F6", "J6", "N6", "R6"]

    def test_eight_picks_per_card(self, layout, division):
        picks = [
            compute_card_cell(0, 3, i, 16, layout)
            for i in range(division["team_size"])
        ]
        assert picks == [f"F{row}" for row in range(6, 14)]

    def test_four_rows_of_cards(self, layout):
        rows = [compute_card_cell(pos, 3, 0, 16, layout) for pos in (0, 4, 8, 12)]
        assert rows == ["F6", "F19", "F32", "F45"]

    def test_league_rules(self, division):
        assert division["team_size"] == 8
        assert division["total_points"] == 90
        assert division["num_coaches"] == 16
        assert division["tera_captain_points"] == 0

    def test_pick_timers_are_three_two_one_hours(self, division):
        assert division["pick_time_initial"] == 3 * 3600
        assert division["pick_time_second"] == 2 * 3600
        assert division["pick_time_final"] == 1 * 3600

    def test_tera_is_hidden(self, league):
        assert league.show_tera is False

    def test_post_draft_commands_are_hidden(self, league):
        disabled = set(league.raw["features"]["disabled_commands"])
        assert {
            "alias_forget", "alias_learn", "alias_list", "battle_summary",
            "killboard", "match_schedule", "match_status", "my_trades",
            "standings", "submit_replay", "timezone", "trade_offer",
            "trade_queue",
        } <= disabled

    def test_draft_commands_stay_available(self, league):
        disabled = set(league.raw["features"]["disabled_commands"])
        assert disabled.isdisjoint(
            {"pick", "bank", "team", "myteam", "picking", "search", "drafted",
             "language", "start_division", "pause_draft", "resume_draft", "replace"}
        )

    def test_nightly_window_is_23_to_9_lisbon(self, league):
        assert league.quiet_hours == {
            "enabled": True,
            "timezone": "Europe/Lisbon",
            "pause_at": "23:00",
            "resume_at": "09:00",
        }

    def test_embeds_are_posted_in_portuguese(self, league):
        assert league.default_locale == "pt"

    def test_league_is_branded(self, league):
        assert league.name == "Tuga Champions League"


class TestProfileFolders:
    """Each league is self-contained, and Arma still reads as it always did."""

    FILES = (
        ".env",
        "league_config.json",
        "division_config.json",
        "sheet_layout.json",
        "coaches.json",
    )

    @pytest.mark.parametrize("filename", FILES)
    def test_champions_profile_is_complete(self, filename):
        assert (CHAMPIONS / filename).is_file()

    @pytest.mark.parametrize("filename", FILES)
    def test_arma_profile_is_complete(self, filename):
        assert (ARMA / filename).is_file()

    def test_arma_state_moved_with_it(self):
        assert (ARMA / "data").is_dir()

    def test_config_files_no_longer_sit_in_the_project_root(self):
        stray = [
            name
            for name in ("league_config.json", "division_config.json", "sheet_layout.json")
            if (PROJECT_ROOT / name).exists()
        ]
        assert stray == []

    def test_league_dir_drives_every_path(self):
        from config import Config

        league_dir = Path(Config.LEAGUE_DIR)
        assert league_dir.name == "arma"
        for path in (
            Config.SHEET_LAYOUT_FILE,
            Config.DIVISION_CONFIG_FILE,
            Config.LEAGUE_CONFIG_FILE,
            Config.COACHES_CONFIG_FILE,
            Config.DATA_DIR,
            Config.LOG_FILE,
        ):
            assert league_dir in Path(path).parents, path

    def test_arma_keeps_english_and_no_branding(self):
        settings = LeagueSettings(
            json.loads((ARMA / "league_config.json").read_text("utf-8"))
        )
        assert settings.default_locale == "en"
        assert settings.name == ""
        assert settings.show_tera is True
