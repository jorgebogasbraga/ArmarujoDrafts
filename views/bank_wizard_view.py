"""Interactive pick bank wizard views."""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

import discord
from models.pick_bank import ConditionalBranch, PlanType
from services.bank_wizard import (
    BankWizardSession,
    build_branch_editor_embed,
    build_conditional_builder_embed,
    build_dashboard_embed,
    build_preview_embed,
    build_simple_builder_embed,
    list_pokemon_options,
)
from constants.embed_colours import BANK_WIZARD_COLOUR, EMBED_WARNING
from utils.i18n import i18n
from utils.pokemon_search import (
    BANK_MIN_POKEMON_SEARCH_LEN,
    bank_search_query_ready,
    search_query_ready,
)
from utils.response_embeds import error_embed, interaction_embed, interaction_error, interaction_success, success_embed

if TYPE_CHECKING:
    from cogs.bank_cog import BankCog

WIZARD_TIMEOUT = 900


def _L(locale: str, key: str, **kwargs) -> str:
    return i18n.t_locale(locale, key, **kwargs)


class BankWizardMixin:
    bank_cog: "BankCog"
    division_name: str
    coach_id: str
    locale: str

    def _state(self):
        return self.bank_cog.draft._get_state(self.division_name)

    def _coach(self):
        state = self._state()
        return state.get_coach_by_id(self.coach_id) if state else None

    def _session(self) -> BankWizardSession:
        return self.bank_cog.get_wizard_session(
            self.division_name, self.coach_id, self.locale
        )

    async def _deny(self, interaction: discord.Interaction) -> bool:
        if str(interaction.user.id) != self.coach_id:
            await interaction_embed(
                interaction,
                error_embed(_L(self.locale, "bank.wizard.not_your_bank"), locale=self.locale),
                ephemeral=True,
            )
            return True
        return False

    async def _edit_dashboard(self, interaction: discord.Interaction) -> None:
        state = self._state()
        coach = self._coach()
        embed = build_dashboard_embed(coach, state, self.locale)
        await interaction.response.edit_message(
            embed=embed,
            view=BankDashboardView(self.bank_cog, self.division_name, self.coach_id, self.locale),
        )

    async def _open_pokemon_search(
        self,
        interaction: discord.Interaction,
        *,
        on_apply,
    ) -> None:
        session = self._session()
        modal = PokemonSearchModal(
            locale=self.locale,
            initial_query=session.picker_query,
            on_apply=on_apply,
        )
        await interaction.response.send_modal(modal)

    def _reset_pokemon_search(self) -> None:
        session = self._session()
        session.picker_query = ""
        session.picker_page = 0


