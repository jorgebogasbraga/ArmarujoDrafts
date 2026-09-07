"""Tests for pick bank plans."""

from models.pick_bank import PickBank


class TestPickBank:
    def test_set_and_get_plan(self):
        bank = PickBank(coach_discord_id="1", division_name="Test")
        bank.set_plan(3, ["Gengar", "Typhlosion"])
        plan = bank.get_plan_for_round(3)
        assert plan is not None
        assert plan.priority_list == ["Gengar", "Typhlosion"]

    def test_replace_plan_for_same_round(self):
        bank = PickBank(coach_discord_id="1", division_name="Test")
        bank.set_plan(2, ["A", "B"])
        bank.set_plan(2, ["C", "D"])
        plan = bank.get_plan_for_round(2)
        assert plan.priority_list == ["C", "D"]

    def test_activate_deactivate(self):
        bank = PickBank(coach_discord_id="1", division_name="Test")
        assert not bank.is_active
        bank.activate()
        assert bank.is_active
        bank.deactivate()
        assert not bank.is_active

    def test_entries_sorted_by_round(self):
        bank = PickBank(coach_discord_id="1", division_name="Test")
        bank.set_plan(5, ["E"])
        bank.set_plan(2, ["B"])
        bank.set_plan(8, ["H"])
        assert [e.round_number for e in bank.entries] == [2, 5, 8]

    def test_entry_all_names_simple_and_conditional(self):
        from models.pick_bank import ConditionalBranch, PickBankEntry, PlanType

        simple = PickBankEntry(round_number=2, priority_list=["Gengar", "Typhlosion"])
        assert simple.all_names() == ["Gengar", "Typhlosion"]

        conditional = PickBankEntry(
            round_number=4,
            plan_type=PlanType.CONDITIONAL,
            branches=[ConditionalBranch(3, "Tinkaton", ["Hydreigon", "Kingambit"])],
            default_list=["Great Tusk"],
        )
        assert conditional.all_names() == ["Hydreigon", "Kingambit", "Great Tusk"]

    def test_clear_entries_deactivates(self):
        bank = PickBank(coach_discord_id="1", division_name="Test")
        bank.set_plan(1, ["A"])
        bank.activate()
        bank.entries.clear()
        bank.deactivate()
        assert not bank.entries
        assert not bank.is_active
