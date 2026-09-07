# Google Sheets template

ArmaDraft mirrors draft state into a Google Spreadsheet. The bot is the **source of truth** during an active draft; the sheet is a live board for spectators and league staff.

Configure tab names and column mapping in `sheet_layout.json`. This document describes the expected structure.

## Required tabs

| Tab name (default) | Purpose |
|---|---|
| `Sheet2` | Master Pokémon pool with point costs |
| `Participants` | Coach roster — row order = snake draft order |
| `Drafting Pool` | Live card grid — bot writes pick names into cells |
| `draftStatus` | Append-only audit log (optional but recommended) |

Rename tabs in `sheet_layout.json` → `sheets` if your league uses different names.

## Master pool (`Sheet2`)

Expected columns (1-based indices in `sheet_layout.json`):

| Column | Field | Example |
|---|---|---|
| A | Pokédex ID | `1006` |
| B | Pokémon name | `Iron Valiant` |
| C | Point cost | `18` |
| E | Tera tax / ban flag | empty or `banned` |

Row 1 is the header. Data starts at row 2.

The bot reads this tab on `/start_division` to build the in-memory pool.

## Participants

Coaches are read starting at row 5 (configurable via `participants.start_row`).

| Column | Field | Example |
|---|---|---|
| D | Coach name | `Folgado` |
| E | Team name | `Team Folgado` |
| F | Discord user ID | `123456789012345678` |
| G | Team logo URL | `https://...` |
| I | Timezone | `GMT+1` or `Europe/Lisbon` |

**Draft order** = row order in this sheet, beginning at the coach named in `first_coach` (`division_config.json`), for `num_coaches` rows.

Use `/sync_coaches` to pull this tab into `coaches.json`.

## Drafting Pool (card grid)

Each division occupies a vertical block of rows on the draft board tab. Block start rows are computed from `divisions_order` and `card_grid` settings in `sheet_layout.json`.

Within each division block:

- Coach names appear in a grid (default: up to 8 coaches per row block)
- Each coach has a column of dropdown cells for their picks
- When a pick is made, the bot writes the Pokémon name into the correct cell

You do **not** need to edit this tab manually during a draft — the bot maintains it.

### Geometry knobs (`sheet_layout.json` → `card_grid`)

| Key | Meaning |
|---|---|
| `first_name_row` / `first_name_col` | Top-left coach name cell |
| `cards_per_row_block` | Coaches per horizontal row before wrapping |
| `column_step` | Columns between coach cards |
| `row_step` | Rows between pick dropdown rows |
| `division_gap_rows` | Empty rows between division blocks |

If you change the physical layout of your sheet, update these values and run `/simulate_draft` to verify.

## draftStatus log

The bot appends lines such as:

```
Acuity — Pick #42: CoachName drafted Pokémon (18 pts)
Acuity — Draft paused. Reason: admin break
```

Useful for post-draft review. Failures here do not block picks.

## Match Proposals (`Match Proposals`)

| Column | Field |
|---|---|
| A | Proposal ID |
| B | Division |
| C | Proposer Discord ID |
| D | Opponent Discord ID |
| E | Scheduled UTC (ISO) |
| F | Status |
| G | Message ID |
| H | Channel ID |
| I | Created at |

Row 1 = header. The bot appends on each proposal create/update.

## Standings (`Standings`)

| Column | Field |
|---|---|
| A | Division |
| B | Coach name |
| C | Team name |
| D | Wins |
| E | Losses |
| F | Kill differential |
| G | Tiebreak winner (coach name, optional) |

One row per coach per division. Sorted by bot: wins → kill diff → tiebreak.

## Match Results (`Match Results`)

| Column | Field |
|---|---|
| A | Division |
| B | Week |
| C | Coach A |
| D | Coach B |
| E | Winner |
| F | Replay URL |
| G | Kills A |
| H | Kills B |
| I | Submitted by (Discord ID) |
| J | Timestamp |

Populated by `/submit_replay`.

## Kill Stats (`Kill Stats`)

| Column | Field |
|---|---|
| A | Division |
| B | Coach |
| C | Pokémon |
| D | Kills |
| E | Deaths |
| F | Battles |

Aggregated by `/killboard` per Pokémon across the division.

## Trades (`Trades`)

| Column | Field |
|---|---|
| A | Trade ID |
| B | Division |
| C | Type (`pool` / `direct`) |
| D | Proposer ID |
| E | Target ID |
| F | Offering |
| G | Receiving |
| H | Points delta A |
| I | Points delta B |
| J | Status |
| K | Created at |
| L | Approved by |

## Adapting for a new league

1. Copy an existing league spreadsheet or build tabs matching the tables above.
2. Edit `sheet_layout.json`:
   - `sheets.*` tab names
   - `master_pool.columns` if your pool uses different columns
   - `participants.columns` if roster columns differ
   - `divisions_order` — list division names top-to-bottom as they appear on the draft board
3. Set `division_config.json` with matching `sheet_name` per division.
4. Share the sheet with the service account.
5. Run `/simulate_draft` before going live.

## Division stacking example

If `divisions_order` is:

```json
["Acuity", "Verity", "Valor"]
```

The bot places **Acuity** at the top block, **Verity** below it (after `division_gap_rows`), then **Valor**, and so on. Each division's `card_block_start_row` is calculated automatically — do not hardcode row numbers in `division_config.json`.
