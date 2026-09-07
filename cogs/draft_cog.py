"""
DraftCog — all slash commands related to the draft itself.

Commands:
  /pick <pokemon>          — Make your draft pick
  /makeup_pick <pokemon>   — Use a makeup pick after a skip
  /marosca                 — Random available pick with optional filters
  /picking                 — Show who is currently picking and their deadline
  /drafted [division]      — Show all picks made in a division
  /myteam                  — Show your own team and remaining points
  /team @coach             — Show another coach's team
"""

import logging
import discord
from discord.ext import commands
from discord import option
from discord.commands import AutocompleteContext
from constants.draft_constants import DraftStatus
from services.draft_service import DraftService
from services.embed_service import EmbedService
from services.marosca_service import (
    distinct_abilities_in_pool,
    distinct_types_in_pool,
    random_marosca_pick,
)
from services.pokemon_service import PokemonService
from services.public_messages import (
    channel_ping,
    default_public_text,
    makeup_reminder_message,
    pick_send_kwargs,
)
from utils.division_helper import get_division_name_by_channel, list_division_names
from utils.i18n import i18n
from utils.staff import member_is_staff
from utils.pokemon_autocomplete import (
    POKEMON_OPTION_DESCRIPTION,
    available_pool_autocomplete,
)
from utils.response_embeds import (
    dismiss_thinking,
    error_embed,
    info_embed,
    interaction_error,
    interaction_info,
    interaction_warning,
    respond_error,
    respond_embed,
    respond_info,
    respond_success,
    respond_warning,
    success_embed,
    warning_embed,
)

logger = logging.getLogger(__name__)


