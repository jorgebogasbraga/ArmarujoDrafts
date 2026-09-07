"""
League-wide settings loaded from league_config.json.

Holds the knobs that differ between leagues but are not tied to the Google
Sheets layout: which optional commands exist, whether Tera is part of the
format, and the nightly window where pick timers stop counting.

Point LEAGUE_CONFIG_FILE at another file to run a different league.
"""

from __future__ import annotations

import json
import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from config import Config

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Cogs that a draft-only league can leave out entirely. Names match main.py.
OPTIONAL_FEATURES = ("league", "match", "trade", "timezone", "alias", "simulation")


def resolve_league_config_path(path: Path | str | None = None) -> Path:
    candidate = Path(path or Config.LEAGUE_CONFIG_FILE)
    if candidate.is_absolute() or candidate.is_file():
        return candidate
    return PROJECT_ROOT / candidate


SUPPORTED_LOCALES = ("en", "pt", "es")


class LeagueSettings:
    def __init__(self, raw: dict[str, Any]) -> None:
        self.raw = raw

    # ── Identity ─────────────────────────────────────────────────────────────

    @property
    def name(self) -> str:
        """Shown in embed footers. Empty means no branding."""
        return str(self.raw.get("name") or "").strip()

    @property
    def default_locale(self) -> str:
        """
        Language of public channel embeds. Blank or unknown falls back to
        English; coaches can always read any embed in their own language.
        """
        value = str(self.raw.get("default_locale") or "").strip().lower()
        return value if value in SUPPORTED_LOCALES else "en"

    # ── Optional command groups ──────────────────────────────────────────────

    def feature_enabled(self, name: str) -> bool:
        """Optional features default to on so existing leagues keep every command."""
        features = self.raw.get("features", {})
        return bool(features.get(name, True))

    @property
    def show_tera(self) -> bool:
        """Tera fields are hidden in formats that have no Terastallisation."""
        return self.feature_enabled("tera")

    # ── Nightly timer window ─────────────────────────────────────────────────

    @property
    def quiet_hours(self) -> dict[str, Any]:
        return dict(self.raw.get("quiet_hours", {}))


def load_league_settings(path: Path | str | None = None) -> LeagueSettings:
    config_path = resolve_league_config_path(path)
    try:
        with open(config_path, encoding="utf-8") as f:
            return LeagueSettings(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.warning("[LeagueSettings] Using defaults (%s): %s", config_path, e)
        return LeagueSettings({})


@lru_cache(maxsize=1)
def get_league_settings() -> LeagueSettings:
    return load_league_settings()


def feature_enabled(name: str) -> bool:
    return get_league_settings().feature_enabled(name)


def show_tera() -> bool:
    return get_league_settings().show_tera


def league_name() -> str:
    return get_league_settings().name


def default_locale() -> str:
    """DEFAULT_LOCALE overrides the JSON, for a quick change without editing it."""
    override = os.getenv("DEFAULT_LOCALE", "").strip().lower()
    if override in SUPPORTED_LOCALES:
        return override
    return get_league_settings().default_locale