class PokemonPickerSelect(discord.ui.Select):
    def __init__(
        self,
        parent_view: discord.ui.View,
        bank_cog: "BankCog",
        division_name: str,
        coach_id: str,
        locale: str,
        *,
        target: str,
    ) -> None:
        self.parent_view_ref = parent_view
        self.bank_cog = bank_cog
        self.division_name = division_name
        self.coach_id = coach_id
        self.locale = locale
        self.target = target

        session = bank_cog.get_wizard_session(division_name, coach_id, locale)
        state = bank_cog.draft._get_state(division_name)
        options, total = list_pokemon_options(
            state, page=session.picker_page, query=session.picker_query
        )
        if not options:
            options = [
                discord.SelectOption(
                    label=_L(locale, "bank.wizard.no_pokemon"),
                    value="__none__",
                )
            ]

        super().__init__(
            placeholder=(
                _L(locale, "bank.wizard.select_pokemon_filtered", query=session.picker_query)
                if session.picker_query and bank_search_query_ready(session.picker_query)
                else _L(locale, "bank.wizard.select_pokemon")
            ),
            options=options[:25],
            min_values=1,
            max_values=1,
            row=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if str(interaction.user.id) != self.coach_id:
            await interaction_error(
                interaction,
                _L(self.locale, "bank.wizard.not_your_bank"),
                locale=self.locale,
            )
            return
        if self.values[0] == "__none__":
            await interaction.response.defer()
            return

        name = self.values[0]
        session = self.bank_cog.get_wizard_session(
            self.division_name, self.coach_id, self.locale
        )

        if self.target == "priority":
            if name not in session.priority_list:
                session.priority_list.append(name)
        elif self.target == "branch_then":
            if name not in session.branch_then_list:
                session.branch_then_list.append(name)
        elif self.target == "branch_if":
            session.branch_if_picked = name
        elif self.target == "default":
            if name not in session.default_list:
                session.default_list.append(name)

        await self._refresh(interaction)

    async def _refresh(self, interaction: discord.Interaction) -> None:
        if isinstance(self.parent_view_ref, BankSimpleBuilderView):
            state = self.bank_cog.draft._get_state(self.division_name)
            coach = state.get_coach_by_id(self.coach_id)
            session = self.bank_cog.get_wizard_session(
                self.division_name, self.coach_id, self.locale
            )
            embed = build_simple_builder_embed(session, coach, state)
            view = BankSimpleBuilderView(
                self.bank_cog, self.division_name, self.coach_id, self.locale
            )
        elif isinstance(self.parent_view_ref, BankBranchEditorView):
            session = self.bank_cog.get_wizard_session(
                self.division_name, self.coach_id, self.locale
            )
            state = self.bank_cog.draft._get_state(self.division_name)
            embed = build_branch_editor_embed(session, state)
            view = BankBranchEditorView(
                self.bank_cog, self.division_name, self.coach_id, self.locale
            )
        elif isinstance(self.parent_view_ref, BankConditionalBuilderView):
            state = self.bank_cog.draft._get_state(self.division_name)
            coach = state.get_coach_by_id(self.coach_id)
            session = self.bank_cog.get_wizard_session(
                self.division_name, self.coach_id, self.locale
            )
            embed = build_conditional_builder_embed(session, coach, state)
            view = BankConditionalBuilderView(
                self.bank_cog, self.division_name, self.coach_id, self.locale
            )
        elif isinstance(self.parent_view_ref, BankDefaultEditorView):
            session = self.bank_cog.get_wizard_session(
                self.division_name, self.coach_id, self.locale
            )
            embed = discord.Embed(
                title=_L(self.locale, "bank.wizard.default_edit_title"),
                description=_L(self.locale, "bank.wizard.default_edit_desc"),
                colour=0xFEE75C,
            )
            embed.add_field(
                name=_L(self.locale, "bank.wizard.field_default"),
                value=" → ".join(session.branch_then_list) or "—",
                inline=False,
            )
            view = BankDefaultEditorView(
                self.bank_cog, self.division_name, self.coach_id, self.locale
            )
        else:
            await interaction.response.defer()
            return

        await interaction.response.edit_message(embed=embed, view=view)


class PokemonSearchModal(discord.ui.Modal):
    """Filter the Pokémon dropdown by name (filters as you type from 1 character)."""

    def __init__(
        self,
        *,
        locale: str,
        initial_query: str,
        on_apply,
    ) -> None:
        super().__init__(title=_L(locale, "bank.wizard.search_title"))
        self.locale = locale
        self.on_apply = on_apply
        self.query_input = discord.ui.InputText(
            label=_L(locale, "bank.wizard.search_label"),
            placeholder=_L(locale, "bank.wizard.search_placeholder"),
            value=initial_query,
            required=False,
            max_length=50,
        )
        self.add_item(self.query_input)

    async def callback(self, interaction: discord.Interaction) -> None:
        query = self.query_input.value.strip()
        if query and not bank_search_query_ready(query):
            await interaction_embed(
                interaction,
                error_embed(
                    _L(
                        self.locale,
                        "bank.wizard.search_too_short",
                        min=BANK_MIN_POKEMON_SEARCH_LEN,
                    ),
                    locale=self.locale,
                ),
                ephemeral=True,
            )
            return
        await self.on_apply(interaction, query)


class BankDashboardView(BankWizardMixin, discord.ui.View):
    def __init__(
        self, bank_cog: "BankCog", division_name: str, coach_id: str, locale: str
    ) -> None:
        super().__init__(timeout=WIZARD_TIMEOUT)
        self.bank_cog = bank_cog
        self.division_name = division_name
        self.coach_id = coach_id
        self.locale = locale

    @discord.ui.button(
        label="Configure round",
        style=discord.ButtonStyle.secondary,
        row=0,
    )
    async def configure(
        self, button: discord.ui.Button, interaction: discord.Interaction
    ) -> None:
        if await self._deny(interaction):
            return
        state = self._state()
        view = BankRoundPickerView(
            self.bank_cog, self.division_name, self.coach_id, self.locale
        )
        embed = discord.Embed(
            title=_L(self.locale, "bank.wizard.pick_round_title"),
            description=_L(self.locale, "bank.wizard.pick_round_desc"),
            colour=BANK_WIZARD_COLOUR,
        )
        await interaction.response.edit_message(embed=embed, view=view)

    @discord.ui.button(
        label="Preview",
        style=discord.ButtonStyle.secondary,
        emoji="👁️",
        row=0,
    )
    async def preview(
        self, button: discord.ui.Button, interaction: discord.Interaction
    ) -> None:
        if await self._deny(interaction):
            return
        state = self._state()
        coach = self._coach()
        bank = state.pick_banks.get(self.coach_id)
        if not bank or not bank.entries:
            await interaction_error(
                interaction, _L(self.locale, "bank.no_plans"), locale=self.locale
            )
            return
        embed = build_preview_embed(coach, state, bank, self.locale)
        await interaction.response.edit_message(
            embed=embed,
            view=BankPreviewView(
                self.bank_cog, self.division_name, self.coach_id, self.locale
            ),
        )

    @discord.ui.button(
        label="Clear all plans",
        style=discord.ButtonStyle.danger,
        emoji="🗑️",
        row=1,
    )
    async def clear_all(
        self, button: discord.ui.Button, interaction: discord.Interaction
    ) -> None:
        if await self._deny(interaction):
            return
        state = self._state()
        bank = state.pick_banks.get(self.coach_id)
        if not bank or not bank.entries:
            await interaction_embed(
                interaction,
                error_embed(_L(self.locale, "bank.no_plans"), locale=self.locale),
                ephemeral=True,
            )
            return
        ok, msg = self.bank_cog.draft.clear_all_bank_plans(
            self.division_name, self.coach_id
        )
        if not ok:
            await interaction_embed(
                interaction,
                error_embed(msg, locale=self.locale),
                ephemeral=True,
            )
            return
        await self._edit_dashboard(interaction)

    @discord.ui.button(
        label="Settings",
        style=discord.ButtonStyle.secondary,
        emoji="🛠️",
        row=1,
    )
    async def settings(
        self, button: discord.ui.Button, interaction: discord.Interaction
    ) -> None:
        if await self._deny(interaction):
            return
        embed = discord.Embed(
            title=_L(self.locale, "bank.wizard.settings_title"),
            description=_L(self.locale, "bank.wizard.settings_desc"),
            colour=BANK_WIZARD_COLOUR,
        )
        await interaction.response.edit_message(
            embed=embed,
            view=BankSettingsView(
                self.bank_cog, self.division_name, self.coach_id, self.locale
            ),
        )


class BankRoundPickerView(BankWizardMixin, discord.ui.View):
    def __init__(
        self, bank_cog: "BankCog", division_name: str, coach_id: str, locale: str
    ) -> None:
        super().__init__(timeout=WIZARD_TIMEOUT)
        self.bank_cog = bank_cog
        self.division_name = division_name
        self.coach_id = coach_id
        self.locale = locale

        state = bank_cog.draft._get_state(division_name)
        options = [
            discord.SelectOption(label=f"Round {r}", value=str(r))
            for r in range(1, state.team_size + 1)
        ]
        select = discord.ui.Select(
            placeholder=_L(locale, "bank.wizard.select_round"),
            options=options,
        )
        select.callback = self._round_selected
        self.add_item(select)

    async def _round_selected(self, interaction: discord.Interaction) -> None:
        if await self._deny(interaction):
            return
        round_num = int(interaction.data["values"][0])
        session = self._session()
        session.editing_round = round_num
        session.priority_list = []
        session.branches = []
        session.default_list = []
        session.branch_then_list = []
        session.branch_if_picked = ""
        session.branch_if_round = max(1, round_num - 1)

        state = self._state()
        bank = state.pick_banks.get(self.coach_id)
        if bank:
            entry = bank.get_plan_for_round(round_num)
            if entry:
                if entry.plan_type == PlanType.SIMPLE:
                    session.priority_list = list(entry.priority_list)
                else:
                    session.branches = list(entry.branches)
                    session.default_list = list(entry.default_list)

        embed = discord.Embed(
            title=_L(self.locale, "bank.wizard.plan_type_title", round=round_num),
            description=_L(self.locale, "bank.wizard.plan_type_desc"),
            colour=BANK_WIZARD_COLOUR,
        )
        await interaction.response.edit_message(
            embed=embed,
            view=BankPlanTypeView(
                self.bank_cog, self.division_name, self.coach_id, self.locale
            ),
        )

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, row=1)
    async def back(
        self, button: discord.ui.Button, interaction: discord.Interaction
    ) -> None:
        if await self._deny(interaction):
            return
        await self._edit_dashboard(interaction)