class DraftCog(commands.Cog):
    def __init__(
        self,
        bot: discord.Bot,
        draft_service: DraftService,
        pokemon_service: PokemonService | None = None,
    ) -> None:
        self.bot = bot
        self.draft = draft_service
        self.pokemon_service = pokemon_service

        # Give DraftService callbacks for channel posts and coach DMs
        self.draft.send_callback = self._send_to_channel
        self.draft.dm_callback = self._send_dm

    async def cog_load(self) -> None:
        self.draft.admin_log.bind(
            self.bot,
            self.draft._get_state,
            self.draft.persistence.save,
        )

    async def _send_dm(self, user_id: int, kwargs: dict) -> None:
        if self.draft.simulation_mode:
            logger.warning(
                "[DraftCog] Blocked DM to %s during simulation/system test.",
                user_id,
            )
            return
        try:
            user = await self.bot.fetch_user(user_id)
            if user:
                await user.send(**kwargs)
        except discord.HTTPException as e:
            logger.error(f"[DraftCog] Failed to DM user {user_id}: {e}")

    def _last_pick(self, division_name: str):
        state = self.draft._get_state(division_name)
        if state and state.pick_history:
            return state.pick_history[-1]
        return None

    async def _post_pick(self, channel, embed, view, division_name: str) -> None:
        await channel.send(
            **pick_send_kwargs(embed, view, self._last_pick(division_name))
        )

    # ── Internal helpers ─────────────────────────────────────────────────────

    async def _send_to_channel(self, channel_id: int, kwargs: dict) -> None:
        """Callback used by DraftService to post messages (timers, bank auto-picks)."""
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except discord.HTTPException as e:
                logger.error(
                    "[DraftCog] Could not fetch channel %s: %s", channel_id, e
                )
                return

        try:
            await channel.send(**kwargs)
        except discord.HTTPException as e:
            logger.error(f"[DraftCog] Failed to send to channel {channel_id}: {e}")
        except Exception as e:
            logger.exception(
                "[DraftCog] Unexpected error sending to channel %s: %s",
                channel_id,
                e,
            )

    def _get_division(
        self, channel_id: int, user_id: str | None = None
    ) -> tuple[str | None, str | None]:
        """
        Resolve division name from the channel the command was used in.
        Returns (division_name, error_message).
        """
        division_name = self.draft.get_division_name_for_channel(channel_id)
        if not division_name:
            return None, i18n.t(user_id, "errors.channel_not_registered") if user_id else (
                "This channel is not registered to any division. "
                "Please use this command in your division's channel."
            )
        return division_name, None

    @staticmethod
    def _locale(user_id: str | int) -> str:
        return i18n.get_user_locale(user_id)

    @staticmethod
    def _is_staff(member: discord.Member) -> bool:
        return member_is_staff(member)

    def _is_division_coach(self, division_name: str, user_id: str) -> bool:
        return self.draft.is_division_coach(division_name, user_id)

    async def _available_pokemon_autocomplete(
        self, ctx: AutocompleteContext
    ) -> list[discord.OptionChoice]:
        return available_pool_autocomplete(
            self.draft,
            ctx.interaction.channel_id,
            ctx.value or "",
        )

    async def _marosca_type_autocomplete(
        self, ctx: AutocompleteContext
    ) -> list[discord.OptionChoice]:
        division_name = self.draft.get_division_name_for_channel(ctx.interaction.channel_id)
        if not division_name:
            return []
        state = self.draft._get_state(division_name)
        if not state:
            return []
        query = (ctx.value or "").strip().lower()
        types = distinct_types_in_pool(state)
        if query:
            types = [t for t in types if query in t]
        return [discord.OptionChoice(name=t.title(), value=t) for t in types[:25]]

    async def _marosca_ability_autocomplete(
        self, ctx: AutocompleteContext
    ) -> list[discord.OptionChoice]:
        division_name = self.draft.get_division_name_for_channel(ctx.interaction.channel_id)
        if not division_name:
            return []
        state = self.draft._get_state(division_name)
        if not state:
            return []
        query = (ctx.value or "").strip().lower()
        abilities = distinct_abilities_in_pool(state, self.pokemon_service)
        if query:
            abilities = [a for a in abilities if query in a.replace("-", " ")]
        return [
            discord.OptionChoice(name=a.replace("-", " ").title(), value=a)
            for a in abilities[:25]
        ]

    # ── /pick ─────────────────────────────────────────────────────────────────

    @discord.slash_command(name="pick", description="Draft a Pokémon when it's your turn.")
    @option(
        "pokemon",
        description=POKEMON_OPTION_DESCRIPTION,
        type=str,
        autocomplete=_available_pokemon_autocomplete,
    )
    async def pick(
        self,
        ctx: discord.ApplicationContext,
        pokemon: str,
    ) -> None:
        await ctx.defer(ephemeral=True)

        division_name, err = self._get_division(ctx.channel_id, str(ctx.author.id))
        if err:
            await ctx.followup.send(embed=error_embed(err, locale=self._locale(ctx.author.id)), ephemeral=True)
            return

        state = self.draft._get_state(division_name)
        if not state or not state.current_coach:
            await ctx.followup.send(
                embed=error_embed(
                    i18n.t(ctx.author.id, "service.no_current_coach"),
                    locale=self._locale(ctx.author.id),
                ),
                ephemeral=True,
            )
            return

        drafting_coach = state.current_coach
        coach_id = drafting_coach.discord_id
        requester_id = str(ctx.author.id)
        requester_is_staff = self._is_staff(ctx.author)

        if state.status.value == "paused" and not requester_is_staff:
            await ctx.followup.send(
                embed=error_embed(
                    i18n.t(ctx.author.id, "service.paused_staff_only"),
                    locale=self._locale(ctx.author.id),
                ),
                ephemeral=True,
            )
            return

        if (
            state.status == DraftStatus.ACTIVE
            and drafting_coach.makeup_picks_owed > 0
            and requester_id == coach_id
            and not requester_is_staff
        ):
            await ctx.followup.send(
                embed=warning_embed(
                    i18n.t(
                        ctx.author.id,
                        "pick.makeup_first",
                        count=drafting_coach.makeup_picks_owed,
                    ),
                    locale=self._locale(ctx.author.id),
                ),
                ephemeral=True,
            )
            return

        success, message, embed, view = await self.draft.make_pick(
            division_name=division_name,
            coach_discord_id=coach_id,
            raw_pokemon_name=pokemon,
            picked_by_discord_id=requester_id,
            requester_is_staff=requester_is_staff,
        )

        if not success:
            if message.startswith("__FORM_CONFIRM__"):
                parts = message.split("__")
                canonical = parts[2]
                related_forms = parts[3].split("|")
                view = FormConfirmView(
                    draft_cog=self,
                    division_name=division_name,
                    coach_id=coach_id,
                    requester_id=requester_id,
                    requester_is_staff=requester_is_staff,
                    related_forms=related_forms,
                )
                forms_list = "\n".join(f"• **{f}**" for f in related_forms)
                await ctx.followup.send(
                    embed=warning_embed(
                        i18n.t(ctx.author.id, "pick.form_confirm", input=pokemon, forms=forms_list),
                        locale=self._locale(ctx.author.id),
                    ),
                    view=view,
                    ephemeral=True,
                )
            elif message.startswith("__FUZZY_MATCH__"):
                suggested = message[len("__FUZZY_MATCH__"):]
                view = FuzzyConfirmView(
                    draft_cog=self,
                    division_name=division_name,
                    coach_id=coach_id,
                    requester_id=requester_id,
                    requester_is_staff=requester_is_staff,
                    suggested_name=suggested,
                )
                await ctx.followup.send(
                    embed=warning_embed(
                        i18n.t(ctx.author.id, "pick.fuzzy_confirm", name=suggested),
                        locale=self._locale(ctx.author.id),
                    ),
                    view=view,
                    ephemeral=True,
                )
            else:
                await ctx.followup.send(
                    embed=error_embed(message, locale=self._locale(ctx.author.id)),
                    ephemeral=True,
                )
            return

        if message == "DRAFT_COMPLETE":
            await self._post_pick(ctx.channel, embed, view, division_name)
            await ctx.channel.send(
                default_public_text("draft.complete", division=division_name)
            )
            await dismiss_thinking(ctx)
            return

        await self._post_pick(ctx.channel, embed, view, division_name)

        state = self.draft._get_state(division_name)

        if state and state.makeup_queue:
            reminder_embed, reminder_view = makeup_reminder_message(state)
            if reminder_embed:
                await ctx.channel.send(embed=reminder_embed, view=reminder_view)

        if state and state.current_coach:
            await ctx.channel.send(content=channel_ping(state.current_coach))

        await dismiss_thinking(ctx)

    # ── /undo_pick ────────────────────────────────────────────────────────────

    @discord.slash_command(
        name="undo_pick",
        description="Revert the last pick if you made it (or you are the coach) and nobody else picked since.",
    )
    async def undo_pick(self, ctx: discord.ApplicationContext) -> None:
        await ctx.defer(ephemeral=True)
        division_name, err = self._get_division(ctx.channel_id, str(ctx.author.id))
        if err:
            await respond_error(ctx, err, locale=self._locale(ctx.author.id))
            return

        success, msg = await self.draft.undo_last_pick(
            division_name, str(ctx.author.id)
        )
        loc = self._locale(ctx.author.id)
        embed = success_embed(msg, locale=loc) if success else error_embed(msg, locale=loc)
        await ctx.followup.send(embed=embed, ephemeral=True)
        if success:
            state = self.draft._get_state(division_name)
            if state and state.current_coach and state.status.value == "active":
                await ctx.channel.send(content=channel_ping(state.current_coach))

    # ── /makeup_pick ──────────────────────────────────────────────────────────

    @discord.slash_command(
        name="makeup_pick",
        description="Make a makeup pick for a coach who was skipped.",
    )
    @option(
        "pokemon",
        description=POKEMON_OPTION_DESCRIPTION,
        autocomplete=_available_pokemon_autocomplete,
    )
    @option("coach_mention", description="@mention the coach", type=discord.Member, required=False)
    @option("coach_name", description="Coach name as string", required=False)
    async def makeup_pick(
        self,
        ctx: discord.ApplicationContext,
        pokemon: str,
        coach_mention: discord.Member = None,
        coach_name: str = None,
    ) -> None:
        await ctx.defer(ephemeral=True)

        division_name, err = self._get_division(ctx.channel_id, str(ctx.author.id))
        if err:
            await respond_error(ctx, err, locale=self._locale(ctx.author.id))
            return

        if not coach_mention and not coach_name:
            await respond_error(
                ctx, i18n.t(ctx.author.id, "makeup.need_coach"), locale=self._locale(ctx.author.id)
            )
            return

        state = self.draft._get_state(division_name)
        if not state:
            await respond_error(
                ctx,
                i18n.t(ctx.author.id, "errors.division_not_found"),
                locale=self._locale(ctx.author.id),
            )
            return

        target_coach = None
        if coach_mention:
            target_coach = state.get_coach_by_id(str(coach_mention.id))
            if not target_coach:
                await respond_error(
                    ctx,
                    i18n.t(
                        ctx.author.id,
                        "makeup.coach_not_in_division",
                        mention=coach_mention.mention(),
                    ),
                    locale=self._locale(ctx.author.id),
                )
                return
        else:
            name_lower = coach_name.lower()
            for c in state.coaches:
                if name_lower in c.name.lower():
                    target_coach = c
                    break
            if not target_coach:
                await respond_error(
                    ctx,
                    i18n.t(
                        ctx.author.id,
                        "makeup.coach_not_found",
                        name=coach_name,
                        available=", ".join(c.name for c in state.coaches),
                    ),
                    locale=self._locale(ctx.author.id),
                )
                return

        requester_id = str(ctx.author.id)
        requester_is_staff = self._is_staff(ctx.author)

        if state.status.value == "paused" and not requester_is_staff:
            await respond_error(
                ctx,
                i18n.t(ctx.author.id, "service.paused_staff_only"),
                locale=self._locale(ctx.author.id),
            )
            return

        success, message, embed, view = await self.draft.make_makeup_pick(
            division_name=division_name,
            coach_discord_id=target_coach.discord_id,
            raw_pokemon_name=pokemon,
            picked_by_discord_id=requester_id,
            requester_is_staff=requester_is_staff,
        )

        if not success:
            if message.startswith("__FUZZY_MATCH__"):
                suggested = message[len("__FUZZY_MATCH__"):]
                view = FuzzyConfirmView(
                    draft_cog=self,
                    division_name=division_name,
                    coach_id=target_coach.discord_id,
                    requester_id=requester_id,
                    requester_is_staff=requester_is_staff,
                    suggested_name=suggested,
                    is_makeup=True,
                )
                await respond_embed(
                    ctx,
                    warning_embed(
                        i18n.t(ctx.author.id, "makeup.fuzzy_confirm", name=suggested),
                        locale=self._locale(ctx.author.id),
                    ),
                    ephemeral=True,
                    view=view,
                )
            else:
                await respond_error(
                    ctx, message, locale=self._locale(ctx.author.id)
                )
            return

        if message == "DRAFT_COMPLETE":
            await self._post_pick(ctx.channel, embed, view, division_name)
            await ctx.channel.send(
                default_public_text("draft.complete", division=division_name)
            )
            await dismiss_thinking(ctx)
            return

        await self._post_pick(ctx.channel, embed, view, division_name)

        state = self.draft._get_state(division_name)
        if state and state.makeup_queue:
            reminder_embed, reminder_view = makeup_reminder_message(state)
            if reminder_embed:
                await ctx.channel.send(embed=reminder_embed, view=reminder_view)

        if state and state.current_coach:
            await ctx.channel.send(content=channel_ping(state.current_coach))

        await dismiss_thinking(ctx)

    # ── /picking ──────────────────────────────────────────────────────────────

    @discord.slash_command(
        name="picking",
        description="Show who is currently picking, their deadline, and the next coaches.",
    )
    async def picking(self, ctx: discord.ApplicationContext) -> None:
        division_name, err = self._get_division(ctx.channel_id, str(ctx.author.id))
        if err:
            await respond_error(ctx, err, locale=self._locale(ctx.author.id))
            return

        state = self.draft._get_state(division_name)
        if not state:
            await respond_error(
                ctx,
                i18n.t(ctx.author.id, "errors.division_not_initialised"),
                locale=self._locale(ctx.author.id),
            )
            return

        loc = self._locale(ctx.author.id)
        embed = EmbedService.picking_status(state, locale=loc)
        await ctx.respond(embed=embed)

    # ── /myteam ───────────────────────────────────────────────────────────────

    @discord.slash_command(name="myteam", description="View your team, weaknesses, and suggestions.")
    async def myteam(self, ctx: discord.ApplicationContext) -> None:
        from services.analysis_service import analyse_team, get_suggestions

        # Find the coach across all divisions
        coach = None
        state = None

        division_name, _ = self._get_division(ctx.channel_id, str(ctx.author.id))
        if division_name:
            state = self.draft._get_state(division_name)
            if state:
                coach = state.get_coach_by_id(str(ctx.author.id))

        if not coach:
            for _, s in self.draft.get_all_states().items():
                c = s.get_coach_by_id(str(ctx.author.id))
                if c:
                    coach = c
                    state = s
                    break

        if not coach or not state:
            await respond_error(
                ctx,
                i18n.t(ctx.author.id, "team.not_registered"),
                locale=self._locale(ctx.author.id),
            )
            return

        # Build analysis and suggestions
        analysis    = analyse_team(coach.team)
        available   = [
            p for p in state.pokemon_pool.values()
            if not p.is_drafted and not p.is_banned
        ]
        suggestions = get_suggestions(coach.team, available, coach.remaining_points)

        loc = self._locale(ctx.author.id)
        view  = MyTeamView(coach, state, analysis, suggestions, locale=loc)
        embed = EmbedService.team_card(coach, state, locale=loc)
        await ctx.respond(embed=embed, view=view, ephemeral=True)

    # ── /team ─────────────────────────────────────────────────────────────────

    @discord.slash_command(name="team", description="View a coach's team.")
    @option(
        "coach_name",
        description="Coach name — partial match works (e.g. 'Squid' finds 'SquidDaFeed')",
    )
    async def team(self, ctx: discord.ApplicationContext, coach_name: str) -> None:
        loc = self._locale(ctx.author.id)
        division_name, err = self._get_division(ctx.channel_id, str(ctx.author.id))
        if err:
            await respond_error(ctx, err, locale=loc)
            return

        state = self.draft._get_state(division_name)
        if not state:
            await respond_error(ctx, i18n.t(ctx.author.id, "errors.division_not_found"), locale=loc)
            return

        from difflib import get_close_matches

        all_names    = [c.name for c in state.coaches]
        name_lower   = coach_name.lower()

        # 1. Substring match (fastest — handles "Squid" → "SquidDaFeed")
        target = next(
            (c for c in state.coaches if name_lower in c.name.lower()), None
        )

        # 2. Fuzzy match as fallback
        if not target:
            matches = get_close_matches(coach_name, all_names, n=1, cutoff=0.5)
            if matches:
                target = next(c for c in state.coaches if c.name == matches[0])

        if not target:
            available = ", ".join(all_names)
            await respond_error(
                ctx,
                i18n.t(
                    ctx.author.id,
                    "team.coach_not_found",
                    name=coach_name,
                    available=available,
                ),
                locale=loc,
            )
            return

        embed = EmbedService.team_card(target, state, locale=loc)
        await ctx.respond(embed=embed)

    # ── /marosca ──────────────────────────────────────────────────────────────

    @discord.slash_command(
        name="marosca",
        description="Get a random available Pokémon matching optional filters.",
    )
    @option(
        "min_points",
        description="Minimum point cost",
        type=int,
        required=False,
        default=None,
    )
    @option(
        "max_points",
        description="Maximum point cost",
        type=int,
        required=False,
        default=None,
    )
    @option(
        "type",
        description="Pokémon type filter",
        type=str,
        required=False,
        autocomplete=_marosca_type_autocomplete,
        default=None,
    )
    @option(
        "ability",
        description="Ability filter",
        type=str,
        required=False,
        autocomplete=_marosca_ability_autocomplete,
        default=None,
    )
    async def marosca(
        self,
        ctx: discord.ApplicationContext,
        min_points: int = None,
        max_points: int = None,
        type: str = None,
        ability: str = None,
    ) -> None:
        loc = self._locale(ctx.author.id)
        division_name, err = self._get_division(ctx.channel_id, str(ctx.author.id))
        if err:
            await respond_error(ctx, err, locale=loc)
            return

        state = self.draft._get_state(division_name)
        if not state:
            await respond_error(ctx, i18n.t(ctx.author.id, "errors.division_not_found"), locale=loc)
            return

        coach = state.get_coach_by_id(str(ctx.author.id))
        if not coach:
            await respond_error(ctx, i18n.t(ctx.author.id, "marosca.no_coach"), locale=loc)
            return

        pokemon = random_marosca_pick(
            state,
            coach,
            self.pokemon_service,
            min_points=min_points,
            max_points=max_points,
            type_filter=type,
            ability_filter=ability,
        )
        if not pokemon:
            await respond_error(ctx, i18n.t(ctx.author.id, "marosca.no_matches"), locale=loc)
            return

        types_label = ", ".join(t.title() for t in (pokemon.types or []) if t != "unknown")
        await respond_info(
            ctx,
            i18n.t(
                ctx.author.id,
                "marosca.result",
                name=pokemon.name,
                points=pokemon.points,
                types=types_label or "—",
            ),
            locale=loc,
        )

    # ── /drafted ──────────────────────────────────────────────────────────────

    @discord.slash_command(
        name="drafted",
        description="Show all Pokémon drafted so far in this division.",
    )
    async def drafted(self, ctx: discord.ApplicationContext) -> None:
        loc = self._locale(ctx.author.id)
        division_name, err = self._get_division(ctx.channel_id, str(ctx.author.id))
        if err:
            await respond_error(ctx, err, locale=loc)
            return

        state = self.draft._get_state(division_name)
        if not state:
            await respond_error(ctx, i18n.t(ctx.author.id, "errors.division_not_found"), locale=loc)
            return

        if not state.pick_history:
            await respond_info(ctx, i18n.t(ctx.author.id, "drafted.empty"), locale=loc)
            return

        embed = discord.Embed(
            title=i18n.t(
                loc,
                "drafted.title",
                division=division_name,
                count=len(state.pick_history),
            ),
            colour=0x57F287,
        )

        by_round: dict[int, list] = {}
        for rec in state.pick_history:
            by_round.setdefault(rec.round_number, []).append(rec)

        for rnd, picks in sorted(by_round.items()):
            lines = []
            for p in picks:
                tag = ""
                if p.is_makeup:
                    tag = i18n.t_locale(loc, "drafted.tag_makeup")
                elif p.is_bank:
                    tag = i18n.t_locale(loc, "drafted.tag_bank")
                lines.append(
                    i18n.t_locale(
                        loc,
                        "drafted.line",
                        coach=p.coach_name,
                        pokemon=p.pokemon_name,
                        points=p.points_cost,
                        tag=tag,
                    )
                )
            embed.add_field(
                name=i18n.t_locale(loc, "drafted.round", round=rnd),
                value="\n".join(lines) or i18n.t_locale(loc, "drafted.none"),
                inline=False,
            )

        await ctx.respond(embed=embed)

