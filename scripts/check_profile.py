"""
Dry-run a league profile without touching Discord or Google Sheets.

    python scripts/check_profile.py leagues/tuga-champions

Prints the sheet ranges the bot would read, the card cells it would write, and
the slash commands it would register — so a new league can be verified before
the first pick.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def main(profile_dir: str) -> int:
    profile = Path(profile_dir)
    if not profile.is_absolute():
        profile = PROJECT_ROOT / profile
    if not profile.is_dir():
        print(f"No such profile directory: {profile}")
        return 1

    os.environ["LEAGUE_DIR"] = str(profile)

    from services.draft.sheet_geometry import (
        compute_card_cell,
        compute_division_block_start_row,
    )
    from utils.division_helper import load_division_config
    from utils.feature_gate import disabled_commands
    from utils.league_settings import load_league_settings
    from utils.quiet_hours import QuietHours
    from utils.sheet_layout import load_sheet_layout

    layout = load_sheet_layout()
    league = load_league_settings()
    divisions = load_division_config()

    print(f"Profile: {profile.name}")
    print(f"  {league.name or '(unnamed league)'}\n")

    print("Reads")
    pool_range = layout.master_pool_read_range() or "(entire tab)"
    print(f"  Pokémon      {layout.master_pool_sheet}!{pool_range}")
    print(f"    name col   {layout.master_pool_column('name')}")
    print(f"    points col {layout.master_pool_column('points')}"
          f"  (require_points={layout.master_pool_requires_points()})")
    print(f"  Participants {layout.participants_sheet}!{layout.participants_read_range()}")
    order = layout.draft_order()
    if order:
        print(f"  Draft order  {order.get('sheet', layout.participants_sheet)}!{order['range']}")
    else:
        print("  Draft order  sheet order from each division's first_coach")

    print("\nDivisions")
    for div in divisions:
        coaches = div.get("num_coaches", 0)
        team = div.get("team_size", 0)
        print(
            f"  {div['name']}: {coaches} coaches · {team} picks · "
            f"{div.get('total_points')} pts · channel {div.get('channel_id')}"
        )
        start = compute_division_block_start_row(div["name"], divisions, layout)
        first = compute_card_cell(0, start, 0, coaches, layout)
        last = compute_card_cell(coaches - 1, start, team - 1, coaches, layout)
        print(f"    writes {layout.draft_board_sheet}!{first} … {last}")

    quiet = QuietHours(league.quiet_hours)
    print("\nTimers")
    if quiet.enabled:
        print(f"  Quiet window {quiet.describe_window()} — picks stay open, clocks stop")
    else:
        print("  Quiet window disabled — timers run around the clock")
    print(f"  Tera fields  {'shown' if league.show_tera else 'hidden'}")

    from config import Config

    print("\nFiles")
    print(f"  Data    {Config.DATA_DIR}")
    print(f"  Log     {Config.LOG_FILE}")
    print(f"  Embeds  posted in '{league.default_locale}'")

    hidden = disabled_commands()
    print("\nCommands")
    print(f"  Hidden ({len(hidden)}): {', '.join(sorted(hidden)) or 'none'}")
    return 0


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "."
    raise SystemExit(main(target))