class BankPlanTypeView(BankWizardMixin, discord.ui.View):
    def __init__(
        self, bank_cog: "BankCog", division_name: str, coach_id: str, locale: str
    ) -> None:
        super().__init__(timeout=WIZARD_TIMEOUT)
        self.bank_cog = bank_cog
        self.division_name = division_name
        self.coach_id = coach_id
        self.locale = locale

    @discord.ui.button(
        label="Simple priority list",
        style=discord.ButtonStyle.primary,
        emoji="📋",
    )
    async def simple(
        self, button: discord.ui.Button, interaction: discord.Interaction
    ) -> None:
        if await self._deny(interaction):
            return
        session = self._session()
        session.plan_type = "simple"
        state = self._state()
        coach = self._coach()
        embed = build_simple_builder_embed(session, coach, state)
        await interaction.response.edit_message(
            embed=embed,
            view=BankSimpleBuilderView(
                self.bank_cog, self.division_name, self.coach_id, self.locale
            ),
        )

    @discord.ui.button(
        label="Conditional plan",
        style=discord.ButtonStyle.primary,
        emoji="🔀",
    )
    async def conditional(
        self, button: discord.ui.Button, interaction: discord.Interaction
    ) -> None:
        if await self._deny(interaction):
            return
        session = self._session()
        session.plan_type = "conditional"
        state = self._state()
        coach = self._coach()
        embed = build_conditional_builder_embed(session, coach, state)
        await interaction.response.edit_message(
            embed=embed,
            view=BankConditionalBuilderView(
                self.bank_cog, self.division_name, self.coach_id, self.locale
            ),
        )

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary)
    async def back(
        self, button: discord.ui.Button, interaction: discord.Interaction
    ) -> None:
        if await self._deny(interaction):
            return
        await self._edit_dashboard(interaction)


