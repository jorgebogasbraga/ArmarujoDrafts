"""Pick bank wizard — embed builders and session helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import discord

from constants.draft_constants import DEFAULT_EMBED_COLOUR
from constants.embed_colours import BANK_WIZARD_COLOUR
from models.coach import Coach
from models.draft_state import DraftState
from models.pick_bank import ConditionalBranch, PickBank, PlanType
from services.draft.bank_service import validate_bank_activation, validate_bank_pokemon_name
from utils.pokemon_search import build_select_options, bank_search_query_ready
from utils.i18n import i18n


def _L(locale: str, key: str, **kwargs) -> str:
    return i18n.t_locale(locale, key, **kwargs)


@dataclass
class BankWizardSession:
    division_name: str
    coach_id: str
    locale: str
    editing_round: int = 1
    plan_type: str = "simple"
    priority_list: list[str] = field(default_factory=list)
    branches: list[ConditionalBranch] = field(default_factory=list)
    default_list: list[str] = field(default_factory=list)
    picker_page: int = 0
    picker_query: str = ""
    branch_edit_idx: Optional[int] = None
    branch_then_list: list[str] = field(default_factory=list)
    branch_if_round: int = 1
    branch_if_picked: str = ""


def round_coverage_icon(state: DraftState, coach_id: str, round_num: int) -> str:
    bank = state.pick_banks.get(coach_id)
    if not bank or not bank.get_plan_for_round(round_num):
        return "⬜"
    entry = bank.get_plan_for_round(round_num)
    if entry.plan_type == PlanType.CONDITIONAL:
        return "🔀"
    return "✅"


def build_coverage_line(state: DraftState, coach_id: str) -> str:
    icons = [
        f"R{r} {round_coverage_icon(state, coach_id, r)}"
        for r in range(1, state.team_size + 1)
    ]
    per_row = min(6, max(4, len(icons)))
    rows = [
        " · ".join(icons[i : i + per_row])
        for i in range(0, len(icons), per_row)
    ]
    return "\n".join(rows)


def list_pokemon_options(
    state: DraftState,
    *,
    available_only: bool = True,
    query: str = "",
    page: int = 0,
    page_size: int = 25,
) -> tuple[list[discord.SelectOption], int]:
    pool = sorted(
        state.pokemon_pool.values(),
        key=lambda p: (-p.points, p.name.lower()),
    )
    if available_only:
        pool = [p for p in pool if not p.is_drafted and not p.is_banned]
    return build_select_options(
        pool,
        query=query,
        page=page,
        page_size=page_size,
        require_min_length=False,
    )


def validate_priority_line(
    state: DraftState,
    coach: Coach,
    name: str,
    team_size: int,
    locale: str,
) -> Optional[str]:
    err = validate_bank_pokemon_name(state, coach, name, team_size)
    if not err:
        return None
    key, kwargs = err
    return _L(locale, key, **kwargs)


def format_priority_list(
    state: DraftState,
    coach: Coach,
    names: list[str],
    locale: str,
) -> str:
    if not names:
        return _L(locale, "bank.wizard.list_empty")
    lines = []
    for i, name in enumerate(names, 1):
        pokemon = state.pokemon_pool.get(name.lower())
        pts = pokemon.points if pokemon else "?"
        warn = validate_priority_line(state, coach, name, state.team_size, locale)
        flag = f" ⚠️ {warn}" if warn else " ✅"
        lines.append(f"**{i}.** {name} ({pts} pts){flag}")
    return "\n".join(lines)


def format_conditional_summary(entry, locale: str) -> str:
    lines = []
    for b in entry.branches:
        lines.append(
            _L(
                locale,
                "bank.wizard.branch_line",
                round=b.if_round,
                picked=b.if_picked,
                list=" → ".join(b.then_list),
            )
        )
    if entry.default_list:
        lines.append(
            _L(locale, "bank.wizard.else_line", list=" → ".join(entry.default_list))
        )
    return "\n".join(lines) if lines else _L(locale, "bank.wizard.list_empty")


def build_dashboard_embed(
    coach: Coach,
    state: DraftState,
    locale: str,
) -> discord.Embed:
    bank = state.pick_banks.get(coach.discord_id)
    embed = discord.Embed(
        title=_L(locale, "bank.wizard.dashboard_title", name=coach.name),
        description=_L(locale, "bank.wizard.dashboard_desc"),
        colour=BANK_WIZARD_COLOUR,
    )

    if bank is None or not bank.entries:
        status = _L(locale, "bank.wizard.status_no_plans")
        mode = "—"
    else:
        status = _L(locale, "bank.wizard.status_active")
        mode = bank.mode.value

    embed.add_field(name=_L(locale, "bank.wizard.field_status"), value=status, inline=True)
    embed.add_field(name=_L(locale, "bank.wizard.field_mode"), value=mode, inline=True)
    embed.add_field(
        name=_L(locale, "bank.wizard.field_coverage"),
        value=build_coverage_line(state, coach.discord_id),
        inline=False,
    )

    if bank and bank.entries:
        for entry in bank.entries:
            if entry.plan_type == PlanType.CONDITIONAL:
                body = format_conditional_summary(entry, locale)
            else:
                body = " → ".join(entry.priority_list) or _L(locale, "bank.wizard.list_empty")
            embed.add_field(
                name=_L(locale, "bank.wizard.round_plan", round=entry.round_number),
                value=body,
                inline=False,
            )

    missing = [
        r for r in range(1, state.team_size + 1)
        if not bank or not bank.has_plan_for_round(r)
    ]
    if missing:
        embed.add_field(
            name=_L(locale, "bank.wizard.field_gaps"),
            value=_L(locale, "bank.wizard.gaps_list", rounds=", ".join(str(r) for r in missing)),
            inline=False,
        )

    embed.set_footer(text=_L(locale, "bank.wizard.dashboard_footer"))
    return embed


def _picker_footer(loc: str, session: BankWizardSession, base_key: str) -> str:
    footer = _L(loc, base_key)
    if session.picker_query and bank_search_query_ready(session.picker_query):
        prefix = _L(loc, "bank.wizard.search_active_footer", query=session.picker_query)
        return f"{prefix} · {footer}"
    return footer


def build_simple_builder_embed(
    session: BankWizardSession,
    coach: Coach,
    state: DraftState,
) -> discord.Embed:
    loc = session.locale
    embed = discord.Embed(
        title=_L(loc, "bank.wizard.simple_title", round=session.editing_round),
        description=_L(loc, "bank.wizard.simple_desc"),
        colour=0x57F287,
    )
    embed.add_field(
        name=_L(loc, "bank.wizard.field_priority"),
        value=format_priority_list(state, coach, session.priority_list, loc),
        inline=False,
    )
    embed.set_footer(text=_picker_footer(loc, session, "bank.wizard.simple_footer"))
    return embed


def build_conditional_builder_embed(
    session: BankWizardSession,
    coach: Coach,
    state: DraftState,
) -> discord.Embed:
    loc = session.locale
    embed = discord.Embed(
        title=_L(loc, "bank.wizard.cond_title", round=session.editing_round),
        description=_L(loc, "bank.wizard.cond_desc"),
        colour=0x5865F2,
    )
    if session.branches:
        for i, b in enumerate(session.branches, 1):
            embed.add_field(
                name=_L(loc, "bank.wizard.branch_header", n=i),
                value=_L(
                    loc,
                    "bank.wizard.branch_line",
                    round=b.if_round,
                    picked=b.if_picked,
                    list=" → ".join(b.then_list),
                ),
                inline=False,
            )
    else:
        embed.add_field(
            name=_L(loc, "bank.wizard.field_branches"),
            value=_L(loc, "bank.wizard.list_empty"),
            inline=False,
        )
    if session.default_list:
        embed.add_field(
            name=_L(loc, "bank.wizard.field_default"),
            value=" → ".join(session.default_list),
            inline=False,
        )
    embed.set_footer(text=_L(loc, "bank.wizard.cond_footer"))
    return embed


def build_branch_editor_embed(session: BankWizardSession, state: DraftState) -> discord.Embed:
    loc = session.locale
    embed = discord.Embed(
        title=_L(loc, "bank.wizard.branch_edit_title"),
        description=_L(loc, "bank.wizard.branch_edit_desc"),
        colour=0xFEE75C,
    )
    embed.add_field(
        name=_L(loc, "bank.wizard.field_condition"),
        value=_L(
            loc,
            "bank.wizard.branch_condition",
            round=session.branch_if_round,
            picked=session.branch_if_picked or "—",
        ),
        inline=False,
    )
    embed.add_field(
        name=_L(loc, "bank.wizard.field_then"),
        value=(
            " → ".join(session.branch_then_list)
            if session.branch_then_list
            else _L(loc, "bank.wizard.list_empty")
        ),
        inline=False,
    )
    embed.set_footer(text=_picker_footer(loc, session, "bank.wizard.branch_edit_desc"))
    return embed


def build_preview_embed(
    coach: Coach,
    state: DraftState,
    bank: PickBank,
    locale: str,
) -> discord.Embed:
    embed = discord.Embed(
        title=_L(locale, "bank.wizard.preview_title"),
        description=_L(locale, "bank.wizard.preview_desc"),
        colour=0x57F287 if bank.is_active else DEFAULT_EMBED_COLOUR,
    )

    activation_err = validate_bank_activation(state, coach, bank)
    if activation_err:
        key, kwargs = activation_err
        embed.add_field(
            name=_L(locale, "bank.wizard.preview_warnings"),
            value=f"⚠️ {_L(locale, key, **kwargs)}",
            inline=False,
        )
    else:
        embed.add_field(
            name=_L(locale, "bank.wizard.preview_ok"),
            value=_L(locale, "bank.wizard.preview_valid"),
            inline=False,
        )

    for entry in bank.entries:
        if entry.plan_type == PlanType.CONDITIONAL:
            body = format_conditional_summary(entry, locale)
        else:
            body = " → ".join(entry.priority_list)
        embed.add_field(
            name=_L(locale, "bank.wizard.round_plan", round=entry.round_number),
            value=body,
            inline=False,
        )

    missing = [r for r in range(1, state.team_size + 1) if not bank.has_plan_for_round(r)]
    if missing:
        embed.add_field(
            name=_L(locale, "bank.wizard.field_gaps"),
            value=_L(locale, "bank.wizard.gaps_warn", rounds=", ".join(str(r) for r in missing)),
            inline=False,
        )

    return embed
