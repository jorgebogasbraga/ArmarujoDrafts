"""
AliasManager — learns and persists Pokémon name aliases.

Examples of what it handles:
  "waterpon"    → "Ogerpon-Wellspring"
  "watershifu"  → "Urshifu-Rapid-Strike"
  "banded ttar" → "Tyranitar"
  "gholdy"      → "Gholdengo"

Two layers:
  1. Static special_cases dict — hardcoded well-known nicknames.
  2. Dynamic learned_aliases — persisted to data/aliases.json after confirmed matches.

When a fuzzy match above the confidence threshold is made, it's saved automatically
so the same alias never needs fuzzy matching again.
"""

import json
import os
import logging
from thefuzz import fuzz, process

logger = logging.getLogger(__name__)

# Well-known competitive aliases — extend as needed
STATIC_ALIASES: dict[str, str] = {
    # Ogerpon forms
    "waterpon":          "Ogerpon-Wellspring",
    "firepon":           "Ogerpon-Hearthflame",
    "rockpon":           "Ogerpon-Cornerstone",
    "grasspon":          "Ogerpon",
    # Urshifu forms
    "watershifu":        "Urshifu-Rapid-Strike",
    "darkshifu":         "Urshifu",
    "singlestrike":      "Urshifu",
    "rapidstrike":       "Urshifu-Rapid-Strike",
    # Calyrex forms
    "shadowrider":       "Calyrex-Shadow",
    "icerider":          "Calyrex-Ice",
    "shadow calyrex":    "Calyrex-Shadow",
    "ice calyrex":       "Calyrex-Ice",
    # Landorus forms
    "lando":             "Landorus-Therian",
    "landot":            "Landorus-Therian",
    "landoi":            "Landorus",
    # Tornadus / Thundurus
    "tornt":             "Tornadus-Therian",
    "thundt":            "Thundurus-Therian",
    # Common nicknames
    "chomp":             "Garchomp",
    "ttar":              "Tyranitar",
    "tran":              "Heatran",
    "pert":              "Swampert",
    "ferro":             "Ferrothorn",
    "toge":              "Togekiss",
    "amoong":            "Amoonguss",
    "clef":              "Clefable",
    "pult":              "Dragapult",
    "mushi":             "Musharna",
    "gholdy":            "Gholdengo",
    "gholdengo":         "Gholdengo",
    "kingamb":           "Kingambit",
    "chien pao":         "Chien-Pao",
    "wo chien":          "Wo-Chien",
    "chi yu":            "Chi-Yu",
    "ting lu":           "Ting-Lu",
    "great tusk":        "Great Tusk",
    "iron hands":        "Iron Hands",
    "iron bundle":       "Iron Bundle",
    "flutter mane":      "Flutter Mane",
    "sandy shocks":      "Sandy Shocks",
    "roaring moon":      "Roaring Moon",
    "iron valiant":      "Iron Valiant",
    "walking wake":      "Walking Wake",
    "iron leaves":       "Iron Leaves",
    "gouging fire":      "Gouging Fire",
    "raging bolt":       "Raging Bolt",
    "iron crown":        "Iron Crown",
    "iron boulder":      "Iron Boulder",
    "terapagos":         "Terapagos",
    "pecharunt":         "Pecharunt",
    "okidogi":           "Okidogi",
    "munkidori":         "Munkidori",
    "fezandipiti":       "Fezandipiti",
}

FUZZY_CONFIDENCE_THRESHOLD = 80   # 0–100; matches below this are rejected
LEARN_THRESHOLD = 85              # Above this we auto-learn and persist