class BankSimpleBuilderView(BankWizardMixin, discord.ui.View):
    def __init__(
        self, bank_cog: "BankCog", division_name: str, coach_id: str, locale: str
    ) -> None:
        super().__init__(timeout=WIZARD_TIMEOUT)
        self.bank_cog = bank_cog
        self.division_name = division_name
        self.coach_id = coach_id
        self.locale = locale
        self.add_item(
            PokemonPickerSelect(
                self, bank_cog, division_name, coach_id, locale, target="priority"
            )
        )
        session = bank_cog.get_wizard_session(division_name, coach_id, locale)
        state = bank_cog.draft._get_state(division_name)
        _, total_pages = list_pokemon_options(
            state, page=session.picker_page, query=session.picker_query
        )
        for child in self.children:
            if isinstance(child, discord.ui.Button) and child.label in ("◀", "▶"):
                child.disabled = total_pages <= 1

    @discord.ui.button(label="Search", style=discord.ButtonStyle.primary, emoji="🔍", row=0)
    async def search(
        self, button: discord.ui.Button, interaction: discord.Interaction
    ) -> None:
        if await self._deny(interaction):
            return

        async def on_apply(modal_interaction: discord.Interaction, query: str) -> None:
            session = self._session()
            session.picker_query = query
            session.picker_page = 0
            state = self._state()
            coach = self._coach()
            embed = build_simple_builder_embed(session, coach, state)
            await modal_interaction.response.edit_message(
                embed=embed,
                view=BankSimpleBuilderView(
                    self.bank_cog, self.division_name, self.coach_id, self.locale
                ),
            )

        await self._open_pokemon_search(interaction, on_apply=on_apply)

    @discord.ui.button(label="Remove last", style=discord.ButtonStyle.danger, row=2)
    async def remove_last(
        self, button: discord.ui.Button, interaction: discord.Interaction
    ) -> None:
        if await self._deny(interaction):
            return
        session = self._session()
        if session.priority_list:
            session.priority_list.pop()
        state = self._state()
        coach = self._coach()
        embed = build_simple_builder_embed(session, coach, state)
        await interaction.response.edit_message(
            embed=embed,
            view=BankSimpleBuilderView(
                self.bank_cog, self.division_name, self.coach_id, self.locale
            ),
        )

    @discord.ui.button(label="Move up", style=discord.ButtonStyle.secondary, row=2)
    async def move_up(
        self, button: discord.ui.Button, interaction: discord.Interaction
    ) -> None:
        if await self._deny(interaction):
            return
        session = self._session()
        if len(session.priority_list) >= 2:
            session.priority_list[-1], session.priority_list[-2] = (
                session.priority_list[-2],
                session.priority_list[-1],
            )
        state = self._state()
        coach = self._coach()
        embed = build_simple_builder_embed(session, coach, state)
        await interaction.response.edit_message(
            embed=embed,
            view=BankSimpleBuilderView(
                self.bank_cog, self.division_name, self.coach_id, self.locale
            ),
        )

    @discord.ui.button(label="Clear filter", style=discord.ButtonStyle.secondary, row=3)
    async def clear_search(
        self, button: discord.ui.Button, interaction: discord.Interaction
    ) -> None:
        if await self._deny(interaction):
            return
        self._reset_pokemon_search()
        state = self._state()
        coach = self._coach()
        session = self._session()
        embed = build_simple_builder_embed(session, coach, state)
        await interaction.response.edit_message(
            embed=embed,
            view=BankSimpleBuilderView(
                self.bank_cog, self.division_name, self.coach_id, self.locale
            ),
        )

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary, row=3)
    async def page_prev(
        self, button: discord.ui.Button, interaction: discord.Interaction
    ) -> None:
        if await self._deny(interaction):
            return
        session = self._session()
        session.picker_page = max(0, session.picker_page - 1)
        state = self._state()
        coach = self._coach()
        embed = build_simple_builder_embed(session, coach, state)
        await interaction.response.edit_message(
            embed=embed,
            view=BankSimpleBuilderView(
                self.bank_cog, self.division_name, self.coach_id, self.locale
            ),
        )

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary, row=3)
    async def page_next(
        self, button: discord.ui.Button, interaction: discord.Interaction
    ) -> None:
        if await self._deny(interaction):
            return
        session = self._session()
        state = self._state()
        _, total = list_pokemon_options(state, page=session.picker_page)
        session.picker_page = min(total - 1, session.picker_page + 1)
        coach = self._coach()
        embed = build_simple_builder_embed(session, coach, state)
        await interaction.response.edit_message(
            embed=embed,
            view=BankSimpleBuilderView(
                self.bank_cog, self.division_name, self.coach_id, self.locale
            ),
        )

    @discord.ui.button(label="Save", style=discord.ButtonStyle.success, row=3)
    async def save(
        self, button: discord.ui.Button, interaction: discord.Interaction
    ) -> None:
        if await self._deny(interaction):
            return
        session = self._session()
        if not session.priority_list:
            await interaction_error(
                interaction,
                _L(self.locale, "bank.wizard.need_pokemon"),
                locale=self.locale,
            )
            return
        ok, msg = self.bank_cog.draft.set_bank_plan(
            self.division_name,
            self.coach_id,
            session.editing_round,
            session.priority_list,
        )
        if not ok:
            await interaction_error(interaction, msg, locale=self.locale)
            return
        await interaction.response.defer(ephemeral=True)
        await interaction.followup.send(
            embed=success_embed(msg, locale=self.locale), ephemeral=True
        )
        state = self._state()
        coach = self._coach()
        embed = build_dashboard_embed(coach, state, self.locale)
        await interaction.message.edit(
            embed=embed,
            view=BankDashboardView(
                self.bank_cog, self.division_name, self.coach_id, self.locale
            ),
        )

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, row=3)
    async def back(
        self, button: discord.ui.Button, interaction: discord.Interaction
    ) -> None:
        if await self._deny(interaction):
            return
        await self._edit_dashboard(interaction)


