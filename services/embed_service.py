"""
EmbedService — factory for all Discord embeds used by the bot.

All user-facing strings come from locales/*.json via utils.i18n.
Public channel embeds use language tab buttons (see views.locale_embed_view).
Ephemeral embeds use the requesting user's locale.
"""

from __future__ import annotations

import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional

import discord

from constants.draft_constants import TYPE_COLOURS, DEFAULT_EMBED_COLOUR
from constants.embed_colours import EMBED_ERROR, EMBED_SUCCESS, EMBED_WARNING
from config import Config
from models.coach import Coach
from models.draft_state import DraftState, PickRecord
from models.pokemon import Pokemon
from utils.i18n import i18n
from utils.league_settings import league_name, show_tera
from utils.timezone_helper import format_coach_deadline


def _find_replay_for_pair(results: list[dict], coach_a: str, coach_b: str) -> str:
    a = coach_a.lower()
    b = coach_b.lower()
    for result in results:
        ra = result.get("coach_a", "").lower()
        rb = result.get("coach_b", "").lower()
        if {ra, rb} == {a, b}:
            return result.get("replay_url") or ""
        if a in ra or ra in a:
            if b in rb or rb in b:
                return result.get("replay_url") or ""
    return ""


class EmbedService:
    """Centralised embed builder with i18n support."""

    @staticmethod
    def _L(locale: str | None, key: str, **kwargs) -> str:
        return i18n.t_locale(locale or i18n.default_locale, key, **kwargs)

    @staticmethod
    def _footer(existing: str = "") -> str:
        """Append the league's name, when it has one, to any footer text."""
        name = league_name()
        if not name:
            return existing
        return f"{existing} · {name}" if existing else name

    @staticmethod
    def _format_duration(seconds: int) -> str:
        if seconds >= 3600:
            h = seconds // 3600
            m = (seconds % 3600) // 60
            return f"{h}h {m}m" if m else f"{h}h"
        if seconds >= 60:
            return f"{seconds // 60}m"
        return f"{seconds}s"

    @staticmethod
    def _type_emoji(type_name: str) -> str:
        emojis = {
            "fire": "🔥", "water": "💧", "grass": "🌿", "electric": "⚡",
            "ice": "❄️", "fighting": "🥊", "poison": "☠️", "ground": "🌍",
            "flying": "🌬️", "psychic": "🔮", "bug": "🐛", "rock": "🪨",
            "ghost": "👻", "dragon": "🐉", "dark": "🌑", "steel": "⚙️",
            "fairy": "✨", "normal": "⭐", "stellar": "🌟",
        }
        return emojis.get(type_name.lower(), "❓")

    # ── Pick announcement ────────────────────────────────────────────────────

    @staticmethod
    def pick_card(
        state: DraftState,
        coach: Coach,
        pokemon: Pokemon,
        pick_record: PickRecord,
        next_coach: Optional[Coach],
        locale: str | None = None,
    ) -> discord.Embed:
        loc = locale or i18n.default_locale
        colour = TYPE_COLOURS.get(pokemon.primary_type(), DEFAULT_EMBED_COLOUR)

        if pick_record.is_makeup:
            title = EmbedService._L(
                loc, "embed.pick.title_makeup",
                round=pick_record.round_number, pokemon=pokemon.name,
            )
            desc_key = "embed.pick.public_makeup"
        elif pick_record.is_bank:
            title = EmbedService._L(
                loc, "embed.pick.title_bank", pokemon=pokemon.name,
            )
            desc_key = "embed.pick.public_bank"
        else:
            title = EmbedService._L(
                loc, "embed.pick.title",
                number=pick_record.pick_number, pokemon=pokemon.name,
            )
            desc_key = "embed.pick.public"

        embed = discord.Embed(title=title, colour=colour, timestamp=datetime.now(timezone.utc))
        embed.description = EmbedService._L(
            loc,
            desc_key,
            pokemon=pokemon.name,
            coach=coach.name,
            points=pokemon.points,
            round=pick_record.round_number,
        )

        picker_name = EmbedService._picker_author_name(state, coach, pick_record, loc)
        if picker_name:
            embed.set_author(name=picker_name)

        embed.add_field(name=EmbedService._L(loc, "embed.pick.field.coach"), value=coach.mention(), inline=True)
        embed.add_field(name=EmbedService._L(loc, "embed.pick.field.pokemon"), value=pokemon.name, inline=True)
        embed.add_field(name=EmbedService._L(loc, "embed.pick.field.points_cost"), value=str(pokemon.points), inline=True)
        embed.add_field(name=EmbedService._L(loc, "embed.pick.field.points_remaining"), value=str(coach.remaining_points), inline=True)
        round_key = (
            "embed.pick.field.round_owed"
            if pick_record.is_makeup
            else "embed.pick.field.round"
        )
        embed.add_field(
            name=EmbedService._L(loc, round_key),
            value=str(pick_record.round_number),
            inline=True,
        )
        if next_coach:
            embed.add_field(
                name=EmbedService._L(loc, "embed.pick.field.next"),
                value=next_coach.name,
                inline=True,
            )

        if coach.team_logo_url:
            embed.set_thumbnail(url=coach.team_logo_url)
        image_url = (getattr(pick_record, "gif_url", None) or "").strip() or pokemon.sprite_url
        if image_url:
            embed.set_image(url=image_url)

        embed.set_footer(text=EmbedService._footer(state.division_name))
        return embed

    @staticmethod
    def _picker_author_name(
        state: DraftState,
        coach: Coach,
        pick_record: PickRecord,
        locale: str,
    ) -> str:
        if pick_record.is_bank:
            return EmbedService._L(locale, "embed.pick.author_bank")
        picker_id = (getattr(pick_record, "picked_by_discord_id", None) or "").strip()
        if not picker_id or picker_id == coach.discord_id:
            return ""
        other = state.get_coach_by_id(picker_id)
        if other:
            return other.name
        return EmbedService._L(locale, "embed.pick.author_staff")

    # ── Team overview ────────────────────────────────────────────────────────

    @staticmethod
    def team_card(coach: Coach, state: DraftState, locale: str | None = None) -> discord.Embed:
        loc = locale or i18n.default_locale
        embed = discord.Embed(
            title=EmbedService._L(loc, "embed.team.title", team=coach.team_name),
            description=EmbedService._L(
                loc, "embed.team.description",
                coach=coach.mention(), division=state.division_name,
            ),
            colour=DEFAULT_EMBED_COLOUR,
        )
        if coach.team:
            lines = [
                EmbedService._L(
                    loc, "embed.team.line",
                    name=p.name, points=p.points,
                )
                for p in coach.team
            ]
            embed.add_field(
                name=EmbedService._L(loc, "embed.team.field.team", current=len(coach.team), max=state.team_size),
                value="\n".join(lines),
                inline=False,
            )
        else:
            embed.add_field(
                name=EmbedService._L(loc, "embed.team.field.team", current=0, max=state.team_size),
                value=EmbedService._L(loc, "embed.team.field.empty"),
                inline=False,
            )

        embed.add_field(name=EmbedService._L(loc, "embed.team.field.points_remaining"), value=str(coach.remaining_points), inline=True)
        if show_tera():
            embed.add_field(name=EmbedService._L(loc, "embed.team.field.tera_remaining"), value=str(coach.tera_points_remaining), inline=True)
        embed.add_field(name=EmbedService._L(loc, "embed.team.field.skips"), value=str(coach.skip_count), inline=True)

        if coach.makeup_picks_owed > 0:
            embed.add_field(
                name=EmbedService._L(loc, "embed.team.field.makeup_owed"),
                value=str(coach.makeup_picks_owed),
                inline=False,
            )
        if coach.team_logo_url:
            embed.set_thumbnail(url=coach.team_logo_url)
        return embed

    # ── Current pick status ──────────────────────────────────────────────────

    @staticmethod
    def picking_status(state: DraftState, locale: str | None = None) -> discord.Embed:
        loc = locale or i18n.default_locale
        coach = state.current_coach
        if not coach:
            return discord.Embed(title=EmbedService._L(loc, "picking.empty"), colour=DEFAULT_EMBED_COLOUR)

        coach.sync_pick_deadline()

        embed = discord.Embed(
            title=EmbedService._L(loc, "embed.picking.title", division=state.division_name),
            colour=DEFAULT_EMBED_COLOUR,
        )
        embed.add_field(
            name=EmbedService._L(loc, "embed.picking.field.now"),
            value=f"**{coach.name}** {coach.mention()}",
            inline=True,
        )
        embed.add_field(name=EmbedService._L(loc, "embed.picking.field.round"), value=str(state.current_round), inline=True)
        embed.add_field(name=EmbedService._L(loc, "embed.picking.field.global_pick"), value=str(state.global_pick_counter + 1), inline=True)

        if coach.pick_deadline:
            ts = int(coach.pick_deadline)
            embed.add_field(
                name=EmbedService._L(loc, "embed.picking.field.deadline"),
                value=format_coach_deadline(coach, ts, loc),
                inline=False,
            )

        embed.add_field(name=EmbedService._L(loc, "embed.picking.field.points"), value=str(coach.remaining_points), inline=True)
        embed.add_field(name=EmbedService._L(loc, "embed.picking.field.skips"), value=f"{coach.skip_count}/3", inline=True)

        coaches = state.coaches
        idx = state.current_coach_index
        direction = int(state.snake_direction)
        upcoming = []
        cur_idx = idx
        turnaround = EmbedService._L(loc, "embed.picking.turnaround")
        for _ in range(3):
            cur_idx += direction
            if 0 <= cur_idx < len(coaches):
                c = coaches[cur_idx]
                upcoming.append(f"**{c.name}** {c.mention()}")
            else:
                direction *= -1
                c = coaches[idx]
                upcoming.append(f"**{c.name}** {c.mention()}" + turnaround)
        if upcoming:
            embed.add_field(name=EmbedService._L(loc, "embed.picking.field.up_next"), value=" → ".join(upcoming), inline=False)

        if state.makeup_queue:
            makeup_mentions = []
            for cid, rnd in state.makeup_queue:
                c = state.get_coach_by_id(cid)
                if c:
                    makeup_mentions.append(
                        EmbedService._L(
                            loc,
                            "embed.picking.makeup_line",
                            mention=f"**{c.name}** {c.mention()}",
                            round=rnd,
                        )
                    )
            embed.add_field(
                name=EmbedService._L(loc, "embed.picking.field.makeup_queue"),
                value="\n".join(makeup_mentions),
                inline=False,
            )
        return embed

    # ── Draft overview ───────────────────────────────────────────────────────

    @staticmethod
    def draft_overview(all_states: dict[str, DraftState], locale: str | None = None) -> discord.Embed:
        loc = locale or i18n.default_locale
        embed = discord.Embed(
            title=EmbedService._L(loc, "embed.overview.title"),
            colour=DEFAULT_EMBED_COLOUR,
            timestamp=datetime.now(timezone.utc),
        )
        for name, state in all_states.items():
            coach = state.current_coach
            if state.status.value == "active" and coach:
                value = EmbedService._L(
                    loc, "embed.overview.active",
                    coach=coach.mention(),
                    current=state.global_pick_counter + 1,
                    total=state.total_picks_expected,
                    round=state.current_round,
                )
            elif state.status.value == "waiting_replace":
                value = EmbedService._L(loc, "embed.overview.waiting_replace")
            elif state.status.value == "paused":
                value = EmbedService._L(loc, "embed.overview.paused")
            elif state.status.value == "completed":
                value = EmbedService._L(loc, "embed.overview.completed")
            else:
                value = EmbedService._L(loc, "embed.overview.other", status=state.status.value)
            embed.add_field(name=name, value=value, inline=True)
        return embed

    # ── Skip / timeout ───────────────────────────────────────────────────────

    @staticmethod
    def skip_warning(
        coach: Coach,
        skips_used: int,
        next_deadline: int,
        deadline_coach: Coach | None = None,
        locale: str | None = None,
    ) -> discord.Embed:
        loc = locale or i18n.default_locale
        tz_coach = deadline_coach or coach
        deadline_line = format_coach_deadline(tz_coach, next_deadline, loc)
        if skips_used >= 3:
            embed = discord.Embed(
                title=EmbedService._L(loc, "embed.skip.title_final", count=skips_used, name=coach.name),
                description=EmbedService._L(loc, "embed.skip.desc_final", mention=coach.mention()),
                colour=0xFF0000,
            )
        elif skips_used == 2:
            embed = discord.Embed(
                title=EmbedService._L(loc, "embed.skip.title_warn", count=skips_used, name=coach.name),
                description=EmbedService._L(
                    loc, "embed.skip.desc_second",
                    mention=coach.mention(), count=skips_used, deadline=deadline_line,
                ),
                colour=0xFF4500,
            )
        else:
            embed = discord.Embed(
                title=EmbedService._L(loc, "embed.skip.title_warn", count=skips_used, name=coach.name),
                description=EmbedService._L(
                    loc, "embed.skip.desc_first",
                    mention=coach.mention(), count=skips_used, deadline=deadline_line,
                ),
                colour=0xFF9900,
            )

        if coach.team_logo_url:
            embed.set_thumbnail(url=coach.team_logo_url)
        embed.set_footer(text=EmbedService._footer(f"{coach.division_name}"))
        return embed

    # ── Makeup reminder ──────────────────────────────────────────────────────

    @staticmethod
    def makeup_reminder(state: DraftState, locale: str | None = None) -> Optional[discord.Embed]:
        if not state.makeup_queue:
            return None
        loc = locale or i18n.default_locale
        embed = discord.Embed(
            title=EmbedService._L(loc, "embed.makeup.title"),
            colour=0x00BFFF,
        )
        by_coach: dict[str, list[int]] = defaultdict(list)
        for coach_id, rnd in sorted(state.makeup_queue, key=lambda x: x[1]):
            by_coach[coach_id].append(rnd)

        lines = []
        for coach_id, rounds in by_coach.items():
            coach = state.get_coach_by_id(coach_id)
            if not coach:
                continue
            rounds_str = ", ".join(
                EmbedService._L(loc, "embed.makeup.round_label", round=r) for r in sorted(rounds)
            )
            lines.append(EmbedService._L(
                loc, "embed.makeup.line",
                mention=coach.mention(), name=coach.name,
                rounds=rounds_str, count=len(rounds),
            ))

        embed.description = "\n".join(lines)
        embed.set_footer(text=EmbedService._footer(EmbedService._L(loc, "embed.makeup.footer")))
        return embed

    # ── Pick bank status ─────────────────────────────────────────────────────

    @staticmethod
    def pick_bank_status(coach: Coach, state: DraftState, locale: str | None = None) -> discord.Embed:
        from models.pick_bank import PlanType
        loc = locale or i18n.default_locale
        bank = state.pick_banks.get(coach.discord_id)

        embed = discord.Embed(
            title=EmbedService._L(loc, "embed.bank.status.title", name=coach.name),
            colour=DEFAULT_EMBED_COLOUR,
        )
        if bank is None or not bank.entries:
            embed.description = EmbedService._L(loc, "embed.bank.status.empty")
            return embed

        status = EmbedService._L(loc, "common.active") if bank.is_active else EmbedService._L(loc, "common.inactive")
        embed.add_field(name=EmbedService._L(loc, "embed.bank.status.field.status"), value=status, inline=True)
        embed.add_field(name=EmbedService._L(loc, "embed.bank.status.field.mode"), value=bank.mode.value, inline=True)

        if state.bank_snipe_pending and state.bank_snipe_pending.coach_discord_id == coach.discord_id:
            ts = int(state.bank_snipe_pending.deadline)
            embed.add_field(
                name="⏳",
                value=EmbedService._L(loc, "embed.bank.status.snipe_pause", deadline=ts),
                inline=False,
            )

        for entry in bank.entries:
            if entry.plan_type == PlanType.CONDITIONAL:
                lines = [
                    EmbedService._L(
                        loc, "embed.bank.status.conditional_if",
                        round=b.if_round, picked=b.if_picked, list=" → ".join(b.then_list),
                    )
                    for b in entry.branches
                ]
                if entry.default_list:
                    lines.append(EmbedService._L(
                        loc, "embed.bank.status.conditional_else",
                        list=" → ".join(entry.default_list),
                    ))
                picks_str = "\n".join(lines)
            else:
                picks_str = " → ".join(entry.priority_list) if entry.priority_list else "*empty*"
            embed.add_field(
                name=EmbedService._L(loc, "embed.bank.status.round", round=entry.round_number),
                value=picks_str,
                inline=False,
            )
        embed.set_footer(text=EmbedService._L(loc, "embed.bank.status.footer"))
        return embed

    @staticmethod
    def bank_snipe_public(
        coach: Coach,
        division_name: str,
        deadline_ts: int,
        locale: str | None = None,
        resume_ts: int | None = None,
    ) -> discord.Embed:
        loc = locale or i18n.default_locale
        # During quiet hours the fallback clock has not started yet, so say when
        # it will rather than showing a deadline that is not being counted down.
        key = (
            "embed.bank.snipe.public.desc_deferred"
            if resume_ts
            else "embed.bank.snipe.public.desc"
        )
        embed = discord.Embed(
            title=EmbedService._L(loc, "embed.bank.snipe.public.title"),
            description=EmbedService._L(
                loc, key,
                coach=coach.name,
                division=division_name,
                deadline=deadline_ts,
                resume=resume_ts or 0,
            ),
            colour=0xFEE75C,
        )
        embed.set_footer(text=EmbedService._footer())
        return embed

    @staticmethod
    def bank_snipe_dm(
        coach: Coach,
        sniped_name: str,
        deadline_ts: int,
        remaining: list[str],
        locale: str | None = None,
        resume_ts: int | None = None,
    ) -> discord.Embed:
        loc = locale or i18n.get_user_locale(coach.discord_id)
        fallback = " → ".join(remaining) if remaining else EmbedService._L(loc, "embed.bank.snipe.fallback_none")
        key = (
            "embed.bank.snipe.dm.desc_deferred"
            if resume_ts
            else "embed.bank.snipe.dm.desc"
        )
        return discord.Embed(
            title=EmbedService._L(loc, "embed.bank.snipe.dm.title"),
            description=EmbedService._L(
                loc, key,
                pokemon=sniped_name,
                fallback=fallback,
                deadline=deadline_ts,
                resume=resume_ts or 0,
            ),
            colour=0xFEE75C,
        )

    # ── Quiet hours ──────────────────────────────────────────────────────────

    @staticmethod
    def quiet_hours_start(
        state: DraftState,
        coach: Optional[Coach],
        resume_ts: int,
        elapsed_seconds: float,
        locale: str | None = None,
    ) -> discord.Embed:
        """Timers stopped for the night — the draft itself keeps going."""
        loc = locale or i18n.default_locale
        embed = discord.Embed(
            title=EmbedService._L(loc, "embed.quiet.start.title"),
            description=EmbedService._L(
                loc, "embed.quiet.start.desc",
                division=state.division_name, resume=resume_ts,
            ),
            colour=0x5865F2,
        )
        if coach:
            embed.add_field(
                name=EmbedService._L(loc, "embed.quiet.field.current"),
                value=f"**{coach.name}** {coach.mention()}",
                inline=True,
            )
            embed.add_field(
                name=EmbedService._L(loc, "embed.quiet.field.used"),
                value=EmbedService._format_duration(int(elapsed_seconds)),
                inline=True,
            )
            if coach.pick_timer_remaining is not None:
                embed.add_field(
                    name=EmbedService._L(loc, "embed.quiet.field.left"),
                    value=EmbedService._format_duration(int(coach.pick_timer_remaining)),
                    inline=True,
                )
        embed.add_field(
            name=EmbedService._L(loc, "embed.quiet.field.picks_open"),
            value=EmbedService._L(loc, "embed.quiet.start.picks_open"),
            inline=False,
        )
        embed.set_footer(text=EmbedService._footer())
        return embed

    @staticmethod
    def quiet_hours_end(
        state: DraftState,
        coach: Optional[Coach],
        deadline_ts: Optional[int],
        locale: str | None = None,
    ) -> discord.Embed:
        """Timers are counting again — ping whoever is on the clock."""
        loc = locale or i18n.default_locale
        embed = discord.Embed(
            title=EmbedService._L(loc, "embed.quiet.end.title"),
            description=EmbedService._L(
                loc, "embed.quiet.end.desc", division=state.division_name
            ),
            colour=EMBED_SUCCESS,
        )
        if coach:
            embed.add_field(
                name=EmbedService._L(loc, "embed.quiet.field.current"),
                value=f"**{coach.name}** {coach.mention()}",
                inline=True,
            )
            if coach.pick_timer_remaining is not None:
                embed.add_field(
                    name=EmbedService._L(loc, "embed.quiet.field.left"),
                    value=EmbedService._format_duration(int(coach.pick_timer_remaining)),
                    inline=True,
                )
            if deadline_ts:
                embed.add_field(
                    name=EmbedService._L(loc, "embed.quiet.field.deadline"),
                    value=EmbedService._L(
                        loc, "embed.quiet.end.deadline_value", deadline=deadline_ts
                    ),
                    inline=False,
                )
        embed.set_footer(text=EmbedService._footer())
        return embed

    @staticmethod
    def bank_all_sniped_public(
        coach: Coach,
        division_name: str,
        round_number: int,
        locale: str | None = None,
    ) -> discord.Embed:
        loc = locale or i18n.default_locale
        embed = discord.Embed(
            title=EmbedService._L(loc, "embed.bank.all_sniped.title"),
            description=EmbedService._L(
                loc,
                "embed.bank.all_sniped.desc",
                coach=coach.name,
                mention=coach.mention(),
                division=division_name,
                round=round_number,
            ),
            colour=0xFEE75C,
        )
        embed.set_footer(text=EmbedService._footer())
        return embed

    # ── Replacement ──────────────────────────────────────────────────────────

    @staticmethod
    def replacement_announcement_global(coach: Coach, state: DraftState, locale: str | None = None) -> discord.Embed:
        loc = locale or i18n.default_locale
        embed = discord.Embed(
            title=EmbedService._L(loc, "embed.replace.global.title", division=state.division_name),
            description=EmbedService._L(loc, "embed.replace.global.desc", division=state.division_name),
            colour=0xFF0000,
        )
        embed.add_field(name=EmbedService._L(loc, "embed.replace.field.coach"), value=coach.name, inline=True)
        embed.add_field(name=EmbedService._L(loc, "embed.replace.field.team"), value=coach.team_name, inline=True)
        embed.add_field(name=EmbedService._L(loc, "embed.replace.field.division"), value=state.division_name, inline=True)
        if coach.team_logo_url:
            embed.set_thumbnail(url=coach.team_logo_url)
        return embed

    @staticmethod
    def replacement_announcement_division(coach: Coach, state: DraftState, locale: str | None = None) -> discord.Embed:
        loc = locale or i18n.default_locale
        embed = discord.Embed(
            title=EmbedService._L(loc, "embed.replace.title"),
            description=EmbedService._L(loc, "embed.replace.division.desc", name=coach.name, team=coach.team_name),
            colour=0xFF0000,
        )
        if coach.team:
            lines = [
                EmbedService._L(loc, "embed.team.line", name=p.name, points=p.points)
                for p in coach.team
            ]
            embed.add_field(
                name=EmbedService._L(loc, "embed.replace.field.inherited_team", current=len(coach.team), max=state.team_size),
                value="\n".join(lines),
                inline=False,
            )
        total_spent = sum(p.points for p in coach.team)
        embed.add_field(name=EmbedService._L(loc, "embed.replace.field.points_spent"), value=str(total_spent), inline=True)
        embed.add_field(name=EmbedService._L(loc, "embed.replace.field.points_remaining"), value=str(coach.remaining_points), inline=True)
        embed.add_field(name=EmbedService._L(loc, "embed.replace.field.current_round"), value=str(state.current_round), inline=True)

        makeups_owed = sum(1 for cid, _ in state.makeup_queue if cid == coach.discord_id)
        if makeups_owed:
            embed.add_field(name=EmbedService._L(loc, "embed.replace.field.makeups"), value=str(makeups_owed), inline=True)
        if coach.team_logo_url:
            embed.set_thumbnail(url=coach.team_logo_url)
        return embed

    @staticmethod
    def replacement_applied_success(
        new_coach: Coach,
        old_coach_name: str,
        state: DraftState,
        *,
        replacement_makeups: int = 0,
        bank_deactivated: bool = False,
        locale: str | None = None,
    ) -> discord.Embed:
        loc = locale or i18n.default_locale
        if replacement_makeups > 0:
            next_step = EmbedService._L(
                loc,
                "embed.replace.success.next_makeups",
                count=replacement_makeups,
            )
        else:
            next_step = EmbedService._L(loc, "embed.replace.success.next_pick")

        embed = discord.Embed(
            title=EmbedService._L(loc, "embed.replace.success.title"),
            description=EmbedService._L(
                loc,
                "embed.replace.success.desc",
                new=new_coach.name,
                old=old_coach_name,
                team=new_coach.team_name,
                division=state.division_name,
            ),
            colour=0x57F287,
        )
        embed.add_field(
            name=EmbedService._L(loc, "embed.replace.field.points_remaining"),
            value=str(new_coach.remaining_points),
            inline=True,
        )
        embed.add_field(
            name=EmbedService._L(loc, "embed.replace.field.current_round"),
            value=str(state.current_round),
            inline=True,
        )
        embed.add_field(
            name=EmbedService._L(loc, "embed.replace.success.field.next"),
            value=next_step,
            inline=False,
        )
        if bank_deactivated:
            embed.add_field(
                name=EmbedService._L(loc, "embed.replace.success.field.bank"),
                value=EmbedService._L(loc, "embed.replace.success.bank_deactivated"),
                inline=False,
            )
        if new_coach.team_logo_url:
            embed.set_thumbnail(url=new_coach.team_logo_url)
        return embed

    # ── Division init / start ────────────────────────────────────────────────

    @staticmethod
    def division_initialised(state: DraftState, locale: str | None = None) -> discord.Embed:
        loc = locale or i18n.default_locale
        embed = discord.Embed(
            title=EmbedService._L(loc, "embed.init.title", division=state.division_name),
            colour=0x57F287,
        )
        embed.add_field(name=EmbedService._L(loc, "embed.init.field.coaches"), value=str(len(state.coaches)), inline=True)
        embed.add_field(name=EmbedService._L(loc, "embed.init.field.pool"), value=str(len(state.pokemon_pool)), inline=True)
        embed.add_field(name=EmbedService._L(loc, "embed.init.field.team_size"), value=str(state.team_size), inline=True)
        embed.add_field(name=EmbedService._L(loc, "embed.init.field.points"), value=str(state.total_points), inline=True)
        embed.add_field(
            name=EmbedService._L(loc, "embed.init.field.timers"),
            value=EmbedService._L(
                loc, "embed.init.timers_body",
                initial=EmbedService._format_duration(state.pick_time_initial),
                second=EmbedService._format_duration(state.pick_time_second),
                final=EmbedService._format_duration(state.pick_time_final),
            ),
            inline=True,
        )
        embed.add_field(
            name=EmbedService._L(loc, "embed.init.field.order", count=len(state.coaches)),
            value="\n".join(
                EmbedService._L(loc, "embed.init.order_line", index=i + 1, mention=c.mention(), team=c.team_name)
                for i, c in enumerate(state.coaches)
            ),
            inline=False,
        )
        embed.set_footer(text=EmbedService._footer(EmbedService._L(loc, "embed.init.footer")))
        return embed

    @staticmethod
    def draft_start_announcement(state: DraftState, locale: str | None = None) -> discord.Embed:
        loc = locale or i18n.default_locale
        first = state.current_coach.name if state.current_coach else "?"
        embed = discord.Embed(
            title=EmbedService._L(loc, "embed.start.title", division=state.division_name),
            colour=DEFAULT_EMBED_COLOUR,
        )
        embed.description = EmbedService._L(
            loc, "embed.start.public", division=state.division_name, coach=first,
        )
        embed.add_field(
            name=EmbedService._L(loc, "embed.start.field.order", count=len(state.coaches)),
            value="\n".join(
                EmbedService._L(loc, "embed.start.order_line", index=i + 1, mention=c.mention(), team=c.team_name)
                for i, c in enumerate(state.coaches)
            ),
            inline=False,
        )
        embed.add_field(name=EmbedService._L(loc, "embed.start.field.team_size"), value=str(state.team_size), inline=True)
        embed.add_field(name=EmbedService._L(loc, "embed.start.field.points"), value=str(state.total_points), inline=True)
        embed.add_field(name=EmbedService._L(loc, "embed.start.field.pool"), value=str(len(state.pokemon_pool)), inline=True)
        embed.add_field(
            name=EmbedService._L(loc, "embed.start.field.times"),
            value=EmbedService._L(
                loc, "embed.start.times_body",
                initial=EmbedService._format_duration(state.pick_time_initial),
                second=EmbedService._format_duration(state.pick_time_second),
                final=EmbedService._format_duration(state.pick_time_final),
            ),
            inline=False,
        )
        embed.set_footer(text=EmbedService._footer())
        return embed

    @staticmethod
    def draft_resume_announcement(
        state: DraftState,
        coach: Coach,
        downtime_seconds: int,
        locale: str | None = None,
    ) -> discord.Embed:
        loc = locale or i18n.default_locale
        coach.sync_pick_deadline()
        ts = int(coach.pick_deadline) if coach.pick_deadline else int(time.time())
        embed = discord.Embed(
            title=EmbedService._L(loc, "embed.resume.title", division=state.division_name),
            colour=0xF39C12,
        )
        embed.description = EmbedService._L(
            loc,
            "embed.resume.description",
            mention=coach.mention(),
            relative=ts,
            absolute=ts,
            downtime=EmbedService._format_duration(downtime_seconds),
        )
        embed.set_footer(text=EmbedService._footer())
        return embed

    @staticmethod
    def coach_vote_embed(
        state: DraftState,
        vote_type: str,
        reason: str,
        threshold: int,
        votes: int,
        locale: str | None = None,
    ) -> discord.Embed:
        loc = locale or i18n.default_locale
        coach = state.current_coach
        if vote_type == "pause":
            title_key = "embed.coach_vote.pause_title"
            desc_key = "embed.coach_vote.pause_desc"
        else:
            title_key = "embed.coach_vote.resume_title"
            desc_key = "embed.coach_vote.resume_desc"

        embed = discord.Embed(
            title=EmbedService._L(loc, title_key, division=state.division_name),
            colour=0xFEE75C,
        )
        embed.description = EmbedService._L(
            loc,
            desc_key,
            mention=coach.mention() if coach else "—",
            reason=reason or EmbedService._L(loc, "common.not_specified"),
            votes=votes,
            threshold=threshold,
        )
        embed.set_footer(text=EmbedService._L(loc, "embed.coach_vote.footer"))
        return embed

    @staticmethod
    def draft_pause_announcement(
        state: DraftState,
        reason: str,
        locale: str | None = None,
    ) -> discord.Embed:
        loc = locale or i18n.default_locale
        coach = state.current_coach
        embed = discord.Embed(
            title=EmbedService._L(loc, "embed.pause.title", division=state.division_name),
            description=EmbedService._L(
                loc,
                "embed.pause.description",
                reason=reason or EmbedService._L(loc, "common.not_specified"),
            ),
            colour=EMBED_WARNING,
        )
        if coach:
            coach.sync_pick_deadline()
            embed.add_field(
                name=EmbedService._L(loc, "embed.pause.field_on_clock"),
                value=f"**{coach.name}** {coach.mention()}",
                inline=True,
            )
            if coach.pick_deadline:
                ts = int(coach.pick_deadline)
                embed.add_field(
                    name=EmbedService._L(loc, "embed.pause.field_deadline"),
                    value=EmbedService._L(
                        loc, "pick.deadline_line", relative=ts, absolute=ts
                    ),
                    inline=True,
                )
        embed.set_footer(text=EmbedService._footer())
        return embed

    @staticmethod
    def draft_manual_resume_announcement(
        state: DraftState,
        locale: str | None = None,
    ) -> discord.Embed:
        loc = locale or i18n.default_locale
        embed = discord.Embed(
            title=EmbedService._L(
                loc, "embed.manual_resume.title", division=state.division_name
            ),
            description=EmbedService._L(loc, "embed.manual_resume.description"),
            colour=EMBED_SUCCESS,
        )
        embed.set_footer(text=EmbedService._footer())
        return embed

    @staticmethod
    def edit_pick_announcement(
        state: DraftState,
        *,
        pick_number: int,
        edited_coach: Coach,
        old_name: str,
        new_name: str,
        locale: str | None = None,
    ) -> discord.Embed:
        loc = locale or i18n.default_locale
        embed = discord.Embed(
            title=EmbedService._L(loc, "embed.edit_pick.title", pick=pick_number),
            description=EmbedService._L(
                loc,
                "embed.edit_pick.description",
                old=old_name,
                new=new_name,
                coach=edited_coach.name,
            ),
            colour=EMBED_SUCCESS,
        )
        current = state.current_coach
        if current:
            current.sync_pick_deadline()
            embed.add_field(
                name=EmbedService._L(loc, "embed.edit_pick.field_on_clock"),
                value=f"**{current.name}** {current.mention()}",
                inline=True,
            )
            if current.pick_deadline:
                ts = int(current.pick_deadline)
                embed.add_field(
                    name=EmbedService._L(loc, "embed.edit_pick.field_deadline"),
                    value=EmbedService._L(
                        loc, "pick.deadline_line", relative=ts, absolute=ts
                    ),
                    inline=True,
                )
        embed.set_footer(text=EmbedService._footer())
        return embed

    # ── Team analysis ────────────────────────────────────────────────────────

    @staticmethod
    def weakness_card(coach: Coach, analysis, locale: str | None = None) -> discord.Embed:
        loc = locale or i18n.default_locale
        embed = discord.Embed(
            title=EmbedService._L(loc, "embed.analysis.weakness.title", team=coach.team_name),
            colour=0xE74C3C,
        )
        if coach.team_logo_url:
            embed.set_thumbnail(url=coach.team_logo_url)
        if not analysis:
            embed.description = EmbedService._L(loc, "embed.analysis.weakness.empty")
            return embed

        if analysis.quad_weaknesses:
            embed.add_field(
                name=EmbedService._L(loc, "embed.analysis.weakness.quad"),
                value="  ".join(f"{EmbedService._type_emoji(t)} {t.capitalize()}" for t in analysis.quad_weaknesses),
                inline=False,
            )
        if analysis.weaknesses:
            lines = [
                EmbedService._L(loc, "embed.analysis.weakness.line", icon=EmbedService._type_emoji(t), type=t.capitalize(), mult=f"{mult:.1f}")
                for t, mult in analysis.weaknesses[:6]
            ]
            embed.add_field(name=EmbedService._L(loc, "embed.analysis.weakness.defensive"), value="\n".join(lines), inline=False)
        if analysis.coverage_gaps:
            embed.add_field(
                name=EmbedService._L(loc, "embed.analysis.weakness.gaps"),
                value="  ".join(f"{EmbedService._type_emoji(t)} {t.capitalize()}" for t in analysis.coverage_gaps[:9]),
                inline=False,
            )
        if analysis.resistances:
            embed.add_field(
                name=EmbedService._L(loc, "embed.analysis.weakness.resist"),
                value="  ".join(f"{EmbedService._type_emoji(t)} {t.capitalize()}" for t in analysis.resistances[:9]),
                inline=False,
            )
        embed.set_footer(text=EmbedService._L(loc, "embed.analysis.weakness.footer", coach=coach.name, count=len(coach.team)))
        return embed

    @staticmethod
    def suggestions_card(coach: Coach, suggestions: list[tuple], budget: int, locale: str | None = None) -> discord.Embed:
        loc = locale or i18n.default_locale
        embed = discord.Embed(
            title=EmbedService._L(loc, "embed.analysis.suggest.title", team=coach.team_name),
            colour=0x2ECC71,
        )
        if coach.team_logo_url:
            embed.set_thumbnail(url=coach.team_logo_url)
        if not coach.team:
            embed.description = EmbedService._L(loc, "embed.analysis.suggest.empty_team")
            return embed
        if not suggestions:
            embed.description = EmbedService._L(loc, "embed.analysis.suggest.no_match")
            return embed

        lines = [
            EmbedService._L(
                loc, "embed.analysis.suggest.line",
                name=pokemon.name,
                types="/".join(t.capitalize() for t in pokemon.types),
                points=pokemon.points,
                reason=reason,
            )
            for pokemon, reason in suggestions
        ]
        embed.add_field(name=EmbedService._L(loc, "embed.analysis.suggest.field"), value="\n\n".join(lines), inline=False)
        embed.set_footer(text=EmbedService._L(loc, "embed.analysis.suggest.footer", count=len(coach.team), budget=budget))
        return embed

    @staticmethod
    def replacement_welcome(
        new_coach: Coach,
        old_coach_name: str,
        state: DraftState,
        locale: str | None = None,
    ) -> discord.Embed:
        loc = locale or i18n.default_locale
        embed = discord.Embed(
            title=EmbedService._L(loc, "embed.welcome.title", name=new_coach.name),
            description=EmbedService._L(
                loc, "embed.welcome.desc",
                new=new_coach.name, team=new_coach.team_name,
                old=old_coach_name, division=state.division_name,
            ),
            colour=0x2ECC71,
        )
        if new_coach.team:
            lines = [
                EmbedService._L(loc, "embed.team.line", name=p.name, points=p.points)
                for p in new_coach.team
            ]
            embed.add_field(
                name=EmbedService._L(loc, "embed.welcome.field.team", current=len(new_coach.team), max=state.team_size),
                value="\n".join(lines),
                inline=False,
            )
        embed.add_field(name=EmbedService._L(loc, "embed.welcome.field.points"), value=str(new_coach.remaining_points), inline=True)

        makeup_rounds = sorted(rnd for cid, rnd in state.makeup_queue if cid == new_coach.discord_id)
        if makeup_rounds:
            embed.add_field(
                name=EmbedService._L(loc, "embed.welcome.field.makeups"),
                value="\n".join(EmbedService._L(loc, "embed.welcome.makeup_line", round=r) for r in makeup_rounds),
                inline=True,
            )
            embed.add_field(
                name=EmbedService._L(loc, "embed.welcome.field.how_start"),
                value=EmbedService._L(loc, "embed.welcome.how_makeup"),
                inline=False,
            )
        else:
            embed.add_field(
                name=EmbedService._L(loc, "embed.welcome.field.action"),
                value=EmbedService._L(loc, "embed.welcome.action_pick"),
                inline=False,
            )
        if new_coach.pick_deadline:
            ts = int(new_coach.pick_deadline)
            embed.add_field(
                name=EmbedService._L(loc, "embed.welcome.field.deadline"),
                value=format_coach_deadline(new_coach, ts, loc),
                inline=False,
            )
        if new_coach.team_logo_url:
            embed.set_thumbnail(url=new_coach.team_logo_url)
        embed.set_footer(text=EmbedService._footer(EmbedService._L(loc, "embed.welcome.footer", division=state.division_name)))
        return embed

    # Legacy alias kept for any external references
    @staticmethod
    def replacement_announcement(coach: Coach, division_name: str, locale: str | None = None) -> discord.Embed:
        loc = locale or i18n.default_locale
        return discord.Embed(
            title=EmbedService._L(loc, "embed.replace.title"),
            description=EmbedService._L(loc, "embed.replace.legacy.desc", name=coach.name, division=division_name),
            colour=0xFF0000,
        )

    @staticmethod
    def match_proposal(
        proposal,
        locale: str | None = None,
        status_override=None,
    ) -> discord.Embed:
        from models.match_proposal import MatchProposalStatus

        loc = locale or i18n.default_locale
        status = status_override or proposal.status
        ts = int(proposal.scheduled_utc)
        colour = {
            MatchProposalStatus.PENDING: 0x3498DB,
            MatchProposalStatus.CONFIRMED: 0x2ECC71,
            MatchProposalStatus.DECLINED: 0xE74C3C,
            MatchProposalStatus.COUNTERED: 0x95A5A6,
        }.get(status, 0x3498DB)
        embed = discord.Embed(
            title=EmbedService._L(loc, "embed.match.title", division=proposal.division),
            colour=colour,
        )
        embed.description = EmbedService._L(
            loc,
            "embed.match.description",
            proposer=f"<@{proposal.proposer_id}>",
            opponent=f"<@{proposal.opponent_id}>",
            full=ts,
            relative=ts,
        )
        embed.add_field(
            name=EmbedService._L(loc, "embed.match.field.status"),
            value=EmbedService._L(loc, f"embed.match.status.{status.value}"),
            inline=True,
        )
        embed.set_footer(text=EmbedService._footer())
        return embed

    @staticmethod
    def match_proposal_preview(
        *,
        division: str,
        week: int,
        opponent: discord.Member | discord.User,
        analysis,
        scheduled_utc: float,
        locale: str | None = None,
    ) -> discord.Embed:
        from utils.match_scheduling import format_local_datetime
        from utils.timezone_helper import format_timezone_source

        loc = locale or i18n.default_locale
        ts = int(scheduled_utc)
        colour = 0xFEE75C if analysis.has_strong_warning else 0x3498DB
        embed = discord.Embed(
            title=EmbedService._L(loc, "embed.match.preview.title"),
            description=EmbedService._L(
                loc,
                "embed.match.preview.description",
                opponent=opponent.mention,
                week=week,
                division=division,
            ),
            colour=colour,
        )
        embed.add_field(
            name=EmbedService._L(loc, "embed.match.preview.field.yours"),
            value=EmbedService._L(
                loc,
                "embed.match.preview.time_line",
                local=format_local_datetime(analysis.proposer_local, analysis.proposer_resolution),
                source=format_timezone_source(analysis.proposer_resolution, loc),
                discord_time=f"<t:{ts}:F>",
            ),
            inline=False,
        )
        embed.add_field(
            name=EmbedService._L(loc, "embed.match.preview.field.opponent"),
            value=EmbedService._L(
                loc,
                "embed.match.preview.time_line",
                local=format_local_datetime(analysis.opponent_local, analysis.opponent_resolution),
                source=format_timezone_source(analysis.opponent_resolution, loc),
                discord_time=f"<t:{ts}:F>",
            ),
            inline=False,
        )
        warning_lines = [
            EmbedService._L(loc, key, **kwargs)
            for key, kwargs in analysis.warning_keys
        ]
        embed.add_field(
            name=EmbedService._L(loc, "embed.match.preview.field.analysis"),
            value="\n".join(warning_lines),
            inline=False,
        )
        embed.set_footer(text=EmbedService._L(loc, "embed.match.preview.footer"))
        return embed

    @staticmethod
    def match_week_schedule(
        division: str,
        week: int,
        proposals: list,
        results: list[dict],
        coach_names: dict[str, str],
        locale: str | None = None,
    ) -> discord.Embed:
        from models.match_proposal import MatchProposalStatus

        loc = locale or i18n.default_locale
        embed = discord.Embed(
            title=EmbedService._L(loc, "embed.match.schedule.title", division=division, week=week),
            colour=0x5865F2,
        )
        lines: list[str] = []
        seen_pairs: set[tuple[str, str]] = set()

        for proposal in proposals:
            a = coach_names.get(proposal.proposer_id, proposal.proposer_id)
            b = coach_names.get(proposal.opponent_id, proposal.opponent_id)
            pair = tuple(sorted((a.lower(), b.lower())))
            seen_pairs.add(pair)
            ts = int(proposal.scheduled_utc)
            status_key = f"embed.match.schedule.status.{proposal.status.value}"
            replay = _find_replay_for_pair(results, a, b)
            replay_display = f"[Replay]({replay})" if replay else "—"
            line = EmbedService._L(
                loc,
                "embed.match.schedule.line_proposal",
                coach_a=f"<@{proposal.proposer_id}>",
                coach_b=f"<@{proposal.opponent_id}>",
                time=ts,
                status=EmbedService._L(loc, status_key),
                replay=replay_display,
            )
            lines.append(line)

        for result in results:
            pair = tuple(sorted((result["coach_a"].lower(), result["coach_b"].lower())))
            if pair in seen_pairs:
                continue
            replay = result.get("replay_url") or ""
            replay_display = f"[Replay]({replay})" if replay else "—"
            line = EmbedService._L(
                loc,
                "embed.match.schedule.line_result",
                coach_a=result["coach_a"],
                coach_b=result["coach_b"],
                winner=result.get("winner") or "—",
                replay=replay_display,
            )
            lines.append(line)

        if not lines:
            embed.description = EmbedService._L(loc, "embed.match.schedule.empty")
        else:
            embed.description = "\n".join(lines)
        embed.set_footer(text=EmbedService._footer())
        return embed

    @staticmethod
    def standings(division: str, rows: list[dict], locale: str | None = None) -> discord.Embed:
        loc = locale or i18n.default_locale
        embed = discord.Embed(
            title=EmbedService._L(loc, "embed.standings.title", division=division),
            colour=0xF1C40F,
        )
        if not rows:
            embed.description = EmbedService._L(loc, "embed.standings.empty")
            return embed
        lines = []
        for i, r in enumerate(rows, 1):
            lines.append(
                EmbedService._L(
                    loc,
                    "embed.standings.line",
                    rank=i,
                    coach=r["coach"],
                    team=r.get("team", ""),
                    wins=r["wins"],
                    losses=r["losses"],
                    diff=r["kill_diff"],
                )
            )
        embed.description = "\n".join(lines)
        embed.set_footer(text=EmbedService._footer())
        return embed

    @staticmethod
    def kill_leaderboard(
        division: str,
        rows: list[dict],
        page: int,
        total_pages: int,
        locale: str | None = None,
    ) -> discord.Embed:
        loc = locale or i18n.default_locale
        embed = discord.Embed(
            title=EmbedService._L(loc, "embed.kills.title", division=division),
            colour=0x9B59B6,
        )
        if not rows:
            embed.description = EmbedService._L(loc, "embed.kills.empty")
        else:
            start = page * 10 + 1
            lines = []
            for i, r in enumerate(rows, start):
                lines.append(
                    EmbedService._L(
                        loc,
                        "embed.kills.line",
                        rank=i,
                        pokemon=r["pokemon"],
                        kills=r["kills"],
                        deaths=r["deaths"],
                    )
                )
            embed.description = "\n".join(lines)
        embed.set_footer(
            text=EmbedService._L(loc, "embed.kills.footer", page=page + 1, total=total_pages)
        )
        return embed

    @staticmethod
    def replay_preview(result, locale: str | None = None) -> discord.Embed:
        loc = locale or i18n.default_locale
        embed = discord.Embed(
            title=EmbedService._L(loc, "embed.replay.title"),
            colour=0x1ABC9C,
        )
        embed.add_field(
            name=EmbedService._L(loc, "embed.replay.field.winner"),
            value=result.winner,
            inline=True,
        )
        embed.add_field(
            name=EmbedService._L(loc, "embed.replay.field.score"),
            value=f"{result.player_a} {result.kills_a} — {result.kills_b} {result.player_b}",
            inline=False,
        )
        if result.deaths_by_pokemon:
            faint_lines = [
                f"**{mon}**: {count}" for mon, count in sorted(
                    result.deaths_by_pokemon.items(), key=lambda x: -x[1]
                )[:10]
            ]
            embed.add_field(
                name=EmbedService._L(loc, "embed.replay.field.faints"),
                value="\n".join(faint_lines) or "—",
                inline=False,
            )
        embed.set_footer(text=EmbedService._footer())
        return embed

    @staticmethod
    def trade_proposal(trade, locale: str | None = None) -> discord.Embed:
        loc = locale or i18n.default_locale
        colour = {
            "awaiting_accept": 0x3498DB,
            "awaiting_mod": 0xF39C12,
            "approved": 0x2ECC71,
            "rejected": 0xE74C3C,
            "cancelled": 0x95A5A6,
        }.get(trade.status.value, 0x3498DB)
        embed = discord.Embed(
            title=EmbedService._L(loc, "embed.trade.title", division=trade.division),
            colour=colour,
        )
        embed.description = EmbedService._L(
            loc,
            "embed.trade.description",
            proposer=f"<@{trade.proposer_id}>",
            target=f"<@{trade.target_id}>" if trade.target_id else "Pool",
            offering=trade.offering or "—",
            receiving=trade.receiving,
        )
        embed.add_field(
            name=EmbedService._L(loc, "embed.trade.field.status"),
            value=EmbedService._L(loc, f"embed.trade.status.{trade.status.value}"),
            inline=True,
        )
        embed.add_field(
            name=EmbedService._L(loc, "embed.trade.field.type"),
            value=trade.trade_type.value,
            inline=True,
        )
        embed.set_footer(text=EmbedService._footer())
        return embed