# ── MyTeam Interaction Discord View ──────────────────────────────────────────── 

class MyTeamView(discord.ui.View):
    """Three-tab view for /myteam: Team · Weaknesses · Suggestions."""

    def __init__(
        self,
        coach,
        state,
        analysis,
        suggestions: list,
        locale: str | None = None,
        timeout: int = 300,
    ) -> None:
        super().__init__(timeout=timeout)
        self.coach       = coach
        self.state       = state
        self.analysis    = analysis
        self.suggestions = suggestions
        self.locale      = locale or i18n.default_locale
        self.current_tab = "team"
        self._refresh_styles()

    def _refresh_styles(self) -> None:
        self.tab_team.style    = (
            discord.ButtonStyle.primary
            if self.current_tab == "team"
            else discord.ButtonStyle.secondary
        )
        self.tab_weak.style    = (
            discord.ButtonStyle.primary
            if self.current_tab == "weak"
            else discord.ButtonStyle.secondary
        )
        self.tab_suggest.style = (
            discord.ButtonStyle.primary
            if self.current_tab == "suggest"
            else discord.ButtonStyle.secondary
        )

    @discord.ui.button(label="📋 Team", style=discord.ButtonStyle.primary)
    async def tab_team(
        self, button: discord.ui.Button, interaction: discord.Interaction
    ) -> None:
        self.current_tab = "team"
        self._refresh_styles()
        embed = EmbedService.team_card(self.coach, self.state, locale=self.locale)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="🛡️ Weaknesses", style=discord.ButtonStyle.secondary)
    async def tab_weak(
        self, button: discord.ui.Button, interaction: discord.Interaction
    ) -> None:
        self.current_tab = "weak"
        self._refresh_styles()
        embed = EmbedService.weakness_card(self.coach, self.analysis, locale=self.locale)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="💡 Suggestions", style=discord.ButtonStyle.secondary)
    async def tab_suggest(
        self, button: discord.ui.Button, interaction: discord.Interaction
    ) -> None:
        self.current_tab = "suggest"
        self._refresh_styles()
        embed = EmbedService.suggestions_card(
            self.coach, self.suggestions, self.coach.remaining_points, locale=self.locale
        )
        await interaction.response.edit_message(embed=embed, view=self)

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True


