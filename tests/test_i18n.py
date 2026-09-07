"""Tests for per-user internationalisation."""

import json
from pathlib import Path

from utils.i18n import I18n


class TestI18n:
    def test_default_english(self, tmp_path):
        locales = tmp_path / "locales"
        locales.mkdir()
        (locales / "en.json").write_text(
            json.dumps({"pick.fuzzy_confirm": "Did you mean {name}?"}),
            encoding="utf-8",
        )
        prefs = tmp_path / "user_locales.json"
        i18n = I18n(locales_dir=locales, preferences_path=prefs)
        assert i18n.t("123", "pick.fuzzy_confirm", name="Gengar") == "Did you mean Gengar?"

    def test_user_locale_preference(self, tmp_path):
        locales = tmp_path / "locales"
        locales.mkdir()
        (locales / "en.json").write_text(json.dumps({"greet": "Hello"}), encoding="utf-8")
        (locales / "pt.json").write_text(json.dumps({"greet": "Olá"}), encoding="utf-8")
        prefs = tmp_path / "user_locales.json"
        i18n = I18n(locales_dir=locales, preferences_path=prefs)
        success, _ = i18n.set_user_locale("42", "pt")
        assert success
        assert i18n.t("42", "greet") == "Olá"
        assert i18n.t("99", "greet") == "Hello"

    def test_fallback_to_english(self, tmp_path):
        locales = tmp_path / "locales"
        locales.mkdir()
        (locales / "en.json").write_text(json.dumps({"key": "English"}), encoding="utf-8")
        (locales / "es.json").write_text(json.dumps({}), encoding="utf-8")
        i18n = I18n(locales_dir=locales, preferences_path=tmp_path / "prefs.json")
        success, _ = i18n.set_user_locale("1", "es")
        assert success
        assert i18n.t("1", "key") == "English"

    def test_t_multi(self, tmp_path):
        locales = tmp_path / "locales"
        locales.mkdir()
        (locales / "en.json").write_text(
            json.dumps({"pick.public": "{pokemon} by {coach}"}), encoding="utf-8"
        )
        (locales / "pt.json").write_text(
            json.dumps({"pick.public": "{pokemon} por {coach}"}), encoding="utf-8"
        )
        (locales / "es.json").write_text(
            json.dumps({"pick.public": "{pokemon} de {coach}"}), encoding="utf-8"
        )
        i18n = I18n(locales_dir=locales, preferences_path=tmp_path / "prefs.json")
        block = i18n.t_multi("pick.public", pokemon="Gengar", coach="Ash")
        assert "Gengar" in block
        assert "por" in block
        assert block.count("\n") == 2
