"""
Hide commands a league does not use.

`features.disabled_commands` in league_config.json lists slash command names
that should never be registered. A cog whose commands are all disabled is not
loaded at all, so a draft-only league can drop the post-draft tooling without
deleting any code.
"""

from __future__ import annotations

import logging
from typing import Any, Iterable

from utils.league_settings import get_league_settings

logger = logging.getLogger(__name__)


def disabled_commands() -> set[str]:
    features = get_league_settings().raw.get("features", {})
    return {str(name).strip().lstrip("/") for name in features.get("disabled_commands", [])}


def _is_disabled(command: Any, disabled: set[str]) -> bool:
    node = command
    while node is not None:
        if getattr(node, "parent", None) is None:
            return getattr(node, "name", None) in disabled
        node = node.parent
    return False


def apply_command_gate(cog, disabled: Iterable[str]) -> bool:
    """
    Strip disabled commands from a cog instance.

    Returns False when the cog has no commands left, meaning the caller should
    skip registering it entirely.
    """
    disabled = set(disabled)
    if not disabled:
        return True

    original = list(cog.__cog_commands__)
    kept = [c for c in original if not _is_disabled(c, disabled)]
    if len(kept) == len(original):
        return True

    removed = [getattr(c, "name", "?") for c in original if c not in kept]
    cog.__cog_commands__ = tuple(kept)
    logger.info(
        "[Features] %s — hidden commands: %s",
        type(cog).__name__,
        ", ".join(sorted(removed)),
    )
    return bool(kept)
