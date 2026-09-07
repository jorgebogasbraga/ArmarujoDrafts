"""
Resolve the picture used on pick embeds.

Order:
  1. PokeAPI official artwork (the large embed image)
  2. PokeAPI Showdown GIF, only when the cache already has a real URL
  3. PokémonDB artwork / HOME / Gen 1–5 animated GIF

PokémonDB URLs are probed before use so a 404 never blanks a Discord embed.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Iterable

import aiohttp

from config import Config

logger = logging.getLogger(__name__)

PDB_ART = "https://img.pokemondb.net/artwork/large/{slug}.jpg"
PDB_HOME = "https://img.pokemondb.net/sprites/home/normal/{slug}.png"
PDB_BW_GIF = "https://img.pokemondb.net/sprites/black-white/anim/normal/{slug}.gif"

BW_ANIM_MAX_DEX = 649
_USER_AGENT = "Mozilla/5.0 (compatible; ArmaDraftBot/1.0)"
_PROBE_TIMEOUT = aiohttp.ClientTimeout(total=3)

# PokeAPI default-form slugs → PokémonDB page slugs
_PDB_SLUG_OVERRIDES: dict[str, str] = {
    "tornadus-incarnate": "tornadus",
    "thundurus-incarnate": "thundurus",
    "landorus-incarnate": "landorus",
    "enamorus-incarnate": "enamorus",
    "deoxys-normal": "deoxys",
    "giratina-altered": "giratina",
    "shaymin-land": "shaymin",
    "keldeo-ordinary": "keldeo",
    "meloetta-aria": "meloetta",
    "mimikyu-disguised": "mimikyu",
    "toxtricity-amped": "toxtricity",
    "eiscue-ice": "eiscue",
    "maushold-family-of-three": "maushold",
    "dudunsparce-two-segment": "dudunsparce",
    "squawkabilly-green-plumage": "squawkabilly",
    "tatsugiri-curly": "tatsugiri",
    "oricorio-baile": "oricorio",
    "lycanroc-midday": "lycanroc",
    "morpeko-full-belly": "morpeko",
    "basculin-red-striped": "basculin",
    "minior-red-meteor": "minior",
    "calyrex-ice": "calyrex-ice-rider",
    "calyrex-shadow": "calyrex-shadow-rider",
    "tauros-paldea-combat-breed": "tauros-paldean-combat",
    "tauros-paldea-blaze-breed": "tauros-paldean-blaze",
    "tauros-paldea-aqua-breed": "tauros-paldean-aqua",
    "darmanitan-galar-standard": "darmanitan-galarian",
    "darmanitan-galar": "darmanitan-galarian",
    "meowstic-male-mega": "meowstic-mega",
    "meowstic-female-mega": "meowstic-mega",
}

_NO_BW_ANIM_PARTS = {
    "mega",
    "alola",
    "alolan",
    "galar",
    "galarian",
    "hisui",
    "hisuian",
    "paldea",
    "paldean",
    "primal",
    "eternal",
    "crowned",
}

_probe_cache: dict[str, bool] | None = None


def _probe_path() -> str:
    return os.path.join(Config.DATA_DIR, "image_url_probe.json")


def pokemondb_slug(api_slug: str) -> str:
    """Map a PokeAPI slug onto PokémonDB's filename convention."""
    slug = (api_slug or "").strip().lower()
    if not slug:
        return ""
    if slug in _PDB_SLUG_OVERRIDES:
        return _PDB_SLUG_OVERRIDES[slug]
    slug = slug.replace("-breed", "").replace("-mask", "")
    replacements = (
        ("-alolan", "-alolan"),
        ("-alola", "-alolan"),
        ("-galarian", "-galarian"),
        ("-galar", "-galarian"),
        ("-hisuian", "-hisuian"),
        ("-hisui", "-hisuian"),
        ("-paldean", "-paldean"),
        ("-paldea", "-paldean"),
    )
    # Apply the longest regional suffix that is not already the PDB form.
    for src, dest in replacements:
        if src == dest:
            continue
        if src in slug and dest not in slug:
            slug = slug.replace(src, dest)
            break
    return slug