class BankConditionalBuilderView(BankWizardMixin, discord.ui.View):
    def __init__(
        self, bank_cog: "BankCog", division_name: str, coach_id: str, locale: str
    ) -> None:
        super().__init__(timeout=WIZARD_TIMEOUT)
        self.bank_cog = bank_cog
        self.division_name = division_name
        self.coach_id = coach_id
        self.locale = locale

    @discord.ui.button(label="Add branch", style=discord.ButtonStyle.primary, emoji="➕")
    async def add_branch(
        self, button: discord.ui.Button, interaction: discord.Interaction
    ) -> None:
        if await self._deny(interaction):
            return
        session = self._session()
        session.branch_then_list = []
        session.branch_if_picked = ""
        session.branch_if_round = max(1, session.editing_round - 1)
        state = self._state()
        embed = build_branch_editor_embed(session, state)
        await interaction.response.edit_message(
            embed=embed,
            view=BankBranchEditorView(
                self.bank_cog, self.division_name, self.coach_id, self.locale
            ),
        )

    @discord.ui.button(label="Edit default (else)", style=discord.ButtonStyle.secondary)
    async def edit_default(
        self, button: discord.ui.Button, interaction: discord.Interaction
    ) -> None:
        if await self._deny(interaction):
            return
        session = self._session()
        session.branch_then_list = list(session.default_list)
        state = self._state()
        embed = discord.Embed(
            title=_L(self.locale, "bank.wizard.default_edit_title"),
            description=_L(self.locale, "bank.wizard.default_edit_desc"),
            colour=0xFEE75C,
        )
        embed.add_field(
            name=_L(self.locale, "bank.wizard.field_default"),
            value=" → ".join(session.branch_then_list) or "—",
            inline=False,
        )
        view = BankDefaultEditorView(
            self.bank_cog, self.division_name, self.coach_id, self.locale
        )
        await interaction.response.edit_message(embed=embed, view=view)

    @discord.ui.button(label="Save plan", style=discord.ButtonStyle.success)
    async def save(
        self, button: discord.ui.Button, interaction: discord.Interaction
    ) -> None:
        if await self._deny(interaction):
            return
        session = self._session()
        ok, msg = self.bank_cog.draft.set_bank_conditional_structured(
            self.division_name,
            self.coach_id,
            session.editing_round,
            session.branches,
            session.default_list,
        )
        if not ok:
            await interaction_error(interaction, msg, locale=self.locale)
            return
        await interaction.response.defer(ephemeral=True)
        await interaction.followup.send(
            embed=success_embed(msg, locale=self.locale), ephemeral=True
        )
        state = self._state()
        coach = self._coach()
        embed = build_dashboard_embed(coach, state, self.locale)
        await interaction.message.edit(
            embed=embed,
            view=BankDashboardView(
                self.bank_cog, self.division_name, self.coach_id, self.locale
            ),
        )

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary)
    async def back(
        self, button: discord.ui.Button, interaction: discord.Interaction
    ) -> None:
        if await self._deny(interaction):
            return
        await self._edit_dashboard(interaction)