# ── Fuzzy match confirmation view ────────────────────────────────────────────

class FuzzyConfirmView(discord.ui.View):
    """
    Shown to the user when a fuzzy Pokémon name match is found.
    They can confirm or cancel. requester_id is who is allowed to click
    (may differ from coach_id when an admin is doing a makeup pick for someone else).
    """
    def __init__(
        self,
        draft_cog: "DraftCog",
        division_name: str,
        coach_id: str,
        requester_id: str,
        suggested_name: str,
        is_makeup: bool = False,
        requester_is_staff: bool = False,
    ) -> None:
        super().__init__(timeout=60)
        self.draft_cog      = draft_cog
        self.division_name  = division_name
        self.coach_id       = coach_id
        self.requester_id   = requester_id
        self.suggested_name = suggested_name
        self.is_makeup      = is_makeup
        self.requester_is_staff = requester_is_staff

    @discord.ui.button(label="Yes, draft it!", style=discord.ButtonStyle.success)
    async def confirm(
        self, button: discord.ui.Button, interaction: discord.Interaction
    ) -> None:
        if str(interaction.user.id) != self.requester_id:
            await interaction_error(
                interaction,
                i18n.t(interaction.user.id, "ui.confirm_only"),
                locale=i18n.get_user_locale(interaction.user.id),
            )
            return

        self.stop()

        if self.is_makeup:
            success, message, embed, view = await self.draft_cog.draft.make_makeup_pick(
                self.division_name,
                self.coach_id,
                self.suggested_name,
                picked_by_discord_id=self.requester_id,
                requester_is_staff=self.requester_is_staff,
            )
        else:
            success, message, embed, view = await self.draft_cog.draft.make_pick(
                self.division_name,
                self.coach_id,
                self.suggested_name,
                picked_by_discord_id=self.requester_id,
                requester_is_staff=self.requester_is_staff,
            )

        if success:
            await interaction.response.defer()
            await interaction.delete_original_response()

            await self.draft_cog._post_pick(
                interaction.channel, embed, view, self.division_name
            )

            state = self.draft_cog.draft._get_state(self.division_name)

            if state and state.makeup_queue:
                reminder_embed, reminder_view = makeup_reminder_message(state)
                if reminder_embed:
                    await interaction.channel.send(
                        embed=reminder_embed, view=reminder_view
                    )

            if state and state.current_coach and message != "DRAFT_COMPLETE":
                await interaction.channel.send(
                    content=channel_ping(state.current_coach)
                )

            if message == "DRAFT_COMPLETE":
                await interaction.channel.send(
                    default_public_text(
                        "draft.complete_short", division=self.division_name
                    )
                )
        else:
            await interaction_error(
                interaction,
                message,
                locale=i18n.get_user_locale(interaction.user.id),
                edit=True,
            )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger)
    async def cancel(
        self, button: discord.ui.Button, interaction: discord.Interaction
    ) -> None:
        if str(interaction.user.id) != self.requester_id:
            await interaction_error(
                interaction,
                i18n.t(interaction.user.id, "ui.cancel_only"),
                locale=i18n.get_user_locale(interaction.user.id),
            )
            return
        self.stop()
        await interaction.response.edit_message(
            embed=info_embed(
                i18n.t(interaction.user.id, "common.pick_cancelled"),
                locale=i18n.get_user_locale(interaction.user.id),
            ),
            view=None,
        )

