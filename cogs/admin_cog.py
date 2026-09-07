"""
AdminCog — commands restricted to the admin role.

Commands:
  /start_division division:<name> [start_at:<HH:MM>]
      Prepares a division and shows a confirmation embed with ✅/❌ buttons.
      If start_at is provided, the draft is scheduled for that time in the admin's timezone.
  /pause_draft               — Pause the active draft
  /resume_draft              — Resume a paused draft
  /force_skip                — Immediately skip the current coach
  /replace                   — Join as replacement after 3-skip removal
  /draft_overview            — Show all division statuses
  /alias_learn / alias_forget / alias_list
  /refresh_pokemon_cache
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

import pytz
from utils.division_helper import load_division_config, update_division_channel_id
from utils.timezone_helper import (
    format_timezone_display,
    format_timezone_source,
    resolve_invoker_timezone,
    schedule_local_time,
)

import discord
from discord import option
from discord.commands import AutocompleteContext
from discord.ext import commands

from config import Config
from constants.draft_constants import DraftStatus
from services.replace_wizard import build_replace_wizard_embed
from views.replace_wizard_view import localized_replace_wizard_view
from services.draft_service import DraftService
from services.embed_service import EmbedService
from services.public_messages import (
    channel_ping,
    default_public_text,
    draft_pause_message,
    draft_manual_resume_message,
    draft_start_message,
    edit_pick_message,
    replacement_welcome_message,
)
from services.pokemon_service import PokemonService
from utils.alias_manager import AliasManager
from utils.coach_vote import CoachVoteTracker, PAUSE_EMOJI, RESUME_EMOJI
from utils.content_filter import alias_is_allowed
from utils.i18n import i18n
from utils.pokemon_autocomplete import POKEMON_OPTION_DESCRIPTION, pool_name_autocomplete
from utils.response_embeds import (
    error_embed,
    info_embed,
    interaction_error,
    interaction_success,
    list_embed,
    respond_embed,
    respond_error,
    respond_info,
    respond_result,
    respond_success,
    success_embed,
)

logger = logging.getLogger(__name__)


def admin_only():
    async def predicate(ctx: discord.ApplicationContext) -> bool:
        role = ctx.guild.get_role(Config.ADMIN_ROLE_ID)
        if role and role in ctx.author.roles:
            return True
        await respond_error(ctx, i18n.t(ctx.author.id, "errors.admin_only"))
        return False
    return commands.check(predicate)


# ── Confirmation view ──────────────────────────────────────────────────────────

class DraftConfirmView(discord.ui.View):
    """
    Buttons shown on the /start_division embed.
    ✅ starts the draft (immediately or at a scheduled local time).
    ❌ cancels and removes the division state.
    """

    def __init__(
        self,
        admin_cog: "AdminCog",
        division_name: str,
        scheduled_utc: Optional[datetime] = None,
    ) -> None:
        super().__init__(timeout=600)   # 10 minutes to confirm
        self.admin_cog = admin_cog
        self.division_name = division_name
        self.scheduled_utc = scheduled_utc

    def _draft_channel(self, fallback: discord.abc.GuildChannel) -> discord.abc.GuildChannel:
        state = self.admin_cog.draft._get_state(self.division_name)
        if state and state.channel_id:
            ch = self.admin_cog.bot.get_channel(state.channel_id)
            if ch:
                return ch
        return fallback

    async def _ack_and_disable(self, interaction: discord.Interaction) -> None:
        """Acknowledge the button click and disable confirm/cancel buttons."""
        for child in self.children:
            child.disabled = True
        self.stop()
        if interaction.response.is_done():
            try:
                await interaction.message.edit(view=self)
            except discord.NotFound:
                logger.warning(
                    "[DraftConfirmView] Message gone for %s", self.division_name
                )
            return
        try:
            await interaction.response.edit_message(view=self)
        except discord.NotFound:
            logger.warning(
                "[DraftConfirmView] Could not edit confirm message for %s",
                self.division_name,
            )
            if not interaction.response.is_done():
                await interaction.response.defer(ephemeral=True)

    @discord.ui.button(emoji="✅", label="Confirm", style=discord.ButtonStyle.success)
    async def confirm(self, button, interaction):
        if not self._is_admin(interaction.user):
            await interaction_error(
                interaction,
                i18n.t(interaction.user.id, "admin.confirm_start_only"),
                locale=i18n.get_user_locale(interaction.user.id),
            )
            return
        await self._ack_and_disable(interaction)
        draft_channel = self._draft_channel(interaction.channel)
        loc = i18n.get_user_locale(interaction.user.id)
        if self.scheduled_utc:
            ts = int(self.scheduled_utc.timestamp())
            await interaction_success(
                interaction,
                i18n.t(
                    interaction.user.id,
                    "admin.scheduled",
                    division=self.division_name,
                    ts=ts,
                ),
                locale=loc,
            )
            asyncio.create_task(
                self._start_after_delay(
                    self.division_name,
                    (self.scheduled_utc - datetime.now(timezone.utc)).total_seconds(),
                    draft_channel,
                )
            )
        else:
            success, msg = await self._launch_draft(self.division_name, draft_channel)
            if not success:
                await interaction_error(interaction, msg, locale=loc)

    @discord.ui.button(emoji="❌", label="Cancel", style=discord.ButtonStyle.danger)
    async def cancel(self, button, interaction):
        if not self._is_admin(interaction.user):
            await interaction_error(
                interaction,
                i18n.t(interaction.user.id, "admin.cancel_only"),
                locale=i18n.get_user_locale(interaction.user.id),
            )
            return
        await self._ack_and_disable(interaction)
        self.admin_cog.draft.states.pop(self.division_name, None)
        await interaction_success(
            interaction,
            i18n.t(
                interaction.user.id,
                "admin.prep_cancelled",
                division=self.division_name,
            ),
            locale=i18n.get_user_locale(interaction.user.id),
        )

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True
        self.stop()

    def _is_admin(self, member: discord.Member) -> bool:
        role = member.guild.get_role(Config.ADMIN_ROLE_ID)
        return role is not None and role in member.roles

    async def _start_after_delay(
        self,
        division_name: str,
        delay: float,
        channel: discord.TextChannel,
    ) -> None:
        await asyncio.sleep(delay)
        await self._launch_draft(division_name, channel)

    async def _launch_draft(
        self,
        division_name: str,
        channel: discord.abc.GuildChannel,
    ) -> tuple[bool, str]:
        success, msg = self.admin_cog.draft.start_draft(division_name)
        if success:
            state = self.admin_cog.draft._get_state(division_name)
            if state and state.current_coach:
                draft_channel = self.admin_cog.bot.get_channel(state.channel_id) or channel
                embed, view = draft_start_message(state)
                await draft_channel.send(
                    content=default_public_text(
                        "admin.start_first_pick",
                        mention=state.current_coach.mention(),
                    ),
                    embed=embed,
                    view=view,
                )
            return True, msg
        return False, msg


# ── Cog ───────────────────────────────────────────────────────────────────────

class AdminCog(commands.Cog):
    def __init__(
        self,
        bot: discord.Bot,
        draft_service: DraftService,
        pokemon_service: PokemonService,
        alias_manager: AliasManager,
    ) -> None:
        self.bot = bot
        self.draft = draft_service
        self.pokemon_service = pokemon_service
        self.alias_manager = alias_manager
        self._coach_votes = CoachVoteTracker()

    @staticmethod
    def _is_admin(member: discord.Member) -> bool:
        role = member.guild.get_role(Config.ADMIN_ROLE_ID)
        return role is not None and role in member.roles

    def _locale(self, user_id: str | int) -> str:
        return i18n.get_user_locale(str(user_id))

    def _get_division(
        self, channel_id: int, user_id: str | None = None
    ) -> tuple[str | None, str | None]:
        division_name = self.draft.get_division_name_for_channel(channel_id)
        if not division_name:
            return None, i18n.t(user_id, "errors.channel_not_registered")
        return division_name, None

    async def _announce_draft_paused(
        self,
        channel: discord.abc.Messageable,
        state,
        reason: str,
    ) -> None:
        pause_embed, pause_view = draft_pause_message(state, reason)
        await channel.send(embed=pause_embed, view=pause_view)
        if self.draft.channel_lock:
            locked = await self.draft.channel_lock.lock(state)
            if not locked:
                logger.warning(
                    "[AdminCog] Channel lock failed for %s — check Manage Channels permission.",
                    state.division_name,
                )

    async def _announce_draft_resumed(
        self,
        channel: discord.abc.Messageable,
        state,
    ) -> None:
        if self.draft.channel_lock:
            await self.draft.channel_lock.unlock(state)
        resume_embed, resume_view = draft_manual_resume_message(state)
        await channel.send(embed=resume_embed, view=resume_view)
        if state.current_coach and state.status.value == "active":
            await channel.send(content=channel_ping(state.current_coach))

    async def _pool_pokemon_autocomplete(
        self, ctx: AutocompleteContext
    ) -> list[discord.OptionChoice]:
        return pool_name_autocomplete(
            self.draft,
            ctx.interaction.channel_id,
            ctx.value or "",
            include_drafted=True,
        )

    def _resolve_division_channel(
        self,
        state,
        ctx: discord.ApplicationContext,
    ) -> discord.TextChannel:
        """
        Return the text channel for division announcements.
        If config channel_id is missing/invalid, bind to the channel where
        /start_division was run and persist to state + division_config.json.
        """
        channel = self.bot.get_channel(state.channel_id)
        if channel is None or not state.channel_id:
            channel = ctx.channel
            state.channel_id = ctx.channel.id
            self.draft.persistence.save(state)
            update_division_channel_id(state.division_name, ctx.channel.id)
            logger.info(
                "[AdminCog] Bound %s to channel %s",
                state.division_name,
                ctx.channel.id,
            )
        return channel

    # ── /start_division ────────────────────────────────────────────────────────

    @discord.slash_command(
        name="start_division",
        description="[ADMIN] Prepara uma divisão e mostra confirmação para iniciar o draft.",
    )
    @option("division", description="Nome da divisão (ex: Acuity)")
    @option(
        "start_at",
        description="Hora local para iniciar (HH:MM). Detetada automaticamente pelo Discord.",
        required=False,
    )
    @admin_only()
    async def start_division(
        self,
        ctx: discord.ApplicationContext,
        division: str,
        start_at: str = None,
    ) -> None:
        await ctx.defer(ephemeral=True)

        # Parse optional scheduled time — interpreta na timezone do admin
        scheduled_utc: Optional[datetime] = None
        tz_res = None
        if start_at:
            tz_res = resolve_invoker_timezone(
                ctx.author.id,
                discord_locale=ctx.locale,
                guild_locale=ctx.guild_locale,
                bot_language=i18n.get_user_locale(ctx.author.id),
            )
            try:
                hh, mm = [int(x) for x in start_at.strip().split(":")]
                scheduled_utc = schedule_local_time(tz_res.tz, hh, mm)
            except (ValueError, AttributeError):
                await respond_error(
                    ctx, i18n.t(ctx.author.id, "admin.invalid_time"), locale=i18n.get_user_locale(ctx.author.id)
                )
                return

        uid = str(ctx.author.id)
        loc = self._locale(uid)
        success, msg = self.draft.initialise_division(division, uid)
        if not success:
            await respond_error(ctx, msg, locale=loc)
            return

        state = self.draft._get_state(division)
        if not state:
            await respond_error(ctx, i18n.t(uid, "admin.state_not_found"), locale=loc)
            return

        # Enrich Pokémon pool in background (non-blocking)
        asyncio.create_task(self._enrich_pool(state))

        # Bind division channel if not configured (used when draft goes public on Confirm)
        self._resolve_division_channel(state, ctx)

        loc = i18n.get_user_locale(uid)
        embed = EmbedService.division_initialised(state, locale=loc)
        if scheduled_utc:
            ts = int(scheduled_utc.timestamp())
            tz_label = format_timezone_source(tz_res, loc)
            tz_display = format_timezone_display(tz_res.tz, loc, at=scheduled_utc)
            embed.add_field(
                name=i18n.t(uid, "admin.scheduled_field"),
                value=(
                    f"<t:{ts}:F> (<t:{ts}:R>)\n"
                    + i18n.t(uid, "timezone.auto_used", timezone=tz_display, source=tz_label)
                ),
                inline=False,
            )

        view = DraftConfirmView(
            admin_cog=self,
            division_name=state.division_name,
            scheduled_utc=scheduled_utc,
        )

        # Preparation is admin-only — public announcement happens on Confirm
        await ctx.followup.send(embed=embed, view=view, ephemeral=True)

    async def _enrich_pool(self, state) -> None:
        """Fetch PokeAPI data for all Pokémon in the pool (background task)."""
        pool = list(state.pokemon_pool.values())
        enriched = await self.pokemon_service.enrich_all(pool)
        for p in enriched:
            state.pokemon_pool[p.name.lower()] = p
        self.draft.persistence.save(state)
        logger.info(
            f"[AdminCog] Pool enrichment complete for {state.division_name}: "
            f"{len(enriched)} Pokémon."
        )

    # ── /pause_draft ───────────────────────────────────────────────────────────

    @discord.slash_command(
        name="pause_draft",
        description="Pause the active draft (admin: immediate · coach: vote with reactions).",
    )
    @option("reason", description="Reason for the pause (optional for coaches)", required=False)
    async def pause_draft(
        self, ctx: discord.ApplicationContext, reason: str = ""
    ) -> None:
        uid = str(ctx.author.id)
        loc = self._locale(uid)
        division_name, err = self._get_division(ctx.channel_id, uid)
        if err:
            await respond_error(ctx, err, locale=loc)
            return

        state = self.draft._get_state(division_name)
        if not state or state.status.value != "active":
            await respond_error(ctx, i18n.t(uid, "coach_vote.not_active"), locale=loc)
            return

        if self._is_admin(ctx.author):
            success, msg = self.draft.pause_draft(division_name, reason, uid)
            if success:
                state = self.draft._get_state(division_name)
                if state:
                    await self._announce_draft_paused(ctx.channel, state, reason)
            await respond_result(ctx, success, msg, locale=loc, ephemeral=True)
            return

        if not self.draft.is_division_coach(division_name, uid):
            await respond_error(ctx, i18n.t(uid, "coach_vote.not_coach"), locale=loc)
            return

        embed = EmbedService.coach_vote_embed(
            state,
            vote_type="pause",
            reason=reason,
            threshold=Config.COACH_VOTE_PAUSE_THRESHOLD,
            votes=0,
            locale=loc,
        )
        await respond_embed(
            ctx,
            success_embed(i18n.t(uid, "coach_vote.posted"), locale=loc),
            ephemeral=True,
        )
        message = await ctx.channel.send(embed=embed)
        await message.add_reaction(PAUSE_EMOJI)
        self._coach_votes.register(message.id, division_name, "pause")

    # ── /resume_draft ──────────────────────────────────────────────────────────

    @discord.slash_command(
        name="resume_draft",
        description="Resume a paused draft (admin: immediate · coach: vote with reactions).",
    )
    async def resume_draft(self, ctx: discord.ApplicationContext) -> None:
        uid = str(ctx.author.id)
        loc = self._locale(uid)
        division_name, err = self._get_division(ctx.channel_id, uid)
        if err:
            await respond_error(ctx, err, locale=loc)
            return

        state = self.draft._get_state(division_name)
        if not state or state.status.value != "paused":
            await respond_error(ctx, i18n.t(uid, "coach_vote.not_paused"), locale=loc)
            return

        if self._is_admin(ctx.author):
            success, msg = self.draft.resume_draft(division_name, uid)
            if success:
                state = self.draft._get_state(division_name)
                if state:
                    await self._announce_draft_resumed(ctx.channel, state)
            await respond_result(ctx, success, msg, locale=loc, ephemeral=True)
            return

        if not self.draft.is_division_coach(division_name, uid):
            await respond_error(ctx, i18n.t(uid, "coach_vote.not_coach"), locale=loc)
            return

        embed = EmbedService.coach_vote_embed(
            state,
            vote_type="resume",
            reason="",
            threshold=Config.COACH_VOTE_PAUSE_THRESHOLD,
            votes=0,
            locale=loc,
        )
        await respond_embed(
            ctx,
            success_embed(i18n.t(uid, "coach_vote.posted"), locale=loc),
            ephemeral=True,
        )
        message = await ctx.channel.send(embed=embed)
        await message.add_reaction(RESUME_EMOJI)
        self._coach_votes.register(message.id, division_name, "resume")

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        if payload.user_id == self.bot.user.id:
            return

        entry = self._coach_votes.get(payload.message_id)
        if not entry:
            return

        emoji = str(payload.emoji)
        if entry["vote_type"] == "pause" and emoji != PAUSE_EMOJI:
            return
        if entry["vote_type"] == "resume" and emoji != RESUME_EMOJI:
            return

        guild = self.bot.get_guild(payload.guild_id) if payload.guild_id else None
        if not guild:
            return
        member = guild.get_member(payload.user_id)
        if not member:
            return

        division_name = entry["division"]
        if not self.draft.is_division_coach(division_name, str(payload.user_id)):
            return

        count, entry = self._coach_votes.add_voter(payload.message_id, str(payload.user_id))
        if not entry or count < Config.COACH_VOTE_PAUSE_THRESHOLD:
            return
        if entry.get("completed"):
            return
        entry["completed"] = True

        self._coach_votes.clear(payload.message_id)
        uid = str(payload.user_id)
        channel = self.bot.get_channel(payload.channel_id)

        if entry["vote_type"] == "pause":
            reason = i18n.t(uid, "coach_vote.pause_reason")
            success, msg = self.draft.pause_draft(division_name, reason=reason, user_id=uid)
            if success and channel:
                state = self.draft._get_state(division_name)
                if state:
                    await self._announce_draft_paused(channel, state, reason)
        else:
            success, msg = self.draft.resume_draft(division_name, user_id=uid)
            if success and channel:
                state = self.draft._get_state(division_name)
                if state:
                    await self._announce_draft_resumed(channel, state)

        if channel:
            loc = self._locale(uid)
            embed = success_embed(msg, locale=loc) if success else error_embed(msg, locale=loc)
            await channel.send(embed=embed)

    @discord.slash_command(
        name="goto",
        description="[ADMIN] Rewind the draft so the next pick is the given number (draft must be paused).",
    )
    @option("pick", description="Next pick number (e.g. 84)", type=int)
    @admin_only()
    async def goto(self, ctx: discord.ApplicationContext, pick: int) -> None:
        uid = str(ctx.author.id)
        division_name, err = self._get_division(ctx.channel_id, uid)
        if err:
            await respond_error(ctx, err, locale=self._locale(uid))
            return
        success, msg = self.draft.goto_pick(division_name, pick, uid)
        await respond_result(ctx, success, msg, locale=self._locale(uid))

    @discord.slash_command(
        name="edit_pick",
        description="[ADMIN] Change a completed pick by number (e.g. fix Rotom-Heat → Rotom-Mow on pick 56).",
    )
    @option("pick", description="Pick number to edit", type=int)
    @option(
        "pokemon",
        description=POKEMON_OPTION_DESCRIPTION,
        type=str,
        autocomplete=_pool_pokemon_autocomplete,
    )
    @admin_only()
    async def edit_pick(
        self, ctx: discord.ApplicationContext, pick: int, pokemon: str
    ) -> None:
        uid = str(ctx.author.id)
        loc = self._locale(uid)
        division_name, err = self._get_division(ctx.channel_id, uid)
        if err:
            await respond_error(ctx, err, locale=loc)
            return
        success, msg, meta = await self.draft.edit_pick(division_name, pick, pokemon, uid)
        await respond_result(ctx, success, msg, locale=loc)
        if success and meta:
            state = self.draft._get_state(division_name)
            if state:
                public_embed, public_view = edit_pick_message(
                    state,
                    pick_number=meta["pick_number"],
                    edited_coach=meta["coach"],
                    old_name=meta["old_name"],
                    new_name=meta["new_name"],
                )
                await ctx.channel.send(embed=public_embed, view=public_view)

    @discord.slash_command(
        name="reset_division",
        description="[ADMIN] Apaga todo o estado de uma divisão para recomeçar do zero.",
    )
    @option("division", description="Nome da divisão a resetar (ex: Acuity)")
    @admin_only()
    async def reset_division(
        self, ctx: discord.ApplicationContext, division: str
    ) -> None:
        state = self.draft._get_state(division)

        if not state:
            await respond_error(
                ctx,
                i18n.t(ctx.author.id, "errors.division_not_initialised"),
                locale=self._locale(ctx.author.id),
            )
            return

        uid = str(ctx.author.id)
        embed = discord.Embed(
            title=i18n.t(uid, "admin.reset_title", division=division),
            description=i18n.t(
                uid,
                "admin.reset_desc",
                division=division,
                picks=state.global_pick_counter,
                coaches=len(state.coaches),
                status=state.status.value,
            ),
            colour=0xFF0000,
        )

        view = ResetConfirmView(admin_cog=self, division_name=division)
        await ctx.respond(embed=embed, view=view)

    # ── /force_skip ────────────────────────────────────────────────────────────

    @discord.slash_command(
        name="force_skip",
        description="[ADMIN] Immediately skip the current coach's pick.",
    )
    @admin_only()
    async def force_skip(self, ctx: discord.ApplicationContext) -> None:
        uid = str(ctx.author.id)
        loc = self._locale(uid)
        division_name, err = self._get_division(ctx.channel_id, uid)
        if err:
            await respond_error(ctx, err, locale=loc)
            return

        state = self.draft._get_state(division_name)
        if not state:
            await respond_error(ctx, i18n.t(uid, "errors.division_not_found"), locale=loc)
            return
        if state.status.value != "active":
            await respond_error(
                ctx,
                i18n.t(uid, "service.draft_not_active", status=state.status.value),
                locale=loc,
            )
            return

        current = state.current_coach
        coach_name = current.name if current else "current coach"

        await respond_success(
            ctx, i18n.t(uid, "admin.force_skip_done", coach=coach_name), locale=loc
        )

        # Cancel timer and call _handle_timeout directly (single execution, no task)
        self.draft.timers.cancel_timer(state)
        await self.draft._handle_timeout(division_name)

    # ── /draft_overview ────────────────────────────────────────────────────────

    @discord.slash_command(
        name="draft_overview",
        description="[ADMIN] Mostra o estado de todas as divisões.",
    )
    @admin_only()
    async def draft_overview(self, ctx: discord.ApplicationContext) -> None:
        all_states = self.draft.get_all_states()
        if not all_states:
            await respond_info(
                ctx, i18n.t(ctx.author.id, "admin.no_divisions"), locale=self._locale(ctx.author.id)
            )
            return
        loc = i18n.get_user_locale(str(ctx.author.id))
        embed = EmbedService.draft_overview(all_states, locale=loc)
        await ctx.respond(embed=embed, ephemeral=True)

    # ── /replace ──────────────────────────────────────────────────

    async def _resolve_replace_coach(
        self,
        ctx: discord.ApplicationContext,
        coach_mention: discord.Member | None,
        coach_name_str: str | None,
        discord_id_str: str | None,
    ) -> tuple[str | None, str | None, str | None]:
        if coach_mention:
            return str(coach_mention.id), coach_mention.display_name, None
        if discord_id_str and coach_name_str:
            return discord_id_str.strip(), coach_name_str.strip(), None
        if coach_name_str:
            manual_id = f"manual_{coach_name_str.lower().replace(' ', '_')}"
            return manual_id, coach_name_str.strip(), None
        if discord_id_str:
            return discord_id_str.strip(), ctx.author.display_name, None
        return str(ctx.author.id), ctx.author.display_name, None

    async def complete_replace_from_wizard(
        self,
        interaction: discord.Interaction,
        *,
        division_name: str,
        team_name: str,
        timezone: str,
        logo_url: str,
        locale: str,
    ) -> None:
        if not team_name:
            await interaction_error(
                interaction,
                i18n.t(interaction.user.id, "replace.wizard.need_team"),
                locale=locale,
            )
            return

        new_discord_id = str(interaction.user.id)
        new_coach_name = interaction.user.display_name

        state = self.draft._get_state(division_name)
        old_coach = state.get_coach_by_id(state.pending_replace_coach_id or "") if state else None
        old_name = old_coach.name if old_coach else "?"

        success, msg, welcome_channel = await self._apply_replace_and_welcome(
            division_name=division_name,
            new_discord_id=new_discord_id,
            new_coach_name=new_coach_name,
            team_name=team_name,
            logo_url=logo_url,
            timezone=timezone or "GMT+0",
        )
        if not success:
            await interaction_error(interaction, msg, locale=locale)
            return

        state = self.draft._get_state(division_name)
        new_coach = state.get_coach_by_id(new_discord_id) if state else None
        makeups = new_coach.makeup_picks_owed if new_coach else 0
        bank_note = "deactivated" in msg.lower()
        embed = EmbedService.replacement_applied_success(
            new_coach,
            old_name,
            state,
            replacement_makeups=makeups,
            bank_deactivated=bank_note,
            locale=locale,
        )
        await interaction_embed(interaction, embed, ephemeral=True)

        if welcome_channel and state and new_coach:
            welcome_embed, welcome_view = replacement_welcome_message(
                new_coach, old_name, state
            )
            await welcome_channel.send(
                content=new_coach.mention(),
                embed=welcome_embed,
                view=welcome_view,
            )

    async def _apply_replace_and_welcome(
        self,
        *,
        division_name: str,
        new_discord_id: str,
        new_coach_name: str,
        team_name: str,
        logo_url: str,
        timezone: str,
    ) -> tuple[bool, str, discord.TextChannel | None]:
        from utils.division_helper import load_coaches_config

        all_coaches = load_coaches_config()
        for c in all_coaches:
            if str(c.get("discord_id", "")) == new_discord_id:
                if not logo_url:
                    logo_url = c.get("logo_url", "")
                if timezone == "GMT+0":
                    timezone = c.get("timezone", "GMT+0")
                break

        success, msg = await self.draft.apply_replacement(
            division_name=division_name,
            new_coach_discord_id=new_discord_id,
            new_coach_name=new_coach_name,
            new_team_name=team_name,
            new_logo_url=logo_url or "",
            new_timezone=timezone,
        )
        if not success:
            return False, msg, None

        state = self.draft._get_state(division_name)
        channel = None
        if state:
            channel = self.bot.get_channel(state.channel_id)
        return True, msg, channel

    @discord.slash_command(
        name="replace",
        description="Replace a coach who was removed after 3 skips.",
    )
    @option("team_name", description="New coach's team name", required=False)
    @option("coach_mention", description="@mention the replacement",
            type=discord.Member, required=False)
    @option("coach_name_str", description="Coach name as string (if not in server)",
            required=False)
    @option("discord_id_str", description="Discord ID as string (if not in server)",
            required=False)
    @option("division", description="Division name (e.g. Acuity)", required=False)
    @option("logo_url", description="Team logo URL (optional)", required=False)
    @option("timezone", description='Timezone in GMT format e.g. "GMT+1"', required=False)
    async def replace(
        self,
        ctx: discord.ApplicationContext,
        team_name: str = None,
        coach_mention: discord.Member = None,
        coach_name_str: str = None,
        discord_id_str: str = None,
        division: str = None,
        logo_url: str = None,
        timezone: str = "GMT+0",
    ) -> None:
        uid = str(ctx.author.id)
        loc = self._locale(uid)

        if not division:
            division = self.draft.get_division_name_for_channel(ctx.channel_id)
        if not division:
            await respond_error(
                ctx, i18n.t(uid, "admin.replace_need_division"), locale=loc
            )
            return

        state = self.draft._get_state(division)
        if not state or state.status != DraftStatus.WAITING_REPLACE:
            await respond_error(
                ctx, i18n.t(uid, "service.not_awaiting_replacement"), locale=loc
            )
            return

        old_coach = state.get_coach_by_id(state.pending_replace_coach_id or "")
        if not old_coach:
            await respond_error(ctx, i18n.t(uid, "service.old_coach_not_found"), locale=loc)
            return

        if not team_name:
            invoker_name = ctx.author.display_name
            embed = build_replace_wizard_embed(
                state, old_coach, locale=loc, invoker_name=invoker_name
            )
            view = localized_replace_wizard_view(
                self,
                division,
                locale=loc,
                default_timezone=timezone or "GMT+0",
            )
            await respond_embed(ctx, embed, ephemeral=True, view=view)
            return

        await ctx.defer(ephemeral=True)

        new_discord_id, new_coach_name, err = await self._resolve_replace_coach(
            ctx, coach_mention, coach_name_str, discord_id_str
        )
        if err:
            await respond_error(ctx, err, locale=loc)
            return
        if not new_discord_id or not new_coach_name:
            await respond_error(ctx, i18n.t(uid, "admin.replace_need_coach"), locale=loc)
            return

        old_name = old_coach.name
        success, msg, welcome_channel = await self._apply_replace_and_welcome(
            division_name=division,
            new_discord_id=new_discord_id,
            new_coach_name=new_coach_name,
            team_name=team_name,
            logo_url=logo_url or "",
            timezone=timezone or "GMT+0",
        )

        if success:
            state = self.draft._get_state(division)
            new_coach = state.get_coach_by_id(new_discord_id) if state else None
            if welcome_channel and state and new_coach:
                welcome_embed, welcome_view = replacement_welcome_message(
                    new_coach, old_name, state
                )
                await welcome_channel.send(
                    content=new_coach.mention(),
                    embed=welcome_embed,
                    view=welcome_view,
                )
            makeups = new_coach.makeup_picks_owed if new_coach else 0
            bank_note = "deactivated" in msg.lower()
            embed = EmbedService.replacement_applied_success(
                new_coach,
                old_name,
                state,
                replacement_makeups=makeups,
                bank_deactivated=bank_note,
                locale=loc,
            )
            await respond_embed(ctx, embed, ephemeral=True)
        else:
            await respond_embed(ctx, error_embed(msg, locale=loc), ephemeral=True)

    # ── Synchronize coaches ───────────────────────────────────────────────────────
    
    @discord.slash_command(
        name="sync_coaches",
        description="[ADMIN] Sync coaches.json with the Participants sheet for a division.",
    )
    @option("division", description="Division name (e.g. Acuity)")
    @option("num_coaches", description="Number of coaches for this division", type=int)
    @option("first_coach", description="Name of the first coach to start counting from")
    @admin_only()
    async def sync_coaches(
        self,
        ctx: discord.ApplicationContext,
        division: str,
        num_coaches: int,
        first_coach: str,
    ) -> None:
        await ctx.defer(ephemeral=True)
        uid = str(ctx.author.id)

        ok, msg, _ = self.draft.sync_coaches_from_sheet(
            division,
            uid,
            num_coaches=num_coaches,
            first_coach=first_coach,
            persist_division_config=True,
        )
        if not ok:
            await respond_error(ctx, msg, locale=self._locale(uid))
            return

        await respond_success(ctx, msg, locale=self._locale(uid))

    # ── Alias management ───────────────────────────────────────────────────────

    @discord.slash_command(
        name="alias_learn",
        description="[ADMIN] Ensina um novo alias de Pokémon ao bot.",
    )
    @option("alias", description='O alias (ex: "waterpon")')
    @option(
        "pokemon",
        description=POKEMON_OPTION_DESCRIPTION,
        autocomplete=_pool_pokemon_autocomplete,
    )
    @admin_only()
    async def alias_learn(
        self, ctx: discord.ApplicationContext, alias: str, pokemon: str
    ) -> None:
        uid = str(ctx.author.id)
        loc = self._locale(uid)
        ok, reason_key = alias_is_allowed(alias)
        if not ok:
            await respond_error(ctx, i18n.t(uid, reason_key), locale=loc)
            return
        self.alias_manager.learn(alias, pokemon)
        await respond_success(
            ctx,
            i18n.t(uid, "admin.alias_learned", alias=alias.lower(), pokemon=pokemon),
            locale=loc,
        )

    @discord.slash_command(
        name="alias_forget",
        description="[ADMIN] Remove um alias aprendido.",
    )
    @option("alias", description="O alias a remover")
    @admin_only()
    async def alias_forget(self, ctx: discord.ApplicationContext, alias: str) -> None:
        removed = self.alias_manager.forget(alias)
        uid = str(ctx.author.id)
        loc = self._locale(uid)
        if removed:
            await respond_success(
                ctx, i18n.t(uid, "admin.alias_removed", alias=alias), locale=loc
            )
        else:
            await respond_error(
                ctx, i18n.t(uid, "admin.alias_not_found", alias=alias), locale=loc
            )

    @discord.slash_command(
        name="alias_list",
        description="[ADMIN] Lista todos os aliases aprendidos.",
    )
    @admin_only()
    async def alias_list(self, ctx: discord.ApplicationContext) -> None:
        learned = self.alias_manager.list_learned()
        uid = str(ctx.author.id)
        loc = self._locale(uid)
        if not learned:
            await respond_info(ctx, i18n.t(uid, "admin.no_aliases"), locale=loc)
            return
        lines = [
            f"**{alias}** → {canonical}"
            for alias, canonical in sorted(learned.items())
        ]
        chunks = [lines[i : i + 20] for i in range(0, len(lines), 20)]
        for i, chunk in enumerate(chunks):
            embed = discord.Embed(
                title=i18n.t(
                    uid,
                    "admin.alias_list_title",
                    page=i + 1,
                    pages=len(chunks),
                ),
                description="\n".join(chunk),
                colour=0x5865F2,
            )
            await ctx.respond(embed=embed, ephemeral=True)

    # ── /refresh_pokemon_cache ─────────────────────────────────────────────────

    @discord.slash_command(
        name="refresh_pokemon_cache",
        description="[ADMIN] Actualiza sprites e tipos via PokeAPI.",
    )
    @admin_only()
    async def refresh_pokemon_cache(self, ctx: discord.ApplicationContext) -> None:
        await ctx.defer(ephemeral=True)
        count = await self.pokemon_service.refresh_cache()
        await respond_success(
            ctx,
            i18n.t(ctx.author.id, "admin.cache_refreshed", count=count),
            locale=self._locale(ctx.author.id),
        )

class ResetConfirmView(discord.ui.View):
    """
    Confirmação antes de apagar todo o estado de uma divisão.
    Destrutivo — pede confirmação explícita antes de prosseguir.
    """

    def __init__(self, admin_cog: "AdminCog", division_name: str) -> None:
        super().__init__(timeout=60)
        self.admin_cog     = admin_cog
        self.division_name = division_name

    def _is_admin(self, member: discord.Member) -> bool:
        role = member.guild.get_role(Config.ADMIN_ROLE_ID)
        return role is not None and role in member.roles

    async def _disable(self, interaction: discord.Interaction) -> None:
        for child in self.children:
            child.disabled = True
        await interaction.message.edit(view=self)
        self.stop()

    @discord.ui.button(emoji="✅", label="Yes, delete everything", style=discord.ButtonStyle.danger)
    async def confirm(self, button, interaction):
        if not self._is_admin(interaction.user):
            await interaction_error(
                interaction,
                i18n.t(interaction.user.id, "admin.reset_confirm_only"),
                locale=i18n.get_user_locale(interaction.user.id),
            )
            return
        await self._disable(interaction)
        uid = str(interaction.user.id)
        loc = i18n.get_user_locale(uid)
        success, msg = await self.admin_cog.draft.reset_division(
            self.division_name, uid
        )
        body = f"{msg}\n{i18n.t(uid, 'admin.reset_after', division=self.division_name)}"
        embed = success_embed(body, locale=loc) if success else error_embed(body, locale=loc)
        await interaction.response.send_message(embed=embed)

    @discord.ui.button(emoji="❌", label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, button, interaction):
        if not self._is_admin(interaction.user):
            await interaction_error(
                interaction,
                i18n.t(interaction.user.id, "admin.reset_cancel_only"),
                locale=i18n.get_user_locale(interaction.user.id),
            )
            return
        await self._disable(interaction)
        await interaction_success(
            interaction,
            i18n.t(interaction.user.id, "admin.reset_cancelled"),
            locale=i18n.get_user_locale(interaction.user.id),
        )

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True
        self.stop()