# Incarnate is the unsuffixed sheet name. -T / -Therian is the other form.
THERIAN_SPECIES = frozenset({"landorus", "tornadus", "thundurus", "enamorus"})
_FORM_NICKNAMES: dict[str, tuple[str, str]] = {
    # species, "default" (unsuffixed) | "alt" (the other sheet form)
    "lando": ("landorus", "alt"),
    "landot": ("landorus", "alt"),
    "landoi": ("landorus", "default"),
    "tornt": ("tornadus", "alt"),
    "thundt": ("thundurus", "alt"),
    "enamot": ("enamorus", "alt"),
    "watershifu": ("urshifu", "alt"),
    "darkshifu": ("urshifu", "default"),
    "singlestrike": ("urshifu", "default"),
    "rapidstrike": ("urshifu", "alt"),
}
_ALT_SUFFIXES = frozenset({
    "t", "therian",
    "rs", "rapid-strike", "rapidstrike", "rapid",
})
_DEFAULT_SUFFIXES = frozenset({
    "incarnate", "i",
    "ss", "single-strike", "singlestrike", "single",
})


class AliasManager:
    def __init__(self, data_dir: str = "data") -> None:
        self.data_dir = data_dir
        self.aliases_path = os.path.join(data_dir, "aliases.json")
        self._learned: dict[str, str] = {}   # alias.lower() → canonical_name
        self._load()

    # ── Public API ──────────────────────────────────────────────────────────

    def resolve(self, raw_input: str, valid_names: list[str]) -> tuple[str | None, bool]:
        """
        Try to resolve a raw user input to a canonical Pokémon name.

        Returns:
            (canonical_name, was_fuzzy)
            canonical_name is None if confidence is too low.
            was_fuzzy=True means a fuzzy match was used (bot should confirm with user).
        """
        cleaned = raw_input.strip().lower()

        # 1. Exact match in pool (fastest path)
        for name in valid_names:
            if name.lower() == cleaned:
                return name, False

        # 1b. Draft shorthand from the pool: unsuffixed = default form,
        #     -T/-Therian/-RS/-SS = the other sheet name.
        form_match = self.resolve_form_shorthand(raw_input, valid_names)
        if form_match:
            return form_match, False

        # 2. Static hardcoded aliases
        if cleaned in STATIC_ALIASES:
            canonical = STATIC_ALIASES[cleaned]
            if canonical in valid_names:
                return canonical, False

        # 3. Learned dynamic aliases — case-insensitive match against pool
        if cleaned in self._learned:
            raw_canonical = self._learned[cleaned]
            # Find the properly-cased name in the pool
            canonical_match = next(
                (n for n in valid_names if n.lower() == raw_canonical.lower()),
                None,
            )
            if canonical_match:
                return canonical_match, False

        # 4. Fuzzy match against all valid names
        result = process.extractOne(
            cleaned,
            [n.lower() for n in valid_names],
            scorer=fuzz.token_sort_ratio,
        )
        if result is None:
            return None, False

        matched_lower, score = result[0], result[1]

        if score < FUZZY_CONFIDENCE_THRESHOLD:
            return None, False

        # Find the properly-cased name
        canonical = next(n for n in valid_names if n.lower() == matched_lower)

        # Auto-learn if confident enough
        if score >= LEARN_THRESHOLD and cleaned != matched_lower:
            self.learn(cleaned, canonical)

        return canonical, True

    def learn(self, alias: str, canonical: str) -> None:
        alias = alias.lower().strip()
        # Store canonical in lowercase — resolve() does case-insensitive match
        canonical_lower = canonical.lower().strip()
        if alias not in self._learned or self._learned[alias] != canonical_lower:
            self._learned[alias] = canonical_lower
            self._save()
            logger.info(f"[AliasManager] Learned: '{alias}' → '{canonical_lower}'")

    def forget(self, alias: str) -> bool:
        """Remove a learned alias (admin command)."""
        alias = alias.lower().strip()
        if alias in self._learned:
            del self._learned[alias]
            self._save()
            return True
        return False

    def list_learned(self) -> dict[str, str]:
        return dict(self._learned)
    
    def find_related_forms(self, canonical_name: str, valid_names: list[str]) -> list[str]:
        """
        Return all Pokémon names in valid_names that share the same base
        species as canonical_name (e.g. "Ogerpon-Wellspring" → also finds
        "Ogerpon", "Ogerpon-Cornerstone", "Ogerpon-Hearthflame").
        Returns an empty list if there's only one form (no ambiguity).
        """
        base = canonical_name.split("-")[0].split(" ")[0].lower()
        related = [
            n for n in valid_names
            if n.split("-")[0].split(" ")[0].lower() == base
        ]
        return related if len(related) > 1 else []

    @staticmethod
    def _norm_form_name(name: str) -> str:
        return name.strip().lower().replace(" ", "-")

    def resolve_therian_name(self, raw_input: str, valid_names: list[str]) -> str | None:
        return self.resolve_form_shorthand(raw_input, valid_names)

    def resolve_form_shorthand(self, raw_input: str, valid_names: list[str]) -> str | None:
        """
        Map draft shorthand onto the actual sheet name.

        Unsuffixed sheet names are the default pick:
          Landorus → Incarnate, Urshifu → Single Strike (dark)
        Explicit suffixes pick the other form:
          Landorus-T / -Therian, Urshifu-RS / -RapidStrike
        """
        cleaned = self._norm_form_name(raw_input)
        species: str | None = None
        want_alt = False

        if cleaned in _FORM_NICKNAMES:
            species, kind = _FORM_NICKNAMES[cleaned]
            want_alt = kind == "alt"
        elif "-" in cleaned:
            species, suffix = cleaned.split("-", 1)
            if suffix in _ALT_SUFFIXES:
                want_alt = True
            elif suffix in _DEFAULT_SUFFIXES:
                want_alt = False
            else:
                return None
        else:
            return None

        default_name, alt_name = self._default_and_alt_form(species, valid_names)
        return alt_name if want_alt else default_name

    def _default_and_alt_form(
        self, species: str, valid_names: list[str]
    ) -> tuple[str | None, str | None]:
        default_name: str | None = None
        alt_name: str | None = None
        prefix = species + "-"
        for name in valid_names:
            n = self._norm_form_name(name)
            if n == species or n == f"{species}-incarnate":
                default_name = name
            elif n.startswith(prefix) and alt_name is None:
                alt_name = name
        return default_name, alt_name

    def is_default_form_pair(self, related: list[str]) -> bool:
        """
        Two sheet names, same species: unsuffixed default + one explicit form.

        Examples: Landorus / Landorus-T, Urshifu / Urshifu-RS, Zacian / Zacian-Crowned.
        Families with 3+ formes (Ogerpon, Deoxys, Rotom) stay ambiguous.
        """
        if len(related) != 2:
            return False
        norms = [self._norm_form_name(name) for name in related]
        bases = {n.split("-")[0] for n in norms}
        if len(bases) != 1:
            return False
        base = next(iter(bases))
        suffixes = [n[len(base):].lstrip("-") for n in norms]
        has_default = "" in suffixes or "incarnate" in suffixes
        has_alt = any(suffix not in ("", "incarnate") for suffix in suffixes)
        return has_default and has_alt

    def is_therian_incarnate_pair(self, related: list[str]) -> bool:
        """True when related names are only Incarnate + Therian of one forces-of-nature species."""
        if not self.is_default_form_pair(related):
            return False
        base = self._norm_form_name(related[0]).split("-")[0]
        return base in THERIAN_SPECIES

    # ── Persistence ─────────────────────────────────────────────────────────

    def _load(self) -> None:
        os.makedirs(self.data_dir, exist_ok=True)
        if os.path.exists(self.aliases_path):
            try:
                with open(self.aliases_path, "r", encoding="utf-8") as f:
                    self._learned = json.load(f)
                logger.info(f"[AliasManager] Loaded {len(self._learned)} learned aliases.")
            except (json.JSONDecodeError, OSError) as e:
                logger.error(f"[AliasManager] Failed to load aliases: {e}")
                self._learned = {}

    def _save(self) -> None:
        try:
            with open(self.aliases_path, "w", encoding="utf-8") as f:
                json.dump(self._learned, f, indent=2, ensure_ascii=False)
        except OSError as e:
            logger.error(f"[AliasManager] Failed to save aliases: {e}")
