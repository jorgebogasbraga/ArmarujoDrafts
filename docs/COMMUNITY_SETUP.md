# Community setup guide

This guide helps a new league adopt ArmaDraft without prior knowledge of the codebase. For day-to-day operation after setup, see the main [README](../README.md).

## What you need

| Item | Notes |
|---|---|
| Discord server | One channel per division recommended |
| Discord bot application | [Discord Developer Portal](https://discord.com/developers/applications) |
| Google Cloud project | Service account with Sheets API enabled |
| Google Spreadsheet | See [SHEET_TEMPLATE.md](SHEET_TEMPLATE.md) |
| Python 3.10+ host | VPS, homelab, or always-on PC |

## 1. Create the Discord bot

1. Open the [Developer Portal](https://discord.com/developers/applications) → **New Application**.
2. **Bot** tab → **Add Bot** → copy the **token** (this is `DISCORD_TOKEN`).
3. Enable **Message Content Intent** only if you plan future features that read plain messages (not required for slash commands).
4. **OAuth2 → URL Generator**:
   - Scopes: `bot`, `applications.commands`
   - Bot permissions (minimum):
     - Send Messages
     - Embed Links
     - Attach Files
     - Read Message History
     - Add Reactions
   - Copy the generated invite URL and open it to add the bot to your server.

Example invite format (replace `YOUR_CLIENT_ID`):

```
https://discord.com/api/oauth2/authorize?client_id=YOUR_CLIENT_ID&permissions=378944&scope=bot%20applications.commands
```

5. Create Discord roles/channels:
   - **Admin role** — people who run `/start_division`, `/pause_draft`, etc.
   - **Division channels** — one text channel per division
   - **Admin log channel** (optional) — consolidated event feed
   - **Announcements channel** (optional) — global replacement pings

Copy IDs via Discord **Developer Mode** (User Settings → Advanced → Developer Mode, then right-click → Copy ID).

## 2. Google Sheets access

1. Create a spreadsheet from your league template (see [SHEET_TEMPLATE.md](SHEET_TEMPLATE.md)).
2. In [Google Cloud Console](https://console.cloud.google.com/):
   - Enable **Google Sheets API**
   - Create a **Service Account**
   - Download the JSON key → save as `credentials.json` in the project root (never commit this file)
3. Share the spreadsheet with the service account email (`...@...iam.gserviceaccount.com`) as **Editor**.

The spreadsheet ID is the long string in the URL:

```
https://docs.google.com/spreadsheets/d/SPREADSHEET_ID/edit
```

## 3. Run the setup wizard

From the project root:

```bash
python scripts/setup_wizard.py
```

The wizard interactively creates:

- `.env` from `.env.example`
- `division_config.json` with your first division
- `data/` directory for runtime state

You can re-run it safely — it will not overwrite existing files unless you confirm.

## 4. Manual configuration reference

### `.env`

| Variable | Description |
|---|---|
| `DISCORD_TOKEN` | Bot token |
| `GUILD_ID` | Your Discord server ID (speeds up slash command sync during development) |
| `SPREADSHEET_ID` | Google Spreadsheet ID |
| `ADMIN_ROLE_ID` | Role allowed to use admin commands |
| `ADMIN_LOG_CHANNEL_ID` | Optional admin event channel |
| `ANNOUNCEMENTS_CHANNEL_ID` | Optional global announcements |
| `REPLACEMENT_ROLE_ID` | Role pinged when a coach needs replacing |
| `DEFAULT_LOCALE` | Default bot language: `en`, `pt`, or `es` |
| `BANK_SNIPE_PAUSE_SECONDS` | Pick bank snipe pause (default `1800` = 30 min) |
| `SIMULATION_BANK_SNIPE_PAUSE_SECONDS` | Snipe pause during simulation commands (default `30`) |
| `SHEETS_BATCH_FLUSH_SECONDS` | Short coalesce before a board write (default `0.4`) |
| `SHEETS_BATCH_MAX_SIZE` | Max cells per batchUpdate (default `5`) |
| `SHEETS_MAX_WRITES_PER_MINUTE` | Global write cap (default `55`) |

### `division_config.json`

Each division maps a Discord channel to draft rules:

```json
{
  "name": "Acuity",
  "channel_id": 1234567890123456789,
  "sheet_name": "Acuity Division",
  "team_size": 10,
  "total_points": 110,
  "tera_captain_points": 30,
  "pick_time_initial": 14400,
  "pick_time_second": 7200,
  "pick_time_final": 3600,
  "num_coaches": 16,
  "first_coach": "CoachNameInSheet"
}
```

Timer values are in **seconds** (`14400` = 4 hours).

### `sheet_layout.json`

Maps tab names and column indices to your spreadsheet. If your league uses the same layout as the reference template, you only need to edit `divisions_order` and tab names under `sheets`.

## 5. First draft checklist

1. Fill the Google Sheet: master pool, participants, draft board tabs.
2. Install dependencies: `pip install -r requirements.txt`
3. Start the bot: `python main.py`
4. In each division channel, run `/sync_coaches` (or rely on auto-sync during `/start_division`).
5. Run `/start_division division:YourDivision` and confirm with ✅.
6. Coaches use `/language` to set EN/PT/ES for private replies.
7. Monitor `#admin-log` (if configured) during the first round.

## 6. Internationalisation

- **Private commands** (`/myteam`, `/bank_set`, errors) use each user's `/language` preference.
- **Public channel embeds** (picks, skips, draft start) show a compact **EN / PT / ES** block plus a footer hint linking to `/language`.
- Pokémon and coach names are never translated.

Set the server-wide default with `DEFAULT_LOCALE=en` in `.env`.

## 7. Getting help / contributing

- Check `data/arma_draft.log` for runtime errors.
- Run `/simulate_draft` in a test channel before a live season.
- Open issues or PRs on GitHub with division config redacted (no tokens, no spreadsheet IDs if private).

## Troubleshooting

| Problem | Fix |
|---|---|
| Slash commands missing | Restart bot; verify `GUILD_ID`; wait up to 1h for global commands |
| Sheets write errors | Confirm service account is Editor; check `[Sheets]` lines in log |
| Wrong draft order | Re-run `/sync_coaches`; verify `first_coach` matches Participants sheet |
| Bot silent after pick | Check channel ID in `division_config.json` matches the division channel |
