from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Pokemon:
    """
    Represents a Pokémon available in the draft pool.
    Loaded from Google Sheets at season setup and cached locally.
    """
    name: str               # Canonical name, e.g. "Ogerpon-Wellspring"
    points: int             # Draft point cost
    pokedex_id: int         # National dex # from sheet (draft conflict key)
    types: list[str]        # e.g. ["water", "grass"]
    sprite_url: str         # URL to official sprite
    is_banned: bool = False
    is_drafted: bool = False
    drafted_by: Optional[str] = None  # Coach discord_id who drafted this
    base_stats: dict[str, int] = field(default_factory=dict)
    api_slug: str = ""
    api_form_id: int = 0    # PokeAPI form id (distinct per form; not used for draft conflicts)
    tera_tax_cost: int = 0

    def species_dex(self) -> int:
        """National Pokédex number used to block duplicate species/forms in a draft."""
        return self.pokedex_id if self.pokedex_id > 0 else 0

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "points": self.points,
            "pokedex_id": self.pokedex_id,
            "types": self.types,
            "sprite_url": self.sprite_url,
            "is_banned": self.is_banned,
            "is_drafted": self.is_drafted,
            "drafted_by": self.drafted_by,
            "api_slug": self.api_slug,
            "api_form_id": self.api_form_id,
            "tera_tax_cost": self.tera_tax_cost,
            "base_stats": self.base_stats,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Pokemon":
        return cls(
            name=data["name"],
            points=data["points"],
            pokedex_id=data.get("pokedex_id", 0),
            types=data.get("types", ["unknown"]),
            sprite_url=data.get("sprite_url", ""),
            is_banned=data.get("is_banned", False),
            is_drafted=data.get("is_drafted", False),
            drafted_by=data.get("drafted_by"),
            api_slug=data.get("api_slug", ""),
            api_form_id=data.get("api_form_id", 0),
            tera_tax_cost=data.get("tera_tax_cost", 0),
            base_stats=data.get("base_stats", {}),
        )

    def primary_type(self) -> str:
        return self.types[0] if self.types else "unknown"

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Pokemon):
            return self.name.lower() == other.name.lower()
        return False

    def __hash__(self) -> int:
        return hash(self.name.lower())

    def __repr__(self) -> str:
        return f"Pokemon({self.name}, {self.points}pts)"
