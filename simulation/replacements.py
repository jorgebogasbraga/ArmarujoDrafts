"""
System test profile for the current league, from <LEAGUE_DIR>/system_test.json.

    {
      "divisions": ["Champions"],
      "replacements": {
        "Champions": {
          "name": "...", "team_name": "...", "discord_id": "...",
          "logo_url": "...", "timezone": "GMT"
        }
      }
    }

`divisions` defaults to every division in division_config.json. A division with
no replacement profile skips the replace phase and does its skips without
escalating to a replacement — useful for a draft-only league where you want to
exercise the timer ladder but never hand a team to somebody else.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from config import Config

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReplacementProfile:
    name: str
    team_name: str
    discord_id: str
    logo_url: str
    timezone: str


def _profile_from_raw(raw: dict[str, Any]) -> ReplacementProfile:
    return ReplacementProfile(
        name=str(raw.get("name") or ""),
        team_name=str(raw.get("team_name") or ""),
        discord_id=str(raw.get("discord_id") or ""),
        logo_url=str(raw.get("logo_url") or ""),
        timezone=str(raw.get("timezone") or "GMT"),
    )


@lru_cache(maxsize=1)
def _load() -> dict[str, Any]:
    path = Path(Config.SYSTEM_TEST_FILE)
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        logger.info("[SystemTest] No %s — testing every division, no replaces.", path)
        return {}
    except json.JSONDecodeError as e:
        logger.error("[SystemTest] %s is not valid JSON: %s", path, e)
        return {}


def system_test_divisions() -> tuple[str, ...]:
    """Divisions the system test runs against, in order."""
    configured = _load().get("divisions")
    if configured:
        return tuple(str(name) for name in configured)

    from utils.division_helper import load_division_config

    return tuple(str(d.get("name")) for d in load_division_config() if d.get("name"))


def replacement_for(division: str) -> Optional[ReplacementProfile]:
    """The stand-in coach for this division, or None to skip the replace phase."""
    raw = (_load().get("replacements") or {}).get(division)
    return _profile_from_raw(raw) if raw else None


def system_test_channel_id() -> int:
    """Optional Discord channel that receives every system-test message."""
    raw = _load().get("channel_id") or 0
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


def system_test_max_attempts() -> int:
    raw = _load().get("max_attempts") or 1
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return 1
