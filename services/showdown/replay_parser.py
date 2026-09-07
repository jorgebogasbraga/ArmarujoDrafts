"""Parse Pokémon Showdown replay logs for match results and kill stats."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse

import aiohttp

from services.showdown.battle_summary import BattleSummary, parse_battle_log

_REPLAY_ID = re.compile(r"/([a-z0-9]+-[a-z0-9]+-[a-z0-9]+)")


@dataclass
class MatchResult:
    winner: str
    player_a: str
    player_b: str
    kills_a: int = 0
    kills_b: int = 0
    kills_by_pokemon: dict[str, int] = field(default_factory=dict)
    deaths_by_pokemon: dict[str, int] = field(default_factory=dict)
    replay_url: str = ""
    summary: Optional[BattleSummary] = None


def extract_replay_id(url: str) -> Optional[str]:
    parsed = urlparse(url.strip())
    path = parsed.path or url
    match = _REPLAY_ID.search(path)
    return match.group(1) if match else None


async def fetch_replay_log(replay_url: str) -> str:
    replay_id = extract_replay_id(replay_url)
    if not replay_id:
        raise ValueError("Invalid replay URL")

    base = "https://replay.pokemonshowdown.com"
    json_url = f"{base}/{replay_id}.json"
    log_url = f"{base}/{replay_id}.log"

    async with aiohttp.ClientSession() as session:
        async with session.get(json_url) as resp:
            if resp.status == 200:
                data = await resp.json()
                if isinstance(data, dict) and "log" in data:
                    return data["log"]
        async with session.get(log_url) as resp:
            if resp.status == 200:
                return await resp.text()
    raise ValueError("Could not fetch replay log")


def summary_to_match_result(summary: BattleSummary) -> MatchResult:
    kills_by: dict[str, int] = {}
    deaths_by: dict[str, int] = {}
    for player in (summary.player1, summary.player2):
        for mon in player.team:
            if mon.kills:
                kills_by[mon.species] = kills_by.get(mon.species, 0) + mon.kills
            if mon.fainted:
                deaths_by[mon.species] = deaths_by.get(mon.species, 0) + 1

    return MatchResult(
        winner=summary.winner,
        player_a=summary.player1.name,
        player_b=summary.player2.name,
        kills_a=summary.player1.kills,
        kills_b=summary.player2.kills,
        kills_by_pokemon=kills_by,
        deaths_by_pokemon=deaths_by,
        replay_url=summary.replay_url,
        summary=summary,
    )


def parse_replay_log(log_text: str, replay_url: str = "") -> MatchResult:
    summary = parse_battle_log(log_text, replay_url=replay_url)
    return summary_to_match_result(summary)


async def parse_replay_url(replay_url: str) -> MatchResult:
    log = await fetch_replay_log(replay_url)
    return parse_replay_log(log, replay_url=replay_url)


async def parse_battle_summary_url(replay_url: str) -> BattleSummary:
    log = await fetch_replay_log(replay_url)
    return parse_battle_log(log, replay_url=replay_url)
