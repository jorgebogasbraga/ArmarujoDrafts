"""The channel messages that explain the nightly window and deferred banks."""

import pytest

from services.embed_service import EmbedService
from tests.helpers import make_state

LOCALES = ("en", "pt", "es")
RESUME_TS = 1788678000  # a fixed 09:00 for stable rendering


def _all_text(embed) -> str:
    parts = [embed.title or "", embed.description or ""]
    for field in embed.fields:
        parts.append(field.name or "")
        parts.append(str(field.value or ""))
    return "\n".join(parts)


@pytest.fixture
def state():
    s = make_state()
    s.current_coach.set_pick_timer(7200.0)
    return s


class TestQuietHoursStart:
    @pytest.mark.parametrize("locale", LOCALES)
    def test_renders_in_every_locale(self, state, locale):
        embed = EmbedService.quiet_hours_start(
            state, state.current_coach, RESUME_TS, 3600, locale=locale
        )
        text = _all_text(embed)
        assert text.strip()
        assert "{" not in text, f"unformatted placeholder in {locale}: {text}"

    def test_names_the_coach_on_the_clock(self, state):
        embed = EmbedService.quiet_hours_start(
            state, state.current_coach, RESUME_TS, 3600, locale="pt"
        )
        assert state.current_coach.name in _all_text(embed)

    def test_shows_time_used_and_time_left(self, state):
        embed = EmbedService.quiet_hours_start(
            state, state.current_coach, RESUME_TS, 3600, locale="en"
        )
        text = _all_text(embed)
        assert "1h" in text   # used
        assert "2h" in text   # left

    def test_says_the_draft_is_still_open(self, state):
        embed = EmbedService.quiet_hours_start(
            state, state.current_coach, RESUME_TS, 3600, locale="en"
        )
        assert "/pick" in _all_text(embed)

    def test_points_at_the_resume_time(self, state):
        embed = EmbedService.quiet_hours_start(
            state, state.current_coach, RESUME_TS, 3600, locale="en"
        )
        assert f"<t:{RESUME_TS}:" in _all_text(embed)

    def test_survives_a_division_with_nobody_on_the_clock(self, state):
        embed = EmbedService.quiet_hours_start(state, None, RESUME_TS, 0, locale="en")
        assert embed.description


class TestQuietHoursEnd:
    @pytest.mark.parametrize("locale", LOCALES)
    def test_renders_in_every_locale(self, state, locale):
        embed = EmbedService.quiet_hours_end(
            state, state.current_coach, RESUME_TS, locale=locale
        )
        text = _all_text(embed)
        assert text.strip()
        assert "{" not in text, f"unformatted placeholder in {locale}: {text}"

    def test_pings_the_coach_with_their_new_deadline(self, state):
        embed = EmbedService.quiet_hours_end(
            state, state.current_coach, RESUME_TS, locale="en"
        )
        text = _all_text(embed)
        assert state.current_coach.mention() in text
        assert f"<t:{RESUME_TS}:" in text

    def test_no_deadline_field_without_a_deadline(self, state):
        embed = EmbedService.quiet_hours_end(
            state, state.current_coach, None, locale="en"
        )
        assert "<t:" not in _all_text(embed)


class TestDeferredBankSnipe:
    @pytest.mark.parametrize("locale", LOCALES)
    def test_public_deferred_notice_renders(self, state, locale):
        embed = EmbedService.bank_snipe_public(
            state.current_coach, state.division_name, 0,
            locale=locale, resume_ts=RESUME_TS,
        )
        text = _all_text(embed)
        assert "{" not in text, f"unformatted placeholder in {locale}: {text}"
        assert f"<t:{RESUME_TS}:" in text

    @pytest.mark.parametrize("locale", LOCALES)
    def test_dm_deferred_notice_renders(self, state, locale):
        embed = EmbedService.bank_snipe_dm(
            state.current_coach, "Landorus-Therian", 0, ["Urshifu"],
            locale=locale, resume_ts=RESUME_TS,
        )
        text = _all_text(embed)
        assert "{" not in text, f"unformatted placeholder in {locale}: {text}"
        assert "Urshifu" in text

    def test_normal_notice_still_counts_down_to_the_deadline(self, state):
        deadline = 1788600000
        embed = EmbedService.bank_snipe_public(
            state.current_coach, state.division_name, deadline, locale="en"
        )
        text = _all_text(embed)
        assert f"<t:{deadline}:R>" in text
        assert str(RESUME_TS) not in text