class FormConfirmView(discord.ui.View):
    """
    Shown when a Pokémon name is ambiguous between multiple forms
    (e.g. "Ogerpon" base vs "Ogerpon-Wellspring", "-Cornerstone", "-Hearthflame").
    Presents one button per form — up to 5 (Discord's row limit).
    """
    def __init__(
        self,
        draft_cog: "DraftCog",
        division_name: str,
        coach_id: str,
        requester_id: str,
        related_forms: list[str],
        requester_is_staff: bool = False,
    ) -> None:
        super().__init__(timeout=60)
        self.draft_cog     = draft_cog
        self.division_name = division_name
        self.coach_id      = coach_id
        self.requester_id  = requester_id
        self.requester_is_staff = requester_is_staff

        for form_name in related_forms[:5]:
            button = discord.ui.Button(
                label=form_name, style=discord.ButtonStyle.primary
            )
            button.callback = self._make_callback(form_name)
            self.add_item(button)

        cancel_btn = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.danger)
        cancel_btn.callback = self._cancel
        self.add_item(cancel_btn)

    def _make_callback(self, form_name: str):
        async def callback(interaction: discord.Interaction) -> None:
            if str(interaction.user.id) != self.requester_id:
                await interaction_error(
                    interaction,
                    i18n.t(interaction.user.id, "ui.confirm_only"),
                    locale=i18n.get_user_locale(interaction.user.id),
                )
                return

            self.stop()
            success, message, embed, view = await self.draft_cog.draft.make_pick(
                self.division_name,
                self.coach_id,
                form_name,
                picked_by_discord_id=self.requester_id,
                requester_is_staff=self.requester_is_staff,
            )

            if success:
                await interaction.response.defer()
                await interaction.delete_original_response()
                await self.draft_cog._post_pick(
                    interaction.channel, embed, view, self.division_name
                )

                state = self.draft_cog.draft._get_state(self.division_name)
                if state and state.makeup_queue:
                    reminder_embed, reminder_view = makeup_reminder_message(state)
                    if reminder_embed:
                        await interaction.channel.send(
                            embed=reminder_embed, view=reminder_view
                        )
                if state and state.current_coach and message != "DRAFT_COMPLETE":
                    await interaction.channel.send(
                        content=channel_ping(state.current_coach)
                    )
                if message == "DRAFT_COMPLETE":
                    await interaction.channel.send(
                        default_public_text(
                            "draft.complete_short", division=self.division_name
                        )
                    )
            else:
                await interaction_error(
                    interaction,
                    message,
                    locale=i18n.get_user_locale(interaction.user.id),
                    edit=True,
                )
        return callback

    async def _cancel(self, interaction: discord.Interaction) -> None:
        if str(interaction.user.id) != self.requester_id:
            await interaction_error(
                interaction,
                i18n.t(interaction.user.id, "ui.cancel_only"),
                locale=i18n.get_user_locale(interaction.user.id),
            )
            return
        self.stop()
        await interaction.response.edit_message(
            embed=info_embed(
                i18n.t(interaction.user.id, "common.pick_cancelled_exact"),
                locale=i18n.get_user_locale(interaction.user.id),
            ),
            view=None,
        )

def setup(bot: discord.Bot) -> None:
    pass  # DraftCog is added manually in main.py with the shared DraftService instance
