"""Offline profanity / slur detection for user-provided alias strings."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from glin_profanity import Filter

from config import Config

logger = logging.getLogger(__name__)

# League-relevant languages (glin-profanity ships wordlists locally — no network calls).
_FILTER_LANGUAGES: tuple[str, ...] = (
    "english",
    "portuguese",
    "spanish",
    "french",
    "german",
    "italian",
    "dutch",
    "polish",
    "russian",
    "hindi",
    "turkish",
    "arabic",
    "japanese",
    "korean",
    "chinese",
    "czech",
    "danish",
    "finnish",
    "hungarian",
    "norwegian",
    "persian",
    "swedish",
    "thai",
)

_filter: Optional[Filter] = None


def _custom_words_path() -> Path:
    return Path(Config.DATA_DIR) / "profanity_custom.txt"


def _load_custom_words() -> list[str]:
    path = _custom_words_path()
    if not path.is_file():
        return []
    words: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        words.append(stripped.lower())
    return words


def get_profanity_filter() -> Filter:
    """Return the shared offline filter (lazy init)."""
    global _filter
    if _filter is None:
        custom = _load_custom_words()
        _filter = Filter(
            {
                "languages": list(_FILTER_LANGUAGES),
                "case_sensitive": False,
                "word_boundaries": True,
                "allow_obfuscated_match": True,
                "custom_words": custom,
            }
        )
        logger.info(
            "[ContentFilter] Loaded glin-profanity (%d languages, %d custom words)",
            len(_FILTER_LANGUAGES),
            len(custom),
        )
    return _filter


def reload_profanity_filter() -> None:
    """Rebuild filter after editing data/profanity_custom.txt."""
    global _filter
    _filter = None
    get_profanity_filter()


def alias_is_allowed(text: str) -> tuple[bool, str | None]:
    """Return (ok, reason_key) for alias_learn validation."""
    cleaned = text.strip()
    if len(cleaned) < 2:
        return False, "admin.alias_too_short"
    if len(cleaned) > 40:
        return False, "admin.alias_too_long"

    filt = get_profanity_filter()
    result = filt.check_profanity(cleaned)
    if result.get("contains_profanity") or filt.is_profane(cleaned):
        return False, "admin.alias_blocked"

    return True, None
