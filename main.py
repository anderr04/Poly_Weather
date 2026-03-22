#!/usr/bin/env python3
"""
main.py - Poly_Weather bot entry point.

Orchestrates:
    - WeatherStrategy (weather mispricing scanner)
    - PaperTrader (simulated execution)
    - ProbabilityValidator (Ollama IA, optional)
    - RiskManager (safeguards)
    - ShadowLogger (CSV logging)

Usage:
    python main.py                # Normal run (loops every 30 min)
    python main.py --once         # Single scan then exit
    python main.py --dry-run      # Startup check - no trading
    python main.py --interval 60  # Custom scan interval (minutes)
"""

from __future__ import annotations

import argparse
import io
import logging
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Fix Windows console encoding
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace"
    )
    sys.stderr = io.TextIOWrapper(
        sys.stderr.buffer, encoding="utf-8", errors="replace"
    )

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).resolve().parent))

import config
from src.paper_trader import PaperTrader
from src.probability_validator import ProbabilityValidator
from src.risk_manager import RiskManager
from src.shadow_logger import ShadowLogger
from strategies.weather.weather_strategy import WeatherStrategy

# -- Logging Setup --------------------------------------------------------

def setup_logging() -> None:
    """Configure logging with file + console output."""
    fmt = "%(asctime)s | %(levelname)-5s | %(name)-20s | %(message)s"

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)

    handlers = [console_handler]

    # File handler (always UTF-8)
    try:
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(
            config.LOG_FILE, encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        handlers.append(fh)
    except Exception:
        pass

    logging.basicConfig(
        level=getattr(logging, config.LOG_LEVEL, logging.INFO),
        format=fmt,
        handlers=handlers,
    )

    # Suppress noisy libraries
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)


logger = logging.getLogger("main")


# -- Banner ---------------------------------------------------------------

BANNER = """
+==============================================================+
|                                                              |
|   Poly_Weather Bot v1.0                                      |
|   Weather Mispricing + Copy-Trade Hybrid                     |
|                                                              |
|   Mode: {mode:<10s}  Capital: ${capital:<10.2f}              |
|   Cities: {n_cities}          Validator: {validator:<8s}     |
|   Interval: {interval}min     Mispricing: >{mispricing}%     |
|                                                              |
+==============================================================+
"""


# -- Shutdown Handler -----------------------------------------------------

_shutdown = False

def _signal_handler(sig, frame):
    global _shutdown
    _shutdown = True
    logger.info("STOP - Shutdown signal received. Finishing current cycle...")


