"""Rich battle summary parsed from Showdown replay logs."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class MonSummary:
    species: str
    nickname: str = ""
    kills: int = 0
    fainted: bool = False

    @property
    def display_name(self) -> str:
        if self.nickname and self.nickname.lower() != self.species.lower():
            return f"{self.nickname} ({self.species})"
        return self.species


@dataclass
class PlayerSummary:
    name: str
    side: str  # "p1" or "p2"
    team: list[MonSummary] = field(default_factory=list)

    @property
    def kills(self) -> int:
        return sum(m.kills for m in self.team)

    @property
    def deaths(self) -> int:
        return sum(1 for m in self.team if m.fainted)

    def mvp(self) -> Optional[MonSummary]:
        if not self.team:
            return None
        return max(self.team, key=lambda m: (m.kills, not m.fainted, m.species))


@dataclass
class BattleSummary:
    player1: PlayerSummary
    player2: PlayerSummary
    winner: str
    format: str = ""
    tier: str = ""
    turns: int = 0
    replay_url: str = ""
    is_tie: bool = False

    @property
    def loser(self) -> str:
        if self.is_tie:
            return ""
        if self.winner.lower() == self.player1.name.lower():
            return self.player2.name
        return self.player1.name

    def winner_player(self) -> Optional[PlayerSummary]:
        if self.is_tie:
            return None
        if self.winner.lower() == self.player1.name.lower():
            return self.player1
        if self.winner.lower() == self.player2.name.lower():
            return self.player2
        return None

    def loser_player(self) -> Optional[PlayerSummary]:
        if self.is_tie:
            return None
        wp = self.winner_player()
        if wp is self.player1:
            return self.player2
        if wp is self.player2:
            return self.player1
        return None


def _species_from_details(details: str) -> str:
    if not details:
        return "Unknown"
    name = details.split(",")[0].strip()
    if name.startswith("L") and " " in name:
        return name.split(" ", 1)[1]
    return name


def _nickname_from_slot(slot: str) -> str:
    if ":" in slot:
        return slot.split(":", 1)[1].strip()
    return ""


def _slot_id(raw: str) -> str:
    return raw.split(":")[0].strip()


def parse_battle_log(log_text: str, replay_url: str = "") -> BattleSummary:
    """Parse a Showdown protocol log into a structured battle summary."""
    players = {"p1": "Player 1", "p2": "Player 2"}
    teams: dict[str, list[MonSummary]] = {"p1": [], "p2": []}
    slot_mon: dict[str, MonSummary] = {}
    active_slot: dict[str, str] = {}
    last_attacker: dict[str, str] = {}
    tier = ""
    gen = ""
    turns = 0
    winner = ""
    is_tie = False

    def _team_side(slot: str) -> str:
        return slot[:2]

    def _bind_slot(slot: str, species: str, nickname: str = "") -> MonSummary:
        side = _team_side(slot)
        mon = _find_or_create_mon(teams[side], species, nickname, slot_mon.values())
        slot_mon[slot] = mon
        active_slot[slot] = slot
        return mon

    def _credit_kill(victim_slot: str) -> None:
        side = _team_side(victim_slot)
        victim = slot_mon.get(victim_slot)
        if victim:
            victim.fainted = True

        attacker_slot = last_attacker.get(victim_slot)
        if not attacker_slot:
            opp = "p1" if side == "p2" else "p2"
            attacker_slot = active_slot.get(f"{opp}a", "")

        if attacker_slot and attacker_slot in slot_mon:
            slot_mon[attacker_slot].kills += 1

    for raw_line in log_text.splitlines():
        if not raw_line.startswith("|"):
            continue
        parts = raw_line.split("|")
        if len(parts) < 2:
            continue
        cmd = parts[1]

        if cmd == "player" and len(parts) >= 4:
            side, name = parts[2], parts[3].strip()
            if side in players and name:
                players[side] = name
        elif cmd == "tier" and len(parts) >= 3:
            tier = parts[2].strip()
        elif cmd == "gen" and len(parts) >= 3:
            gen = parts[2].strip()
        elif cmd == "turn" and len(parts) >= 3:
            try:
                turns = max(turns, int(parts[2]))
            except ValueError:
                pass
        elif cmd == "poke" and len(parts) >= 4:
            side, details = parts[2], parts[3]
            if side in teams:
                teams[side].append(MonSummary(species=_species_from_details(details)))
        elif cmd in ("switch", "drag", "replace") and len(parts) >= 4:
            slot = _slot_id(parts[2])
            nickname = _nickname_from_slot(parts[2])
            species = _species_from_details(parts[3])
            _bind_slot(slot, species, nickname)
        elif cmd == "move" and len(parts) >= 4:
            attacker = _slot_id(parts[2])
            active_slot[_team_side(attacker)] = attacker
            if len(parts) >= 4 and parts[3]:
                target_raw = parts[3]
                if ":" in target_raw:
                    target = _slot_id(target_raw)
                    last_attacker[target] = attacker
        elif cmd == "faint" and len(parts) >= 3:
            _credit_kill(_slot_id(parts[2]))
        elif cmd == "-damage" and len(parts) >= 3:
            target = parts[2]
            if target.endswith(" fnt") or (len(parts) > 3 and parts[-1].strip() == "fnt"):
                _credit_kill(_slot_id(target.replace(" fnt", "")))
        elif cmd == "win" and len(parts) >= 3:
            winner = parts[2].strip()
        elif cmd == "tie":
            is_tie = True
            winner = "Tie"

    fmt = tier or gen or "Pokémon Showdown"
    p1 = PlayerSummary(name=players["p1"], side="p1", team=teams["p1"])
    p2 = PlayerSummary(name=players["p2"], side="p2", team=teams["p2"])

    if not winner and not is_tie:
        if p1.kills > p2.kills:
            winner = p1.name
        elif p2.kills > p1.kills:
            winner = p2.name
        else:
            winner = p1.name

    return BattleSummary(
        player1=p1,
        player2=p2,
        winner=winner,
        format=fmt,
        tier=tier,
        turns=turns,
        replay_url=replay_url,
        is_tie=is_tie,
    )


def _find_or_create_mon(
    team: list[MonSummary],
    species: str,
    nickname: str,
    bound: list[MonSummary],
) -> MonSummary:
    bound_ids = {id(mon) for mon in bound}
    species_lower = species.lower()
    for mon in team:
        if mon.species.lower() == species_lower and id(mon) not in bound_ids:
            if nickname:
                mon.nickname = nickname
            return mon
    mon = MonSummary(species=species, nickname=nickname)
    team.append(mon)
    return mon
