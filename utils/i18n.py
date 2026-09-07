"""
Per-user internationalisation.

- Ephemeral command replies: i18n.t(user_id, key)
- Public channel embeds: language tab buttons (EN/PT/ES) via LocaleEmbedView
- Ephemeral / DM messages: i18n.t(user_id, key) — user's /language locale
- Single-locale public fallback: i18n.t_locale(default_locale, key)
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

from config import Config
from utils.league_settings import default_locale as league_default_locale

logger = logging.getLogger(__name__)

LOCALES_DIR = Path(__file__).resolve().parent.parent / "locales"
# The league's own language, from league_config.json (DEFAULT_LOCALE overrides).
DEFAULT_LOCALE = league_default_locale()
FALLBACK_CHAIN = ("en",)

# Locales included in public multi-language blocks (order matters for display)
PUBLIC_LOCALES = ("en", "pt", "es")
LOCALE_FLAGS = {"en": "🇬🇧", "pt": "🇵🇹", "es": "🇪🇸"}


class I18n:
    def __init__(
        self,
        locales_dir: Path = LOCALES_DIR,
        default_locale: str = DEFAULT_LOCALE,
        preferences_path: Optional[Path] = None,
    ) -> None:
        self.locales_dir = locales_dir
        self.default_locale = default_locale
        self.preferences_path = preferences_path or (
            Path(Config.DATA_DIR) / "user_locales.json"
        )
        self._catalogues: dict[str, dict[str, str]] = {}
        self._user_locales: dict[str, str] = {}
        self._load_catalogues()
        self._load_preferences()

    def _load_catalogues(self) -> None:
        self._catalogues.clear()
        if not self.locales_dir.exists():
            logger.warning("[I18n] Locales directory not found: %s", self.locales_dir)
            return
        for path in self.locales_dir.glob("*.json"):
            locale = path.stem
            with open(path, encoding="utf-8") as f:
                self._catalogues[locale] = json.load(f)
        logger.info("[I18n] Loaded locales: %s", list(self._catalogues.keys()))

    def _load_preferences(self) -> None:
        if not self.preferences_path.exists():
            self._user_locales = {}
            return
        try:
            with open(self.preferences_path, encoding="utf-8") as f:
                self._user_locales = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.error("[I18n] Could not load user preferences: %s", e)
            self._user_locales = {}

    def _save_preferences(self) -> None:
        self.preferences_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.preferences_path, "w", encoding="utf-8") as f:
            json.dump(self._user_locales, f, indent=2, ensure_ascii=False)

    def available_locales(self) -> list[str]:
        return sorted(self._catalogues.keys())

    def locale_display_names(self) -> dict[str, str]:
        names = {}
        for locale in self.available_locales():
            names[locale] = self._lookup(locale, "meta.locale_name") or locale
        return names

    def get_user_locale(self, user_id: str | int) -> str:
        uid = str(user_id)
        locale = self._user_locales.get(uid, self.default_locale)
        if locale in self._catalogues:
            return locale
        return self.default_locale

    def set_user_locale(self, user_id: str | int, locale: str) -> tuple[bool, str]:
        if locale not in self._catalogues:
            available = ", ".join(self.available_locales())
            return False, self.t_locale(
                self.default_locale,
                "errors.unknown_locale",
                locale=locale,
                available=available,
            )
        self._user_locales[str(user_id)] = locale
        self._save_preferences()
        names = self.locale_display_names()
        display = names.get(locale, locale)
        return True, self.t(user_id, "language.updated", language=display)

    def _lookup(self, locale: str, key: str) -> Optional[str]:
        catalogue = self._catalogues.get(locale, {})
        if key in catalogue:
            return catalogue[key]
        for fallback in FALLBACK_CHAIN:
            if fallback == locale:
                continue
            fb = self._catalogues.get(fallback, {})
            if key in fb:
                return fb[key]
        return None

    def _format(self, template: str, key: str, **kwargs: Any) -> str:
        try:
            return template.format(**kwargs)
        except KeyError as e:
            logger.warning("[I18n] Missing placeholder %s in key '%s'", e, key)
            return template

    def t(self, user_id: str | int | None, key: str, **kwargs: Any) -> str:
        if user_id is not None:
            locale = self.get_user_locale(user_id)
        else:
            locale = self.default_locale
        return self.t_locale(locale, key, **kwargs)

    def t_locale(self, locale: str, key: str, **kwargs: Any) -> str:
        template = self._lookup(locale, key)
        if template is None:
            logger.warning("[I18n] Missing key '%s' for locale '%s'", key, locale)
            return key
        return self._format(template, key, **kwargs)

    def t_multi(self, key: str, **kwargs: Any) -> str:
        """Build a compact multi-language block for public channel messages."""
        lines: list[str] = []
        for loc in PUBLIC_LOCALES:
            if loc not in self._catalogues:
                continue
            template = self._lookup(loc, key)
            if not template:
                continue
            flag = LOCALE_FLAGS.get(loc, "")
            lines.append(f"{flag} {self._format(template, key, **kwargs)}")
        if not lines:
            return self.t_locale(self.default_locale, key, **kwargs)
        return "\n".join(lines)

    def embed_footer_hint(self, locale: str | None = None) -> str:
        loc = locale or self.default_locale
        return self.t_locale(loc, "meta.language_hint")


i18n = I18n()