class BankBranchEditorView(BankWizardMixin, discord.ui.View):
    def __init__(
        self, bank_cog: "BankCog", division_name: str, coach_id: str, locale: str
    ) -> None:
        super().__init__(timeout=WIZARD_TIMEOUT)
        self.bank_cog = bank_cog
        self.division_name = division_name
        self.coach_id = coach_id
        self.locale = locale

        state = bank_cog.draft._get_state(division_name)
        session = bank_cog.get_wizard_session(division_name, coach_id, locale)
        round_opts = [
            discord.SelectOption(label=f"Round {r}", value=str(r))
            for r in range(1, session.editing_round)
        ] or [discord.SelectOption(label="Round 1", value="1")]
        if_select = discord.ui.Select(
            placeholder=_L(locale, "bank.wizard.select_if_round"),
            options=round_opts,
            row=0,
        )
        if_select.callback = self._if_round
        self.add_item(if_select)

        self.add_item(
            PokemonPickerSelect(
                self, bank_cog, division_name, coach_id, locale, target="branch_if"
            )
        )
        self.add_item(
            PokemonPickerSelect(
                self, bank_cog, division_name, coach_id, locale, target="branch_then"
            )
        )

    async def _if_round(self, interaction: discord.Interaction) -> None:
        if await self._deny(interaction):
            return
        session = self._session()
        session.branch_if_round = int(interaction.data["values"][0])
        state = self._state()
        embed = build_branch_editor_embed(session, state)
        await interaction.response.edit_message(
            embed=embed,
            view=BankBranchEditorView(
                self.bank_cog, self.division_name, self.coach_id, self.locale
            ),
        )

    @discord.ui.button(label="Remove last (then)", style=discord.ButtonStyle.danger, row=3)
    async def remove_then(
        self, button: discord.ui.Button, interaction: discord.Interaction
    ) -> None:
        if await self._deny(interaction):
            return
        session = self._session()
        if session.branch_then_list:
            session.branch_then_list.pop()
        state = self._state()
        embed = build_branch_editor_embed(session, state)
        await interaction.response.edit_message(
            embed=embed,
            view=BankBranchEditorView(
                self.bank_cog, self.division_name, self.coach_id, self.locale
            ),
        )

    @discord.ui.button(label="Save branch", style=discord.ButtonStyle.success, row=3)
    async def save_branch(
        self, button: discord.ui.Button, interaction: discord.Interaction
    ) -> None:
        if await self._deny(interaction):
            return
        session = self._session()
        if not session.branch_if_picked or not session.branch_then_list:
            await interaction_error(
                interaction,
                _L(self.locale, "bank.wizard.branch_incomplete"),
                locale=self.locale,
            )
            return
        session.branches.append(
            ConditionalBranch(
                session.branch_if_round,
                session.branch_if_picked,
                list(session.branch_then_list),
            )
        )
        state = self._state()
        coach = self._coach()
        embed = build_conditional_builder_embed(session, coach, state)
        await interaction.response.edit_message(
            embed=embed,
            view=BankConditionalBuilderView(
                self.bank_cog, self.division_name, self.coach_id, self.locale
            ),
        )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, row=3)
    async def cancel(
        self, button: discord.ui.Button, interaction: discord.Interaction
    ) -> None:
        if await self._deny(interaction):
            return
        session = self._session()
        state = self._state()
        coach = self._coach()
        embed = build_conditional_builder_embed(session, coach, state)
        await interaction.response.edit_message(
            embed=embed,
            view=BankConditionalBuilderView(
                self.bank_cog, self.division_name, self.coach_id, self.locale
            ),
        )

    @discord.ui.button(label="Search", style=discord.ButtonStyle.primary, emoji="🔍", row=4)
    async def search(
        self, button: discord.ui.Button, interaction: discord.Interaction
    ) -> None:
        if await self._deny(interaction):
            return

        async def on_apply(modal_interaction: discord.Interaction, query: str) -> None:
            session = self._session()
            session.picker_query = query
            session.picker_page = 0
            state = self._state()
            embed = build_branch_editor_embed(session, state)
            await modal_interaction.response.edit_message(
                embed=embed,
                view=BankBranchEditorView(
                    self.bank_cog, self.division_name, self.coach_id, self.locale
                ),
            )

        await self._open_pokemon_search(interaction, on_apply=on_apply)

    @discord.ui.button(label="Clear", style=discord.ButtonStyle.secondary, row=4)
    async def clear_search(
        self, button: discord.ui.Button, interaction: discord.Interaction
    ) -> None:
        if await self._deny(interaction):
            return
        self._reset_pokemon_search()
        session = self._session()
        state = self._state()
        embed = build_branch_editor_embed(session, state)
        await interaction.response.edit_message(
            embed=embed,
            view=BankBranchEditorView(
                self.bank_cog, self.division_name, self.coach_id, self.locale
            ),
        )


