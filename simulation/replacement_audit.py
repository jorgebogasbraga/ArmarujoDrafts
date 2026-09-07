"""Append-only log of coach replacements during a system test session."""

from __future__ import annotations

import json
import os
import time
from typing import Any


def replacements_path(session_dir: str) -> str:
    return os.path.join(session_dir, "replacements.json")


def append_replacement(
    session_dir: str,
    *,
    division: str,
    old_coach: dict[str, Any],
    new_coach: dict[str, Any],
) -> None:
    path = replacements_path(session_dir)
    entries: list[dict] = []
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                entries = json.load(f)
        except (json.JSONDecodeError, OSError):
            entries = []

    entries.append({
        "division": division,
        "old": old_coach,
        "new": new_coach,
        "timestamp": time.time(),
    })

    with open(path, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2)


def load_replacements(session_dir: str) -> list[dict]:
    path = replacements_path(session_dir)
    if not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []
