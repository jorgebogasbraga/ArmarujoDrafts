"""
Runtime configuration, resolved from one league profile directory.

A league lives entirely in its own folder — .env, the JSON configs, its draft
state and its log:

    leagues/arma/
      .env  league_config.json  division_config.json  sheet_layout.json
      coaches.json  data/  logs/

Point LEAGUE_DIR at that folder and everything below follows from it. Run one
bot process per league:

    $env:LEAGUE_DIR = "leagues/tuga-champions"; python main.py

Any individual path can still be overridden with its own variable when a league
needs something out of place.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent


def _resolve(path: str | Path) -> Path:
    candidate = Path(path)
    return candidate if candidate.is_absolute() else PROJECT_ROOT / candidate


# LEAGUE_DIR has to be known before the .env is read, so it comes from the
# process environment (set by the launcher), never from the .env itself.
_league_dir_raw = os.getenv("LEAGUE_DIR", "").strip()
LEAGUE_DIR: Path | None = _resolve(_league_dir_raw) if _league_dir_raw else None

if LEAGUE_DIR:
    load_dotenv(LEAGUE_DIR / ".env")
else:
    load_dotenv()


def _env_int(name: str, default: int) -> int:
    """A key left blank in a .env means 'not set', not a crash at import time."""
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        raise EnvironmentError(f"{name} must be a whole number, got '{raw}'") from None


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        raise EnvironmentError(f"{name} must be a number, got '{raw}'") from None


def _profile_path(env_var: str, filename: str) -> str:
    """A per-league file: explicit override, else inside LEAGUE_DIR, else root."""
    override = os.getenv(env_var, "").strip()
    if override:
        return str(_resolve(override))
    base = LEAGUE_DIR or PROJECT_ROOT
    return str(base / filename)


class Config:
    DISCORD_TOKEN: str = os.getenv("DISCORD_TOKEN", "")
    GOOGLE_CREDENTIALS_FILE: str = os.getenv("GOOGLE_CREDENTIALS_FILE", "credentials.json")
    SPREADSHEET_ID: str = os.getenv("SPREADSHEET_ID", "")

    # League profile — everything below lives inside LEAGUE_DIR when it is set.
    LEAGUE_DIR: str = str(LEAGUE_DIR) if LEAGUE_DIR else ""
    SHEET_LAYOUT_FILE: str = _profile_path("SHEET_LAYOUT_FILE", "sheet_layout.json")
    DIVISION_CONFIG_FILE: str = _profile_path("DIVISION_CONFIG_FILE", "division_config.json")
    LEAGUE_CONFIG_FILE: str = _profile_path("LEAGUE_CONFIG_FILE", "league_config.json")
    COACHES_CONFIG_FILE: str = _profile_path("COACHES_CONFIG_FILE", "coaches.json")
    SYSTEM_TEST_FILE: str = _profile_path("SYSTEM_TEST_FILE", "system_test.json")

    GUILD_ID: int = _env_int("GUILD_ID", 0)
    ANNOUNCEMENTS_CHANNEL_ID: int = _env_int("ANNOUNCEMENTS_CHANNEL_ID", 0)
    ADMIN_LOG_CHANNEL_ID: int = _env_int("ADMIN_LOG_CHANNEL_ID", 0)
    ADMIN_ROLE_ID: int = _env_int("ADMIN_ROLE_ID", 0)
    DRAFT_PARTICIPANT_ROLE_ID: int = _env_int("DRAFT_PARTICIPANT_ROLE_ID", 0)
    REPLACEMENT_ROLE_ID: int = _env_int("REPLACEMENT_ROLE_ID", 0)

    # Draft timing settings (in seconds)
    PICK_TIME_INITIAL: int = 4 * 3600   # 4 hours for first skip
    PICK_TIME_SECOND: int = 2 * 3600    # 2 hours after first skip
    PICK_TIME_FINAL: int = 1 * 3600     # 1 hour after second skip
    MAX_SKIPS: int = 3                  # auto-replace after 3 skips
    COACH_VOTE_PAUSE_THRESHOLD: int = 3  # coach reactions needed to pause/resume
    MOD_ROLE_ID: int = _env_int("MOD_ROLE_ID", 0)
    LOCK_CHANNEL_ON_PAUSE: bool = (
        os.getenv("LOCK_CHANNEL_ON_PAUSE", "true").strip().lower()
        in ("1", "true", "yes", "")
    )

    # Pick bank — pause after primary snipe before executing fallback (seconds)
    BANK_SNIPE_PAUSE_SECONDS: int = _env_int("BANK_SNIPE_PAUSE_SECONDS", 30 * 60)
    SIMULATION_BANK_SNIPE_PAUSE_SECONDS: int = _env_int(
        "SIMULATION_BANK_SNIPE_PAUSE_SECONDS", 30
    )

    # Short coalesce window so near-simultaneous picks share one API call.
    # Keep this small (sub-second) so the live board does not stall.
    SHEETS_BATCH_FLUSH_SECONDS: float = _env_float("SHEETS_BATCH_FLUSH_SECONDS", 0.8)
    SHEETS_BATCH_MAX_SIZE: int = _env_int("SHEETS_BATCH_MAX_SIZE", 25)
    SHEETS_MAX_WRITES_PER_MINUTE: int = _env_int("SHEETS_MAX_WRITES_PER_MINUTE", 55)
    # Legacy — no longer used between writes (kept for .env compatibility)
    SHEETS_WRITE_DELAY_SECONDS: float = _env_float("SHEETS_WRITE_DELAY_SECONDS", 0.0)

    # Persistence and logs — draft state, caches, aliases and the bot log all
    # stay inside the league folder so one league can never read another's.
    DATA_DIR: str = _profile_path("DATA_DIR", "data")
    LOG_FILE: str = _profile_path("LOG_FILE", "logs/bot.log")

    # PokeAPI
    POKEAPI_BASE: str = "https://pokeapi.co/api/v2"
    POKEAPI_SPRITE_BASE: str = "https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon"

    @classmethod
    def require_profile(cls) -> None:
        """Fail early and clearly when LEAGUE_DIR is missing or wrong."""
        absent = [
            path
            for path in (
                cls.SHEET_LAYOUT_FILE,
                cls.DIVISION_CONFIG_FILE,
                cls.LEAGUE_CONFIG_FILE,
            )
            if not Path(path).is_file()
        ]
        if absent:
            raise EnvironmentError(
                "League config files not found:\n  "
                + "\n  ".join(absent)
                + "\n\nSet LEAGUE_DIR to the league's folder before starting, e.g.\n"
                '  $env:LEAGUE_DIR = "leagues/arma"\n'
                "or use the launcher: .\\run_arma.ps1"
            )

    @classmethod
    def validate(cls) -> None:
        missing = []
        if not cls.DISCORD_TOKEN:
            missing.append("DISCORD_TOKEN")
        if not cls.SPREADSHEET_ID:
            missing.append("SPREADSHEET_ID")
        if not cls.GUILD_ID:
            missing.append("GUILD_ID")
        if missing:
            raise EnvironmentError(
                f"Missing required environment variables: {', '.join(missing)}\n"
                f"Profile: {cls.LEAGUE_DIR or 'project root (LEAGUE_DIR not set)'}"
            )
        cls.require_profile()