class BankDefaultEditorView(BankWizardMixin, discord.ui.View):
    def __init__(
        self, bank_cog: "BankCog", division_name: str, coach_id: str, locale: str
    ) -> None:
        super().__init__(timeout=WIZARD_TIMEOUT)
        self.bank_cog = bank_cog
        self.division_name = division_name
        self.coach_id = coach_id
        self.locale = locale
        self.add_item(
            PokemonPickerSelect(
                self, bank_cog, division_name, coach_id, locale, target="branch_then"
            )
        )

    @discord.ui.button(label="Done", style=discord.ButtonStyle.success, row=1)
    async def done(
        self, button: discord.ui.Button, interaction: discord.Interaction
    ) -> None:
        if await self._deny(interaction):
            return
        session = self._session()
        session.default_list = list(session.branch_then_list)
        state = self._state()
        coach = self._coach()
        embed = build_conditional_builder_embed(session, coach, state)
        await interaction.response.edit_message(
            embed=embed,
            view=BankConditionalBuilderView(
                self.bank_cog, self.division_name, self.coach_id, self.locale
            ),
        )

    @discord.ui.button(label="Search", style=discord.ButtonStyle.primary, emoji="🔍", row=1)
    async def search(
        self, button: discord.ui.Button, interaction: discord.Interaction
    ) -> None:
        if await self._deny(interaction):
            return

        async def on_apply(modal_interaction: discord.Interaction, query: str) -> None:
            session = self._session()
            session.picker_query = query
            session.picker_page = 0
            embed = discord.Embed(
                title=_L(self.locale, "bank.wizard.default_edit_title"),
                description=_L(self.locale, "bank.wizard.default_edit_desc"),
                colour=0xFEE75C,
            )
            embed.add_field(
                name=_L(self.locale, "bank.wizard.field_default"),
                value=" → ".join(session.branch_then_list) or "—",
                inline=False,
            )
            await modal_interaction.response.edit_message(
                embed=embed,
                view=BankDefaultEditorView(
                    self.bank_cog, self.division_name, self.coach_id, self.locale
                ),
            )

        await self._open_pokemon_search(interaction, on_apply=on_apply)

    @discord.ui.button(label="Clear", style=discord.ButtonStyle.secondary, row=1)
    async def clear_search(
        self, button: discord.ui.Button, interaction: discord.Interaction
    ) -> None:
        if await self._deny(interaction):
            return
        self._reset_pokemon_search()
        session = self._session()
        embed = discord.Embed(
            title=_L(self.locale, "bank.wizard.default_edit_title"),
            description=_L(self.locale, "bank.wizard.default_edit_desc"),
            colour=0xFEE75C,
        )
        embed.add_field(
            name=_L(self.locale, "bank.wizard.field_default"),
            value=" → ".join(session.branch_then_list) or "—",
            inline=False,
        )
        await interaction.response.edit_message(
            embed=embed,
            view=BankDefaultEditorView(
                self.bank_cog, self.division_name, self.coach_id, self.locale
            ),
        )