def should_try_bw_gif(pokedex_id: int, pdb_slug: str) -> bool:
    """Black/White animated GIFs exist for Gen 1–5 base (and a few in-game) forms."""
    if not pdb_slug:
        return False
    parts = set(pdb_slug.split("-"))
    if parts & _NO_BW_ANIM_PARTS:
        return False
    if pokedex_id > BW_ANIM_MAX_DEX:
        return False
    if pokedex_id <= 0:
        return "-" not in pdb_slug
    return True


def pokemondb_gif_url(pdb_slug: str) -> str:
    return PDB_BW_GIF.format(slug=pdb_slug)


def pokemondb_artwork_url(pdb_slug: str) -> str:
    return PDB_ART.format(slug=pdb_slug)


def pokemondb_home_url(pdb_slug: str) -> str:
    return PDB_HOME.format(slug=pdb_slug)


def pokemondb_candidates(api_slug: str, pokedex_id: int = 0) -> list[str]:
    """PokémonDB fallback: large static first, then the Gen 1–5 GIF if any."""
    slug = pokemondb_slug(api_slug)
    if not slug:
        return []
    urls: list[str] = [
        pokemondb_artwork_url(slug),
        pokemondb_home_url(slug),
    ]
    if should_try_bw_gif(pokedex_id, slug):
        urls.append(pokemondb_gif_url(slug))
    return urls


def pokeapi_candidates(showdown_gif: str = "", pokeapi_static: str = "") -> list[str]:
    urls: list[str] = []
    static = (pokeapi_static or "").strip()
    gif = (showdown_gif or "").strip()
    if static:
        urls.append(static)
    if gif and gif not in urls:
        urls.append(gif)
    return urls


def all_embed_candidates(
    api_slug: str,
    pokedex_id: int = 0,
    *,
    showdown_gif: str = "",
    pokeapi_static: str = "",
) -> list[str]:
    seen: list[str] = []
    for url in (
        *pokeapi_candidates(showdown_gif, pokeapi_static),
        *pokemondb_candidates(api_slug, pokedex_id),
    ):
        if url and url not in seen:
            seen.append(url)
    return seen


def _load_probe_cache() -> dict[str, bool]:
    global _probe_cache
    if _probe_cache is not None:
        return _probe_cache
    _probe_cache = {}
    path = _probe_path()
    if os.path.exists(path):
        try:
            raw = json.loads(open(path, encoding="utf-8").read())
            if isinstance(raw, dict):
                _probe_cache = {str(k): bool(v) for k, v in raw.items()}
        except (json.JSONDecodeError, OSError):
            _probe_cache = {}
    return _probe_cache


def _save_probe_cache() -> None:
    if _probe_cache is None:
        return
    path = _probe_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_probe_cache, f, ensure_ascii=False)
        os.replace(tmp, path)
    except OSError as e:
        logger.warning("[PokemonImages] Could not save URL probe cache: %s", e)


async def url_is_reachable(
    url: str,
    session: aiohttp.ClientSession | None = None,
) -> bool:
    """HEAD/GET the URL once and remember whether Discord could embed it."""
    if not url:
        return False
    cache = _load_probe_cache()
    if url in cache:
        return cache[url]

    headers = {"User-Agent": _USER_AGENT, "Range": "bytes=0-64"}
    own_session = session is None
    if own_session:
        session = aiohttp.ClientSession()
    ok = False
    try:
        async with session.get(
            url, timeout=_PROBE_TIMEOUT, allow_redirects=True, headers=headers
        ) as resp:
            if resp.status in (200, 206):
                ok = True
            elif resp.status in (403, 416):
                async with session.get(
                    url,
                    timeout=_PROBE_TIMEOUT,
                    allow_redirects=True,
                    headers={"User-Agent": _USER_AGENT},
                ) as retry:
                    ok = retry.status == 200
    except Exception:
        ok = False
    finally:
        if own_session:
            await session.close()

    cache[url] = ok
    _save_probe_cache()
    return ok


async def first_reachable(
    urls: Iterable[str],
    session: aiohttp.ClientSession | None = None,
) -> str:
    for url in urls:
        if await url_is_reachable(url, session=session):
            return url
    return ""
