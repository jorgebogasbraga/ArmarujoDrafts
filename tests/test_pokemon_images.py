"""PokeAPI-first embed URLs, with PokémonDB as fallback."""

from services.pokemon_images import (
    all_embed_candidates,
    pokemondb_candidates,
    pokemondb_slug,
    should_try_bw_gif,
)
from services.pokemon_service import expand_slug_candidates, normalise_slug


def test_pokemondb_regional_and_breed_slugs():
    assert pokemondb_slug("raichu-alola") == "raichu-alolan"
    assert pokemondb_slug("lilligant-hisui") == "lilligant-hisuian"
    assert pokemondb_slug("tauros-paldea-blaze-breed") == "tauros-paldean-blaze"
    assert pokemondb_slug("wooper-paldea") == "wooper-paldean"
    assert pokemondb_slug("calyrex-ice") == "calyrex-ice-rider"


def test_pokemondb_fallback_prefers_large_static():
    urls = pokemondb_candidates("mamoswine", 473)
    assert urls[0].endswith("/mamoswine.jpg")
    assert any("black-white/anim" in u and u.endswith("/mamoswine.gif") for u in urls)


def test_failed_list_has_working_pokemondb_fallbacks():
    assert any(u.endswith("/politoed.gif") for u in pokemondb_candidates("politoed", 186))
    assert any(u.endswith("/garchomp.gif") for u in pokemondb_candidates("garchomp", 445))
    assert any(u.endswith("/clefable.gif") for u in pokemondb_candidates("clefable", 36))
    assert any(u.endswith("/staraptor.gif") for u in pokemondb_candidates("staraptor", 398))
    assert any(
        u.endswith("/manectric-mega.jpg")
        for u in pokemondb_candidates("manectric-mega", 310)
    )
    assert any(
        u.endswith("/lopunny-mega.jpg")
        for u in pokemondb_candidates("lopunny-mega", 428)
    )
    assert any(
        u.endswith("/sylveon.jpg") for u in pokemondb_candidates("sylveon", 700)
    )
    assert any(
        u.endswith("/meowstic-male.jpg")
        for u in pokemondb_candidates("meowstic-male", 678)
    )


def test_gen6_and_megas_do_not_invent_a_bw_gif():
    assert not should_try_bw_gif(700, "sylveon")
    assert not should_try_bw_gif(310, "manectric-mega")
    assert not should_try_bw_gif(623, "golurk-mega")
    assert pokemondb_candidates("sylveon", 700)[0].endswith(".jpg")


def test_pokeapi_official_art_beats_gif_and_pokemondb():
    urls = all_embed_candidates(
        "mamoswine",
        473,
        showdown_gif="https://example.invalid/showdown/mamoswine.gif",
        pokeapi_static="https://example.invalid/art/473.png",
    )
    assert urls[0] == "https://example.invalid/art/473.png"
    assert urls[1] == "https://example.invalid/showdown/mamoswine.gif"
    assert urls[2].startswith("https://img.pokemondb.net/")


def test_showdown_gif_beats_pokemondb_when_there_is_no_official_art():
    urls = all_embed_candidates(
        "mamoswine",
        473,
        showdown_gif="https://example.invalid/showdown/mamoswine.gif",
        pokeapi_static="",
    )
    assert urls[0] == "https://example.invalid/showdown/mamoswine.gif"
    assert urls[1].startswith("https://img.pokemondb.net/")


def test_mega_meowstic_maps_to_the_male_mega_api_slug():
    assert normalise_slug("Mega Meowstic") == "meowstic-male-mega"
    assert "meowstic-male-mega" in expand_slug_candidates("meowstic-mega")
