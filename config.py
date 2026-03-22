"""
config.py – Centralised configuration for Poly_Weather bot.

Manages all parameters for:
    • Weather strategy (cities, thresholds, Open-Meteo)
    • Whale copy-trading (wallets, sizing)
    • Paper-trading simulation
    • IA probability validator (Ollama)
    • Risk management / safeguards
"""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ── Paths ────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
TRADES_CSV = DATA_DIR / "trades.csv"
SHADOW_CSV = DATA_DIR / "shadow_trades.csv"
LOG_FILE = DATA_DIR / "bot.log"

# ── Mode ─────────────────────────────────────────────────────────────
PAPER_MODE: bool = os.getenv(
    "PAPER_MODE", "true"
).lower() in ("1", "true", "yes")

INITIAL_CAPITAL: float = float(os.getenv("INITIAL_CAPITAL", "100.0"))

# ── Polymarket APIs ──────────────────────────────────────────────────
POLYMARKET_CLOB_URL = "https://clob.polymarket.com"
POLYMARKET_GAMMA_URL = "https://gamma-api.polymarket.com"

# Live trading credentials (NEVER commit real values)
POLYMARKET_API_KEY: str = os.getenv("POLYMARKET_API_KEY", "")
POLYMARKET_SECRET: str = os.getenv("POLYMARKET_SECRET", "")
POLYMARKET_PASSPHRASE: str = os.getenv("POLYMARKET_PASSPHRASE", "")
PRIVATE_KEY: str = os.getenv("PRIVATE_KEY", "")

# ── Logging ──────────────────────────────────────────────────────────
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")


# =====================================================================
#  🌤️  WEATHER STRATEGY CONFIGURATION
# =====================================================================

# Target cities — mid-tier, less saturated on Polymarket.
# Format: { "city_name": (latitude, longitude, timezone) }
# Easy to edit: add/remove cities as needed.
WEATHER_CITIES: dict[str, tuple[float, float, str]] = {
    "Madrid":           (40.4168,  -3.7038, "Europe/Madrid"),
    "Berlin":           (52.5200,  13.4050, "Europe/Berlin"),
    "Sydney":           (-33.8688, 151.2093, "Australia/Sydney"),
    "Singapore":        (1.3521,   103.8198, "Asia/Singapore"),
    "Mexico City":      (19.4326, -99.1332, "America/Mexico_City"),
    "Warsaw":           (52.2297,  21.0122, "Europe/Warsaw"),
    "Athens":           (37.9838,  23.7275, "Europe/Athens"),
    "Lisbon":           (38.7223,  -9.1393, "Europe/Lisbon"),
    "Istanbul":         (41.0082,  28.9784, "Europe/Istanbul"),
    "Buenos Aires":     (-34.6037, -58.3816, "America/Argentina/Buenos_Aires"),
    "Prague":           (50.0755,  14.4378, "Europe/Prague"),
    "Budapest":         (47.4979,  19.0402, "Europe/Budapest"),
    "Vienna":           (48.2082,  16.3738, "Europe/Vienna"),
    "Dublin":           (53.3498,  -6.2603, "Europe/Dublin"),
    "Helsinki":         (60.1699,  24.9384, "Europe/Helsinki"),
}

# Also support common US cities if markets exist for them
WEATHER_CITIES_EXTENDED: dict[str, tuple[float, float, str]] = {
    "New York":         (40.7128, -74.0060, "America/New_York"),
    "Los Angeles":      (34.0522, -118.2437, "America/Los_Angeles"),
    "Chicago":          (41.8781, -87.6298, "America/Chicago"),
    "Miami":            (25.7617, -80.1918, "America/New_York"),
    "London":           (51.5074, -0.1278, "Europe/London"),
    "Paris":            (48.8566,  2.3522, "Europe/Paris"),
    "Tokyo":            (35.6762, 139.6503, "Asia/Tokyo"),
}

# Combine: primary cities + extended (primary = priority)
ALL_WEATHER_CITIES = {**WEATHER_CITIES_EXTENDED, **WEATHER_CITIES}

