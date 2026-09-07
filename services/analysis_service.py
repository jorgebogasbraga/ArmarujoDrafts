"""
AnalysisService — team weakness and suggestion analysis.

All logic is rule-based using the Gen 9 type chart.
No AI or external calls required.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from constants.type_chart import ALL_TYPES, get_effectiveness
from models.pokemon import Pokemon


@dataclass
class TeamAnalysis:
    """Results of analysing a coach's current team defensively and offensively."""
    # (attacking_type, avg_multiplier_across_team) sorted by multiplier desc
    weaknesses: list[tuple[str, float]] = field(default_factory=list)
    # Types that hit at least one team member for 4x
    quad_weaknesses: list[str] = field(default_factory=list)
    # Types resisted by more than half the team
    resistances: list[str] = field(default_factory=list)
    # Types the team can hit super-effectively via STAB
    offensive_coverage: set[str] = field(default_factory=set)
    # Types the team has NO STAB super-effective coverage against
    coverage_gaps: list[str] = field(default_factory=list)


def analyse_team(team: list[Pokemon]) -> Optional[TeamAnalysis]:
    """
    Analyse a team's defensive weaknesses and offensive coverage.
    Returns None if the team is empty.
    """
    if not team:
        return None

    n = len(team)
    weak_scores: dict[str, float] = {}
    quad_weaknesses: list[str]    = []
    resist_count: dict[str, int]  = {t: 0 for t in ALL_TYPES}

    for atk_type in ALL_TYPES:
        total_mult = 0.0
        max_mult   = 0.0
        for pokemon in team:
            if not pokemon.types or pokemon.types == ["unknown"]:
                continue
            mult        = get_effectiveness(atk_type, pokemon.types)
            total_mult += mult
            max_mult    = max(max_mult, mult)
            if mult < 1.0:
                resist_count[atk_type] += 1

        avg_mult = total_mult / n
        if avg_mult > 1.0:
            weak_scores[atk_type] = round(avg_mult, 2)
        if max_mult >= 4.0:
            quad_weaknesses.append(atk_type)

    weaknesses  = sorted(weak_scores.items(), key=lambda x: -x[1])
    resistances = [t for t, c in resist_count.items() if c >= (n // 2 + 1)]

    # Offensive: STAB coverage
    coverage: set[str] = set()
    for pokemon in team:
        for stab_type in pokemon.types:
            if stab_type == "unknown":
                continue
            for def_type in ALL_TYPES:
                if get_effectiveness(stab_type, [def_type]) >= 2.0:
                    coverage.add(def_type)

    gaps = [t for t in ALL_TYPES if t not in coverage]

    return TeamAnalysis(
        weaknesses=weaknesses,
        quad_weaknesses=quad_weaknesses,
        resistances=resistances,
        offensive_coverage=coverage,
        coverage_gaps=gaps,
    )


def get_suggestions(
    team: list[Pokemon],
    available_pool: list[Pokemon],
    budget: int,
    max_suggestions: int = 6,
) -> list[tuple[Pokemon, str]]:
    """
    Score available Pokémon by how well they address team gaps.
    Only considers Pokémon the coach can still afford.
    Returns list of (pokemon, reason) sorted best-first.
    """
    if not team:
        return []

    analysis = analyse_team(team)
    if not analysis:
        return []

    top_weak_types = [t for t, _ in analysis.weaknesses[:5]]
    gap_types      = analysis.coverage_gaps[:6]

    scored: list[tuple[float, Pokemon, list[str]]] = []

    for pokemon in available_pool:
        if pokemon.is_banned or pokemon.is_drafted:
            continue
        if pokemon.points > budget:
            continue
        if not pokemon.types or pokemon.types == ["unknown"]:
            continue

        score   = 0.0
        reasons: list[str] = []

        # Defensive value: resists or is immune to team's top weaknesses
        for weak_type in top_weak_types:
            mult = get_effectiveness(weak_type, pokemon.types)
            if mult == 0.0:
                score  += 3.0
                reasons.append(f"immune to {weak_type}")
            elif mult <= 0.5:
                score  += 2.0
                reasons.append(f"resists {weak_type}")

        # Offensive value: covers gaps with STAB
        for gap_type in gap_types:
            for stab_type in pokemon.types:
                if get_effectiveness(stab_type, [gap_type]) >= 2.0:
                    score  += 1.5
                    reasons.append(f"covers {gap_type}")
                    break

        if score > 0.0:
            scored.append((score, pokemon, reasons))

    # Sort by score descending, then by points descending within same score
    scored.sort(key=lambda x: (-x[0], -x[1].points))

    result = []
    for _, pokemon, reasons in scored[:max_suggestions]:
        seen: set[str] = set()
        unique_reasons = [r for r in reasons if not (r in seen or seen.add(r))]
        result.append((pokemon, ", ".join(unique_reasons[:3])))

    return result