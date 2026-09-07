"""
DraftService — the core draft engine.
"""

import asyncio
import logging
import time
import discord
from typing import Optional, Callable, Awaitable
from models.draft_state import DraftState, PickRecord
from models.coach import Coach
from models.pokemon import Pokemon
from models.pick_bank import PickBank, BankSnipePending, BankMode, ConditionalBranch, PlanType
from services.draft.bank_service import (
    resolve_priority_list,
    find_pickable,
    first_available_in_list,
    all_options_sniped,
    parse_conditional_branches,
    validate_bank_plan_entry,
    validate_bank_activation,
    diagnose_bank_failure,
    pool_pokemon_names,
    resolve_bank_name_list,
    resolve_bank_pokemon_name,
    resolve_runtime_bank_names,
)
from constants.draft_constants import DraftStatus
from services.persistence_service import PersistenceService
from services.sheets_service import SheetsService
from services.embed_service import EmbedService
from services.public_messages import (
    bank_snipe_message,
    channel_ping,
    default_public_text,
    draft_resume_message,
    makeup_reminder_message,
    pick_announcement,
    pick_lead_in,
    quiet_hours_end_message,
    quiet_hours_start_message,
    replacement_division_message,
    replacement_global_message,
    skip_announcement,
)
from services.admin_log_service import AdminLogService
from services.draft.pick_validator import PickValidator
from services.draft.timer_service import DraftTimerService
from services.draft.replacement_handler import ReplacementHandler
from utils.quiet_hours import get_quiet_hours
from utils.sheet_layout import get_sheet_layout
from utils.alias_manager import AliasManager
from utils.i18n import i18n
from config import Config

logger = logging.getLogger(__name__)


def _channel_ping(coach: Coach) -> str:
    return channel_ping(coach)


def _bank_error_message(
    coach_discord_id: str | None,
    err: tuple[str, dict],
    *,
    public: bool = False,
) -> str:
    key, kwargs = err
    if public:
        return default_public_text(key, **kwargs)
    return i18n.t(coach_discord_id, key, **kwargs)


def _format_bank_normalizations(normalizations: list[tuple[str, str]]) -> str:
    if not normalizations:
        return ""
    return ", ".join(f"{raw}→{canonical}" for raw, canonical in normalizations)

SendCallback = Callable[[int, dict], Awaitable[None]]
DmCallback = Callable[[int, dict], Awaitable[None]]


