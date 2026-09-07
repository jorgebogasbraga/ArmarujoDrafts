"""
Remember which Pokémon a system test has already drafted.

Picks stay random, but unseen names are preferred so a 10-run batch walks
the whole pool — Megas, regionals, Eternal Floette — at least once.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Iterable

from config import Config

logger = logging.getLogger(__name__)


def _path() -> Path:
    return Path(Config.DATA_DIR) / "system_test_coverage.json"


def _read() -> dict:
    path = _path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            return raw
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return {}


def _write(data: dict) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    seen = sorted({str(name).lower() for name in data.get("seen", []) if name})
    pool = sorted({str(name) for name in data.get("pool", []) if name})
    payload = {
        "seen": seen,
        "count": len(seen),
        "pool": pool,
        "pool_size": len(pool),
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def reset_seen() -> None:
    """Start a fresh batch so the next N runs can cover the whole pool."""
    data = _read()
    data["seen"] = []
    _write(data)
    logger.info("[Coverage] Reset — next batch will walk the pool from scratch.")


def load_seen() -> set[str]:
    return {str(name).lower() for name in _read().get("seen", [])}


def mark_seen(names: Iterable[str]) -> None:
    data = _read()
    seen = {str(name).lower() for name in data.get("seen", [])}
    before = len(seen)
    seen.update(str(name).lower() for name in names if name)
    data["seen"] = sorted(seen)
    _write(data)
    if len(seen) != before:
        logger.info(
            "[Coverage] %s new name(s) — %s seen in total.",
            len(seen) - before,
            len(seen),
        )


def remember_pool(names: Iterable[str]) -> None:
    data = _read()
    pool = {str(name) for name in data.get("pool", []) if name}
    pool.update(str(name) for name in names if name)
    data["pool"] = sorted(pool)
    _write(data)


def remaining_unseen() -> list[str]:
    data = _read()
    seen = {str(name).lower() for name in data.get("seen", [])}
    leftovers = [
        name for name in data.get("pool", [])
        if str(name).lower() not in seen
    ]
    leftovers.sort(key=str.lower)
    return leftovers


def prefer_unseen(candidates: list) -> list:
    """Keep the random pool, but drain unseen names first."""
    if not candidates:
        return candidates
    seen = load_seen()
    unseen = [p for p in candidates if getattr(p, "name", "").lower() not in seen]
    return unseen or candidates


def using_unseen_only(candidates: list) -> bool:
    """True when every candidate is still unseen (coverage drain in progress)."""
    if not candidates:
        return False
    seen = load_seen()
    return all(getattr(p, "name", "").lower() not in seen for p in candidates)


def coverage_summary(pool_size: int) -> str:
    seen = len(load_seen())
    leftovers = remaining_unseen()
    size = pool_size or len(_read().get("pool", []))
    if size <= 0:
        return f"{seen} unique Pokémon drafted across system tests"
    extra = f" — {len(leftovers)} still unseen" if leftovers else " — pool complete"
    return f"{seen}/{size} unique Pokémon drafted across system tests{extra}"
