# ArmaDraft

Discord bot for Pokémon snake-draft leagues. Manages multi-division drafts with point budgets, pick timers, skip/replacement flows, pick banks, and live Google Sheets mirroring.

## Features

- **Snake draft engine** — round reversals, escalating pick timers (4h → 2h → 1h), makeup picks, auto-replacement after 3 skips
- **Pick bank** — coaches set priority lists per round for automatic picks
- **Google Sheets integration** — bot is source of truth; sheet is a live mirror for the draft board
- **Multi-division** — one bot, many divisions, each mapped to a Discord channel
- **Pokémon enrichment** — PokeAPI sprites/types, fuzzy name matching, learned aliases
- **Team analysis** — `/myteam` shows weaknesses, resistances, and coverage gaps
- **Per-user language** — `/language` lets each coach choose EN, PT, or ES for bot messages
- **Admin log channel** — consolidated feed of picks, skips, pauses, and replacements
- **Crash recovery** — JSON persistence + timer rehydration on restart

## Requirements

- Python 3.10+
- A Discord application with bot token
- Google Cloud service account with Sheets API access
- A Google Spreadsheet configured for your league (see [Sheet layout](#google-sheets-layout))

## Quick start

### 1. Clone and install

```bash
git clone <your-repo-url>
cd ClaudeArmaDraft
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -r requirements.txt
```

### 2. Configure environment

Copy `.env.example` to `.env` and fill in:

| Variable | Description |
|---|---|
| `DISCORD_TOKEN` | Bot token from Discord Developer Portal |
| `SPREADSHEET_ID` | Google Spreadsheet ID (from the URL) |
| `GUILD_ID` | Your Discord server ID (slash commands sync to this guild instantly) |
| `ADMIN_ROLE_ID` | Role allowed to run admin commands |
| `MOD_ROLE_ID` | Moderator role (can chat when draft channel is locked; optional) |
| `LOCK_CHANNEL_ON_PAUSE` | Lock division channels on pause (`true`/`false`, default `true`) |
| `ADMIN_LOG_CHANNEL_ID` | Channel for admin event feed (optional) |
| `ANNOUNCEMENTS_CHANNEL_ID` | Global replacement announcements (optional) |
| `REPLACEMENT_ROLE_ID` | Role pinged when a coach needs replacing |
| `BANK_SNIPE_PAUSE_SECONDS` | Wait time after a bank primary is sniped before fallback (default: 1800 = 30 min) |
| `SIMULATION_BANK_SNIPE_PAUSE_SECONDS` | Same pause during `/simulate_draft` and `/simulate_system` (default: 30) |
| `SHEETS_BATCH_FLUSH_SECONDS` | Short wait to merge near-simultaneous picks into one API call (default: `0.4`) |
| `SHEETS_BATCH_MAX_SIZE` | Max cells per batchUpdate call (default: `5`) |
| `SHEETS_MAX_WRITES_PER_MINUTE` | Global API write cap to avoid 429 quota errors (default: 55) |
| `DEFAULT_LOCALE` | Default language (`en`, `pt`, `es`) for users who haven't set `/language` |
| `GOOGLE_CREDENTIALS_FILE` | Path to service account JSON (default: `credentials.json`) |

Place your Google service account key at `credentials.json` (gitignored).

**New league?** Run the interactive wizard or read the community guides:

```bash
python scripts/setup_wizard.py
```

- [Community setup guide](docs/COMMUNITY_SETUP.md) — Discord bot, Google Sheets, first draft
- [Sheet template reference](docs/SHEET_TEMPLATE.md) — spreadsheet structure and `sheet_layout.json`

### Invite the bot

In the [Discord Developer Portal](https://discord.com/developers/applications) → OAuth2 → URL Generator:

- Scopes: `bot`, `applications.commands`
- Permissions: Send Messages, Embed Links, Attach Files, Read Message History, Add Reactions, **Manage Channels** (required for channel lock on pause)

Replace `YOUR_CLIENT_ID` in:

```
https://discord.com/api/oauth2/authorize?client_id=YOUR_CLIENT_ID&permissions=378960&scope=bot%20applications.commands
```

### 3. League configuration

**`division_config.json`** — one entry per division:

```json
{
  "name": "Acuity",
  "channel_id": 1234567890,
  "team_size": 10,
  "total_points": 110,
  "num_coaches": 16,
  "first_coach": "CoachNameInSheet"
}
```

**`coaches.json`** — populated via `/sync_coaches` or auto-sync on `/start_division`.

**`sheet_layout.json`** — Google Sheets tab names, column mappings, and card grid geometry. Edit this when adapting to a different spreadsheet layout (no code changes needed).

### 4. Run

```bash
python main.py
```

## Commands

### Coaches

| Command | Description |
|---|---|
| `/pick <pokemon>` | Draft when it's your turn |
| `/makeup_pick <pokemon>` | Use a makeup pick after a skip |
| `/picking` | Who is picking and deadline |
| `/myteam` | Your team + analysis |
| `/team @coach` | View another coach's team |
| `/drafted` | Full pick history |
| `/search` | Browse the pool with filters |
| `/bank_set`, `/bank_set_conditional`, `/bank_mode`, `/bank_stealth` | Pick bank automation |
| `/bank_on`, `/bank_off`, `/bank_status` | Activate / view pick bank |
| `/language [en\|pt\|es]` | Set your preferred bot language |

### Admins

| Command | Description |
|---|---|
| `/start_division` | Initialise + confirm draft start |
| `/pause_draft` / `/resume_draft` | Pause or resume |
| `/force_skip` | Skip current coach immediately |
| `/replace` | Join as replacement (after 3 skips) |
| `/reset_division` | Wipe division state |
| `/sync_coaches` | Pull roster from Google Sheets |
| `/draft_overview` | Status of all divisions |
| `/alias_learn`, `/alias_forget`, `/alias_list` | Manage Pokémon aliases |
| `/simulate_draft` | Automated end-to-end draft dry-run (single division) |
| `/simulate_system` | Full system test across all 5 divisions (manual rollback via `/rollback_system_test`) |
| `/system_test_logs` | Summary + log file from the last system test |
| `/rollback_system_test` | Restore coaches, state, Participants sheet, and draft board from last snapshot |

After `/simulate_system`, reports are saved under `data/system_test_backup/<session_id>/`:

- `report.log` — human-readable timeline (also copied to `latest_report.log`)
- `report.json` — structured results (also copied to `latest_report.json`)

Use `/system_test_logs` in Discord for a quick summary and to download the log file.

## Pick bank

Coaches can leave picks with the bot to avoid timer skips and pick without being online.

### Simple plans (`/bank_set`)
```
/bank_set round:4 picks:Tinkaton, Scream Tail, Enamorus
```
Tries each name in order. If the **first** choice was sniped by another coach, the draft pauses for 30 minutes (configurable via `BANK_SNIPE_PAUSE_SECONDS`) before trying fallbacks — giving the coach time to pick manually or stop the bank; otherwise the next plan pick runs automatically.

### Conditional plans (`/bank_set_conditional`)
```
/bank_set_conditional round:5 branches:if:4=Tinkaton>then:Hydreigon,Kingambit|if:4=Scream Tail>then:Scizor|else:Great Tusk
```
Round 5 picks depend on what you drafted in Round 4.

### Modes
| Mode | Behaviour |
|---|---|
| `fallback` (default) | Primary sniped → 1h pause → try remaining options |
| `strict` | Only picks the first target; sniped → 1h pause → coach picks manually if still gone |

### Stealth (`/bank_stealth`)
When enabled, auto-picks appear as normal picks without the "Pick Bank Auto-Pick" label.

Plans are **private** (ephemeral commands). Snipe announcements in the channel do **not** reveal which Pokémon was targeted.

## Google Sheets layout

The bot reads/writes three areas of your spreadsheet (configured in `sheet_layout.json`):

| Sheet | Purpose |
|---|---|
| Master pool (`Sheet2`) | All Pokémon with point costs |
| Participants | Coach roster — row order = snake draft order |
| Drafting Pool | Live card grid — bot writes pick names into dropdown cells |
| draftStatus | Append-only audit log |

To adapt for another league, edit `sheet_layout.json`:

- Tab names under `sheets`
- Column indices under `master_pool.columns` and `participants.columns`
- Card grid geometry under `card_grid`
- Division stacking order under `divisions_order`

## Architecture

```
Discord Cogs (commands + UI)
        ↓
   DraftService (orchestrator)
        ↓
┌───────┼────────┬──────────────┬─────────────┐
│       │        │              │             │
PickValidator  TimerService  ReplacementHandler
Persistence   SheetsService  AdminLogService
              PokemonService  AliasManager / i18n
```

Draft engine modules live in `services/draft/`. User-facing strings live in `locales/*.json`.

## Internationalisation

| Context | Mechanism |
|---|---|
| Slash command replies (ephemeral) | Per-user locale via `/language` |
| Public channel embeds | Multi-language block (EN/PT/ES) + footer hint |
| Default language | `DEFAULT_LOCALE` in `.env` |

Pokémon names, coach names, and team names are never translated.

## Testing

```bash
pip install pytest pytest-asyncio
pytest
```

Tests cover snake navigation, point validation, pick banks, replacement logic, sheet geometry, and i18n.

## Runbook

### Starting a new season

1. Update the Google Sheet (pool, participants, draft board).
2. Set `division_config.json` with channel IDs and coach counts.
3. Run `/sync_coaches` or let `/start_division` auto-sync.
4. Run `/start_division` in the division channel and confirm with ✅.
5. Monitor `#admin-log` for events.

### Bot crashed mid-draft

1. Restart `python main.py`.
2. On boot, the bot restores JSON state from `data/state_<division>.json`.
3. Active timers are re-scheduled with remaining time.
4. If a deadline expired during downtime, the skip fires immediately.

### Coach needs replacing

1. After 3 timeouts, status becomes `waiting_replace`.
2. Announcement is posted in the division + announcements channels.
3. New coach runs `/replace` in the division channel.
4. Replacement completes any makeup picks, then resumes the draft.

### Sheets write failing

- Check `data/arma_draft.log` for `[Sheets]` errors.
- Verify service account has Editor access to the spreadsheet.
- Writes retry 3× with backoff; failed writes are logged but don't block picks.

### Changing spreadsheet layout

1. Edit `sheet_layout.json` to match your new sheet structure.
2. Restart the bot.
3. Run `/simulate_draft` in a test channel to verify reads/writes.

## Roadmap

Planned features for future seasons:

- Standings and match results
- League match scheduler (timezone-aware)
- Weekly matchup announcements
- Showdown replay analysis (results + kill leaderboard)

See `DATABASE_MIGRATION.md` for guidance on moving from JSON to SQLite when scaling up.

## License

Private league tool — adjust licensing as needed for your project.