class DraftService:
    def __init__(
        self,
        persistence,
        sheets,
        alias_manager,
        send_callback=None,
        channel_lock=None,
        pokemon_service=None,
    ):
        self.persistence    = persistence
        self.sheets         = sheets
        self.alias_manager  = alias_manager
        self.channel_lock   = channel_lock
        self.pokemon_service = pokemon_service
        self._simulation_mode = False
        self._system_test_session_dir: str | None = None
        self._send_callback = send_callback
        self._dm_callback: Optional[DmCallback] = None
        self.admin_log      = AdminLogService()
        self.states: dict[str, DraftState] = {}
        self._bank_snipe_tokens: dict[str, int] = {}
        self._bank_snipe_tick_starts: dict[str, float] = {}
        self._bg_tasks: set[asyncio.Task] = set()
        self.quiet_hours = get_quiet_hours()
        self._quiet_hours_task: Optional[asyncio.Task] = None
        self.timers = DraftTimerService(
            persistence=persistence,
            on_timeout=self._handle_timeout,
            on_replacement_timeout=self._handle_replacement_timeout,
            quiet_hours=self.quiet_hours,
        )
        self._restore_states()

    @property
    def dm_callback(self) -> Optional[DmCallback]:
        return self._dm_callback

    @dm_callback.setter
    def dm_callback(self, callback: Optional[DmCallback]) -> None:
        self._dm_callback = callback

    @property
    def send_callback(self):
        return self._send_callback

    @send_callback.setter
    def send_callback(self, callback):
        self._send_callback = callback

    @property
    def simulation_mode(self) -> bool:
        return self._simulation_mode

    def enter_simulation_mode(self) -> None:
        self._simulation_mode = True
        suppress = getattr(self.sheets, "suppress_misc_writes", None)
        if callable(suppress):
            suppress(True)
        logger.info("[DraftService] Simulation mode enabled.")

    def exit_simulation_mode(self) -> None:
        for task in list(self._bg_tasks):
            task.cancel()
        self._bg_tasks.clear()
        for division in list(self._bank_snipe_tokens):
            self._bank_snipe_tokens[division] = (
                self._bank_snipe_tokens.get(division, 0) + 1
            )
        self._simulation_mode = False
        suppress = getattr(self.sheets, "suppress_misc_writes", None)
        if callable(suppress):
            suppress(False)
        logger.info("[DraftService] Simulation mode disabled.")

    def _spawn(self, coro) -> None:
        """Track background work so simulation shutdown can cancel it."""
        try:
            task = asyncio.get_running_loop().create_task(coro)
        except RuntimeError:
            return
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

    def set_system_test_session(self, session_dir: str | None) -> None:
        self._system_test_session_dir = session_dir

    def _bank_snipe_pause_seconds(self) -> float:
        if self._simulation_mode:
            return float(Config.SIMULATION_BANK_SNIPE_PAUSE_SECONDS)
        return float(Config.BANK_SNIPE_PAUSE_SECONDS)

    async def _ensure_pokemon_embed_data(self, pokemon: Pokemon) -> None:
        """Resolve sprite + types for pick embeds (same path as enriched pool picks)."""
        if not self.pokemon_service:
            return
        needs_enrich = (
            not pokemon.sprite_url
            or not pokemon.types
            or pokemon.types == ["unknown"]
        )
        if needs_enrich:
            await self.pokemon_service.enrich_pokemon(pokemon)

    async def _apply_default_pick_image(self, pokemon: Pokemon, record: PickRecord) -> None:
        """PokeAPI official art, then Showdown GIF, then PokémonDB."""
        if not self.pokemon_service:
            return
        url = await self.pokemon_service.resolve_embed_image(pokemon)
        if url:
            record.gif_url = url
            if not pokemon.sprite_url:
                pokemon.sprite_url = url
            return
        if not pokemon.sprite_url:
            logger.warning("[DraftService] No sprite or GIF for '%s'", pokemon.name)

    def _schedule(self, coro) -> None:
        """Fire-and-forget async work (admin log, etc.) when an event loop is running."""
        try:
            asyncio.get_running_loop().create_task(coro)
        except RuntimeError:
            pass

    # ── Setup ────────────────────────────────────────────────────────────────

    def _restore_states(self) -> None:
        saved = self.persistence.load_all()
        for name, state in saved.items():
            self.states[name] = state
            logger.info(f"[DraftService] Restored state: {name} (status={state.status})")

    def sync_coaches_from_sheet(
        self,
        division_name: str,
        user_id: str | None = None,
        *,
        num_coaches: int | None = None,
        first_coach: str | None = None,
        persist_division_config: bool = False,
    ) -> tuple[bool, str, list[dict]]:
        """Pull coaches from the Participants sheet into coaches.json."""
        from utils.division_helper import (
            load_division_config,
            load_coaches_config,
            save_coaches_config,
            save_division_config,
        )

        config_list = load_division_config()
        div_cfg = next(
            (d for d in config_list if d["name"].lower() == division_name.lower()),
            None,
        )
        if not div_cfg:
            return False, i18n.t(
                user_id, "service.division_not_in_config", division=division_name
            ), []

        resolved_num = num_coaches if num_coaches is not None else div_cfg.get("num_coaches", 0)
        resolved_first = (
            first_coach.strip()
            if first_coach is not None
            else div_cfg.get("first_coach", "").strip()
        )

        # An explicit draft-order range already says who drafts and in what
        # position, so those leagues have no first_coach row to anchor on.
        uses_draft_order = get_sheet_layout().draft_order() is not None
        if resolved_num <= 0 or (not resolved_first and not uses_draft_order):
            return False, i18n.t(
                user_id, "service.no_coaches_sync", division=division_name
            ), []

        if persist_division_config:
            div_cfg["num_coaches"] = resolved_num
            div_cfg["first_coach"] = resolved_first
            save_division_config(config_list)

        logger.info(
            "[DraftService] Syncing coaches for '%s' from Participants sheet "
            "(num_coaches=%s, first_coach='%s').",
            division_name,
            resolved_num,
            resolved_first,
        )

        participants = self.sheets.read_participants_for_division(
            div_cfg["name"], config_list
        )
        if len(participants) != resolved_num:
            return False, i18n.t(
                user_id,
                "service.auto_sync_failed",
                expected=resolved_num,
                division=division_name,
                found=len(participants),
            ), []

        all_coaches = load_coaches_config()
        all_coaches = [
            c for c in all_coaches
            if c.get("division", "").lower() != division_name.lower()
        ]
        for p in participants:
            all_coaches.append({
                "name": p["name"],
                "team_name": p["team_name"],
                "discord_id": p["discord_id"],
                "logo_url": p["logo_url"],
                "timezone": p["timezone"],
                "division": div_cfg["name"],
            })
        save_coaches_config(all_coaches)

        coach_entries = [
            c for c in all_coaches
            if c.get("division", "").lower() == division_name.lower()
        ]
        logger.info(
            "[DraftService] Coach sync complete — %d coaches for '%s'.",
            len(coach_entries),
            division_name,
        )
        return True, i18n.t(
            user_id,
            "admin.sync_success",
            count=len(coach_entries),
            division=div_cfg["name"],
        ), coach_entries

    def initialise_division(
        self, division_name: str, user_id: str | None = None
    ) -> tuple[bool, str]:
        from utils.division_helper import load_division_config

        config_list = load_division_config()
        div_cfg = next(
            (d for d in config_list if d["name"].lower() == division_name.lower()), None
        )
        if not div_cfg:
            return False, i18n.t(
                user_id, "service.division_not_in_config", division=division_name
            )

        if division_name in self.states:
            s = self.states[division_name]
            if s.status not in (DraftStatus.PENDING, DraftStatus.COMPLETED):
                return False, i18n.t(
                    user_id,
                    "service.division_already_init",
                    division=division_name,
                    status=s.status,
                )

        ok, sync_msg, coach_entries = self.sync_coaches_from_sheet(
            division_name, user_id
        )
        if not ok:
            return False, sync_msg

        coaches: list[Coach] = []
        for c in coach_entries:
            coaches.append(Coach(
                discord_id=str(c["discord_id"]),
                name=c["name"],
                team_name=c.get("team_name", "TBA"),
                team_logo_url=c.get("logo_url", ""),
                timezone=c.get("timezone", "GMT+0"),
                division_name=div_cfg["name"],
                remaining_points=div_cfg["total_points"],
                tera_points_remaining=div_cfg.get("tera_captain_points", 30),
            ))

        pool_list = self.sheets.read_master_pool()
        if not pool_list:
            return False, i18n.t(user_id, "service.pool_read_failed")

        pokemon_pool = {p.name.lower(): p for p in pool_list}
        card_block_start_row = self.sheets.compute_division_block_start_row(
            div_cfg["name"], config_list
        )

        state = DraftState(
            division_name=div_cfg["name"],
            channel_id=div_cfg.get("channel_id", 0),
            team_size=div_cfg["team_size"],
            total_points=div_cfg["total_points"],
            tera_captain_points=div_cfg.get("tera_captain_points", 30),
            pick_time_initial=div_cfg.get("pick_time_initial", 14400),
            pick_time_second=div_cfg.get("pick_time_second", 7200),
            pick_time_final=div_cfg.get("pick_time_final", 3600),
            sheet_name=div_cfg.get("sheet_name", div_cfg["name"]),
            coaches=coaches,
            pokemon_pool=pokemon_pool,
            card_block_start_row=card_block_start_row,
            status=DraftStatus.PENDING,
        )

        self.states[div_cfg["name"]] = state
        self.persistence.save(state)
        self._schedule(self.admin_log.draft_initialized(
            state, len(coaches), len(pool_list)
        ))
        logger.info(
            f"[DraftService] Initialised '{div_cfg['name']}' with {len(coaches)} coaches "
            f"(card block starts at row {card_block_start_row}) and {len(pool_list)} Pokémon."
        )
        return True, i18n.t(
            user_id,
            "service.division_init_success",
            division=div_cfg["name"],
            coaches=len(coaches),
            pool=len(pool_list),
        )

    def start_draft(
        self, division_name: str, user_id: str | None = None
    ) -> tuple[bool, str]:
        state = self._get_state(division_name)
        if not state:
            return False, i18n.t(
                user_id, "service.division_not_init", division=division_name
            )
        if state.status == DraftStatus.ACTIVE:
            return False, i18n.t(user_id, "service.draft_already_active")
        if state.status == DraftStatus.COMPLETED:
            return False, i18n.t(user_id, "service.draft_already_completed")

        state.status = DraftStatus.ACTIVE
        self.persistence.save(state)
        self.timers.start_timer(state)
        first = state.current_coach.name if state.current_coach else "Unknown"
        self._schedule(self.admin_log.draft_started(state, first))
        logger.info(f"[DraftService] Draft started for '{division_name}'.")
        return True, i18n.t(
            user_id,
            "service.draft_started",
            mention=state.current_coach.mention(),
        )

    # ── Core pick logic ──────────────────────────────────────────────────────

    async def make_pick(
        self,
        division_name: str,
        coach_discord_id: str,
        raw_pokemon_name: str,
        is_makeup: bool = False,
        picked_by_discord_id: str | None = None,
        *,
        requester_is_staff: bool = False,
    ) -> tuple[bool, str, Optional[discord.Embed], Optional[discord.ui.View]]:
        state = self._get_state(division_name)
        if not state:
            return False, i18n.t(coach_discord_id, "service.not_initialised"), None, None

        async with state._lock:
            if state.bank_snipe_pending and state.bank_snipe_pending.coach_discord_id == coach_discord_id:
                self._clear_bank_snipe(state, restart_timer=False)
            return await self._execute_pick(
                state,
                coach_discord_id,
                raw_pokemon_name,
                is_makeup=is_makeup,
                picked_by_discord_id=picked_by_discord_id or coach_discord_id,
                requester_is_staff=requester_is_staff,
            )

    async def _execute_pick(
        self,
        state: DraftState,
        coach_discord_id: str,
        raw_pokemon_name: str,
        is_makeup: bool = False,
        is_bank: bool = False,
        makeup_round: int = 0,
        picked_by_discord_id: str | None = None,
        *,
        requester_is_staff: bool = False,
    ) -> tuple[bool, str, Optional[discord.Embed], Optional[discord.ui.View]]:

        # ── Status gate ───────────────────────────────────────────────────────
        ok, err = PickValidator.validate_draft_status(
            state,
            is_makeup,
            coach_discord_id,
            requester_is_staff=requester_is_staff,
        )
        if not ok:
            if err == "__PAUSED_STAFF_ONLY__":
                uid = picked_by_discord_id or coach_discord_id
                return False, i18n.t(uid, "service.paused_staff_only"), None, None
            return False, err, None, None

        # ── Resolve coach ─────────────────────────────────────────────────────
        if is_makeup:
            coach = state.get_coach_by_id(coach_discord_id)
        else:
            coach = state.current_coach
            if not coach:
                return False, i18n.t(coach_discord_id, "service.no_current_coach"), None, None

        if not coach:
            return False, i18n.t(coach_discord_id, "service.coach_not_found"), None, None

        coach.bank_all_sniped_exhausted = False

        # Defensive resync
        coach.recalculate_points(state.total_points)

        # A coach with a full team cannot pick more Pokémon
        ok, err = PickValidator.validate_team_not_full(coach, state.team_size)
        if not ok:
            return False, err, None, None

        # ── Resolve Pokémon name ──────────────────────────────────────────────
        valid_names = [
            p.name for p in state.pokemon_pool.values()
            if not p.is_drafted and not p.is_banned
        ]
        canonical, was_fuzzy = self.alias_manager.resolve(raw_pokemon_name, valid_names)

        if canonical is None:
            all_names = [p.name for p in state.pokemon_pool.values()]
            close = self.alias_manager.resolve(raw_pokemon_name, all_names)
            if close[0]:
                taken = state.pokemon_pool.get(close[0].lower())
                if taken and taken.is_drafted:
                    drafter = state.get_coach_by_id(taken.drafted_by)
                    if drafter:
                        return (
                            False,
                            f"**{close[0]}** has already been drafted by "
                            f"**{drafter.name}** ({drafter.team_name}).",
                            None,
                            None,
                        )
            return (
                False,
                f"**{raw_pokemon_name}** could not be matched to any available "
                f"Pokémon. Check spelling and try again.",
                None,
                None,
            )

        if was_fuzzy:
            return False, f"__FUZZY_MATCH__{canonical}", None, None

        # Form ambiguity: 3+ formes still confirm. Two-form pairs
        # (Landorus/Landorus-T, Urshifu/Urshifu-RS) use the unsuffixed default.
        related_forms = self.alias_manager.find_related_forms(canonical, valid_names)
        if (
            related_forms
            and not PickValidator.is_unambiguous_input(raw_pokemon_name, canonical)
            and not self.alias_manager.is_default_form_pair(related_forms)
        ):
            return False, f"__FORM_CONFIRM__{canonical}__{'|'.join(related_forms)}", None, None

        pokemon = state.get_available_pokemon(canonical.lower())
        if not pokemon:
            existing = state.pokemon_pool.get(canonical.lower())
            if existing and existing.is_drafted:
                drafter = state.get_coach_by_id(existing.drafted_by)
                if drafter:
                    return (
                        False,
                        f"**{canonical}** has already been drafted by "
                        f"**{drafter.name}** ({drafter.team_name}).",
                        None,
                        None,
                    )
                return False, f"**{canonical}** has already been drafted.", None, None
            if existing and existing.is_banned:
                return False, f"**{canonical}** is banned in this draft.", None, None
            return False, f"**{canonical}** is not available in this pool.", None, None

        # ── Species / form conflict (team or pick bank, same Pokédex #) ───────
        conflict = PickValidator.species_conflict_for_pick(state, pokemon, coach)
        if conflict:
            key, kwargs = conflict
            return False, i18n.t(coach_discord_id, key, **kwargs), None, None

        # ── Point validation ──────────────────────────────────────────────────
        ok, err = PickValidator.validate_pick(coach, pokemon, state.team_size)
        if not ok:
            return False, err, None, None

        # ── Apply the pick ────────────────────────────────────────────────────
        round_for_record = makeup_round if is_makeup else state.current_round

        self.timers.cancel_timer(state)
        coach.add_pokemon(pokemon)
        state.global_pick_counter += 1

        record = PickRecord(
            pick_number=state.global_pick_counter,
            round_number=round_for_record,
            coach_discord_id=coach.discord_id,
            coach_name=coach.name,
            pokemon_name=pokemon.name,
            points_cost=pokemon.points,
            is_makeup=is_makeup,
            is_bank=is_bank,
            timestamp=time.time(),
            picked_by_discord_id=picked_by_discord_id,
        )
        state.pick_history.append(record)

        if is_makeup:
            coach.makeup_picks_owed -= 1
            state.makeup_queue = [
                (cid, rnd) for cid, rnd in state.makeup_queue
                if not (cid == coach_discord_id and rnd == makeup_round)
            ]
            # If this is the replacement's last makeup, resume the draft
            if (
                state.status == DraftStatus.REPLACEMENT_PENDING
                and coach_discord_id == state.replacement_coach_discord_id
            ):
                state.replacement_picks_owed = max(0, state.replacement_picks_owed - 1)
                if state.replacement_picks_owed == 0:
                    state.replacement_coach_discord_id = None
                    state.status = DraftStatus.ACTIVE
                    logger.info(
                        f"[DraftService] Replacement makeups complete — "
                        f"draft resumed in {state.division_name}. "
                        f"Replacement's normal pick is next."
                    )
                    self.timers.start_timer(state)
        else:
            state.advance_snake()

        self.persistence.save(state)
        await self.admin_log.pick_made(
            state,
            coach.name,
            pokemon.name,
            pokemon.points,
            record.pick_number,
            is_makeup=is_makeup,
            is_bank=is_bank,
        )
        self._queue_record_sheet_write(state, record, pokemon.name)

        # ── Draft complete? ───────────────────────────────────────────────────
        if state.is_complete():
            state.status = DraftStatus.COMPLETED
            self.persistence.save(state)
            await self.admin_log.draft_completed(state)
            self.sheets.queue_status_write(state.division_name, "Draft completed!")
            self.persistence.delete(state.division_name)
            await self._ensure_pokemon_embed_data(pokemon)
            await self._apply_default_pick_image(pokemon, record)
            embed, view = pick_announcement(state, coach, pokemon, record, None)
            return True, "DRAFT_COMPLETE", embed, view

        if not is_makeup and state.status == DraftStatus.ACTIVE:
            self.timers.start_timer(state)
            self._spawn(self._try_bank_pick(state))

        await self._ensure_pokemon_embed_data(pokemon)
        await self._apply_default_pick_image(pokemon, record)

        next_coach = state.current_coach if not is_makeup else None
        embed, view = pick_announcement(state, coach, pokemon, record, next_coach)
        return True, "OK", embed, view

    async def reset_division(
        self, division_name: str, user_id: str | None = None
    ) -> tuple[bool, str]:
        state = self._get_state(division_name)
        if not state:
            return False, i18n.t(
                user_id, "service.division_not_init", division=division_name
            )

        self.timers.cancel_timer(state)
        if self.channel_lock:
            await self.channel_lock.release(state)
        keys_to_remove = [k for k in self.states if k.lower() == division_name.lower()]
        self._schedule(self.admin_log.division_reset(state))
        for k in keys_to_remove:
            del self.states[k]
        self.persistence.delete(division_name)

        logger.info(f"[DraftService] Division '{division_name}' reset.")
        return True, i18n.t(
            user_id, "service.division_reset", division=division_name
        )

    # ── Makeup picks ──────────────────────────────────────────────────────────

    async def make_makeup_pick(
        self,
        division_name: str,
        coach_discord_id: str,
        raw_pokemon_name: str,
        *,
        picked_by_discord_id: str | None = None,
        requester_is_staff: bool = False,
    ) -> tuple[bool, str, Optional[discord.Embed], Optional[discord.ui.View]]:
        state = self._get_state(division_name)
        if not state:
            return False, i18n.t(coach_discord_id, "errors.division_not_found"), None, None

        requester_id = picked_by_discord_id or coach_discord_id

        async with state._lock:
            coach_entries = [
                (cid, rnd) for cid, rnd in state.makeup_queue
                if cid == coach_discord_id
            ]
            if not coach_entries:
                coach = state.get_coach_by_id(coach_discord_id)
                name  = coach.name if coach else coach_discord_id
                return False, i18n.t(
                    requester_id, "makeup.no_pending", name=name
                ), None, None

            makeup_round = min(rnd for _, rnd in coach_entries)
            return await self._execute_pick(
                state, coach_discord_id, raw_pokemon_name,
                is_makeup=True, makeup_round=makeup_round,
                picked_by_discord_id=requester_id,
                requester_is_staff=requester_is_staff,
            )

    # ── Pick bank ─────────────────────────────────────────────────────────────

    def _normalize_bank_names(
        self,
        state: DraftState,
        coach_discord_id: str,
        raw_names: list[str],
    ) -> tuple[Optional[list[str]], Optional[str], list[tuple[str, str]]]:
        """Resolve raw user input to canonical sheet names via AliasManager."""
        all_names = pool_pokemon_names(state)
        resolved, err, normalizations = resolve_bank_name_list(
            raw_names, all_names, self.alias_manager
        )
        if err:
            return None, _bank_error_message(coach_discord_id, err), []
        return resolved, None, normalizations

    def _resolve_runtime_bank_list(
        self, state: DraftState, names: list[str]
    ) -> list[str]:
        return resolve_runtime_bank_names(state, names, self.alias_manager)

    def set_bank_plan(
        self,
        division_name: str,
        coach_discord_id: str,
        round_number: int,
        priority_list: list[str],
    ) -> tuple[bool, str]:
        state = self._get_state(division_name)
        if not state:
            return False, i18n.t(coach_discord_id, "errors.division_not_found")
        coach = state.get_coach_by_id(coach_discord_id)
        if not coach:
            return False, i18n.t(coach_discord_id, "service.not_participant")

        err = self._validate_bank_round_edit(state, coach_discord_id, round_number)
        if err:
            return False, err

        priority_list, norm_err, normalizations = self._normalize_bank_names(
            state, coach_discord_id, priority_list
        )
        if norm_err:
            return False, norm_err

        bank = state.pick_banks.get(coach_discord_id)
        plan_err = validate_bank_plan_entry(
            state,
            coach,
            bank,
            round_number,
            priority_lists=[priority_list],
            all_new_names=priority_list,
        )
        if plan_err:
            return False, _bank_error_message(coach_discord_id, plan_err)

        if coach_discord_id not in state.pick_banks:
            state.pick_banks[coach_discord_id] = PickBank(
                coach_discord_id=coach_discord_id,
                division_name=division_name,
            )
        state.pick_banks[coach_discord_id].set_plan(round_number, priority_list)
        self._refresh_snipe_remaining(state, coach_discord_id, round_number, priority_list)
        self._sync_bank_active(state, coach_discord_id)
        self.persistence.save(state)

        msg = i18n.t(
            coach_discord_id,
            "bank.plan_set",
            round=round_number,
            list=" → ".join(priority_list),
        )
        if normalizations:
            msg += " " + i18n.t(
                coach_discord_id,
                "bank.names_normalized",
                mapping=_format_bank_normalizations(normalizations),
            )
        return True, msg

    def set_bank_conditional_plan(
        self,
        division_name: str,
        coach_discord_id: str,
        round_number: int,
        branches_str: str,
    ) -> tuple[bool, str]:
        branches, default_list, parse_err = parse_conditional_branches(branches_str)
        if parse_err:
            return False, parse_err
        return self.set_bank_conditional_structured(
            division_name, coach_discord_id, round_number, branches, default_list
        )

    def set_bank_conditional_structured(
        self,
        division_name: str,
        coach_discord_id: str,
        round_number: int,
        branches: list,
        default_list: list[str],
    ) -> tuple[bool, str]:
        from models.pick_bank import ConditionalBranch

        state = self._get_state(division_name)
        if not state:
            return False, i18n.t(coach_discord_id, "errors.division_not_found")
        coach = state.get_coach_by_id(coach_discord_id)
        if not coach:
            return False, i18n.t(coach_discord_id, "service.not_participant")

        err = self._validate_bank_round_edit(state, coach_discord_id, round_number)
        if err:
            return False, err

        if not branches:
            return False, i18n.t(coach_discord_id, "bank.wizard.need_branch")

        all_names = pool_pokemon_names(state)
        all_normalizations: list[tuple[str, str]] = []
        normalized_branches: list[ConditionalBranch] = []

        for branch in branches:
            if isinstance(branch, ConditionalBranch):
                b = ConditionalBranch(branch.if_round, branch.if_picked, list(branch.then_list))
            else:
                b = ConditionalBranch(
                    branch["if_round"], branch["if_picked"], list(branch["then_list"])
                )

            canonical, err = resolve_bank_pokemon_name(
                b.if_picked, all_names, self.alias_manager
            )
            if err:
                return False, _bank_error_message(coach_discord_id, err)
            if canonical.lower() != b.if_picked.strip().lower():
                all_normalizations.append((b.if_picked.strip(), canonical))
            b.if_picked = canonical

            resolved_then, err, norms = resolve_bank_name_list(
                b.then_list, all_names, self.alias_manager
            )
            if err:
                return False, _bank_error_message(coach_discord_id, err)
            b.then_list = resolved_then
            all_normalizations.extend(norms)
            normalized_branches.append(b)

        resolved_default: list[str] = []
        if default_list:
            resolved_default, err, norms = resolve_bank_name_list(
                default_list, all_names, self.alias_manager
            )
            if err:
                return False, _bank_error_message(coach_discord_id, err)
            all_normalizations.extend(norms)

        priority_lists = [b.then_list for b in normalized_branches]
        if resolved_default:
            priority_lists.append(resolved_default)
        all_new_names: list[str] = []
        for names in priority_lists:
            all_new_names.extend(names)

        bank = state.pick_banks.get(coach_discord_id)
        plan_err = validate_bank_plan_entry(
            state,
            coach,
            bank,
            round_number,
            priority_lists=priority_lists,
            all_new_names=all_new_names,
        )
        if plan_err:
            return False, _bank_error_message(coach_discord_id, plan_err)

        if coach_discord_id not in state.pick_banks:
            state.pick_banks[coach_discord_id] = PickBank(
                coach_discord_id=coach_discord_id,
                division_name=division_name,
            )
        state.pick_banks[coach_discord_id].set_conditional_plan(
            round_number, normalized_branches, resolved_default
        )
        resolved = resolve_priority_list(
            state.pick_banks[coach_discord_id].get_plan_for_round(round_number),
            state,
            coach_discord_id,
        )
        self._refresh_snipe_remaining(state, coach_discord_id, round_number, resolved)
        self._sync_bank_active(state, coach_discord_id)
        self.persistence.save(state)

        summary_parts = [
            f"if R{b.if_round}={b.if_picked} → {' → '.join(b.then_list)}"
            for b in normalized_branches
        ]
        if resolved_default:
            summary_parts.append(f"else → {' → '.join(resolved_default)}")
        msg = i18n.t(
            coach_discord_id,
            "bank.conditional_set",
            round=round_number,
            summary=" | ".join(summary_parts),
        )
        if all_normalizations:
            msg += " " + i18n.t(
                coach_discord_id,
                "bank.names_normalized",
                mapping=_format_bank_normalizations(all_normalizations),
            )
        return True, msg

    def set_bank_mode(
        self, division_name: str, coach_discord_id: str, mode: str
    ) -> tuple[bool, str]:
        state = self._get_state(division_name)
        if not state:
            return False, i18n.t(coach_discord_id, "errors.division_not_found")
        bank = state.pick_banks.get(coach_discord_id)
        if not bank:
            return False, i18n.t(coach_discord_id, "bank.no_plans")
        try:
            bank.mode = BankMode(mode.lower())
        except ValueError:
            return False, i18n.t(coach_discord_id, "bank.invalid_mode")
        self.persistence.save(state)
        return True, i18n.t(
            coach_discord_id, "bank.mode_set", mode=bank.mode.value
        )

    def _sync_bank_active(self, state: DraftState, coach_discord_id: str) -> None:
        bank = state.pick_banks.get(coach_discord_id)
        if not bank:
            return
        if bank.entries:
            bank.activate()
        else:
            bank.deactivate()

    def clear_all_bank_plans(
        self, division_name: str, coach_discord_id: str
    ) -> tuple[bool, str]:
        state = self._get_state(division_name)
        if not state:
            return False, i18n.t(coach_discord_id, "errors.division_not_found")
        bank = state.pick_banks.get(coach_discord_id)
        if not bank or not bank.entries:
            return False, i18n.t(coach_discord_id, "bank.no_plans")
        bank.entries.clear()
        bank.deactivate()
        had_snipe = (
            state.bank_snipe_pending
            and state.bank_snipe_pending.coach_discord_id == coach_discord_id
        )
        if had_snipe:
            self._clear_bank_snipe(state, restart_timer=True)
        else:
            self.persistence.save(state)
        return True, i18n.t(coach_discord_id, "bank.wizard.cleared_all")

    def activate_bank(self, division_name: str, coach_discord_id: str) -> tuple[bool, str]:
        state = self._get_state(division_name)
        if not state:
            return False, i18n.t(coach_discord_id, "errors.division_not_found")
        bank = state.pick_banks.get(coach_discord_id)
        if not bank or not bank.entries:
            return False, i18n.t(coach_discord_id, "bank.no_plans")

        coach = state.get_coach_by_id(coach_discord_id)
        if not coach:
            return False, i18n.t(coach_discord_id, "service.not_participant")

        if state.current_coach and state.current_coach.discord_id == coach_discord_id:
            future_entries = [e for e in bank.entries if e.round_number > state.current_round]
            if not future_entries and not state.bank_snipe_pending:
                return False, i18n.t(coach_discord_id, "bank.turn_now")

        activation_err = validate_bank_activation(state, coach, bank)
        if activation_err:
            return False, _bank_error_message(coach_discord_id, activation_err)

        bank.activate()
        self.persistence.save(state)
        return True, i18n.t(coach_discord_id, "bank.activated")

    def deactivate_bank(self, division_name: str, coach_discord_id: str) -> tuple[bool, str]:
        state = self._get_state(division_name)
        if not state:
            return False, i18n.t(coach_discord_id, "errors.division_not_found")
        bank = state.pick_banks.get(coach_discord_id)
        if bank:
            bank.deactivate()
        had_snipe = (
            state.bank_snipe_pending
            and state.bank_snipe_pending.coach_discord_id == coach_discord_id
        )
        if had_snipe:
            self._clear_bank_snipe(state, restart_timer=True)
        else:
            self.persistence.save(state)
        return True, i18n.t(coach_discord_id, "bank.deactivated")

    def _validate_bank_round_edit(
        self, state: DraftState, coach_discord_id: str, round_number: int
    ) -> Optional[str]:
        if state.bank_snipe_pending and state.bank_snipe_pending.coach_discord_id == coach_discord_id:
            return None
        if (
            state.current_coach
            and state.current_coach.discord_id == coach_discord_id
            and round_number == state.current_round
        ):
            return i18n.t(
                coach_discord_id,
                "bank.turn_round_now",
                round=round_number,
            )
        return None

    def _refresh_snipe_remaining(
        self,
        state: DraftState,
        coach_discord_id: str,
        round_number: int,
        priority_list: list[str],
    ) -> None:
        snipe = state.bank_snipe_pending
        if not snipe or snipe.coach_discord_id != coach_discord_id:
            return
        if snipe.round_number != round_number:
            return
        sniped_lower = snipe.sniped_primary.lower()
        if priority_list and priority_list[0].lower() == sniped_lower:
            snipe.remaining_priority = priority_list[1:]
        else:
            snipe.remaining_priority = list(priority_list)

    def _clear_bank_snipe(self, state: DraftState, restart_timer: bool = True) -> None:
        self._bank_snipe_tokens[state.division_name] = (
            self._bank_snipe_tokens.get(state.division_name, 0) + 1
        )
        state.bank_snipe_pending = None
        self.persistence.save(state)
        if restart_timer and state.status == DraftStatus.ACTIVE and state.current_coach:
            self.timers.start_timer(state)

    def _schedule_bank_snipe_timer(self, state: DraftState, seconds: float) -> None:
        division = state.division_name
        if state.bank_snipe_pending:
            state.bank_snipe_pending.remaining_seconds = seconds
            state.bank_snipe_pending.sync_deadline()
            self.persistence.save(state)
        token = self._bank_snipe_tokens.get(division, 0) + 1
        self._bank_snipe_tokens[division] = token

        if self.quiet_hours.is_quiet():
            # The fallback window only burns while timers run, so a snipe at
            # 02:00 still gives its owner the full pause after resume time.
            self._bank_snipe_tick_starts.pop(division, None)
            logger.info(
                "[DraftService] Quiet hours — holding %.0fs bank snipe pause for %s.",
                seconds,
                division,
            )
            return

        self._bank_snipe_tick_starts[division] = time.time()
        asyncio.create_task(
            self._bank_snipe_timer_loop(division, seconds, token)
        )

    async def _bank_snipe_timer_loop(
        self, division_name: str, seconds: float, token: int
    ) -> None:
        remaining = seconds
        try:
            while remaining > 0:
                if self._bank_snipe_tokens.get(division_name) != token:
                    return
                chunk = min(30.0, remaining)
                await asyncio.sleep(chunk)
                remaining -= chunk
                if self._bank_snipe_tokens.get(division_name) != token:
                    return
                state = self._get_state(division_name)
                if state and state.bank_snipe_pending:
                    state.bank_snipe_pending.remaining_seconds = remaining
                    state.bank_snipe_pending.sync_deadline()
                    self.persistence.save(state)
            if self._bank_snipe_tokens.get(division_name) == token:
                await self._handle_bank_snipe_timeout(division_name)
        except asyncio.CancelledError:
            pass

    def _snapshot_bank_snipe_remaining(self, state: DraftState) -> None:
        snipe = state.bank_snipe_pending
        if not snipe:
            return
        division = state.division_name
        tick = self._bank_snipe_tick_starts.get(division)
        if tick is not None:
            elapsed = time.time() - tick
            snipe.remaining_seconds = max(0.0, snipe.remaining_seconds - elapsed)
        snipe.sync_deadline()
        self._bank_snipe_tick_starts.pop(division, None)

    def _freeze_bank_snipe(self, state: DraftState) -> None:
        self._snapshot_bank_snipe_remaining(state)
        self._bank_snipe_tokens[state.division_name] = (
            self._bank_snipe_tokens.get(state.division_name, 0) + 1
        )
        self.persistence.save(state)

    async def _fail_bank_plan(
        self,
        state: DraftState,
        coach: Coach,
        bank: PickBank,
        failure: tuple[str, dict],
    ) -> None:
        """Deactivate the bank and post a specific failure reason to the channel."""
        bank.deactivate()
        self.persistence.save(state)
        if self.send_callback:
            detail = _bank_error_message(coach.discord_id, failure, public=True)
            await self.send_callback(
                state.channel_id,
                {"content": f"⚠️ {coach.mention()} — {detail}"},
            )
        if state.status == DraftStatus.ACTIVE and state.current_coach:
            self.timers.start_timer(state)

    async def _bank_all_sniped_exhausted(
        self,
        state: DraftState,
        coach: Coach,
        bank: PickBank,
        round_number: int,
        sniped_names: list[str],
    ) -> None:
        """Bank was used but every listed option was taken by other coaches."""
        bank.deactivate()
        coach.bank_all_sniped_exhausted = True
        self.persistence.save(state)
        logger.info(
            "[DraftService] Bank all-sniped for %s in %s R%d (%d options): %s",
            coach.name,
            state.division_name,
            round_number,
            len(sniped_names),
            ", ".join(sniped_names) or "—",
        )
        if self.send_callback:
            embed = EmbedService.bank_all_sniped_public(
                coach,
                state.division_name,
                round_number,
            )
            await self.send_callback(
                state.channel_id,
                {"content": channel_ping(coach), "embed": embed},
            )
        if state.status == DraftStatus.ACTIVE and state.current_coach:
            self.timers.start_timer(state)

    async def _handle_bank_plan_failure(
        self,
        state: DraftState,
        coach: Coach,
        bank: PickBank,
        priority: list[str],
        failure: tuple[str, dict],
    ) -> None:
        if all_options_sniped(state, coach, priority, state.team_size):
            await self._bank_all_sniped_exhausted(
                state,
                coach,
                bank,
                state.current_round,
                priority,
            )
        else:
            await self._fail_bank_plan(state, coach, bank, failure)

    async def _handle_bank_snipe_timeout(self, division_name: str) -> None:
        state = self._get_state(division_name)
        if not state or not state.bank_snipe_pending:
            return
        if state.status != DraftStatus.ACTIVE:
            async with state._lock:
                self._clear_bank_snipe(state, restart_timer=False)
            return

        async with state._lock:
            snipe = state.bank_snipe_pending
            if not snipe:
                return
            coach = state.get_coach_by_id(snipe.coach_discord_id)
            bank = state.pick_banks.get(snipe.coach_discord_id)
            if not coach or not bank or not bank.is_active:
                self._clear_bank_snipe(state, restart_timer=True)
                return

            state.bank_snipe_pending = None
            self.persistence.save(state)

            remaining = self._resolve_runtime_bank_list(
                state, list(snipe.remaining_priority)
            )
            plan_entry = bank.get_plan_for_round(snipe.round_number)
            full_priority = (
                self._resolve_runtime_bank_list(
                    state,
                    resolve_priority_list(plan_entry, state, coach.discord_id),
                )
                if plan_entry
                else remaining
            )
            scan_list = remaining or full_priority

            if bank.mode == BankMode.STRICT and not remaining:
                bank.deactivate()
                if self.send_callback:
                    await self.send_callback(
                        state.channel_id,
                        {
                            "content": default_public_text(
                                "bank.snipe_strict_end",
                                mention=coach.mention(),
                            )
                        },
                    )
                self.timers.start_timer(state)
                return

            pick_name = first_available_in_list(
                state, coach, scan_list, state.team_size
            )
            if pick_name:
                await self._commit_bank_pick(state, coach, bank, pick_name)
                return

            if all_options_sniped(state, coach, full_priority, state.team_size):
                await self._bank_all_sniped_exhausted(
                    state,
                    coach,
                    bank,
                    snipe.round_number,
                    full_priority,
                )
                return

            failure = diagnose_bank_failure(
                state,
                coach,
                scan_list,
                bank.mode,
                state.team_size,
                snipe.round_number,
            )
            await self._fail_bank_plan(state, coach, bank, failure)

    async def _enter_bank_snipe_pause(
        self,
        state: DraftState,
        coach: Coach,
        bank: PickBank,
        sniped_primary: str,
        remaining: list[str],
    ) -> None:
        self.timers.cancel_timer(state)
        pause = self._bank_snipe_pause_seconds()

        state.bank_snipe_pending = BankSnipePending(
            coach_discord_id=coach.discord_id,
            round_number=state.current_round,
            sniped_primary=sniped_primary,
            remaining_seconds=pause,
            remaining_priority=remaining,
        )
        state.bank_snipe_pending.sync_deadline()
        coach.set_pick_timer(pause)
        self.persistence.save(state)
        self._schedule_bank_snipe_timer(state, pause)

        ts = int(state.bank_snipe_pending.deadline)
        deferred_to = self.quiet_hours.resume_timestamp()
        if self.send_callback:
            public_embed, public_view = bank_snipe_message(
                coach, state.division_name, ts, resume_ts=deferred_to
            )
            await self.send_callback(
                state.channel_id,
                {"embed": public_embed, "view": public_view},
            )

        dm_embed = EmbedService.bank_snipe_dm(
            coach, sniped_primary, ts, remaining, resume_ts=deferred_to
        )
        if self._simulation_mode:
            logger.info(
                "[DraftService] Simulation snipe for %s in %s R%d — "
                "mirroring DM in-channel, not sending to the coach.",
                coach.name,
                state.division_name,
                state.current_round,
            )
            if self.send_callback:
                await self.send_callback(
                    state.channel_id,
                    {
                        "content": default_public_text(
                            "bank.snipe.sim_channel_notice",
                            name=coach.name,
                        ),
                        "embed": dm_embed,
                    },
                )
        elif self.dm_callback:
            try:
                await self.dm_callback(int(coach.discord_id), {"embed": dm_embed})
            except Exception as e:
                logger.warning("[DraftService] Could not DM coach %s: %s", coach.name, e)

        self._schedule(self.admin_log.draft_paused(
            state, f"Bank snipe pause for {coach.name} (Round {state.current_round})"
        ))

    async def _commit_bank_pick(
        self,
        state: DraftState,
        coach: Coach,
        bank: PickBank,
        pokemon_name: str,
    ) -> None:
        success, msg, embed, view = await self._execute_pick(
            state, coach.discord_id, pokemon_name, is_bank=True
        )
        if not success:
            logger.error("[DraftService] Bank pick failed for %s: %s", coach.name, msg)
            bank.deactivate()
            if self.send_callback:
                await self.send_callback(
                    state.channel_id,
                    {
                        "content": default_public_text(
                            "bank.pick_failed",
                            mention=coach.mention(),
                        )
                    },
                )
            self.timers.start_timer(state)
            return

        if success and self.send_callback:
            await self._announce_bank_pick(state, coach, embed, view)

    async def _announce_bank_pick(
        self,
        state: DraftState,
        coach: Coach,
        embed,
        view,
    ) -> None:
        record = state.pick_history[-1] if state.pick_history else None
        lead = pick_lead_in(record) if record else default_public_text(
            "embed.pick.lead_bank"
        )
        payload: dict = {
            "content": lead,
            "embed": embed,
            "view": view,
        }
        await self.send_callback(state.channel_id, payload)

        if state.makeup_queue:
            reminder_embed, reminder_view = makeup_reminder_message(state)
            if reminder_embed:
                await self.send_callback(
                    state.channel_id,
                    {"embed": reminder_embed, "view": reminder_view},
                )

        next_coach = state.current_coach
        if next_coach and next_coach.pick_deadline:
            await self.send_callback(
                state.channel_id,
                {"content": _channel_ping(next_coach)},
            )

    async def _try_bank_pick(self, state: DraftState) -> None:
        try:
            await asyncio.sleep(1)
        except asyncio.CancelledError:
            return
        async with state._lock:
            if state.status != DraftStatus.ACTIVE:
                return
            if state.bank_snipe_pending:
                return
            coach = state.current_coach
            if not coach:
                return
            bank = state.pick_banks.get(coach.discord_id)
            if not bank or not bank.is_active:
                return
            plan_entry = bank.get_plan_for_round(state.current_round)
            if not plan_entry:
                return

            priority = resolve_priority_list(plan_entry, state, coach.discord_id)
            priority = self._resolve_runtime_bank_list(state, priority)
            if not priority:
                if plan_entry.plan_type == PlanType.CONDITIONAL:
                    await self._fail_bank_plan(
                        state,
                        coach,
                        bank,
                        (
                            "bank.conditional_no_match",
                            {"round": state.current_round},
                        ),
                    )
                return

            if all_options_sniped(state, coach, priority, state.team_size):
                await self._bank_all_sniped_exhausted(
                    state,
                    coach,
                    bank,
                    state.current_round,
                    priority,
                )
                return

            pick_name, sniped_primary, remaining = find_pickable(
                state, coach, priority, bank.mode, state.team_size
            )

            if sniped_primary:
                if not first_available_in_list(
                    state, coach, remaining, state.team_size
                ):
                    await self._bank_all_sniped_exhausted(
                        state,
                        coach,
                        bank,
                        state.current_round,
                        priority,
                    )
                    return
                await self._enter_bank_snipe_pause(
                    state, coach, bank, sniped_primary, remaining
                )
                return

            if pick_name:
                await self._commit_bank_pick(state, coach, bank, pick_name)
                return

            failure = diagnose_bank_failure(
                state,
                coach,
                priority,
                bank.mode,
                state.team_size,
                state.current_round,
            )
            await self._handle_bank_plan_failure(
                state, coach, bank, priority, failure
            )

    # ── Timers and skips ──────────────────────────────────────────────────────

    async def _handle_timeout(self, division_name: str) -> None:
        state = self._get_state(division_name)
        if not state or state.status != DraftStatus.ACTIVE:
            return

        async with state._lock:
            if state.status != DraftStatus.ACTIVE:
                return

            coach = state.current_coach
            if not coach:
                return

            # Ignore stale/duplicate timeout calls (e.g. timer restarted after a pick)
            if coach.pick_deadline and coach.pick_deadline > time.time() + 1:
                logger.info(
                    "[DraftService] Ignoring stale timeout for %s in %s — deadline not reached",
                    coach.name,
                    division_name,
                )
                return

            if state.bank_snipe_pending:
                if state.bank_snipe_pending.coach_discord_id == coach.discord_id:
                    logger.info(
                        "[DraftService] Timeout suppressed for %s in %s — bank snipe pending",
                        coach.name,
                        division_name,
                    )
                    return

            skip_penalty = True
            if coach.bank_all_sniped_exhausted:
                coach.bank_all_sniped_exhausted = False
                skip_penalty = False
                logger.info(
                    "[DraftService] Skip without timer penalty for %s in %s "
                    "— bank options were all sniped",
                    coach.name,
                    division_name,
                )

            if skip_penalty:
                coach.skip_count += 1
            coach.makeup_picks_owed += 1
            state.makeup_queue.append((coach.discord_id, state.current_round))

            logger.info(
                f"[DraftService] Timeout: {coach.name} in {division_name} "
                f"— skip #{coach.skip_count}"
            )

            if coach.skip_count >= 3:
                state.status = DraftStatus.WAITING_REPLACE
                state.pending_replace_coach_id = coach.discord_id
                self.persistence.save(state)
                self._schedule(self.admin_log.replacement_needed(state, coach.name))

                role_mention = (
                    f"<@&{Config.REPLACEMENT_ROLE_ID}>"
                    if Config.REPLACEMENT_ROLE_ID else ""
                )
                if self.send_callback:
                    division_embed, division_view = replacement_division_message(
                        coach, state
                    )
                    await self.send_callback(
                        state.channel_id,
                        {
                            "content": role_mention,
                            "embed": division_embed,
                            "view": division_view,
                        },
                    )
                    if Config.ANNOUNCEMENTS_CHANNEL_ID:
                        global_embed, global_view = replacement_global_message(
                            coach, state
                        )
                        await self.send_callback(
                            Config.ANNOUNCEMENTS_CHANNEL_ID,
                            {
                                "content": role_mention,
                                "embed": global_embed,
                                "view": global_view,
                            },
                        )
                return

            # Advance snake FIRST so the new coach's deadline is set correctly
            state.advance_snake()
            self.persistence.save(state)
            self._schedule(self.admin_log.skip(state, coach.name, coach.skip_count))
            self.timers.start_timer(state)
            self._spawn(self._try_bank_pick(state))

            next_coach = state.current_coach
            next_time  = (
                int(next_coach.pick_deadline)
                if next_coach and next_coach.pick_deadline
                else int(time.time() + 3600)
            )

            skip_embed, skip_view = skip_announcement(
                coach, coach.skip_count, next_time, deadline_coach=next_coach
            )

            if self.send_callback:
                # Message 1: skip embed
                await self.send_callback(
                    state.channel_id,
                    {"embed": skip_embed, "view": skip_view},
                )
                logger.info(
                    "[DraftService] Skip announcement posted for %s in %s → next: %s",
                    coach.name,
                    division_name,
                    next_coach.name if next_coach else "?",
                )

                # Message 2 (optional): makeup pending reminder
                if state.makeup_queue:
                    reminder_embed, reminder_view = makeup_reminder_message(state)
                    if reminder_embed:
                        await self.send_callback(
                            state.channel_id,
                            {"embed": reminder_embed, "view": reminder_view},
                        )

                # Message 3: ping with countdown
                if next_coach and next_coach.pick_deadline:
                    await self.send_callback(
                        state.channel_id,
                        {"content": _channel_ping(next_coach)},
                    )

    # ── Replace system ────────────────────────────────────────────────────────

    async def apply_replacement(
        self,
        division_name: str,
        new_coach_discord_id: str,
        new_coach_name: str,
        new_team_name: str = "",
        new_logo_url: str = "",
        new_timezone: str = "GMT+0",
    ) -> tuple[bool, str]:
        state = self._get_state(division_name)
        if not state:
            return False, i18n.t(None, "errors.division_not_found")
        if state.status != DraftStatus.WAITING_REPLACE:
            return False, i18n.t(None, "service.not_awaiting_replacement")

        old_coach = ReplacementHandler.get_old_coach(state)
        if not old_coach:
            return False, i18n.t(None, "service.old_coach_not_found")
        old_name = old_coach.name

        success, message = ReplacementHandler.apply(
            state,
            new_coach_discord_id=new_coach_discord_id,
            new_coach_name=new_coach_name,
            new_team_name=new_team_name,
            new_logo_url=new_logo_url,
            new_timezone=new_timezone,
        )
        if not success:
            return False, message

        if self._system_test_session_dir:
            from simulation.replacement_audit import append_replacement

            append_replacement(
                self._system_test_session_dir,
                division=division_name,
                old_coach={
                    "discord_id": old_coach.discord_id,
                    "name": old_coach.name,
                    "team_name": old_coach.team_name,
                    "timezone": old_coach.timezone,
                    "logo_url": old_coach.team_logo_url,
                },
                new_coach={
                    "discord_id": new_coach_discord_id,
                    "name": new_coach_name,
                    "team_name": new_team_name or old_coach.team_name,
                    "timezone": new_timezone,
                    "logo_url": new_logo_url or old_coach.team_logo_url,
                },
            )

        if state.status == DraftStatus.ACTIVE:
            self.timers.start_timer(state)

        asyncio.create_task(self._update_participants_sheet(
            old_discord_id=old_coach.discord_id,
            new_discord_id=new_coach_discord_id,
            new_name=new_coach_name,
            new_team_name=new_team_name or old_coach.team_name,
            new_timezone=new_timezone,
            new_logo_url=new_logo_url or old_coach.team_logo_url,
        ))

        new_coach = state.get_coach_by_id(new_coach_discord_id)
        if new_coach:
            new_coach.set_pick_timer(float(state.pick_time_initial))
        self.timers.start_replacement_timer(state)
        self.persistence.save(state)
        self._schedule(self.admin_log.replacement_applied(
            state, old_name, new_coach_name
        ))
        return True, message

    async def _handle_replacement_timeout(self, division_name: str) -> None:
        state = self._get_state(division_name)
        if not state or state.status != DraftStatus.REPLACEMENT_PENDING:
            return

        async with state._lock:
            if state.status != DraftStatus.REPLACEMENT_PENDING:
                return

            coach = state.get_coach_by_id(state.replacement_coach_discord_id)
            if not coach:
                return

            logger.warning(
                f"[DraftService] Replacement {coach.name} timed out in {division_name}."
            )

            state.status                       = DraftStatus.WAITING_REPLACE
            state.pending_replace_coach_id     = coach.discord_id
            state.replacement_coach_discord_id = None
            state.replacement_picks_owed       = 0
            self.persistence.save(state)

            role_mention = (
                f"<@&{Config.REPLACEMENT_ROLE_ID}>"
                if Config.REPLACEMENT_ROLE_ID else ""
            )
            if self.send_callback:
                division_embed, division_view = replacement_division_message(
                    coach, state
                )
                await self.send_callback(
                    state.channel_id,
                    {
                        "content": default_public_text(
                            "admin.replacement_timeout",
                            coach=coach.name,
                            role=role_mention,
                        ),
                        "embed": division_embed,
                        "view": division_view,
                    },
                )
                if Config.ANNOUNCEMENTS_CHANNEL_ID:
                    global_embed, global_view = replacement_global_message(
                        coach, state
                    )
                    await self.send_callback(
                        Config.ANNOUNCEMENTS_CHANNEL_ID,
                        {
                            "content": role_mention,
                            "embed": global_embed,
                            "view": global_view,
                        },
                    )

    async def _update_participants_sheet(
        self,
        old_discord_id: str,
        new_discord_id: str,
        new_name: str,
        new_team_name: str,
        new_timezone: str,
        new_logo_url: str,
    ) -> None:
        await asyncio.to_thread(
            self.sheets.update_participant_row,
            old_discord_id,
            new_discord_id,
            new_name,
            new_team_name,
            new_timezone,
            new_logo_url,
        )

    # ── Pick corrections ────────────────────────────────────────────────────

    def _coach_position(self, state: DraftState, coach_discord_id: str) -> int:
        for i, coach in enumerate(state.coaches):
            if coach.discord_id == coach_discord_id:
                return i
        return 0

    def _queue_record_sheet_write(
        self,
        state: DraftState,
        record: PickRecord,
        pokemon_name: str,
    ) -> None:
        from utils.division_helper import load_division_config

        division_config = load_division_config()
        block_start_row = self.sheets.compute_division_block_start_row(
            state.division_name, division_config
        )
        if block_start_row != state.card_block_start_row:
            logger.info(
                "[DraftService] Refreshed card_block_start_row for %s: %s → %s",
                state.division_name,
                state.card_block_start_row,
                block_start_row,
            )
            state.card_block_start_row = block_start_row
        self.sheets.queue_pick_write(
            division_name=state.division_name,
            position_in_division=self._coach_position(state, record.coach_discord_id),
            pick_index=record.round_number - 1,
            pokemon_name=pokemon_name,
            division_block_start_row=block_start_row,
            num_coaches=len(state.coaches),
        )

    async def undo_last_pick(
        self,
        division_name: str,
        user_id: str,
    ) -> tuple[bool, str]:
        state = self._get_state(division_name)
        if not state:
            return False, i18n.t(user_id, "errors.division_not_found")
        if state.status not in (DraftStatus.ACTIVE, DraftStatus.PAUSED):
            return False, i18n.t(user_id, "undo.draft_not_undoable", status=state.status.value)

        async with state._lock:
            if not state.pick_history:
                return False, i18n.t(user_id, "undo.no_picks")

            last = state.pick_history[-1]
            if last.pick_number != state.global_pick_counter:
                return False, i18n.t(user_id, "undo.not_last_pick")

            allowed_ids = {last.coach_discord_id}
            if last.picked_by_discord_id:
                allowed_ids.add(last.picked_by_discord_id)
            if user_id not in allowed_ids:
                return False, i18n.t(user_id, "undo.not_authorized")

            coach = state.get_coach_by_id(last.coach_discord_id)
            pokemon = state.pokemon_pool.get(last.pokemon_name.lower())

            self.timers.cancel_timer(state)
            if state.bank_snipe_pending:
                self._clear_bank_snipe(state, restart_timer=False)

            if coach:
                coach.team = [
                    p for p in coach.team
                    if p.name.lower() != last.pokemon_name.lower()
                ]
                coach.recalculate_points(state.total_points)

            if pokemon:
                pokemon.is_drafted = False
                pokemon.drafted_by = None

            state.pick_history.pop()
            state.global_pick_counter -= 1

            if last.is_makeup:
                if coach:
                    coach.makeup_picks_owed += 1
                state.makeup_queue.append((last.coach_discord_id, last.round_number))
            else:
                from services.draft.state_rebuild import compute_cursor_after_picks

                normal_picks = sum(1 for r in state.pick_history if not r.is_makeup)
                idx, rnd, direction = compute_cursor_after_picks(
                    len(state.coaches), normal_picks
                )
                state.current_coach_index = idx
                state.current_round = rnd
                state.snake_direction = direction

            if state.status == DraftStatus.COMPLETED:
                state.status = DraftStatus.ACTIVE

            self._queue_record_sheet_write(state, last, "")
            self.persistence.save(state)
            self._schedule(
                self.admin_log.pick_correction(
                    state,
                    "undo",
                    last.coach_name,
                    last.pokemon_name,
                    last.pick_number,
                )
            )

            if state.status == DraftStatus.ACTIVE and not last.is_makeup:
                self.timers.start_timer(state)

        return True, i18n.t(
            user_id,
            "undo.success",
            pick=last.pick_number,
            pokemon=last.pokemon_name,
            coach=last.coach_name,
        )

    def goto_pick(
        self,
        division_name: str,
        pick_number: int,
        user_id: str | None = None,
    ) -> tuple[bool, str]:
        state = self._get_state(division_name)
        if not state:
            return False, i18n.t(user_id, "errors.division_not_found")
        if state.status != DraftStatus.PAUSED:
            return False, i18n.t(user_id, "goto.must_be_paused")
        if pick_number < 1:
            return False, i18n.t(user_id, "goto.invalid_pick")
        if pick_number > state.global_pick_counter + 1:
            return False, i18n.t(
                user_id,
                "goto.pick_not_reached",
                current=state.global_pick_counter + 1,
            )

        removed = [r for r in state.pick_history if r.pick_number >= pick_number]
        kept = [r for r in state.pick_history if r.pick_number < pick_number]

        self.timers.cancel_timer(state)
        if state.bank_snipe_pending:
            self._clear_bank_snipe(state, restart_timer=False)

        from services.draft.state_rebuild import rebuild_rosters_from_history

        rebuild_rosters_from_history(state, kept)

        for record in removed:
            self._queue_record_sheet_write(state, record, "")

        if state.status == DraftStatus.COMPLETED:
            state.status = DraftStatus.PAUSED

        self.persistence.save(state)
        self._schedule(
            self.admin_log.pick_correction(
                state,
                "goto",
                "",
                f"→ pick #{pick_number}",
                pick_number,
            )
        )

        coach = state.current_coach
        coach_name = coach.name if coach else "—"
        return True, i18n.t(
            user_id,
            "goto.success",
            pick=pick_number,
            coach=coach_name,
            removed=len(removed),
        )

    async def edit_pick(
        self,
        division_name: str,
        pick_number: int,
        raw_pokemon_name: str,
        user_id: str | None = None,
    ) -> tuple[bool, str, dict | None]:
        state = self._get_state(division_name)
        if not state:
            return False, i18n.t(user_id, "errors.division_not_found"), None
        if state.status not in (DraftStatus.ACTIVE, DraftStatus.PAUSED):
            return False, i18n.t(user_id, "edit_pick.draft_not_editable", status=state.status.value), None

        async with state._lock:
            record = next(
                (r for r in state.pick_history if r.pick_number == pick_number),
                None,
            )
            if not record:
                return False, i18n.t(user_id, "edit_pick.not_found", pick=pick_number), None

            coach = state.get_coach_by_id(record.coach_discord_id)
            if not coach:
                return False, i18n.t(user_id, "service.coach_not_found"), None

            old_name = record.pokemon_name
            old_pokemon = state.pokemon_pool.get(old_name.lower())

            valid_names = [p.name for p in state.pokemon_pool.values() if not p.is_banned]
            canonical, was_fuzzy = self.alias_manager.resolve(raw_pokemon_name, valid_names)
            if canonical is None:
                return False, i18n.t(user_id, "edit_pick.not_found_pokemon", name=raw_pokemon_name), None
            if was_fuzzy:
                return False, i18n.t(user_id, "edit_pick.use_exact_name", name=canonical), None

            new_pokemon = state.pokemon_pool.get(canonical.lower())
            if not new_pokemon:
                return False, i18n.t(user_id, "edit_pick.not_in_pool", name=canonical), None
            if new_pokemon.is_banned:
                return False, i18n.t(user_id, "edit_pick.banned", name=canonical), None
            if new_pokemon.is_drafted and new_pokemon.name.lower() != old_name.lower():
                drafter = state.get_coach_by_id(new_pokemon.drafted_by or "")
                who = drafter.name if drafter else "another coach"
                return False, i18n.t(
                    user_id, "edit_pick.already_drafted", name=canonical, coach=who
                ), None

            if new_pokemon.name.lower() == old_name.lower():
                return True, i18n.t(
                    user_id, "edit_pick.no_change", pick=pick_number, name=old_name
                ), None

            if old_pokemon:
                old_pokemon.is_drafted = False
                old_pokemon.drafted_by = None
            coach.team = [p for p in coach.team if p.name.lower() != old_name.lower()]
            coach.recalculate_points(state.total_points)

            conflict = PickValidator.species_conflict_for_pick(state, new_pokemon, coach)
            if conflict:
                if old_pokemon:
                    coach.add_pokemon(old_pokemon)
                key, kwargs = conflict
                return False, i18n.t(user_id, key, **kwargs), None

            if not coach.can_afford(new_pokemon):
                if old_pokemon:
                    coach.add_pokemon(old_pokemon)
                return False, i18n.t(
                    user_id,
                    "edit_pick.cannot_afford",
                    name=canonical,
                    points=new_pokemon.points,
                    remaining=coach.remaining_points,
                ), None

            coach.add_pokemon(new_pokemon)
            record.pokemon_name = new_pokemon.name
            record.points_cost = new_pokemon.points

            self._queue_record_sheet_write(state, record, new_pokemon.name)
            self.persistence.save(state)
            self._schedule(
                self.admin_log.pick_correction(
                    state,
                    "edit",
                    coach.name,
                    f"{old_name} → {new_pokemon.name}",
                    pick_number,
                )
            )

        return True, i18n.t(
            user_id,
            "edit_pick.success",
            pick=pick_number,
            old=old_name,
            new=canonical,
            coach=coach.name,
        ), {
            "pick_number": pick_number,
            "old_name": old_name,
            "new_name": new_pokemon.name,
            "coach": coach,
        }

    def is_division_coach(self, division_name: str, user_id: str) -> bool:
        state = self._get_state(division_name)
        if not state:
            return False
        return state.get_coach_by_id(user_id) is not None

    # ── Admin controls ────────────────────────────────────────────────────────

    def pause_draft(
        self, division_name: str, reason: str = "", user_id: str | None = None
    ) -> tuple[bool, str]:
        state = self._get_state(division_name)
        if not state:
            return False, i18n.t(user_id, "errors.division_not_found")
        if state.status != DraftStatus.ACTIVE:
            return False, i18n.t(
                user_id,
                "service.draft_not_active",
                status=state.status.value,
            )
        self.timers.freeze_timer(state)
        if state.bank_snipe_pending:
            self._freeze_bank_snipe(state)
        state.status = DraftStatus.PAUSED
        self.persistence.save(state)
        self.sheets.queue_status_write(division_name, f"Draft paused. Reason: {reason}")
        self._schedule(self.admin_log.draft_paused(state, reason))
        return True, i18n.t(
            user_id,
            "service.draft_paused",
            reason=reason or i18n.t(user_id, "common.not_specified"),
        )

    def resume_draft(
        self, division_name: str, user_id: str | None = None
    ) -> tuple[bool, str]:
        state = self._get_state(division_name)
        if not state:
            return False, i18n.t(user_id, "errors.division_not_found")
        if state.status != DraftStatus.PAUSED:
            return False, i18n.t(
                user_id,
                "service.draft_not_paused",
                status=state.status.value,
            )

        state.status = DraftStatus.ACTIVE
        coach = state.current_coach

        if state.bank_snipe_pending:
            remaining = state.bank_snipe_pending.remaining_seconds
            if remaining <= 0:
                self.persistence.save(state)
                asyncio.create_task(self._handle_bank_snipe_timeout(division_name))
            else:
                self._schedule_bank_snipe_timer(state, remaining)
        elif coach and coach.pick_timer_remaining is not None:
            remaining = coach.pick_timer_remaining
            if remaining <= 0:
                self.persistence.save(state)
                asyncio.create_task(self._handle_timeout(division_name))
                return True, i18n.t(
                    user_id,
                    "service.draft_resumed_skip",
                    coach=coach.name,
                )
            self.timers.resume_timer_with_remaining(state, remaining)
        elif coach and coach.pick_deadline:
            remaining = max(0.0, coach.pick_deadline - time.time())
            if remaining <= 0:
                self.persistence.save(state)
                asyncio.create_task(self._handle_timeout(division_name))
                return True, i18n.t(
                    user_id,
                    "service.draft_resumed_skip",
                    coach=coach.name,
                )
            self.timers.resume_timer_with_remaining(state, remaining)
        else:
            self.timers.start_timer(state)

        self.persistence.save(state)
        coach_name = coach.name if coach else "Unknown"
        self._schedule(self.admin_log.draft_resumed(state, coach_name))

        ping = ""
        if coach and coach.pick_deadline:
            ts = int(coach.pick_deadline)
            ping = i18n.t(
                user_id,
                "service.draft_resumed_ping",
                mention=coach.mention(),
                deadline=ts,
            )
        return True, i18n.t(user_id, "service.draft_resumed", ping=ping)

    def force_skip(
        self, division_name: str, user_id: str | None = None
    ) -> tuple[bool, str]:
        state = self._get_state(division_name)
        if not state:
            return False, i18n.t(user_id, "errors.division_not_found")
        if state.status != DraftStatus.ACTIVE:
            return False, i18n.t(
                user_id,
                "service.draft_not_active",
                status=state.status.value,
            )
        coach_name = state.current_coach.name if state.current_coach else "Unknown"
        self.timers.cancel_timer(state)
        if (
            state.bank_snipe_pending
            and state.current_coach
            and state.bank_snipe_pending.coach_discord_id == state.current_coach.discord_id
        ):
            self._clear_bank_snipe(state, restart_timer=False)
        self._schedule(self.admin_log.force_skip(state, coach_name))
        asyncio.create_task(self._handle_timeout(division_name))
        return True, i18n.t(user_id, "service.force_skip")

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _get_state(self, division_name: str) -> Optional[DraftState]:
        for key, state in self.states.items():
            if key.lower() == division_name.lower():
                return state
        return None

    def get_division_name_for_channel(self, channel_id: int) -> Optional[str]:
        for state in self.states.values():
            if state.channel_id == channel_id:
                return state.division_name
        try:
            from simulation.replacements import (
                system_test_channel_id,
                system_test_divisions,
            )

            if channel_id and channel_id == system_test_channel_id():
                names = system_test_divisions()
                if len(names) == 1:
                    return names[0]
        except Exception:
            pass
        return None

    def get_all_states(self) -> dict[str, DraftState]:
        return dict(self.states)

    def freeze_all_timers(self) -> None:
        """Snapshot all active timers before bot shutdown."""
        now = time.time()
        for state in self.states.values():
            if state.status not in (
                DraftStatus.ACTIVE,
                DraftStatus.REPLACEMENT_PENDING,
                DraftStatus.PAUSED,
            ):
                continue
            state.last_shutdown_at = now
            self.timers.freeze_timer(state)
            if state.bank_snipe_pending:
                self._freeze_bank_snipe(state)
            self.persistence.save(state)

    async def restore_timers_after_startup(self) -> None:
        """Restart timers from persisted remaining seconds (downtime does not count)."""
        for name, state in self.states.items():
            downtime: float | None = None
            if state.last_shutdown_at:
                downtime = time.time() - state.last_shutdown_at

            if state.status == DraftStatus.ACTIVE:
                if state.bank_snipe_pending:
                    remaining = state.bank_snipe_pending.remaining_seconds
                    if remaining > 0:
                        logger.info(
                            "Restarting bank snipe for %s: %.0fs remaining",
                            name,
                            remaining,
                        )
                        self._schedule_bank_snipe_timer(state, remaining)
                        if downtime and self.send_callback:
                            await self._post_draft_resume_message(state, downtime)
                    else:
                        asyncio.create_task(self._handle_bank_snipe_timeout(name))
                elif state.current_coach:
                    coach = state.current_coach
                    remaining = coach.pick_timer_remaining
                    if remaining is None and coach.pick_deadline:
                        remaining = max(0.0, coach.pick_deadline - time.time())
                    if remaining is not None and remaining > 0:
                        logger.info(
                            "Restarting timer for %s in %s: %.0fs remaining",
                            coach.name,
                            name,
                            remaining,
                        )
                        self.timers.resume_timer_with_remaining(state, remaining)
                        if downtime and self.send_callback:
                            await self._post_draft_resume_message(state, downtime)
                    elif remaining is not None and remaining <= 0:
                        logger.warning(
                            "Timer expired for %s in %s — triggering timeout.",
                            coach.name,
                            name,
                        )
                        asyncio.create_task(self._handle_timeout(name))
                    else:
                        self.timers.start_timer(state)
            elif state.status == DraftStatus.REPLACEMENT_PENDING:
                coach = state.get_coach_by_id(state.replacement_coach_discord_id or "")
                remaining = coach.pick_timer_remaining if coach else None
                if remaining is not None and remaining > 0:
                    self.timers.resume_replacement_timer_with_remaining(state, remaining)
                else:
                    self.timers.start_replacement_timer(state)

            if state.last_shutdown_at is not None:
                state.last_shutdown_at = None
                self.persistence.save(state)

    async def _post_draft_resume_message(
        self, state: DraftState, downtime_seconds: float
    ) -> None:
        coach = state.current_coach
        if not coach or not self.send_callback:
            return
        coach.sync_pick_deadline()
        embed, view = draft_resume_message(state, coach, int(downtime_seconds))
        await self.send_callback(
            state.channel_id,
            {"embed": embed, "view": view},
        )

    # ── Quiet hours ───────────────────────────────────────────────────────────

    def start_quiet_hours_watcher(self) -> None:
        """Watch for the nightly window opening and closing."""
        if not self.quiet_hours.enabled:
            return
        if self._quiet_hours_task and not self._quiet_hours_task.done():
            return
        self._quiet_hours_task = asyncio.create_task(self._quiet_hours_loop())
        logger.info(
            "[QuietHours] Watching window %s — pick timers stop overnight.",
            self.quiet_hours.describe_window(),
        )

    async def _quiet_hours_loop(self) -> None:
        was_quiet = self.quiet_hours.is_quiet()
        while True:
            try:
                # Wake at the boundary, but never sleep so long that a suspended
                # host or a clock jump makes us miss the transition entirely.
                delay = min(self.quiet_hours.seconds_until_next_boundary() + 1.0, 900.0)
                await asyncio.sleep(delay)

                is_quiet = self.quiet_hours.is_quiet()
                if is_quiet == was_quiet:
                    continue
                was_quiet = is_quiet
                if is_quiet:
                    await self.enter_quiet_hours()
                else:
                    await self.exit_quiet_hours()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error("[QuietHours] Transition failed: %s", e, exc_info=True)
                await asyncio.sleep(60.0)

    def _timed_states(self) -> list[DraftState]:
        return [
            state
            for state in self.states.values()
            if state.status in (DraftStatus.ACTIVE, DraftStatus.REPLACEMENT_PENDING)
        ]

    async def enter_quiet_hours(self) -> None:
        """Freeze every running clock and tell each channel the night has started."""
        resume_ts = self.quiet_hours.resume_timestamp()
        for state in self._timed_states():
            coach = self.timers.active_timed_coach(state)
            remaining = self.timers.freeze_timer(state)
            if state.bank_snipe_pending:
                self._freeze_bank_snipe(state)

            logger.info(
                "[QuietHours] %s — held %.0fs for %s.",
                state.division_name,
                remaining or 0.0,
                coach.name if coach else "?",
            )

            if not self.send_callback or not resume_ts:
                continue
            total = (
                self._bank_snipe_pause_seconds()
                if state.bank_snipe_pending
                else (self.timers.get_pick_time(coach, state) if coach else 0)
            )
            elapsed = max(0.0, total - (remaining or 0.0))
            embed, view = quiet_hours_start_message(state, coach, resume_ts, elapsed)
            await self.send_callback(
                state.channel_id, {"embed": embed, "view": view}
            )

    async def exit_quiet_hours(self) -> None:
        """Restart every held clock from where it stopped and ping the channel."""
        for state in self._timed_states():
            name = state.division_name
            coach = self.timers.active_timed_coach(state)

            if state.bank_snipe_pending:
                remaining = state.bank_snipe_pending.remaining_seconds
                coach = state.get_coach_by_id(
                    state.bank_snipe_pending.coach_discord_id
                ) or coach
                if remaining > 0:
                    self._schedule_bank_snipe_timer(state, remaining)
                else:
                    asyncio.create_task(self._handle_bank_snipe_timeout(name))
            elif state.status == DraftStatus.REPLACEMENT_PENDING:
                remaining = coach.pick_timer_remaining if coach else None
                if remaining is not None and remaining > 0:
                    self.timers.resume_replacement_timer_with_remaining(state, remaining)
                else:
                    self.timers.start_replacement_timer(state)
            else:
                remaining = coach.pick_timer_remaining if coach else None
                if remaining is not None and remaining > 0:
                    self.timers.resume_timer_with_remaining(state, remaining)
                elif coach:
                    self.timers.start_timer(state)

            logger.info("[QuietHours] %s — timers running again.", name)

            if not self.send_callback:
                continue
            if coach:
                coach.sync_pick_deadline()
            deadline_ts = int(coach.pick_deadline) if coach and coach.pick_deadline else None
            embed, view = quiet_hours_end_message(state, coach, deadline_ts)
            await self.send_callback(
                state.channel_id, {"embed": embed, "view": view}
            )
