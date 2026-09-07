"""Trade system — proposals, validation, admin approval."""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Optional

from models.coach import Coach
from models.pokemon import Pokemon
from models.trade import TradeProposal, TradeStatus, TradeType
from utils.division_helper import load_division_config

logger = logging.getLogger(__name__)


def load_league_config(path: str | None = None) -> dict:
    from utils.league_settings import resolve_league_config_path

    try:
        with open(resolve_league_config_path(path), "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {
            "regular_season_weeks": 10,
            "playoff_start_week": 11,
            "max_trades_per_coach": 3,
            "trade_deadline_week_offset": 2,
            "trades_channel_ids": {},
        }


class TradeService:
    def __init__(self, data_dir: str = "data", sheets=None, draft_service=None) -> None:
        self.data_dir = data_dir
        self.sheets = sheets
        self.draft_service = draft_service
        self.league_config = load_league_config()
        os.makedirs(data_dir, exist_ok=True)
        self._path = os.path.join(data_dir, "trades.json")
        self._trades: dict[str, TradeProposal] = {}
        self._load()

    def _load(self) -> None:
        if not os.path.exists(self._path):
            return
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            for item in raw.get("trades", []):
                t = TradeProposal.from_dict(item)
                self._trades[t.id] = t
        except (json.JSONDecodeError, OSError, KeyError) as e:
            logger.error("[TradeService] load failed: %s", e)

    def _save(self) -> None:
        data = {"trades": [t.to_dict() for t in self._trades.values()]}
        tmp = self._path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, self._path)

    def trade_deadline_week(self) -> int:
        weeks = self.league_config.get("regular_season_weeks", 10)
        offset = self.league_config.get("trade_deadline_week_offset", 2)
        return weeks - offset

    def max_trades_per_coach(self) -> int:
        return int(self.league_config.get("max_trades_per_coach", 3))

    def coach_trade_count(self, division: str, coach_id: str) -> int:
        return sum(
            1 for t in self._trades.values()
            if t.division.lower() == division.lower()
            and t.proposer_id == coach_id
            and t.status == TradeStatus.APPROVED
        )

    def _get_state(self, division: str):
        if not self.draft_service:
            return None
        return self.draft_service._get_state(division)

    @staticmethod
    def _find_roster_pokemon(coach: Coach, pokemon_name: str) -> Optional[Pokemon]:
        key = pokemon_name.strip().lower()
        for pokemon in coach.team:
            if pokemon.name.lower() == key:
                return pokemon
        return None

    @staticmethod
    def _projected_remaining(
        current_remaining: int, giving_points: int, receiving_points: int
    ) -> int:
        """Budget after giving one mon and receiving another."""
        return current_remaining + giving_points - receiving_points

    def _resolve_pokemon_name(self, state, raw_name: str) -> Optional[str]:
        """Resolve user input to a canonical Pokémon name on a roster or in the pool."""
        if not raw_name or not state:
            return None
        raw = raw_name.strip()
        key = raw.lower()

        pool = state.pokemon_pool
        if key in pool:
            return pool[key].name

        for coach in state.coaches:
            found = self._find_roster_pokemon(coach, raw)
            if found:
                return found.name

        alias_mgr = getattr(self.draft_service, "alias_manager", None)
        if alias_mgr:
            pool_names = [p.name for p in pool.values()]
            canonical, _ = alias_mgr.resolve(raw, pool_names)
            if canonical:
                return canonical

        return None

    def validate_trade(
        self,
        division: str,
        proposer_id: str,
        target_id: str,
        offering: str,
        receiving: str,
        trade_type: TradeType,
    ) -> tuple[bool, str]:
        if self.coach_trade_count(division, proposer_id) >= self.max_trades_per_coach():
            return False, "trade.limit_reached"

        state = self._get_state(division)
        if not state:
            return False, "trade.no_roster"

        proposer = state.get_coach_by_id(proposer_id)
        if not proposer:
            return False, "errors.not_coach"

        proposer.recalculate_points(state.total_points)

        receiving_name = self._resolve_pokemon_name(state, receiving) or receiving.strip()

        if trade_type == TradeType.POOL:
            receiving_p = state.pokemon_pool.get(receiving_name.lower())
            if not receiving_p or receiving_p.is_drafted:
                return False, "trade.pool_unavailable"
            if not proposer.can_afford(receiving_p):
                return False, "trade.insufficient_points"
            return True, ""

        offering_name = self._resolve_pokemon_name(state, offering) or offering.strip()
        if not offering_name:
            return False, "trade.pokemon_not_found"

        target = state.get_coach_by_id(target_id)
        if not target:
            return False, "trade.target_not_coach"

        target.recalculate_points(state.total_points)

        proposer_pokemon = self._find_roster_pokemon(proposer, offering_name)
        target_pokemon = self._find_roster_pokemon(target, receiving_name)

        if not proposer_pokemon:
            return False, "trade.not_on_roster"
        if not target_pokemon:
            return False, "trade.target_missing"

        off_pts = proposer_pokemon.points
        recv_pts = target_pokemon.points

        if self._projected_remaining(proposer.remaining_points, off_pts, recv_pts) < 0:
            return False, "trade.insufficient_points"
        if self._projected_remaining(target.remaining_points, recv_pts, off_pts) < 0:
            return False, "trade.target_insufficient_points"
        return True, ""

    def create_trade(
        self,
        division: str,
        proposer_id: str,
        target_id: str,
        offering: str,
        receiving: str,
        trade_type: TradeType,
    ) -> tuple[Optional[TradeProposal], str]:
        ok, err = self.validate_trade(
            division, proposer_id, target_id, offering, receiving, trade_type
        )
        if not ok:
            return None, err

        state = self._get_state(division)
        offering_name = self._resolve_pokemon_name(state, offering) if offering else ""
        receiving_name = self._resolve_pokemon_name(state, receiving) or receiving

        offering_p = state.pokemon_pool.get(offering_name.lower()) if state and offering_name else None
        receiving_p = state.pokemon_pool.get(receiving_name.lower()) if state else None

        if trade_type == TradeType.DIRECT and state:
            proposer = state.get_coach_by_id(proposer_id)
            target = state.get_coach_by_id(target_id)
            proposer_mon = self._find_roster_pokemon(proposer, offering_name) if proposer else None
            target_mon = self._find_roster_pokemon(target, receiving_name) if target else None
            off_pts = proposer_mon.points if proposer_mon else 0
            recv_pts = target_mon.points if target_mon else 0
        else:
            off_pts = offering_p.points if offering_p else 0
            recv_pts = receiving_p.points if receiving_p else 0

        delta_p = recv_pts - off_pts
        delta_t = -delta_p if trade_type == TradeType.DIRECT else -(receiving_p.points if receiving_p else 0)

        trade = TradeProposal(
            id=TradeProposal.new_id(),
            division=division,
            trade_type=trade_type,
            proposer_id=proposer_id,
            target_id=target_id,
            offering=offering_name or offering,
            receiving=receiving_name,
            points_delta_proposer=delta_p,
            points_delta_target=delta_t if trade_type == TradeType.DIRECT else 0,
            status=TradeStatus.AWAITING_MOD if trade_type == TradeType.POOL else TradeStatus.AWAITING_ACCEPT,
            created_at=time.time(),
        )
        self._trades[trade.id] = trade
        self._save()
        if self.sheets and self.sheets.is_connected:
            self.sheets.queue_trade_write(trade.to_dict())
        return trade, ""

    def get(self, trade_id: str) -> Optional[TradeProposal]:
        return self._trades.get(trade_id)

    def accept_trade(self, trade_id: str, user_id: str) -> tuple[bool, str]:
        trade = self.get(trade_id)
        if not trade:
            return False, "trade.not_found"
        if trade.status != TradeStatus.AWAITING_ACCEPT:
            return False, "trade.invalid_status"
        if user_id != trade.target_id and trade.trade_type == TradeType.DIRECT:
            return False, "trade.not_target"

        ok, err = self.validate_trade(
            trade.division,
            trade.proposer_id,
            trade.target_id,
            trade.offering,
            trade.receiving,
            trade.trade_type,
        )
        if not ok:
            return False, err

        trade.status = TradeStatus.AWAITING_MOD
        self._save()
        if self.sheets and self.sheets.is_connected:
            self.sheets.queue_trade_write(trade.to_dict())
        return True, ""

    def approve_trade(self, trade_id: str, mod_id: str) -> tuple[bool, str]:
        trade = self.get(trade_id)
        if not trade or trade.status != TradeStatus.AWAITING_MOD:
            return False, "trade.invalid_status"

        ok, err = self.validate_trade(
            trade.division,
            trade.proposer_id,
            trade.target_id,
            trade.offering,
            trade.receiving,
            trade.trade_type,
        )
        if not ok:
            return False, err

        state = self._get_state(trade.division)
        if not state:
            return False, "trade.no_roster"

        proposer = state.get_coach_by_id(trade.proposer_id)
        target = state.get_coach_by_id(trade.target_id) if trade.trade_type == TradeType.DIRECT else None
        if not proposer:
            return False, "trade.no_roster"

        offering_p = state.pokemon_pool.get(trade.offering.lower())
        receiving_p = state.pokemon_pool.get(trade.receiving.lower())

        if trade.trade_type == TradeType.POOL and receiving_p:
            receiving_p.is_drafted = True
            receiving_p.drafted_by = trade.proposer_id
            proposer.add_pokemon(receiving_p)
        elif trade.trade_type == TradeType.DIRECT and target and offering_p and receiving_p:
            proposer_pokemon = next(p for p in proposer.team if p.name.lower() == trade.offering.lower())
            target_pokemon = next(p for p in target.team if p.name.lower() == trade.receiving.lower())
            proposer.team.remove(proposer_pokemon)
            target.team.remove(target_pokemon)
            proposer.add_pokemon(receiving_p)
            target.add_pokemon(offering_p)
            proposer.recalculate_points(state.total_points)
            target.recalculate_points(state.total_points)

        trade.status = TradeStatus.APPROVED
        trade.approved_by = mod_id
        self._save()
        if self.draft_service:
            self.draft_service.persistence.save(state)
        if self.sheets and self.sheets.is_connected:
            self.sheets.queue_trade_write(trade.to_dict())
        return True, ""

    def reject_trade(self, trade_id: str) -> tuple[bool, str]:
        trade = self.get(trade_id)
        if not trade:
            return False, "trade.not_found"
        trade.status = TradeStatus.REJECTED
        self._save()
        if self.sheets and self.sheets.is_connected:
            self.sheets.queue_trade_write(trade.to_dict())
        return True, ""

    def pending_mod_queue(self, division: Optional[str] = None) -> list[TradeProposal]:
        out = [
            t for t in self._trades.values()
            if t.status == TradeStatus.AWAITING_MOD
        ]
        if division:
            out = [t for t in out if t.division.lower() == division.lower()]
        return sorted(out, key=lambda t: t.created_at)

    def get_trades_channel(self, division: str) -> Optional[int]:
        channels = self.league_config.get("trades_channel_ids", {})
        return channels.get(division) or channels.get(division.lower())
