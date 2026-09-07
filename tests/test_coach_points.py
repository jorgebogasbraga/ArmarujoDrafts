"""Tests for coach point tracking."""

from tests.helpers import make_coach, make_pokemon


class TestCoachPoints:
    def test_add_pokemon_deducts_points(self):
        coach = make_coach("1", "Alice", remaining_points=20)
        mon = make_pokemon("Eevee", 5)
        coach.add_pokemon(mon)
        assert coach.remaining_points == 15
        assert mon.is_drafted
        assert mon.drafted_by == "1"

    def test_recalculate_points(self):
        coach = make_coach("1", "Alice", remaining_points=999)
        coach.team = [make_pokemon("A", 3), make_pokemon("B", 7)]
        coach.recalculate_points(total_points=20)
        assert coach.remaining_points == 10

    def test_can_afford_boundary(self):
        coach = make_coach("1", "Alice", remaining_points=5)
        assert coach.can_afford(make_pokemon("X", 5))
        assert not coach.can_afford(make_pokemon("Y", 6))

    def test_has_slot(self):
        coach = make_coach("1", "Alice")
        assert coach.has_slot(3)
        coach.team = [make_pokemon("A", 1), make_pokemon("B", 1), make_pokemon("C", 1)]
        assert not coach.has_slot(3)
