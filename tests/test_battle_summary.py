"""Tests for Showdown battle log parsing."""

from services.showdown.battle_summary import parse_battle_log
from services.showdown.replay_parser import (
    extract_replay_id,
    parse_replay_log,
    summary_to_match_result,
)

SAMPLE_LOG = """
|player|p1|Alice|
|player|p2|Bob|
|tier|[Gen 9] OU|
|gen|9|
|poke|p1|Pikachu, L50|
|poke|p1|Charizard, L50|
|poke|p2|Blastoise, L50|
|poke|p2|Venusaur, L50|
|turn|1|
|switch|p1a: Sparky|Pikachu, L50|
|switch|p2a: Shell|Blastoise, L50|
|move|p1a: Sparky|Thunderbolt|p2a: Shell
|-damage|p2a: Shell|0 fnt
|faint|p2a: Shell
|turn|2|
|switch|p2b: Leaf|Venusaur, L50|
|move|p1a: Sparky|Thunderbolt|p2b: Leaf
|-damage|p2b: Leaf|0 fnt
|faint|p2b: Leaf
|win|Alice|
"""


def test_parse_battle_log_players_and_winner():
    summary = parse_battle_log(SAMPLE_LOG, replay_url="https://replay.pokemonshowdown.com/gen9ou-123")
    assert summary.player1.name == "Alice"
    assert summary.player2.name == "Bob"
    assert summary.winner == "Alice"
    assert summary.is_tie is False
    assert summary.turns == 2
    assert summary.format == "[Gen 9] OU"
    assert summary.replay_url.endswith("gen9ou-123")


def test_parse_battle_log_teams_and_kills():
    summary = parse_battle_log(SAMPLE_LOG)
    assert len(summary.player1.team) == 2
    assert len(summary.player2.team) == 2

    pikachu = next(m for m in summary.player1.team if m.species == "Pikachu")
    assert pikachu.nickname == "Sparky"
    assert pikachu.kills == 2
    assert pikachu.fainted is False

    blastoise = next(m for m in summary.player2.team if m.species == "Blastoise")
    venusaur = next(m for m in summary.player2.team if m.species == "Venusaur")
    assert blastoise.fainted is True
    assert venusaur.fainted is True

    assert summary.player1.kills == 2
    assert summary.player2.kills == 0
    assert summary.player2.deaths == 2


def test_parse_battle_log_mvp():
    summary = parse_battle_log(SAMPLE_LOG)
    mvp = summary.player1.mvp()
    assert mvp is not None
    assert mvp.species == "Pikachu"
    assert mvp.kills == 2


def test_summary_to_match_result():
    summary = parse_battle_log(SAMPLE_LOG)
    result = summary_to_match_result(summary)
    assert result.winner == "Alice"
    assert result.kills_a == 2
    assert result.kills_b == 0
    assert result.kills_by_pokemon["Pikachu"] == 2
    assert result.deaths_by_pokemon["Blastoise"] == 1
    assert result.deaths_by_pokemon["Venusaur"] == 1


def test_parse_replay_log_compat():
    result = parse_replay_log(SAMPLE_LOG, replay_url="https://example.com/replay")
    assert result.player_a == "Alice"
    assert result.summary is not None
    assert result.summary.winner == "Alice"


def test_extract_replay_id():
    url = "https://replay.pokemonshowdown.com/gen9ou-1234567890-abcdef"
    assert extract_replay_id(url) == "gen9ou-1234567890-abcdef"

    assert extract_replay_id("not-a-url") is None
