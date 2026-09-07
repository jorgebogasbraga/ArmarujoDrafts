"""Tests for draft dry-run simulation helpers."""

import pytest

from simulation.mock_context import MockContext, MockResponse
from simulation.runner import SimulationRoles


class TestMockContext:
    def test_response_not_done_initially(self):
        resp = MockResponse()
        assert resp.is_done() is False

    @pytest.mark.asyncio
    async def test_defer_marks_response_done(self):
        class FakeChannel:
            id = 123
            sent = []

            async def send(self, **kwargs):
                self.sent.append(kwargs)

        channel = FakeChannel()
        ctx = MockContext(channel, guild=None, discord_id="1", display_name="Test")  # type: ignore[arg-type]
        await ctx.defer(ephemeral=True)
        assert ctx.response.is_done() is True

    @pytest.mark.asyncio
    async def test_followup_after_defer(self):
        class FakeChannel:
            id = 123
            sent = []

            async def send(self, **kwargs):
                self.sent.append(kwargs)

        channel = FakeChannel()
        ctx = MockContext(channel, guild=None, discord_id="1", display_name="Test")  # type: ignore[arg-type]
        await ctx.defer(ephemeral=True)
        await ctx.followup.send(embed=object(), ephemeral=True)
        assert len(channel.sent) == 0  # ephemeral suppressed

        await ctx.followup.send(content="public", ephemeral=False)
        assert len(channel.sent) == 1
        assert channel.sent[0]["content"] == "public"

    @pytest.mark.asyncio
    async def test_dismiss_thinking_after_defer(self):
        from utils.response_embeds import dismiss_thinking

        class FakeChannel:
            id = 123

            async def send(self, **kwargs):
                return None

        channel = FakeChannel()
        ctx = MockContext(channel, guild=None, discord_id="1", display_name="Test")  # type: ignore[arg-type]
        await ctx.defer(ephemeral=True)
        await dismiss_thinking(ctx)
        assert ctx.response.is_done() is True


class TestSimulationRoles:
    def test_small_division(self):
        roles = SimulationRoles.for_coach_count(4)
        assert roles.bank == 3
        assert roles.error == 3  # min(5, 3)

    def test_full_division(self):
        roles = SimulationRoles.for_coach_count(16)
        assert roles.bank == 3
        assert roles.error == 5
        assert roles.timer_skip == 7
        assert roles.force_skip == 9
        assert roles.skip3 == 11

    def test_skip3_falls_back_for_medium_divisions(self):
        roles = SimulationRoles.for_coach_count(10)
        assert roles.skip3 == 5


def test_select_valid_bank_lists_skips_team_species_conflict():
    from simulation.bank_helpers import select_valid_bank_priority_lists
    from tests.helpers import make_pokemon, make_state

    state = make_state(num_coaches=1, team_size=10, total_points=100)
    coach = state.coaches[0]
    defense = make_pokemon("Deoxys-Defense", 20, pokedex_id=386)
    speed = make_pokemon("Deoxys-Speed", 19, pokedex_id=386)
    gengar = make_pokemon("Gengar", 18, pokedex_id=94)
    typh = make_pokemon("Typhlosion", 17, pokedex_id=157)
    scizor = make_pokemon("Scizor", 16, pokedex_id=212)
    tusk = make_pokemon("Great Tusk", 15, pokedex_id=984)
    hydreigon = make_pokemon("Hydreigon", 14, pokedex_id=635)
    state.pokemon_pool = {
        p.name.lower(): p
        for p in (defense, speed, gengar, typh, scizor, tusk, hydreigon)
    }
    defense.is_drafted = True
    defense.drafted_by = coach.discord_id
    coach.add_pokemon(defense)

    r4, r6 = select_valid_bank_priority_lists(state, coach, names_per_round=3)
    names = r4 + r6
    assert "Deoxys-Speed" not in names
    assert "Deoxys-Defense" not in names
    assert names == ["Gengar", "Typhlosion", "Scizor", "Great Tusk", "Hydreigon"]
    assert len(set(names)) == len(names)