# Mispricing threshold: only trade if |real_prob - poly_prob| > this
WEATHER_MISPRICING_THRESHOLD: float = float(
    os.getenv("WEATHER_MISPRICING_THRESHOLD", "0.15")
)

# How often to scan for weather markets (seconds)
WEATHER_SCAN_INTERVAL_S: int = int(
    os.getenv("WEATHER_SCAN_INTERVAL_S", "1800")  # 30 minutes
)

# Open-Meteo ensemble members to use for probability calculation
WEATHER_ENSEMBLE_MODELS: list[str] = [
    "gfs_seamless",  # GFS ensemble (primary)
]

# Historical lookback years for base-rate calculation
WEATHER_HISTORICAL_YEARS: int = int(
    os.getenv("WEATHER_HISTORICAL_YEARS", "5")
)

# Weight: ensemble forecast vs historical base rate
# 0.7 = 70% ensemble, 30% historical
WEATHER_ENSEMBLE_WEIGHT: float = float(
    os.getenv("WEATHER_ENSEMBLE_WEIGHT", "0.70")
)


# =====================================================================
#  🐋  WHALE COPY-TRADE CONFIGURATION
# =====================================================================

# Polygon WebSocket
POLYGON_WSS_URL: str = os.getenv(
    "POLYGON_WSS_URL",
    "wss://polygon-mainnet.g.alchemy.com/v2/YOUR_ALCHEMY_API_KEY",
)

# CTFExchange Contracts (Polygon Mainnet)
CTF_EXCHANGE_ADDRESS: str = os.getenv(
    "CTF_EXCHANGE_ADDRESS",
    "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E",
)
NEG_RISK_CTF_EXCHANGE_ADDRESS: str = os.getenv(
    "NEG_RISK_CTF_EXCHANGE_ADDRESS",
    "0xC5d563A36AE78145C45a50134d48A1215220f80a",
)

# Target whale wallets  (format in .env: "Label:EOA[:proxy1,proxy2];Label2:EOA2")
WHALE_WALLETS: dict[str, str] = {}
WHALE_PROXIES: dict[str, list[str]] = {}

_whale_env = os.getenv("WHALE_WALLETS", "")
if _whale_env:
    for _entry in _whale_env.split(";"):
        _entry = _entry.strip()
        if not _entry:
            continue
        _parts = _entry.split(":", 2)
        if len(_parts) < 2:
            continue
        _lbl = _parts[0].strip()
        _eoa = _parts[1].strip()
        if _lbl and _eoa:
            WHALE_WALLETS[_lbl] = _eoa
            if len(_parts) == 3 and _parts[2].strip():
                _proxy_list = [p.strip() for p in _parts[2].split(",") if p.strip()]
                if _proxy_list:
                    WHALE_PROXIES[_lbl] = _proxy_list

# Whale portfolio sizes (for conviction calculation)
WHALE_PORTFOLIOS: dict[str, float] = {}
DEFAULT_WHALE_PORTFOLIO_USD: float = float(
    os.getenv("DEFAULT_WHALE_PORTFOLIO_USD", "500000.0")
)

# Copy-trade sizing
COPY_MIN_CONVICTION_PCT: float = float(os.getenv("COPY_MIN_CONVICTION_PCT", "0.001"))
COPY_CONVICTION_MULTIPLIER: float = float(os.getenv("COPY_CONVICTION_MULTIPLIER", "10.0"))
COPY_MAX_POSITION_PCT: float = float(os.getenv("COPY_MAX_POSITION_PCT", "0.03"))  # 3% max
COPY_MIN_TRADE_USD: float = float(os.getenv("COPY_MIN_TRADE_USD", "1.0"))
COPY_MAX_SLIPPAGE_PCT: float = float(os.getenv("COPY_MAX_SLIPPAGE_PCT", "0.05"))
COPY_MIN_WHALE_SIZE_USD: float = float(os.getenv("COPY_MIN_WHALE_SIZE_USD", "100.0"))
COPY_MAX_PRICE: float = float(os.getenv("COPY_MAX_PRICE", "0.95"))
COPY_MIN_PRICE: float = float(os.getenv("COPY_MIN_PRICE", "0.05"))
COPY_BATCH_WINDOW_S: float = float(os.getenv("COPY_BATCH_WINDOW_S", "120.0"))