class BankPreviewView(BankWizardMixin, discord.ui.View):
    def __init__(
        self, bank_cog: "BankCog", division_name: str, coach_id: str, locale: str
    ) -> None:
        super().__init__(timeout=WIZARD_TIMEOUT)
        self.bank_cog = bank_cog
        self.division_name = division_name
        self.coach_id = coach_id
        self.locale = locale

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary)
    async def back(
        self, button: discord.ui.Button, interaction: discord.Interaction
    ) -> None:
        if await self._deny(interaction):
            return
        await self._edit_dashboard(interaction)


class BankSettingsView(BankWizardMixin, discord.ui.View):
    def __init__(
        self, bank_cog: "BankCog", division_name: str, coach_id: str, locale: str
    ) -> None:
        super().__init__(timeout=WIZARD_TIMEOUT)
        self.bank_cog = bank_cog
        self.division_name = division_name
        self.coach_id = coach_id
        self.locale = locale

    @discord.ui.button(label="Mode: fallback", style=discord.ButtonStyle.secondary)
    async def mode_fallback(
        self, button: discord.ui.Button, interaction: discord.Interaction
    ) -> None:
        if await self._deny(interaction):
            return
        ok, msg = self.bank_cog.draft.set_bank_mode(
            self.division_name, self.coach_id, "fallback"
        )
        if ok:
            await interaction_success(interaction, msg, locale=self.locale)
        else:
            await interaction_error(interaction, msg, locale=self.locale)

    @discord.ui.button(label="Mode: strict", style=discord.ButtonStyle.secondary)
    async def mode_strict(
        self, button: discord.ui.Button, interaction: discord.Interaction
    ) -> None:
        if await self._deny(interaction):
            return
        ok, msg = self.bank_cog.draft.set_bank_mode(
            self.division_name, self.coach_id, "strict"
        )
        if ok:
            await interaction_success(interaction, msg, locale=self.locale)
        else:
            await interaction_error(interaction, msg, locale=self.locale)

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary)
    async def back(
        self, button: discord.ui.Button, interaction: discord.Interaction
    ) -> None:
        if await self._deny(interaction):
            return
        await self._edit_dashboard(interaction)
