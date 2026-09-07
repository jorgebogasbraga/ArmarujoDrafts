"""Tests for PokeAPI slug normalisation of regional and alternate forms."""

import pytest

from services.pokemon_service import (
    expand_slug_candidates,
    form_suffix,
    normalise_slug,
    slim_api_data,
    sprite_url_from_api_data,
    showdown_gif_url_from_api_data,
)


def test_hisuian_and_paldean_slugs():
    assert normalise_slug("Hisuian Lilligant") == "lilligant-hisui"
    assert normalise_slug("Hisuian Liligant") == "lilligant-hisui"
    assert normalise_slug("Paldean Tauros") == "tauros-paldea-combat-breed"
    assert normalise_slug("Tauros-Paldea") == "tauros-paldea-combat-breed"
    assert normalise_slug("Tauros-Paldea-Aqua") == "tauros-paldea-aqua-breed"
    assert normalise_slug("Paldean Tauros Blaze") == "tauros-paldea-blaze-breed"
    assert normalise_slug("Tauros-Paldea-Blaze") == "tauros-paldea-blaze-breed"
    assert normalise_slug("Alolan Raichu") == "raichu-alola"
    assert normalise_slug("Galarian Darmanitan") == "darmanitan-galar"


def test_form_slugs():
    assert normalise_slug("Lycanroc-Dusk") == "lycanroc-dusk"
    assert normalise_slug("Deoxys-Defense") == "deoxys-defense"
    assert normalise_slug("Deoxys-Speed") == "deoxys-speed"
    assert normalise_slug("Ogerpon-Hearthflame") == "ogerpon-hearthflame-mask"
    assert normalise_slug("Iron Hands") == "iron-hands"


def test_expand_slug_candidates_adds_breed_and_mask():
    assert "tauros-paldea-aqua-breed" in expand_slug_candidates("tauros-paldea-aqua")
    assert "ogerpon-hearthflame-mask" in expand_slug_candidates("ogerpon-hearthflame")


class TestMegaSlugs:
    """The sheet writes 'Mega X'; PokeAPI wants 'x-mega'."""

    @pytest.mark.parametrize(
        "name, expected",
        [
            ("Mega Gengar", "gengar-mega"),
            ("Mega Floette", "floette-mega"),
            ("Mega Steelix", "steelix-mega"),
            ("Mega Falinks", "falinks-mega"),
        ],
    )
    def test_mega_moves_to_the_end(self, name, expected):
        assert normalise_slug(name) == expected

    @pytest.mark.parametrize(
        "name, expected",
        [
            ("Mega Charizard X", "charizard-mega-x"),
            ("Mega Charizard Y", "charizard-mega-y"),
            ("Mega Raichu X", "raichu-mega-x"),
            ("Mega Mewtwo Y", "mewtwo-mega-y"),
        ],
    )
    def test_variant_letter_stays_last(self, name, expected):
        assert normalise_slug(name) == expected

    def test_eternal_floette(self):
        assert normalise_slug("Eternal Floette") == "floette-eternal"
        assert normalise_slug("Floette-Eternal") == "floette-eternal"

    def test_primal_forms(self):
        assert normalise_slug("Primal Kyogre") == "kyogre-primal"
        assert normalise_slug("Primal Groudon") == "groudon-primal"

    def test_mega_meowstic_uses_the_gendered_api_slug(self):
        assert normalise_slug("Mega Meowstic") == "meowstic-male-mega"

    def test_plain_pokemon_are_untouched(self):
        assert normalise_slug("Meganium") == "meganium"
        assert normalise_slug("Yanmega") == "yanmega"
        assert normalise_slug("Primarina") == "primarina"


class TestFormSuffix:
    """Guards the fuzzy fallback against silently returning the base form."""

    def test_base_forms_have_no_suffix(self):
        assert form_suffix("floette") == ""
        assert form_suffix("charizard") == ""

    def test_mega_is_detected(self):
        assert form_suffix("floette-mega") == "-mega"

    def test_mega_is_detected_behind_a_variant_letter(self):
        assert form_suffix("charizard-mega-x") == "-mega"

    def test_regional_forms_are_detected(self):
        assert form_suffix("raichu-alola") == "-alola"
        assert form_suffix("lilligant-hisui") == "-hisui"

    def test_longest_marker_wins(self):
        assert form_suffix("tauros-paldea-aqua") == "-paldea-aqua"

    def test_a_mega_never_matches_its_base_form(self):
        assert form_suffix("floette-mega") != form_suffix("floette")


def test_official_art_beats_the_tiny_front_sprite():
    url = sprite_url_from_api_data(
        {
            "sprites": {
                "front_default": "https://example.invalid/front.png",
                "other": {
                    "official-artwork": {
                        "front_default": "https://example.invalid/art.png"
                    }
                },
            }
        }
    )
    assert url == "https://example.invalid/art.png"


def test_slim_api_data_keeps_embed_fields_and_drops_the_rest():
    fat = {
        "id": 473,
        "name": "mamoswine",
        "moves": [{"move": {"name": "earthquake"}}] * 80,
        "types": [{"slot": 1, "type": {"name": "ice", "url": "https://example.invalid/ice"}}],
        "stats": [{"base_stat": 110, "stat": {"name": "attack"}}],
        "sprites": {
            "front_default": "https://example.invalid/front.png",
            "back_default": "https://example.invalid/back.png",
            "other": {
                "official-artwork": {
                    "front_default": "https://example.invalid/art.png",
                    "front_shiny": "https://example.invalid/art-shiny.png",
                },
                "showdown": {"front_default": "https://example.invalid/sd.gif"},
                "dream_world": {"front_default": "https://example.invalid/dream.png"},
            },
        },
    }
    slim = slim_api_data(fat)
    assert slim["id"] == 473
    assert "moves" not in slim
    assert sprite_url_from_api_data(slim) == "https://example.invalid/art.png"
    assert showdown_gif_url_from_api_data(slim) == "https://example.invalid/sd.gif"
    assert "dream_world" not in (slim.get("sprites") or {}).get("other", {})
