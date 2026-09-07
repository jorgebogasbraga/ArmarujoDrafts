"""
Utility functions for resolving division and coach configuration.
"""
import json
import logging
import os
from typing import Optional

from config import Config

logger = logging.getLogger(__name__)

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

# Passing None to any loader below uses the current league profile.
DIVISION_CONFIG = None
COACHES_CONFIG = None


def resolve_config_path(config_path: str) -> str:
    if os.path.isabs(config_path):
        return config_path
    if os.path.isfile(config_path):
        return config_path
    return os.path.join(_PROJECT_ROOT, config_path)


def _division_config_path(config_path: Optional[str]) -> str:
    return resolve_config_path(config_path or Config.DIVISION_CONFIG_FILE)


def _coaches_config_path(config_path: Optional[str]) -> str:
    return resolve_config_path(config_path or Config.COACHES_CONFIG_FILE)


def load_division_config(config_path: Optional[str] = DIVISION_CONFIG) -> list[dict]:
    path = _division_config_path(config_path)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("divisions", [])
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.error("[DivisionHelper] Failed to load division config from %s: %s", path, e)
        return []


def load_coaches_config(config_path: Optional[str] = COACHES_CONFIG) -> list[dict]:
    path = _coaches_config_path(config_path)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("coaches", [])
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.error("[DivisionHelper] Failed to load coaches config from %s: %s", path, e)
        return []


def save_coaches_config(
    coaches: list[dict], config_path: Optional[str] = COACHES_CONFIG
) -> None:
    path = _coaches_config_path(config_path)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"coaches": coaches}, f, indent=2, ensure_ascii=False)
        logger.info("[DivisionHelper] Saved %d coaches to %s", len(coaches), path)
    except OSError as e:
        logger.error("[DivisionHelper] Failed to save coaches config: %s", e)


def get_division_name_by_channel(
    channel_id: int,
    config_path: Optional[str] = DIVISION_CONFIG,
) -> Optional[str]:
    divisions = load_division_config(config_path)
    for div in divisions:
        if div.get("channel_id") == channel_id:
            return div["name"]
    try:
        from simulation.replacements import system_test_channel_id, system_test_divisions

        if channel_id and channel_id == system_test_channel_id():
            names = system_test_divisions()
            if len(names) == 1:
                return names[0]
    except Exception:
        pass
    return None


def get_division_config(
    division_name: str,
    config_path: Optional[str] = DIVISION_CONFIG,
) -> Optional[dict]:
    divisions = load_division_config(config_path)
    for div in divisions:
        if div["name"].lower() == division_name.lower():
            return div
    return None


def get_coaches_for_division(
    division_name: str,
    division_config_path: Optional[str] = DIVISION_CONFIG,
    coaches_config_path: Optional[str] = COACHES_CONFIG,
) -> list[dict]:
    """Return the branding (team_name, logo_url, etc.) for coaches in a division."""
    coaches = load_coaches_config(coaches_config_path)
    return [c for c in coaches if c.get("division", "").lower() == division_name.lower()]


def save_division_config(
    divisions: list[dict], config_path: Optional[str] = DIVISION_CONFIG
) -> None:
    path = _division_config_path(config_path)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"divisions": divisions}, f, indent=2, ensure_ascii=False)
        logger.info("[DivisionHelper] Saved division config (%d divisions) to %s", len(divisions), path)
    except OSError as e:
        logger.error("[DivisionHelper] Failed to save division config: %s", e)


def update_division_channel_id(
    division_name: str,
    channel_id: int,
    config_path: Optional[str] = DIVISION_CONFIG,
) -> None:
    divisions = load_division_config(config_path)
    for div in divisions:
        if div["name"].lower() == division_name.lower():
            div["channel_id"] = channel_id
            save_division_config(divisions, config_path)
            return


def list_division_names(config_path: Optional[str] = DIVISION_CONFIG) -> list[str]:
    divisions = load_division_config(config_path)
    return [d["name"] for d in divisions]