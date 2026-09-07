"""Therian / Incarnate name resolution for draft shorthand."""

from utils.alias_manager import AliasManager


POOL = [
    "Landorus", "Landorus-T", "Tornadus", "Tornadus-T",
    "Ogerpon", "Ogerpon-Wellspring", "Ogerpon-Hearthflame", "Ogerpon-Cornerstone",
]


def test_bare_landorus_is_incarnate():
    mgr = AliasManager(data_dir="data")
    name, fuzzy = mgr.resolve("Landorus", POOL)
    assert name == "Landorus"
    assert fuzzy is False


def test_landorus_therian_suffixes_map_to_sheet_t():
    mgr = AliasManager(data_dir="data")
    for raw in ("Landorus-T", "Landorus-Therian", "lando", "landot"):
        name, fuzzy = mgr.resolve(raw, POOL)
        assert name == "Landorus-T", raw
        assert fuzzy is False


def test_landorus_incarnate_explicit():
    mgr = AliasManager(data_dir="data")
    name, fuzzy = mgr.resolve("Landorus-Incarnate", POOL)
    assert name == "Landorus"
    assert fuzzy is False


def test_therian_pair_detection():
    mgr = AliasManager(data_dir="data")
    assert mgr.is_therian_incarnate_pair(["Landorus", "Landorus-T"])
    assert mgr.is_therian_incarnate_pair(["Tornadus-Therian", "Tornadus"])
    assert not mgr.is_therian_incarnate_pair(["Ogerpon", "Ogerpon-Wellspring"])
    assert not mgr.is_therian_incarnate_pair(["Landorus"])


def test_ogerpon_still_has_related_forms():
    mgr = AliasManager(data_dir="data")
    related = mgr.find_related_forms("Ogerpon", POOL)
    assert "Ogerpon-Wellspring" in related
    assert not mgr.is_therian_incarnate_pair(related)
    assert not mgr.is_default_form_pair(related)


def test_urshifu_bare_is_single_strike():
    mgr = AliasManager(data_dir="data")
    pool = ["Urshifu", "Urshifu-RS", "Landorus", "Landorus-T"]
    name, fuzzy = mgr.resolve("Urshifu", pool)
    assert name == "Urshifu"
    assert fuzzy is False
    assert mgr.is_default_form_pair(["Urshifu", "Urshifu-RS"])


def test_urshifu_rapid_aliases_map_to_rs():
    mgr = AliasManager(data_dir="data")
    pool = ["Urshifu", "Urshifu-RS"]
    for raw in ("Urshifu-RS", "Urshifu-RapidStrike", "Urshifu-Rapid-Strike", "watershifu"):
        name, fuzzy = mgr.resolve(raw, pool)
        assert name == "Urshifu-RS", raw
        assert fuzzy is False
