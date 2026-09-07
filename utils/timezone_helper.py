"""Timezone resolution for users, coaches, and Discord client locale."""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Optional

import pytz

from config import Config
from utils.division_helper import load_coaches_config

logger = logging.getLogger(__name__)

_GMT_RE = re.compile(r"^GMT([+-]?\d+(?::\d{2})?)$", re.IGNORECASE)

# Discord client locales → IANA timezone (best-effort proxy — Discord does NOT expose
# the user's system timezone to bots; locale is the closest signal we get).
# https://discord.com/developers/docs/reference#locales
_LOCALE_TO_TZ: dict[str, str] = {
    "id": "Asia/Jakarta",
    "da": "Europe/Copenhagen",
    "de": "Europe/Berlin",
    "en-GB": "Europe/London",
    "en-US": "America/New_York",
    "en-CA": "America/Toronto",
    "en-AU": "Australia/Sydney",
    "en-NZ": "Pacific/Auckland",
    "en-IN": "Asia/Kolkata",
    "en-SG": "Asia/Singapore",
    "en-PH": "Asia/Manila",
    "es-ES": "Europe/Madrid",
    "es-419": "America/Mexico_City",
    "es": "Europe/Madrid",
    "fr": "Europe/Paris",
    "hr": "Europe/Zagreb",
    "it": "Europe/Rome",
    "lt": "Europe/Vilnius",
    "hu": "Europe/Budapest",
    "nl": "Europe/Amsterdam",
    "no": "Europe/Oslo",
    "nb": "Europe/Oslo",
    "pl": "Europe/Warsaw",
    "pt-BR": "America/Sao_Paulo",
    "pt-PT": "Europe/Lisbon",
    "pt": "Europe/Lisbon",
    "ro": "Europe/Bucharest",
    "fi": "Europe/Helsinki",
    "sv-SE": "Europe/Stockholm",
    "vi": "Asia/Ho_Chi_Minh",
    "tr": "Europe/Istanbul",
    "cs": "Europe/Prague",
    "el": "Europe/Athens",
    "bg": "Europe/Sofia",
    "ru": "Europe/Moscow",
    "uk": "Europe/Kyiv",
    "hi": "Asia/Kolkata",
    "th": "Asia/Bangkok",
    "zh-CN": "Asia/Shanghai",
    "zh-TW": "Asia/Taipei",
    "ja": "Asia/Tokyo",
    "ko": "Asia/Seoul",
}

# Bot /language preference → timezone when Discord locale is missing or generic.
_BOT_LANG_TO_TZ: dict[str, str] = {
    "pt": "Europe/Lisbon",
    "es": "Europe/Madrid",
}

# Fixed GMT labels used in the Participants sheet → IANA (handles DST automatically).
# Portugal alternates WET (UTC+0) / WEST (UTC+1); never use FixedOffset for these.
_PORTUGAL_TZ_ALIASES: frozenset[str] = frozenset({
    "gmt",
    "gmt+0",
    "gmt-0",
    "gmt0",
    "wet",
    "west",
    "utc",
    "wet0",
    "gmt+1",  # legacy sheet value — IANA picks +0 or +1 by date
})

# Discord en-US is a poor proxy for Portugal wall-clock time.
_MISLEADING_LOCALES_FOR_LISBON: frozenset[str] = frozenset({
    "en-us",
    "en-ca",
})


@dataclass(frozen=True)
class TimezoneResolution:
    tz: pytz.BaseTzInfo
    source: str
    detail: str = ""

    @property
    def name(self) -> str:
        return str(self.tz)


