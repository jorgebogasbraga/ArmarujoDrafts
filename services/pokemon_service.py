"""
PokemonService — Pokémon data from PokeAPI with local cache.

Slug resolution strategy (por ordem):
  1. Cache local → sem chamada à API
  2. Override estático → slug correcto directo
  3. Fetch directo com o slug normalizado
  4. Fuzzy match (difflib) contra lista completa de slugs da PokeAPI
  5. Sem resultado → Pokémon fica sem sprite/tipo (draft continua na mesma)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from difflib import get_close_matches
from typing import Optional

import aiohttp

from config import Config
from models.pokemon import Pokemon
from services.pokemon_images import all_embed_candidates, first_reachable

logger = logging.getLogger(__name__)

CACHE_PATH      = os.path.join(Config.DATA_DIR, "pokemon_cache.json")
SLUGS_LIST_PATH = os.path.join(Config.DATA_DIR, "api_slugs.json")

MAX_CONCURRENT_REQUESTS = 10
REQUEST_DELAY_SECONDS   = 0.05
FUZZY_CUTOFF            = 0.75   # score mínimo para aceitar um match

# ── Overrides estáticos (casos que o normaliser genérico não consegue) ────────
SLUG_SPECIFIC_OVERRIDES: dict[str, str] = {
    # Forças da Natureza — formas incarnate são o default na API
    "tornadus":        "tornadus-incarnate",
    "thundurus":       "thundurus-incarnate",
    "landorus":        "landorus-incarnate",
    "enamorus":        "enamorus-incarnate",
    "tornadus-t":      "tornadus-therian",
    "thundurus-t":     "thundurus-therian",
    "landorus-t":      "landorus-therian",
    "enamorus-t":      "enamorus-therian",
    # Pokémon com forma obrigatória
    "deoxys":          "deoxys-normal",
    "giratina":        "giratina-altered",
    "shaymin":         "shaymin-land",
    "keldeo":          "keldeo-ordinary",
    "meloetta":        "meloetta-aria",
    "mimikyu":         "mimikyu-disguised",
    "toxtricity":      "toxtricity-amped",
    "eiscue":          "eiscue-ice",
    "indeedee":        "indeedee-male",
    "meowstic":        "meowstic-male",
    "meowstic-mega":   "meowstic-male-mega",
    "mega-meowstic":   "meowstic-male-mega",
    "maushold":        "maushold-family-of-three",
    "dudunsparce":     "dudunsparce-two-segment",
    "palafin":         "palafin-hero",
    "oinkologne":      "oinkologne-male",
    "squawkabilly":    "squawkabilly-green-plumage",
    "tatsugiri":       "tatsugiri-curly",
    "oricorio":        "oricorio-baile",
    "lycanroc":        "lycanroc-midday",
    "morpeko":         "morpeko-full-belly",
    "basculin":        "basculin-red-striped",
    "basculegion":     "basculegion-male",
    "minior":          "minior-red-meteor",
    "pyroar":          "pyroar",             # API tem mesmo sem forma
    # Urshifu
    "urshifu":         "urshifu-single-strike",
    "urshifu-rs":      "urshifu-rapid-strike",
    # Calyrex fusions — sheet usa "-rider", API usa forma curta
    "calyrex-ice-rider":    "calyrex-ice",
    "calyrex-shadow-rider": "calyrex-shadow",
    # Necrozma fusions — sheet põe a fusão primeiro
    "dusk-mane-necrozma":  "necrozma-dusk-mane",
    "dawn-wings-necrozma": "necrozma-dawn-wings",
    "ultra-necrozma":      "necrozma-ultra",
    # Formas primals
    "primal-kyogre":   "kyogre-primal",
    "primal-groudon":  "groudon-primal",
    # Crowned — API não adiciona "-sword"/"-shield"
    "zacian-crowned":         "zacian-crowned",
    "zamazenta-crowned":      "zamazenta-crowned",
    "zacian-crowned-sword":   "zacian-crowned",
    "zamazenta-crowned-shield": "zamazenta-crowned",
    # Ogerpon — API usa sufixo "-mask"
    "ogerpon-wellspring":   "ogerpon-wellspring-mask",
    "ogerpon-hearthflame":  "ogerpon-hearthflame-mask",
    "ogerpon-cornerstone":  "ogerpon-cornerstone-mask",
    # Tauros Paldea — PokeAPI uses the `-breed` suffix
    "paldean-tauros":       "tauros-paldea-combat-breed",
    "paldean-tauros-fire":  "tauros-paldea-blaze-breed",
    "paldean-tauros-blaze": "tauros-paldea-blaze-breed",
    "paldean-tauros-water": "tauros-paldea-aqua-breed",
    "paldean-tauros-aqua":  "tauros-paldea-aqua-breed",
    "tauros-paldea":        "tauros-paldea-combat-breed",
    "tauros-paldea-combat": "tauros-paldea-combat-breed",
    "tauros-paldea-blaze":  "tauros-paldea-blaze-breed",
    "tauros-paldea-aqua":   "tauros-paldea-aqua-breed",
    "tauros-paldea-fire":   "tauros-paldea-blaze-breed",
    "tauros-paldea-water":  "tauros-paldea-aqua-breed",
    "paldean-wooper":       "wooper-paldea",
    # Ash-Greninja
    "eternal-floette": "floette-eternal",
    "floette-eternal": "floette-eternal",
    "ash-greninja":    "greninja-ash",
    "hisuian-liligant": "lilligant-hisui",
    "liligant-hisui":   "lilligant-hisui",
}

# Formas escritas como prefixo na sheet mas sufixo na PokeAPI:
# "hisuian-X" → "X-hisui", "mega-X" → "X-mega".
SLUG_PREFIX_MAP: dict[str, str] = {
    "hisuian-":  "-hisui",
    "galarian-": "-galar",
    "alolan-":   "-alola",
    "paldean-":  "-paldea",
    "mega-":     "-mega",
    "primal-":   "-primal",
    "eternal-":  "-eternal",
}

# A letra da variante fica sempre no fim: "Mega Charizard X" → charizard-mega-x.
_VARIANT_SUFFIXES = ("-x", "-y")


def move_prefix_to_suffix(slug: str, prefix: str, suffix: str) -> str:
    body = slug[len(prefix):]
    for variant in _VARIANT_SUFFIXES:
        if body.endswith(variant):
            return f"{body[:-len(variant)]}{suffix}{variant}"
    return f"{body}{suffix}"

# Do not fall back to national dex # for these — that returns the base form.
_FORM_SLUG_HINTS = (
    "-alola", "-galar", "-hisui", "-paldea",
    "-defense", "-attack", "-speed",
    "-dusk", "-midnight", "-midday",
    "-mask", "-breed", "-mega", "-primal", "-eternal",
    "-origin", "-therian", "-crowned",
    "-wellspring", "-hearthflame", "-cornerstone",
    "-paldea-combat", "-paldea-blaze", "-paldea-aqua",
)


def form_suffix(slug: str) -> str:
    """
    O marcador de forma do slug — '-mega' tanto em 'floette-mega' como em
    'charizard-mega-x'. Vazio quer dizer forma base.
    """
    body = slug
    for variant in _VARIANT_SUFFIXES:
        if body.endswith(variant):
            body = body[: -len(variant)]
            break
    matched = ""
    for hint in _FORM_SLUG_HINTS:
        if body.endswith(hint) and len(hint) > len(matched):
            matched = hint
    return matched


def normalise_slug(raw: str) -> str:
    slug = raw.strip().lower().replace(" ", "-").replace("'", "").replace(".", "")
    slug = slug.replace("liligant", "lilligant")
    if slug in SLUG_SPECIFIC_OVERRIDES:
        return SLUG_SPECIFIC_OVERRIDES[slug]
    for prefix, suffix in SLUG_PREFIX_MAP.items():
        if slug.startswith(prefix):
            slug = move_prefix_to_suffix(slug, prefix, suffix)
            break
    if slug in SLUG_SPECIFIC_OVERRIDES:
        return SLUG_SPECIFIC_OVERRIDES[slug]
    return slug


def expand_slug_candidates(slug: str) -> list[str]:
    """Extra PokeAPI slugs to try when the first lookup 404s."""
    candidates = [slug]
    if "paldea" in slug and not slug.endswith("-breed"):
        candidates.append(f"{slug}-breed")
        if slug == "tauros-paldea":
            candidates.append("tauros-paldea-combat-breed")
    if slug.startswith("ogerpon-") and slug != "ogerpon" and not slug.endswith("-mask"):
        candidates.append(f"{slug}-mask")
    if slug == "darmanitan-galar":
        candidates.append("darmanitan-galar-standard")
    if slug.endswith("-mega"):
        base = slug[: -len("-mega")]
        if base in {"meowstic", "indeedee", "basculegion", "oinkologne"}:
            candidates.append(f"{base}-male-mega")
            candidates.append(f"{base}-female-mega")
        if base == "tatsugiri":
            for form in ("curly", "droopy", "stretchy"):
                candidates.append(f"{base}-{form}-mega")
        if base == "magearna":
            candidates.append("magearna-original-mega")
    seen: list[str] = []
    for item in candidates:
        if item not in seen:
            seen.append(item)
    return seen


def sprite_url_from_api_data(data: dict) -> str:
    sprites = data.get("sprites") or {}
    official = (sprites.get("other") or {}).get("official-artwork") or {}
    home = (sprites.get("other") or {}).get("home") or {}
    showdown = (sprites.get("other") or {}).get("showdown") or {}
    # Official art first — the tiny front_default of new Megas is often a
    # placeholder, and Discord shows a blank embed when the chosen URL 404s.
    return (
        official.get("front_default")
        or home.get("front_default")
        or sprites.get("front_default")
        or showdown.get("front_default")
        or ""
    )


def showdown_gif_url_from_api_data(data: dict) -> str:
    sprites = data.get("sprites") or {}
    showdown = (sprites.get("other") or {}).get("showdown") or {}
    return (showdown.get("front_default") or "").strip()


def slim_api_data(data: dict) -> dict:
    """Keep only the PokeAPI fields used for embeds, types and stats."""
    if not isinstance(data, dict):
        return {}
    sprites = data.get("sprites") or {}
    other = sprites.get("other") or {}

    def _front(block: object) -> dict:
        if not isinstance(block, dict):
            return {}
        url = (block.get("front_default") or "").strip()
        return {"front_default": url} if url else {}

    slim_other: dict[str, dict] = {}
    for key in ("official-artwork", "home", "showdown"):
        front = _front(other.get(key) or {})
        if front:
            slim_other[key] = front
    slim_sprites: dict = {}
    if (sprites.get("front_default") or "").strip():
        slim_sprites["front_default"] = sprites["front_default"]
    if slim_other:
        slim_sprites["other"] = slim_other
    return {
        "id": data.get("id", 0),
        "types": data.get("types") or [],
        "stats": data.get("stats") or [],
        "sprites": slim_sprites,
    }


class PokemonService:
    def __init__(self) -> None:
        self._cache: dict[str, dict]  = {}
        self._api_slugs: list[str]    = []
        self._slug_map: dict[str, str] = {}  # sheet_slug → api_slug (aprendido em runtime)
        self._semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
        self._cache_lock = asyncio.Lock()
        self._enrich_lock = asyncio.Lock()
        self._load_cache()
        self._load_slug_list()

    # ── Public ────────────────────────────────────────────────────────────────

    async def enrich_pokemon(self, pokemon: Pokemon) -> Pokemon:
        if not pokemon.api_slug:
            pokemon.api_slug = normalise_slug(pokemon.name)

        # Verifica se já temos um mapeamento aprendido
        if pokemon.api_slug in self._slug_map:
            pokemon.api_slug = self._slug_map[pokemon.api_slug]

        data = await self._smart_fetch(pokemon.api_slug, species_dex=pokemon.pokedex_id)
        if data is None:
            return pokemon

        pokemon.api_form_id = data.get("id", 0)
        # Keep sheet pokedex_id as the draft conflict key — do not overwrite with API form id.
        pokemon.types = [t["type"]["name"] for t in data.get("types", [])]
        pokemon.sprite_url = sprite_url_from_api_data(data)
        pokemon.base_stats = {
            s["stat"]["name"]: s["base_stat"]
            for s in data.get("stats", [])
        }
        return pokemon

    async def enrich_all(self, pokemon_list: list[Pokemon]) -> list[Pokemon]:
        async with self._enrich_lock:
            await self._ensure_slug_list()

            tasks   = [self.enrich_pokemon(p) for p in pokemon_list]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            enriched = []
            failed   = 0
            for original, result in zip(pokemon_list, results):
                if isinstance(result, Exception):
                    logger.warning(f"[PokemonService] Erro ao enriquecer '{original.name}': {result}")
                    enriched.append(original)
                    failed += 1
                else:
                    enriched.append(result)

            await self._save_cache_async()
            logger.info(
                f"[PokemonService] Enriquecimento concluído: "
                f"{len(enriched) - failed} ok, {failed} sem dados."
            )
            return enriched

    async def refresh_cache(self) -> int:
        slugs     = list(self._cache.keys())
        refreshed = 0
        async with aiohttp.ClientSession() as session:
            for slug in slugs:
                data = await self._fetch_from_api(session, slug)
                if data:
                    self._cache[slug] = slim_api_data(data)
                    refreshed += 1
                await asyncio.sleep(0.2)
        await self._save_cache_async()
        return refreshed

    def get_sprite_url(self, api_slug: str) -> str:
        slug = self._slug_map.get(api_slug, api_slug)
        return sprite_url_from_api_data(self._cache.get(slug, {}))

    def get_showdown_gif_url(self, api_slug: str) -> str:
        slug = self._slug_map.get(api_slug, api_slug)
        return showdown_gif_url_from_api_data(self._cache.get(slug, {}))

    def embed_image_candidates(self, pokemon: Pokemon) -> list[str]:
        slug = pokemon.api_slug or normalise_slug(pokemon.name)
        if slug in self._slug_map:
            slug = self._slug_map[slug]
        return all_embed_candidates(
            slug,
            pokemon.pokedex_id,
            showdown_gif=self.get_showdown_gif_url(slug),
            pokeapi_static=(pokemon.sprite_url or self.get_sprite_url(slug) or "").strip(),
        )

    def image_for_embed(self, pokemon: Pokemon) -> str:
        """First constructed URL (no network). Prefer resolve_embed_image live."""
        candidates = self.embed_image_candidates(pokemon)
        return candidates[0] if candidates else ""

    async def resolve_embed_image(self, pokemon: Pokemon) -> str:
        """
        PokeAPI official art, then Showdown GIF, then PokémonDB.
        Unreachable URLs are skipped so the embed never blanks.
        """
        candidates = self.embed_image_candidates(pokemon)
        if not candidates:
            return ""
        return await first_reachable(candidates)

    async def resolve_sprite_url(self, species_name: str) -> str:
        """Return a display sprite URL for a species name (official art preferred)."""
        slug = normalise_slug(species_name)
        if slug in self._slug_map:
            slug = self._slug_map[slug]
        cached = self.get_sprite_url(slug)
        if cached:
            return cached
        data = await self._smart_fetch(slug)
        if data:
            return sprite_url_from_api_data(data)
        return ""

    # ── Smart fetch: cache → candidates → fuzzy ───────────────────────────────

    async def _smart_fetch(
        self, slug: str, species_dex: int = 0
    ) -> Optional[dict]:
        for candidate in expand_slug_candidates(slug):
            mapped = self._slug_map.get(candidate, candidate)
            cached = self._cache.get(mapped)
            if cached:
                if mapped != slug:
                    self._slug_map[slug] = mapped
                return cached

        async with self._semaphore:
            await asyncio.sleep(REQUEST_DELAY_SECONDS)

            async with aiohttp.ClientSession() as session:
                for candidate in expand_slug_candidates(slug):
                    data = await self._fetch_from_api(session, candidate)
                    if data:
                        data = slim_api_data(data)
                        self._cache[candidate] = data
                        if candidate != slug:
                            self._slug_map[slug] = candidate
                            logger.info(
                                "[PokemonService] '%s' → candidate '%s'",
                                slug,
                                candidate,
                            )
                        return data

                resolved = self._fuzzy_resolve(slug)
                if resolved and resolved != slug:
                    logger.info(f"[PokemonService] '{slug}' → fuzzy match '{resolved}'")
                    self._slug_map[slug] = resolved
                    data = await self._fetch_from_api(session, resolved)
                    if data:
                        data = slim_api_data(data)
                        self._cache[resolved] = data
                        return data

                is_form = any(hint in slug for hint in _FORM_SLUG_HINTS)
                if species_dex > 0 and not is_form:
                    data = await self._fetch_from_api(session, str(species_dex))
                    if data:
                        logger.info(
                            f"[PokemonService] '{slug}' → dex fallback #{species_dex}"
                        )
                        data = slim_api_data(data)
                        self._cache[slug] = data
                        return data

        logger.warning(f"[PokemonService] Sem resultado para '{slug}' — sem sprite/tipo.")
        return None

    def _fuzzy_resolve(self, slug: str) -> Optional[str]:
        """Usa difflib para encontrar o slug mais próximo na lista da PokeAPI."""
        if not self._api_slugs:
            return None
        wanted = form_suffix(slug)
        matches = get_close_matches(slug, self._api_slugs, n=5, cutoff=FUZZY_CUTOFF)
        for match in matches:
            # 'floette-mega' parece-se muito com 'floette', e a forma base seria
            # aceite sem se notar. Antes nenhum sprite do que o Pokémon errado.
            if form_suffix(match) == wanted:
                return match
            logger.info(
                "[PokemonService] Fuzzy '%s' → '%s' rejeitado: perde a forma '%s'.",
                slug,
                match,
                wanted or "(base)",
            )
        return None

    @staticmethod
    async def _fetch_from_api(
        session: aiohttp.ClientSession, slug: str
    ) -> Optional[dict]:
        url = f"{Config.POKEAPI_BASE}/pokemon/{slug}"
        try:
            async with session.get(
                url, timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                if resp.status == 200:
                    return await resp.json()
                if resp.status != 404:
                    logger.warning(f"[PokemonService] HTTP {resp.status} para '{slug}'")
                return None
        except asyncio.TimeoutError:
            logger.error(f"[PokemonService] Timeout: '{slug}'")
            return None
        except aiohttp.ClientError as e:
            logger.error(f"[PokemonService] Erro de rede '{slug}': {e}")
            return None

    # ── Slug list (fetched once, cached to disk) ──────────────────────────────

    async def _ensure_slug_list(self) -> None:
        if self._api_slugs:
            return
        try:
            async with aiohttp.ClientSession() as session:
                url = f"{Config.POKEAPI_BASE}/pokemon?limit=100000"
                async with session.get(
                    url, timeout=aiohttp.ClientTimeout(total=30)
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        self._api_slugs = [p["name"] for p in data.get("results", [])]
                        await asyncio.to_thread(self._save_slug_list)
                        logger.info(
                            f"[PokemonService] Lista de slugs obtida: "
                            f"{len(self._api_slugs)} entradas."
                        )
        except Exception as e:
            logger.error(f"[PokemonService] Falha ao obter lista de slugs: {e}")

    def _load_slug_list(self) -> None:
        if os.path.exists(SLUGS_LIST_PATH):
            try:
                with open(SLUGS_LIST_PATH, "r", encoding="utf-8") as f:
                    self._api_slugs = json.load(f)
                logger.info(
                    f"[PokemonService] Slugs carregados do disco: "
                    f"{len(self._api_slugs)} entradas."
                )
            except (json.JSONDecodeError, OSError):
                self._api_slugs = []

    def _save_slug_list(self) -> None:
        try:
            with open(SLUGS_LIST_PATH, "w", encoding="utf-8") as f:
                json.dump(self._api_slugs, f, ensure_ascii=False)
        except OSError as e:
            logger.error(f"[PokemonService] Falha ao guardar lista de slugs: {e}")

    # ── Cache I/O ─────────────────────────────────────────────────────────────

    def _load_cache(self) -> None:
        os.makedirs(Config.DATA_DIR, exist_ok=True)
        if os.path.exists(CACHE_PATH):
            try:
                size = os.path.getsize(CACHE_PATH)
                with open(CACHE_PATH, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                if not isinstance(raw, dict):
                    self._cache = {}
                    return
                self._cache = {
                    key: slim_api_data(value) if isinstance(value, dict) else {}
                    for key, value in raw.items()
                }
                logger.info(
                    "[PokemonService] Cache carregado: %s entradas.",
                    len(self._cache),
                )
                # Full PokeAPI blobs used to be ~100MB+ and blocked startup.
                if size > 1_000_000:
                    logger.info(
                        "[PokemonService] Cache era %.1f MB — a gravar versão reduzida.",
                        size / (1024 * 1024),
                    )
                    self._save_cache_sync()
            except (json.JSONDecodeError, OSError):
                self._cache = {}

    async def _save_cache_async(self) -> None:
        async with self._cache_lock:
            await asyncio.to_thread(self._save_cache_sync)

    def _save_cache_sync(self) -> None:
        import time
        tmp = CACHE_PATH + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._cache, f, ensure_ascii=False)
        except OSError as e:
            logger.error(f"[PokemonService] Falha ao escrever cache temp: {e}")
            return

        for attempt in range(8):
            try:
                os.replace(tmp, CACHE_PATH)
                return
            except OSError as e:
                if attempt == 7:
                    logger.error(f"[PokemonService] Falha ao guardar cache: {e}")
                    return
                time.sleep(0.05 * (attempt + 1))