"""Pick / makeup / bank announcement layout."""

from models.draft_state import PickRecord
from services.embed_service import EmbedService
from services.public_messages import pick_lead_in
from tests.helpers import make_pokemon, make_state


def _record(**kwargs) -> PickRecord:
    defaults = dict(
        pick_number=12,
        round_number=3,
        coach_discord_id="1",
        coach_name="Coach1",
        pokemon_name="Gengar",
        points_cost=14,
    )
    defaults.update(kwargs)
    return PickRecord(**defaults)


def test_regular_pick_footer_is_just_division_and_league(monkeypatch):
    monkeypatch.setattr(
        "services.embed_service.league_name", lambda: "Tuga Champions League"
    )
    state = make_state()
    pokemon = make_pokemon("Gengar", 14, types=["ghost"])
    next_coach = state.coaches[1]
    embed = EmbedService.pick_card(
        state, state.coaches[0], pokemon, _record(), next_coach, locale="pt"
    )
    assert embed.footer.text == "TestDivision · Tuga Champions League"
    assert "A seguir" not in (embed.footer.text or "")
    names = [f.name for f in embed.fields]
    assert "A seguir" in names
    assert embed.fields[names.index("A seguir")].value == next_coach.name
    assert not getattr(embed.author, "name", None)
    assert embed.title == "Pick 12: Gengar"


def test_makeup_pick_is_visibly_different():
    state = make_state()
    pokemon = make_pokemon("Gengar", 14, types=["ghost"])
    record = _record(is_makeup=True, round_number=3)
    embed = EmbedService.pick_card(
        state, state.coaches[0], pokemon, record, None, locale="pt"
    )
    assert embed.title == "Makeup · Ronda 3: Gengar"
    assert "makeup da ronda 3" in embed.description
    assert any(f.name == "Ronda em dívida" for f in embed.fields)
    assert "Makeup" in pick_lead_in(record)


def test_bank_pick_uses_bank_author_and_lead_in():
    state = make_state()
    pokemon = make_pokemon("Gengar", 14, types=["ghost"])
    record = _record(is_bank=True)
    embed = EmbedService.pick_card(
        state, state.coaches[0], pokemon, record, state.coaches[1], locale="pt"
    )
    assert embed.title == "Auto-bank: Gengar"
    assert embed.author.name == "Pick Bank"
    assert pick_lead_in(record)
    assert "Makeup" not in pick_lead_in(record)


def test_staff_pick_sets_author_to_staff():
    state = make_state()
    pokemon = make_pokemon("Gengar", 14, types=["ghost"])
    record = _record(picked_by_discord_id="999")
    embed = EmbedService.pick_card(
        state, state.coaches[0], pokemon, record, None, locale="pt"
    )
    assert embed.author.name == "Staff"


def test_team_card_has_no_type_emoji():
    state = make_state()
    coach = state.coaches[0]
    coach.team = [make_pokemon("Gengar", 14, types=["ghost"])]
    embed = EmbedService.team_card(coach, state, locale="pt")
    team_field = next(f for f in embed.fields if f.name.startswith("Equipa"))
    assert "👻" not in team_field.value
    assert "**Gengar** — 14 pts" in team_field.value
    assert embed.title == coach.team_name