# -- Main -----------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Poly_Weather - Weather mispricing bot for Polymarket")
    parser.add_argument(
        "--once", action="store_true",
        help="Run a single scan cycle then exit")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Startup check only - verify APIs and config, no trading")
    parser.add_argument(
        "--interval", type=int, default=None,
        help="Scan interval in minutes (overrides config)")
    args = parser.parse_args()

    setup_logging()

    # Register shutdown handler
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    interval_s = (args.interval * 60) if args.interval else config.WEATHER_SCAN_INTERVAL_S
    mode = "PAPER" if config.PAPER_MODE else "LIVE"

    # Print banner
    banner = BANNER.format(
        mode=mode,
        capital=config.INITIAL_CAPITAL,
        n_cities=len(config.WEATHER_CITIES),
        validator="ON" if config.VALIDATOR_ENABLED else "OFF",
        interval=interval_s // 60,
        mispricing=int(config.WEATHER_MISPRICING_THRESHOLD * 100),
    )
    for line in banner.split("\n"):
        logger.info(line)

    # -- Initialize Components ----------------------------------------

    logger.info("Initializing components...")

    # Paper Trader
    paper_trader = PaperTrader(capital=config.INITIAL_CAPITAL)
    logger.info("[PAPER] PaperTrader: $%.2f initial capital", config.INITIAL_CAPITAL)

    # Risk Manager
    risk_manager = RiskManager(capital=config.INITIAL_CAPITAL)
    logger.info(
        "[RISK] RiskManager: max %.0f%%/trade, min $%dK liquidity, "
        "min %dh resolution",
        config.MAX_CAPITAL_PER_TRADE_PCT * 100,
        int(config.MIN_LIQUIDITY_USD / 1000),
        config.MIN_RESOLUTION_HOURS,
    )

    # Shadow Logger
    shadow_logger = ShadowLogger()
    logger.info("[CSV] ShadowLogger: %s", config.SHADOW_CSV)

    # Probability Validator (optional)
    validator = None
    if config.VALIDATOR_ENABLED:
        validator = ProbabilityValidator()
        if validator.is_available():
            logger.info(
                "[AI] Validator: ENABLED (model=%s)",
                config.VALIDATOR_MODEL,
            )
        else:
            logger.warning(
                "[AI] Validator: ENABLED but Ollama not available at %s. "
                "Will run without validation.",
                config.VALIDATOR_OLLAMA_URL,
            )
            validator = None
    else:
        logger.info("[AI] Validator: DISABLED")

    # Weather Strategy
    weather = WeatherStrategy(
        paper_trader=paper_trader,
        risk_manager=risk_manager,
        shadow_logger=shadow_logger,
        validator=validator,
    )
    logger.info(
        "[WEATHER] WeatherStrategy: %d target cities, "
        "mispricing threshold %.0f%%",
        len(config.WEATHER_CITIES),
        config.WEATHER_MISPRICING_THRESHOLD * 100,
    )

    # -- Dry Run --------------------------------------------------

    if args.dry_run:
        logger.info("")
        logger.info("=" * 50)
        logger.info("  DRY RUN - Testing API connections...")
        logger.info("=" * 50)

        # Test Open-Meteo
        from strategies.weather.open_meteo import OpenMeteoClient
        meteo = OpenMeteoClient()
        test_city = list(config.WEATHER_CITIES.keys())[0]
        forecast = meteo.get_ensemble_forecast(test_city, days=3)
        if forecast:
            logger.info("  [OK] Open-Meteo Ensemble: OK (%s)", test_city)
        else:
            logger.warning("  [FAIL] Open-Meteo Ensemble: FAILED")

        # Test historical
        from datetime import timedelta
        test_date = datetime.now(timezone.utc) + timedelta(days=5)
        hist = meteo.get_historical(test_city, test_date, years_back=2)
        if hist:
            logger.info("  [OK] Open-Meteo Historical: OK (%d obs)",
                         hist.get("stats", {}).get("total_observations", 0))
        else:
            logger.warning("  [FAIL] Open-Meteo Historical: FAILED")

        # Test Polymarket
        from src.polymarket_api import PolymarketAPI
        api = PolymarketAPI()
        test_markets = api.search_markets(query="temperature", limit=5)
        logger.info("  [OK] Polymarket Gamma: %d markets found",
                     len(test_markets))

        # Test Ollama (if enabled)
        if config.VALIDATOR_ENABLED:
            from src.probability_validator import OllamaClient
            ollama = OllamaClient(
                base_url=config.VALIDATOR_OLLAMA_URL,
                model=config.VALIDATOR_MODEL,
            )
            if ollama.is_available():
                logger.info("  [OK] Ollama: available (model=%s)",
                             config.VALIDATOR_MODEL)
            else:
                logger.warning(
                    "  [WARN] Ollama: not available (bot will run without "
                    "validation)")

        logger.info("")
        logger.info("  Dry run complete. All systems checked.")
        logger.info("  Run without --dry-run to start trading.")
        return

    # -- Main Loop ------------------------------------------------

    logger.info("")
    logger.info("[START] Starting main loop (interval=%dm)...", interval_s // 60)
    logger.info("")

    cycle = 0
    while not _shutdown:
        cycle += 1
        cycle_start = time.time()

        logger.info("")
        logger.info("-" * 60)
        logger.info("  CYCLE #%d - %s",
                     cycle,
                     datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))
        logger.info("-" * 60)

        try:
            # Run weather strategy
            signals = weather.scan_and_evaluate()

            # Log status
            logger.info("")
            logger.info("[STATUS] Capital=$%.2f | PnL=$%+.4f | "
                         "Open=%d | Closed=%d | WR=%.0f%%",
                         paper_trader.capital,
                         paper_trader.total_pnl,
                         len(paper_trader.positions),
                         len(paper_trader.closed_trades),
                         paper_trader.win_rate * 100)
            logger.info("[RISK]   Exposure=$%.2f (%.1f%%) | "
                         "Daily PnL=$%+.2f | %s",
                         risk_manager.total_exposure,
                         risk_manager.total_exposure / risk_manager.capital * 100
                         if risk_manager.capital > 0 else 0,
                         risk_manager.daily_pnl,
                         "PAUSED" if risk_manager.is_paused else "ACTIVE")
            logger.info("[SHADOW] %d entries logged",
                         shadow_logger.total_logged)

            if validator:
                vs = validator.status()
                logger.info("[VALIDATOR] %d validated | %d/%d "
                             "approved (%.0f%%)",
                             vs["total_validated"],
                             vs["approved"], vs["total_validated"],
                             vs["approval_rate"])

        except Exception as e:
            logger.error("[ERROR] Error in cycle #%d: %s", cycle, e, exc_info=True)

        # Single scan mode
        if args.once:
            logger.info("Single scan mode - exiting.")
            break

        # Wait for next cycle
        elapsed = time.time() - cycle_start
        sleep_time = max(0, interval_s - elapsed)
        logger.info(
            "[WAIT] Next scan in %d min %.0f sec...",
            int(sleep_time // 60), sleep_time % 60,
        )

        # Interruptible sleep
        sleep_end = time.time() + sleep_time
        while time.time() < sleep_end and not _shutdown:
            time.sleep(1)

    # -- Shutdown -------------------------------------------------

    logger.info("")
    logger.info("=" * 50)
    logger.info("  SHUTDOWN - Final Status")
    logger.info("=" * 50)
    logger.info("  Capital: $%.2f (started $%.2f)",
                 paper_trader.capital, paper_trader.initial_capital)
    logger.info("  Total PnL: $%+.4f", paper_trader.total_pnl)
    logger.info("  Trades: %d closed, %d open",
                 len(paper_trader.closed_trades),
                 len(paper_trader.positions))
    logger.info("  Shadow entries: %d", shadow_logger.total_logged)
    logger.info("  Weather scanned: %d, mispriced: %d, traded: %d",
                 weather.total_scanned, weather.total_mispriced,
                 weather.total_traded)
    logger.info("")
    logger.info("  Run 'python analysis.py' to analyze results.")
    logger.info("=" * 50)


if __name__ == "__main__":
    main()