class TimezonePreferenceStore:
    """Optional per-user timezone overrides (rare — auto-detection is the default)."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path or Path(Config.DATA_DIR) / "user_timezones.json")
        self._cache: dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            self._cache = {}
            return
        try:
            with open(self.path, encoding="utf-8") as f:
                self._cache = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.error("[Timezone] Failed to load preferences: %s", e)
            self._cache = {}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._cache, f, indent=2, ensure_ascii=False)
        os.replace(tmp, self.path)

    def get(self, discord_id: str | int) -> Optional[str]:
        return self._cache.get(str(discord_id))

    def set(self, discord_id: str | int, timezone: str) -> None:
        self._cache[str(discord_id)] = timezone.strip()
        self._save()


_timezone_store = TimezonePreferenceStore()


def get_league_default_timezone_name() -> str:
    """League-wide fallback when Discord signals are inconclusive."""
    env_tz = os.getenv("DEFAULT_TIMEZONE", "").strip()
    if env_tz:
        return env_tz
    from utils.league_settings import get_league_settings

    tz_name = (get_league_settings().raw.get("default_timezone") or "").strip()
    return tz_name or "Europe/Lisbon"


def get_user_timezone_preference(discord_id: str | int) -> Optional[str]:
    return _timezone_store.get(discord_id)


def set_user_timezone_preference(discord_id: str | int, timezone: str) -> tuple[bool, str]:
    tz = parse_timezone(timezone)
    if tz is None:
        return False, timezone
    canonical = canonical_timezone_name(timezone)
    _timezone_store.set(discord_id, canonical)
    return True, str(tz)


def _normalize_timezone_key(tz_name: str) -> str:
    return (tz_name or "").strip().lower().replace(" ", "")


def _map_portugal_alias(tz_name: str) -> Optional[str]:
    """Map legacy GMT/WET labels to IANA Europe/Lisbon (DST-aware)."""
    league_iana = get_league_default_timezone_name()
    if league_iana != "Europe/Lisbon":
        return None
    key = _normalize_timezone_key(tz_name)
    if key in _PORTUGAL_TZ_ALIASES:
        return "Europe/Lisbon"
    return None


def canonical_timezone_name(tz_name: str) -> str:
    """Return a stable stored name (prefer IANA over fixed GMT offsets for Portugal)."""
    mapped = _map_portugal_alias(tz_name)
    if mapped:
        return mapped
    return tz_name.strip()


def parse_timezone(tz_name: str) -> pytz.BaseTzInfo | None:
    tz_name = (tz_name or "").strip()
    if not tz_name:
        return None

    mapped = _map_portugal_alias(tz_name)
    if mapped:
        return pytz.timezone(mapped)

    match = _GMT_RE.match(tz_name)
    if match:
        raw = match.group(1)
        if ":" in raw:
            sign = -1 if raw.startswith("-") else 1
            parts = raw.lstrip("+-").split(":")
            hours = int(parts[0])
            minutes = int(parts[1]) if len(parts) > 1 else 0
            total_minutes = sign * (hours * 60 + minutes)
        else:
            total_minutes = int(raw) * 60
        return pytz.FixedOffset(total_minutes)

    try:
        return pytz.timezone(tz_name)
    except pytz.UnknownTimeZoneError:
        return None


def timezone_from_discord_locale(locale: str | None) -> pytz.BaseTzInfo | None:
    """Map Discord Interaction.locale / guild_locale to a timezone."""
    if not locale:
        return None
    normalized = locale.strip().replace("_", "-")
    if get_league_default_timezone_name() == "Europe/Lisbon":
        if normalized.lower() in _MISLEADING_LOCALES_FOR_LISBON:
            return None
    tz_name = _LOCALE_TO_TZ.get(normalized)
    if not tz_name:
        lang = normalized.split("-")[0].lower()
        if lang == "en":
            return None
        tz_name = _LOCALE_TO_TZ.get(lang)
    if not tz_name:
        return None
    return parse_timezone(tz_name)


def timezone_from_bot_language(bot_language: str | None) -> pytz.BaseTzInfo | None:
    if not bot_language:
        return None
    tz_name = _BOT_LANG_TO_TZ.get(bot_language.strip().lower())
    if not tz_name:
        return None
    return parse_timezone(tz_name)


def _league_default_resolution() -> TimezoneResolution:
    tz_name = get_league_default_timezone_name()
    tz = parse_timezone(tz_name) or pytz.timezone("Europe/Lisbon")
    return TimezoneResolution(tz=tz, source="league_default", detail=tz_name)


def is_dst_active(tz: pytz.BaseTzInfo, at: datetime | None = None) -> bool:
    ref = at or datetime.now(tz)
    dst = ref.dst()
    return bool(dst and dst.total_seconds() > 0)


def format_utc_offset(tz: pytz.BaseTzInfo, at: datetime | None = None) -> str:
    ref = at or datetime.now(tz)
    offset = ref.utcoffset()
    if offset is None:
        return "UTC"
    total_minutes = int(offset.total_seconds() // 60)
    sign = "+" if total_minutes >= 0 else "-"
    total_minutes = abs(total_minutes)
    hours, minutes = divmod(total_minutes, 60)
    if minutes:
        return f"UTC{sign}{hours}:{minutes:02d}"
    return f"UTC{sign}{hours}"


def format_timezone_display(tz: pytz.BaseTzInfo, locale: str, at: datetime | None = None) -> str:
    from utils.i18n import i18n

    if at is not None:
        ref = at.astimezone(tz)
    else:
        ref = datetime.now(tz)
    offset = format_utc_offset(tz, ref)
    iana = getattr(tz, "zone", None) or str(tz)
    if is_dst_active(tz, ref):
        season = i18n.t_locale(locale, "timezone.season.summer")
    else:
        season = i18n.t_locale(locale, "timezone.season.winter")
    return i18n.t_locale(
        locale,
        "timezone.display",
        iana=iana,
        offset=offset,
        season=season,
    )


def schedule_local_time(
    tz: pytz.BaseTzInfo,
    hour: int,
    minute: int,
    *,
    now: datetime | None = None,
) -> datetime:
    """
    Build the next occurrence of ``hour:minute`` in ``tz``, returned as UTC.

    Uses ``tz.localize()`` so DST transitions (e.g. Portugal WET/WEST) are correct.
    """
    ref = now or datetime.now(tz)
    target_date = ref.date()

    def _localize_on(date) -> datetime:
        naive = datetime.combine(date, time(hour, minute))
        if hasattr(tz, "localize"):
            return tz.localize(naive, is_dst=None)
        return naive.replace(tzinfo=tz)

    scheduled = _localize_on(target_date)
    if scheduled <= ref:
        scheduled = _localize_on(target_date + timedelta(days=1))
    return scheduled.astimezone(pytz.UTC)


def resolve_invoker_timezone(
    discord_id: str | int,
    *,
    discord_locale: str | None = None,
    guild_locale: str | None = None,
    bot_language: str | None = None,
) -> TimezoneResolution:
    """
    Timezone for whoever runs a command (e.g. /start_division start_at).

    Discord bots cannot read the user's system clock/timezone — the client only
    sends ``locale`` (language/region). We combine that with bot language and a
    league default so scheduling always works without /timezone.
    """
    uid = str(discord_id)
    league_iana = get_league_default_timezone_name()

    pref = _timezone_store.get(uid)
    if pref:
        tz = parse_timezone(pref)
        if tz:
            return TimezoneResolution(
                tz=tz,
                source="preference",
                detail=canonical_timezone_name(pref),
            )

    # Portuguese league: bot language / league default beat misleading en-US client locale.
    if league_iana == "Europe/Lisbon":
        tz = timezone_from_bot_language(bot_language)
        if tz:
            logger.debug("[Timezone] Invoker %s via bot language %s", uid, bot_language)
            return TimezoneResolution(
                tz=tz, source="bot_language", detail=bot_language or ""
            )

    tz = timezone_from_discord_locale(discord_locale)
    if tz:
        logger.debug("[Timezone] Invoker %s via Discord locale %s", uid, discord_locale)
        return TimezoneResolution(
            tz=tz, source="discord_locale", detail=discord_locale or ""
        )

    tz = timezone_from_bot_language(bot_language)
    if tz:
        logger.debug("[Timezone] Invoker %s via bot language %s", uid, bot_language)
        return TimezoneResolution(
            tz=tz, source="bot_language", detail=bot_language or ""
        )

    tz = timezone_from_discord_locale(guild_locale)
    if tz:
        logger.debug("[Timezone] Invoker %s via guild locale %s", uid, guild_locale)
        return TimezoneResolution(
            tz=tz, source="guild_locale", detail=guild_locale or ""
        )

    fallback = _league_default_resolution()
    logger.info(
        "[Timezone] Invoker %s — using league default %s",
        uid,
        fallback.detail,
    )
    return fallback


def resolve_user_timezone(
    discord_id: str | int,
    *,
    discord_locale: str | None = None,
    guild_locale: str | None = None,
    bot_language: str | None = None,
) -> TimezoneResolution:
    """
    Resolve timezone for scheduling (matches, etc.).

    Same auto-detection as resolve_invoker_timezone, plus coaches.json when the
    user is a registered coach (timezone from the Participants sheet).
    """
    resolved = resolve_invoker_timezone(
        discord_id,
        discord_locale=discord_locale,
        guild_locale=guild_locale,
        bot_language=bot_language,
    )
    if resolved.source != "league_default":
        return resolved

    uid = str(discord_id)
    for coach in load_coaches_config():
        if str(coach.get("discord_id", "")) == uid:
            coach_tz = parse_timezone(coach.get("timezone", ""))
            if coach_tz:
                return TimezoneResolution(
                    tz=coach_tz,
                    source="coach_sheet",
                    detail=canonical_timezone_name(coach.get("timezone", "")),
                )

    return resolved


def resolve_coach_timezone(discord_id: str | int) -> pytz.BaseTzInfo | None:
    """Backward-compatible alias."""
    return resolve_user_timezone(discord_id).tz


def resolve_drafting_coach_timezone(coach) -> TimezoneResolution:
    """
    Timezone for pick timers and deadlines.

    Always uses the coach who is on the clock (from roster/sheet), never the
    Discord user who typed /pick on their behalf.
    """
    sheet_tz = parse_timezone(getattr(coach, "timezone", "") or "")
    if sheet_tz:
        return TimezoneResolution(
            tz=sheet_tz,
            source="coach_sheet",
            detail=canonical_timezone_name(coach.timezone),
        )
    return resolve_user_timezone(coach.discord_id)


def format_coach_deadline(
    coach,
    unix_ts: float | int,
    locale: str,
    *,
    include_relative: bool = True,
) -> str:
    """Human-readable pick deadline in the drafting coach's timezone."""
    from utils.i18n import i18n

    ts = int(unix_ts)
    resolution = resolve_drafting_coach_timezone(coach)
    local_dt = datetime.fromtimestamp(ts, tz=pytz.UTC).astimezone(resolution.tz)
    local_time = local_dt.strftime("%Y-%m-%d %H:%M")
    tz_label = format_timezone_display(resolution.tz, locale, at=local_dt)
    local_part = f"**{local_time}** ({tz_label})"
    if include_relative:
        return i18n.t_locale(
            locale,
            "pick.deadline_line",
            relative=ts,
            absolute=ts,
        )
    return local_part


def format_timezone_source(resolution: TimezoneResolution, locale: str) -> str:
    from utils.i18n import i18n

    key = f"timezone.source.{resolution.source}"
    label = i18n.t_locale(locale, key)
    if resolution.detail and resolution.source in {
        "discord_locale",
        "guild_locale",
        "bot_language",
        "preference",
        "coach_sheet",
        "league_default",
    }:
        return f"{label} (`{resolution.detail}`)"
    return label


def parse_local_datetime(date_str: str, time_str: str, tz: pytz.BaseTzInfo) -> datetime:
    local = datetime.strptime(f"{date_str.strip()} {time_str.strip()}", "%Y-%m-%d %H:%M")
    if hasattr(tz, "localize"):
        return tz.localize(local, is_dst=None)
    return local.replace(tzinfo=tz)
