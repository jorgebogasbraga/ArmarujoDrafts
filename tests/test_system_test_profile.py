"""
Per-league system test profiles.

Which divisions the test walks, and whether it escalates a skipped coach into
a replacement, both come from <LEAGUE_DIR>/system_test.json. A draft-only
league can exercise timers and skips without ever handing a team over.
"""

import json
from pathlib import Path

import pytest

import simulation.replacements as profiles
from utils.quiet_hours import QuietHours, get_quiet_hours, quiet_hours_suspended

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LEAGUES = PROJECT_ROOT / "leagues"


@pytest.fixture
def profile(tmp_path, monkeypatch):
    def write(data):
        path = tmp_path / "system_test.json"
        if data is not None:
            path.write_text(json.dumps(data), encoding="utf-8")
        monkeypatch.setattr(profiles.Config, "SYSTEM_TEST_FILE", str(path))
        profiles._load.cache_clear()
        return path

    yield write
    profiles._load.cache_clear()


class TestDivisionSelection:
    def test_configured_divisions_are_used_in_order(self, profile):
        profile({"divisions": ["Champions", "Rookie"]})
        assert profiles.system_test_divisions() == ("Champions", "Rookie")

    def test_missing_file_falls_back_to_every_division(self, profile, monkeypatch):
        profile(None)
        monkeypatch.setattr(
            "utils.division_helper.load_division_config",
            lambda *a, **k: [{"name": "Acuity"}, {"name": "Verity"}],
        )
        assert profiles.system_test_divisions() == ("Acuity", "Verity")

    def test_broken_json_does_not_explode(self, profile, monkeypatch):
        path = profile({})
        path.write_text("{ not json", encoding="utf-8")
        profiles._load.cache_clear()
        monkeypatch.setattr(
            "utils.division_helper.load_division_config", lambda *a, **k: []
        )
        assert profiles.system_test_divisions() == ()


class TestReplacementProfiles:
    PROFILE = {
        "name": "dudetaiga",
        "team_name": "52 BPWCN",
        "discord_id": "1504567329936244928",
        "logo_url": "https://example.invalid/logo.png",
        "timezone": "GMT",
    }

    def test_configured_division_gets_its_stand_in(self, profile):
        profile({"divisions": ["Acuity"], "replacements": {"Acuity": self.PROFILE}})
        found = profiles.replacement_for("Acuity")
        assert found.name == "dudetaiga"
        assert found.discord_id == "1504567329936244928"

    def test_division_without_a_profile_returns_none(self, profile):
        profile({"divisions": ["Champions"], "replacements": {}})
        assert profiles.replacement_for("Champions") is None

    def test_no_replacements_block_at_all(self, profile):
        profile({"divisions": ["Champions"]})
        assert profiles.replacement_for("Champions") is None

    def test_channel_and_retry_defaults(self, profile):
        profile({"divisions": ["Champions"]})
        assert profiles.system_test_channel_id() == 0
        assert profiles.system_test_max_attempts() == 1

    def test_channel_and_retry_are_read(self, profile):
        profile({"divisions": ["Champions"], "channel_id": 99, "max_attempts": 4})
        assert profiles.system_test_channel_id() == 99
        assert profiles.system_test_max_attempts() == 4


class TestShippedProfiles:
    def _read(self, league):
        return json.loads(
            (LEAGUES / league / "system_test.json").read_text(encoding="utf-8")
        )

    def test_champions_tests_its_single_division(self):
        assert self._read("tuga-champions")["divisions"] == ["Champions"]

    def test_champions_never_replaces_a_coach(self):
        assert self._read("tuga-champions").get("replacements") == {}

    def test_champions_posts_in_the_draft_tests_channel(self):
        assert self._read("tuga-champions")["channel_id"] == 1546119232675381370

    def test_champions_retries_failed_runs(self):
        assert self._read("tuga-champions")["max_attempts"] == 10

    def test_arma_keeps_all_five_divisions(self):
        assert self._read("arma")["divisions"] == [
            "Acuity",
            "Verity",
            "Valor",
            "Origin",
            "Lunar",
        ]

    def test_arma_keeps_a_stand_in_for_every_division(self):
        data = self._read("arma")
        assert set(data["replacements"]) == set(data["divisions"])
        for name, raw in data["replacements"].items():
            assert raw["discord_id"], name
            assert raw["team_name"], name


class TestQuietHoursSuspension:
    """A compressed-timer simulation must not freeze at 23:00."""

    def test_window_is_off_inside_the_block(self):
        quiet = get_quiet_hours()
        quiet.enabled = True
        try:
            with quiet_hours_suspended():
                assert quiet.enabled is False
        finally:
            quiet.enabled = False

    def test_window_is_restored_afterwards(self):
        quiet = get_quiet_hours()
        quiet.enabled = True
        try:
            with quiet_hours_suspended():
                pass
            assert quiet.enabled is True
        finally:
            quiet.enabled = False

    def test_restored_even_when_the_test_blows_up(self):
        quiet = get_quiet_hours()
        quiet.enabled = True
        try:
            with pytest.raises(RuntimeError):
                with quiet_hours_suspended():
                    raise RuntimeError("division failed")
            assert quiet.enabled is True
        finally:
            quiet.enabled = False

    def test_a_league_without_the_window_stays_without_it(self):
        quiet = get_quiet_hours()
        quiet.enabled = False
        with quiet_hours_suspended():
            assert quiet.enabled is False
        assert quiet.enabled is False

    def test_a_suspended_window_reports_no_quiet_time(self):
        window = QuietHours(
            {
                "enabled": True,
                "timezone": "Europe/Lisbon",
                "pause_at": "23:00",
                "resume_at": "09:00",
            }
        )
        assert window.is_quiet() in (True, False)
        window.enabled = False
        assert window.is_quiet() is False