# =====================================================================
#  🔒  RISK MANAGEMENT / SAFEGUARDS
# =====================================================================

# Maximum % of capital per single trade (HARD LIMIT)
MAX_CAPITAL_PER_TRADE_PCT: float = float(
    os.getenv("MAX_CAPITAL_PER_TRADE_PCT", "0.05")  # 3%
)

# Minimum market liquidity (USD) to consider trading
MIN_LIQUIDITY_USD: float = float(
    os.getenv("MIN_LIQUIDITY_USD", "8000")  # $30K
)

# Minimum hours until resolution to trade
MIN_RESOLUTION_HOURS: int = int(
    os.getenv("MIN_RESOLUTION_HOURS", "24")
)

# Daily loss limit (% of capital) — auto-pause
MAX_DAILY_LOSS_PCT: float = float(
    os.getenv("MAX_DAILY_LOSS_PCT", "0.05")  # 5%
)

# Maximum total exposure (% of capital across ALL positions)
MAX_TOTAL_EXPOSURE_PCT: float = float(
    os.getenv("MAX_TOTAL_EXPOSURE_PCT", "0.30")  # 30%
)

# Maximum concurrent open positions
MAX_OPEN_POSITIONS: int = int(
    os.getenv("MAX_OPEN_POSITIONS", "10")
)

# Kelly criterion fraction (half-Kelly for safety)
KELLY_FRACTION: float = float(
    os.getenv("KELLY_FRACTION", "0.5")
)

# Slippage model
SLIPPAGE_PCT: float = float(os.getenv("SLIPPAGE_PCT", "0.005"))


# =====================================================================
#  🧠  IA PROBABILITY VALIDATOR (Ollama)
# =====================================================================

VALIDATOR_ENABLED: bool = os.getenv(
    "VALIDATOR_ENABLED", "true"
).lower() in ("1", "true", "yes")

VALIDATOR_MODEL: str = os.getenv("VALIDATOR_MODEL", "phi3:mini")
VALIDATOR_OLLAMA_URL: str = os.getenv(
    "VALIDATOR_OLLAMA_URL", "http://localhost:11434"
)
VALIDATOR_TIMEOUT_S: float = float(os.getenv("VALIDATOR_TIMEOUT_S", "30.0"))
VALIDATOR_MIN_CONFIDENCE: int = int(os.getenv("VALIDATOR_MIN_CONFIDENCE", "60"))
VALIDATOR_EDGE_THRESHOLD: float = float(os.getenv("VALIDATOR_EDGE_THRESHOLD", "0.15"))


# =====================================================================
#  📊  SHADOW TRADE CSV COLUMNS
# =====================================================================

SHADOW_COLUMNS = [
    "timestamp",
    "signal_source",           # "weather" or "whale_copy"
    "city",                    # city name (weather) or whale label
    "market_question",
    "market_slug",
    "condition_id",
    "token_id",
    "outcome",                 # YES / NO
    "poly_price",              # current Polymarket price
    "forecast_probability",    # our calculated real probability
    "mispricing",              # forecast_prob - poly_price
    "model_probability",       # Ollama's estimate
    "model_confidence",        # Ollama's confidence (0-100)
    "model_explanation",
    "model_sources",
    "would_trade",             # did it pass all filters?
    "trade_side",              # BUY_YES / BUY_NO / SKIP
    "position_size_usd",
    "kelly_size",
    "market_liquidity_usd",
    "resolution_date",
    "hours_to_resolution",
    "ensemble_members_yes",    # how many ensemble members predict YES
    "ensemble_total_members",
    "historical_base_rate",
    "bot_action",              # PAPER_BUY / SHADOW_LOG / SKIPPED
    "actual_outcome",          # filled later (WIN/LOSS)
    "pnl_simulated",           # filled later
    "model_name",
    "latency_ms",
]
