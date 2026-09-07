"""
ArmaDraft Bot — Entry Point

Startup sequence:
  1. Validate environment variables
  2. Connect to Google Sheets
  3. Initialise all shared services
  4. Register all cogs (passing shared services via constructor injection)
  5. Start the bot
  6. On ready: restore any persisted draft states and start Sheets write worker
"""

import asyncio
import logging
import os
import signal
import sys
import discord
from config import Config

# Half the modules below read the league's JSON at import time, so a wrong or
# missing LEAGUE_DIR has to be caught here — otherwise the first thing you see
# is a FileNotFoundError from deep inside an import chain.
Config.require_profile()

from services.persistence_service import PersistenceService
from services.sheets_service import SheetsService
from services.pokemon_service import PokemonService
from services.embed_service import EmbedService
from services.draft_service import DraftService
from services.channel_lock_service import ChannelLockService
from utils.alias_manager import AliasManager
from cogs.draft_cog import DraftCog
from cogs.bank_cog import BankCog
from cogs.admin_cog import AdminCog
from cogs.search_cog import SearchCog
from cogs.simulation_cog import SimulationCog
from cogs.language_cog import LanguageCog
from cogs.match_cog import MatchCog
from cogs.league_cog import LeagueCog
from cogs.trade_cog import TradeCog
from services.match_service import MatchService
from services.league.standings_service import StandingsService
from services.league.kill_leaderboard_service import KillLeaderboardService
from services.trade_service import TradeService
from utils.command_errors import handle_application_command_error
from utils.feature_gate import apply_command_gate, disabled_commands

# ── Logging ──────────────────────────────────────────────────────────────────

os.makedirs(os.path.dirname(Config.LOG_FILE) or ".", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(Config.LOG_FILE, encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)
logger.info(
    "League profile: %s", Config.LEAGUE_DIR or "project root (LEAGUE_DIR not set)"
)

# ── Bot setup ─────────────────────────────────────────────────────────────────

intents = discord.Intents.default()
intents.members = True       # Required to look up guild members by ID
intents.message_content = True
intents.reactions = True     # Coach vote pause/resume

# Guild-scoped slash commands sync instantly (seconds) instead of waiting up to
# ~1 hour for global command propagation — fine for this single-server league bot.
bot = discord.Bot(
    intents=intents,
    debug_guilds=[Config.GUILD_ID] if Config.GUILD_ID else None,
)

# ── Service instances (shared across all cogs) ────────────────────────────────

persistence = PersistenceService(data_dir=Config.DATA_DIR)
sheets = SheetsService()
pokemon_service = PokemonService()
alias_manager = AliasManager(data_dir=Config.DATA_DIR)
channel_lock = ChannelLockService(persistence=persistence)
draft_service = DraftService(
    persistence=persistence,
    sheets=sheets,
    alias_manager=alias_manager,
    channel_lock=channel_lock,
    pokemon_service=pokemon_service,
)
match_service = MatchService(data_dir=Config.DATA_DIR, sheets=sheets)
standings_service = StandingsService(sheets=sheets)
kill_service = KillLeaderboardService(sheets=sheets)
trade_service = TradeService(
    data_dir=Config.DATA_DIR,
    sheets=sheets,
    draft_service=draft_service,
)

# ── Cog registration ──────────────────────────────────────────────────────────

_disabled_commands = disabled_commands()


def register(cog) -> None:
    """Add a cog unless this league has hidden every command it provides."""
    if apply_command_gate(cog, _disabled_commands):
        bot.add_cog(cog)
    else:
        logger.info("[Features] %s not loaded — all its commands are disabled.", type(cog).__name__)


register(DraftCog(bot, draft_service, pokemon_service))
register(BankCog(bot, draft_service))
register(AdminCog(bot, draft_service, pokemon_service, alias_manager))
register(SearchCog(bot, draft_service))
register(SimulationCog(bot, draft_service))
register(LanguageCog(bot))
register(MatchCog(bot, match_service))
register(LeagueCog(bot, standings_service, kill_service, sheets, pokemon_service))
register(TradeCog(bot, trade_service))

# ── Events ────────────────────────────────────────────────────────────────────

@bot.event
async def on_ready() -> None:
    logger.info(f"Bot is online: {bot.user} (ID: {bot.user.id})")
    if Config.GUILD_ID:
        logger.info(
            "Slash commands registered to guild %s (instant sync on startup).",
            Config.GUILD_ID,
        )

    # gspread is blocking HTTP — keep it off the Discord heartbeat thread.
    await asyncio.to_thread(sheets.connect)

    channel_lock.bind(bot)

    # Start the async Sheets write worker
    sheets.start_worker(asyncio.get_event_loop())

    # Log restored states
    restored = draft_service.get_all_states()
    if restored:
        logger.info(f"Restored {len(restored)} division state(s): {list(restored.keys())}")

    await draft_service.restore_timers_after_startup()
    draft_service.start_quiet_hours_watcher()
    await channel_lock.reconcile_on_startup(draft_service.get_all_states())
    await draft_service.admin_log.restore_boards(draft_service.get_all_states())
    draft_service.timers.start_watchdog(draft_service.get_all_states)

    logger.info("ArmaDraft is ready.")


@bot.event
async def on_application_command_error(
    ctx: discord.ApplicationContext,
    error: discord.DiscordException,
) -> None:
    """Global error handler for all slash commands."""
    await handle_application_command_error(ctx, error, bot=bot)


def _shutdown_handler(signum, frame) -> None:
    logger.info("Shutdown signal received — freezing draft timers.")
    draft_service.freeze_all_timers()
    sys.exit(0)


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    try:
        Config.validate()
    except EnvironmentError as e:
        logger.critical(str(e))
        sys.exit(1)

    signal.signal(signal.SIGINT, _shutdown_handler)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _shutdown_handler)

    try:
        bot.run(Config.DISCORD_TOKEN)
    finally:
        draft_service.freeze_all_timers()
