"""Tests for offline alias profanity filtering."""

from __future__ import annotations

from pathlib import Path

import pytest

from utils import content_filter


@pytest.fixture(autouse=True)
def reset_filter():
    content_filter.reload_profanity_filter()
    yield
    content_filter.reload_profanity_filter()


class TestContentFilter:
    def test_allows_normal_alias(self):
        ok, reason = content_filter.alias_is_allowed("waterpon")
        assert ok is True
        assert reason is None

    def test_rejects_too_short(self):
        ok, reason = content_filter.alias_is_allowed("a")
        assert ok is False
        assert reason == "admin.alias_too_short"

    def test_rejects_too_long(self):
        ok, reason = content_filter.alias_is_allowed("x" * 41)
        assert ok is False
        assert reason == "admin.alias_too_long"

    def test_rejects_english_slur(self):
        ok, reason = content_filter.alias_is_allowed("nigger")
        assert ok is False
        assert reason == "admin.alias_blocked"

    def test_rejects_obfuscated_slur(self):
        ok, reason = content_filter.alias_is_allowed("n1gg3r")
        assert ok is False
        assert reason == "admin.alias_blocked"

    def test_custom_words_file(self, tmp_path, monkeypatch):
        custom = tmp_path / "profanity_custom.txt"
        custom.write_text("badcoachalias\n", encoding="utf-8")
        monkeypatch.setattr(content_filter, "_custom_words_path", lambda: Path(custom))
        content_filter.reload_profanity_filter()
        ok, reason = content_filter.alias_is_allowed("badcoachalias")
        assert ok is False
        assert reason == "admin.alias_blocked"
