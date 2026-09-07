"""Shared helpers to invoke real cog handlers during simulations."""



from __future__ import annotations



import logging

import random

from typing import Optional



import discord



from constants.draft_constants import DraftStatus

from models.draft_state import DraftState

from models.pokemon import Pokemon

from services.draft.pick_validator import PickValidator

from services.draft_service import DraftService

from simulation.mock_context import MockContext

from simulation.timer_helpers import wait_for_natural_timer_skip

from simulation.coverage import mark_seen, prefer_unseen, using_unseen_only



logger = logging.getLogger(__name__)



POINT_WEIGHTS: dict[int, int] = {

    1: 1, 2: 1, 3: 2, 4: 2, 5: 3, 6: 4, 7: 5, 8: 6, 9: 8, 10: 10,

    11: 12, 12: 14, 13: 16, 14: 18, 15: 20, 16: 18, 17: 16, 18: 12, 19: 8, 20: 4,

}





class DraftCommandHarness:

    def __init__(

        self,

        bot: discord.Bot,

        draft: DraftService,

        guild: discord.Guild,

        admin_id: str,

    ) -> None:

        self.bot = bot

        self.draft = draft

        self.guild = guild

        self.admin_id = admin_id



    def ctx(

        self,

        channel: discord.TextChannel,

        discord_id: str,

        display_name: str = "SimBot",

        *,

        admin: bool = False,

    ) -> MockContext:

        return MockContext(channel, self.guild, discord_id, display_name, admin=admin)



    def _draft_cog(self):

        return self.bot.cogs.get("DraftCog")



    def _admin_cog(self):

        return self.bot.cogs.get("AdminCog")



    @staticmethod

    def valid_picks_for_coach(

        state: DraftState,

        coach,

        tried: set[str],

    ) -> list[Pokemon]:

        valid: list[Pokemon] = []

        for pokemon in state.pokemon_pool.values():

            if pokemon.is_drafted or pokemon.is_banned:

                continue

            if pokemon.name.lower() in tried:

                continue

            ok, _ = PickValidator.validate_pick(coach, pokemon, state.team_size)

            if ok:

                valid.append(pokemon)

        return valid



    @classmethod

    def weighted_pick(cls, state: DraftState, coach) -> Optional[Pokemon]:

        available = prefer_unseen(cls.valid_picks_for_coach(state, coach, set()))

        if not available:

            return None

        by_points: dict[int, list[Pokemon]] = {}

        for pokemon in available:

            by_points.setdefault(pokemon.points, []).append(pokemon)

        costs = list(by_points.keys())

        # Drain expensive unseen first so 18–20pt leftovers are not stranded
        # on coaches who already spent their budget.
        if using_unseen_only(available):

            weights = [max(1, cost * cost) for cost in costs]

        else:

            weights = [POINT_WEIGHTS.get(cost, 1) for cost in costs]

        chosen = random.choices(costs, weights=weights, k=1)[0]

        return random.choice(by_points[chosen])



    @staticmethod

    def cheapest_valid_pick(state: DraftState, coach, tried: set[str]) -> Optional[Pokemon]:

        candidates = prefer_unseen(

            DraftCommandHarness.valid_picks_for_coach(state, coach, tried)

        )

        return min(candidates, key=lambda p: p.points) if candidates else None



    async def invoke_pick(

        self, channel: discord.TextChannel, coach, pokemon_name: str,

    ) -> bool:

        draft_cog = self._draft_cog()

        if not draft_cog:

            return False

        division = self.draft.get_division_name_for_channel(channel.id)

        before = self.draft._get_state(division)

        before_count = before.global_pick_counter if before else 0

        ctx = self.ctx(channel, coach.discord_id, coach.name)

        await draft_cog.pick.callback(draft_cog, ctx, pokemon=pokemon_name)

        after = self.draft._get_state(division)

        return after is not None and after.global_pick_counter > before_count



    async def invoke_makeup(

        self, channel: discord.TextChannel, coach, pokemon_name: str, *, as_staff: bool = False,

    ) -> bool:

        draft_cog = self._draft_cog()

        if not draft_cog:

            return False

        division = self.draft.get_division_name_for_channel(channel.id)

        state = self.draft._get_state(division)

        if not state:

            return False

        before_owed = coach.makeup_picks_owed

        before_queue = sum(1 for cid, _ in state.makeup_queue if cid == coach.discord_id)

        ctx = self.ctx(

            channel,

            self.admin_id if as_staff else coach.discord_id,

            "Admin" if as_staff else coach.name,

            admin=as_staff,

        )

        await draft_cog.makeup_pick.callback(

            draft_cog,

            ctx,

            pokemon=pokemon_name,

            coach_mention=None,

            coach_name=coach.name,

        )

        state = self.draft._get_state(division)

        if not state:

            return False

        coach = state.get_coach_by_id(coach.discord_id) or coach

        after_queue = sum(1 for cid, _ in state.makeup_queue if cid == coach.discord_id)

        return coach.makeup_picks_owed < before_owed or after_queue < before_queue



    async def invoke_picking(self, channel: discord.TextChannel) -> None:

        draft_cog = self._draft_cog()

        if not draft_cog:

            return

        ctx = self.ctx(channel, self.admin_id, "Admin", admin=True)

        await draft_cog.picking.callback(draft_cog, ctx)



    async def invoke_pause(self, channel: discord.TextChannel, reason: str) -> None:

        admin_cog = self._admin_cog()

        if not admin_cog:

            return

        ctx = self.ctx(channel, self.admin_id, "Admin", admin=True)

        await admin_cog.pause_draft.callback(admin_cog, ctx, reason=reason)



    async def invoke_resume(self, channel: discord.TextChannel) -> None:

        admin_cog = self._admin_cog()

        if not admin_cog:

            return

        ctx = self.ctx(channel, self.admin_id, "Admin", admin=True)

        await admin_cog.resume_draft.callback(admin_cog, ctx)



    async def invoke_replace(

        self,

        channel: discord.TextChannel,

        division_name: str,

        *,

        team_name: str,

        coach_name: str,

        discord_id: str,

        logo_url: str,

        timezone: str,

    ) -> None:

        admin_cog = self._admin_cog()

        if not admin_cog:

            return

        ctx = self.ctx(channel, discord_id, coach_name)

        await admin_cog.replace.callback(

            admin_cog,

            ctx,

            team_name=team_name,

            coach_mention=None,

            coach_name_str=coach_name,

            discord_id_str=discord_id,

            division=division_name,

            logo_url=logo_url,

            timezone=timezone,

        )



    async def wait_for_timer_skip(

        self,

        division_name: str,

        coach_discord_id: str,

        *,

        timer_seconds: int = 5,

        wait_buffer: int = 3,

    ) -> bool:

        return await wait_for_natural_timer_skip(

            self.draft,

            division_name,

            coach_discord_id,

            timer_seconds=timer_seconds,

            wait_buffer=wait_buffer,

        )



    async def pick_for_current_coach(self, channel: discord.TextChannel) -> bool:

        return await self.advance_draft_pick(channel)



    async def drain_queued_makeup(self, channel: discord.TextChannel) -> bool:

        """Staff /makeup_pick for the oldest makeup_queue entry (any coach)."""

        division = self.draft.get_division_name_for_channel(channel.id)

        state = self.draft._get_state(division)

        if not state or not state.makeup_queue:

            return False

        coach_id, _rnd = state.makeup_queue[0]

        coach = state.get_coach_by_id(coach_id)

        if not coach:

            return False

        return await self._pick_for_coach(channel, coach, makeup=True, as_staff=True)



    async def resolve_replacement_picks(self, channel: discord.TextChannel) -> bool:

        """Complete replacement makeup picks, then their on-clock pick if needed."""

        division = self.draft.get_division_name_for_channel(channel.id)

        for _ in range(30):

            state = self.draft._get_state(division)

            if not state:

                return False

            if state.status == DraftStatus.ACTIVE:

                return True

            if state.status == DraftStatus.REPLACEMENT_PENDING:

                if not await self.advance_draft_pick(channel):

                    return False

                continue

            return False

        return self.draft._get_state(division) is not None and (

            self.draft._get_state(division).status == DraftStatus.ACTIVE

        )



    async def advance_draft_pick(self, channel: discord.TextChannel) -> bool:

        """Pick or makeup depending on draft state and pending obligations."""

        division = self.draft.get_division_name_for_channel(channel.id)

        state = self.draft._get_state(division)

        if not state:

            return False



        if state.status == DraftStatus.REPLACEMENT_PENDING:

            rep = state.get_coach_by_id(state.replacement_coach_discord_id or "")

            if not rep:

                return False

            if rep.makeup_picks_owed > 0:

                return await self._pick_for_coach(channel, rep, makeup=True)

            return await self._pick_for_coach(channel, rep, makeup=False)



        if state.status == DraftStatus.PAUSED:

            coach = state.current_coach

            if not coach:

                return False

            if coach.makeup_picks_owed > 0:

                return await self._pick_for_coach(

                    channel, coach, makeup=True, as_staff=True

                )

            return await self._pick_for_coach(channel, coach, makeup=False, as_staff=True)



        coach = state.current_coach

        if not coach:

            return False

        if coach.makeup_picks_owed > 0:

            return await self._pick_for_coach(channel, coach, makeup=True)

        return await self._pick_for_coach(channel, coach, makeup=False)



    async def _pick_for_coach(

        self,

        channel: discord.TextChannel,

        coach,

        *,

        makeup: bool,

        as_staff: bool = False,

    ) -> bool:

        division = self.draft.get_division_name_for_channel(channel.id)

        tried: set[str] = set()



        for _ in range(15):

            state = self.draft._get_state(division)

            if not state:

                return False



            pick = self._select_pick(state, coach, tried)

            if not pick:

                logger.warning(

                    "[Harness] No valid pick for %s in %s (makeup=%s)",

                    coach.name,

                    division,

                    makeup,

                )

                return False



            tried.add(pick.name.lower())

            if makeup:

                if not await self.invoke_makeup(

                    channel, coach, pick.name, as_staff=as_staff

                ):

                    continue
                mark_seen([pick.name])

                state = self.draft._get_state(division)

                if not state:

                    return False

                coach = state.get_coach_by_id(coach.discord_id) or coach

                if coach.makeup_picks_owed <= 0:

                    return True

                continue



            before = state.global_pick_counter

            if await self.invoke_pick(channel, coach, pick.name):
                mark_seen([pick.name])
                return True

            after = self.draft._get_state(division)

            if after and after.global_pick_counter > before:

                return True



        return False



    def _select_pick(

        self,

        state: DraftState,

        coach,

        tried: set[str],

    ) -> Optional[Pokemon]:

        pick = self.weighted_pick(state, coach)

        if pick and pick.name.lower() not in tried:

            return pick

        pick = self.cheapest_valid_pick(state, coach, tried)

        if pick:

            return pick

        valid = self.valid_picks_for_coach(state, coach, tried)

        return valid[0] if valid else None


