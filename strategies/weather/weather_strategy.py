"""
weather_strategy.py – Main weather mispricing strategy.

The core edge: compares Open-Meteo GFS ensemble + historical data
against Polymarket prices to detect mispricing > 15%.

Flow:
    1. Scanner → finds weather markets for target cities
    2. Open-Meteo → calculates real probability
    3. Compare vs poly_price → detect mispricing
    4. Risk checks → safeguards
    5. Validator (Ollama) → optional second opinion
    6. Execute → paper trade + shadow log
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Optional

import config
from src.paper_trader import PaperTrader
from src.probability_validator import ProbabilityValidator
from src.risk_manager import RiskManager
from src.shadow_logger import ShadowLogger
from strategies.weather.market_scanner import WeatherMarket, WeatherMarketScanner
from strategies.weather.open_meteo import OpenMeteoClient

logger = logging.getLogger(__name__)


class WeatherStrategy:
    """
    Weather mispricing strategy.

    Detects when Polymarket weather markets are mispriced vs
    real meteorological data (GFS ensemble + 5yr historical).
    """

    def __init__(
        self,
        paper_trader: PaperTrader,
        risk_manager: RiskManager,
        shadow_logger: ShadowLogger,
        validator: Optional[ProbabilityValidator] = None,
    ):
        self.trader = paper_trader
        self.risk = risk_manager
        self.shadow = shadow_logger
        self.validator = validator

        self.scanner = WeatherMarketScanner()
        self.meteo = OpenMeteoClient()

        # Stats
        self.total_scanned = 0
        self.total_mispriced = 0
        self.total_traded = 0
        self.total_skipped = 0
        self.last_scan_time: Optional[datetime] = None

    # ── Main Loop ────────────────────────────────────────────────

    def scan_and_evaluate(self) -> list[dict[str, Any]]:
        """
        Full scan cycle:
        1. Find weather markets on Polymarket
        2. Calculate real probabilities via Open-Meteo
        3. Detect mispricing
        4. Apply risk checks
        5. Optionally validate with Ollama
        6. Execute paper trades

        Returns:
            List of signal dicts (for dashboard/logging).
        """
        t0 = time.time()
        self.last_scan_time = datetime.now(timezone.utc)
        signals = []

        # 1. Scan for markets
        logger.info("=" * 60)
        logger.info("[WEATHER] SCAN START - %s",
                     self.last_scan_time.strftime("%H:%M:%S UTC"))
        logger.info("=" * 60)

        markets = self.scanner.scan()
        self.total_scanned += len(markets)

        if not markets:
            logger.info("[WEATHER] No weather markets found for target cities")
            return signals

        # 2. Evaluate each market
        for market in markets:
            try:
                signal = self._evaluate_market(market)
                if signal:
                    signals.append(signal)
            except Exception as e:
                logger.error(
                    "Error evaluating %s: %s", market.question[:50], e,
                    exc_info=True,
                )

        elapsed = time.time() - t0
        logger.info(
            "[WEATHER] SCAN COMPLETE | %d markets | %d mispriced | "
            "%d traded | %.1fs",
            len(markets), self.total_mispriced,
            self.total_traded, elapsed,
        )

        return signals

    # ── Market Evaluation ────────────────────────────────────────

    def _evaluate_market(
        self, market: WeatherMarket,
    ) -> Optional[dict[str, Any]]:
        """
        Evaluate a single weather market for mispricing.

        Returns signal dict if mispricing found, None otherwise.
        """
        t0 = time.time()

        # Skip if price data not available
        if not market.tokens or market.yes_price <= 0:
            logger.debug("Skipping %s: no price data", market.question[:40])
            return None

        # 1. Calculate real probability from Open-Meteo
        if not market.resolution_date:
            logger.debug("Skipping %s: no resolution date",
                         market.question[:40])
            return None

        prob_data = self.meteo.calculate_probability(
            city=market.city,
            metric=market.metric,
            operator=market.operator,
            threshold=market.threshold,
            target_date=market.resolution_date,
            threshold_unit=market.threshold_unit,
        )

        real_prob = prob_data["combined_probability"]
        poly_price = market.yes_price

        # 2. Detect mispricing
        mispricing = real_prob - poly_price
        abs_mispricing = abs(mispricing)

        logger.info(
            "[EVAL] %s | %s | %s %s %.0f %s | "
            "real=%.3f vs poly=%.3f | mispricing=%+.3f",
            market.city, market.question[:40],
            market.operator, market.metric,
            market.threshold, market.threshold_unit,
            real_prob, poly_price, mispricing,
        )

        if abs_mispricing < config.WEATHER_MISPRICING_THRESHOLD:
            # Not enough edge — shadow log and skip
            self._log_shadow(
                market, prob_data, None, "SKIP",
                0, 0, "NO_EDGE", time.time() - t0,
            )
            return None

        self.total_mispriced += 1

        # 3. Determine trade side
        if mispricing > 0:
            # Polymarket underpricing the event -> BUY YES
            trade_side = "BUY_YES"
            trade_price = poly_price
        else:
            # Polymarket overpricing the event -> BUY NO
            trade_side = "BUY_NO"
            trade_price = market.no_price if market.no_price > 0 else (1 - poly_price)

        # 4. Risk checks
        kelly_size = self.risk.calculate_kelly_size(real_prob, trade_price)

        if kelly_size <= 0:
            self._log_shadow(
                market, prob_data, None, trade_side,
                0, 0, "NO_KELLY_EDGE", time.time() - t0,
            )
            return None

        passed, reason = self.risk.check_trade(
            trade_size_usd=kelly_size,
            market_liquidity_usd=market.liquidity_usd,
            resolution_date=market.resolution_date,
            source="weather",
        )

        if not passed:
            logger.info("[RISK BLOCK] %s | %s", market.city, reason)
            self._log_shadow(
                market, prob_data, None, trade_side,
                kelly_size, kelly_size, f"RISK_{reason}",
                time.time() - t0,
            )
            return None

        # 5. Validator (Ollama) — optional
        model_result = None
        if self.validator and self.validator.is_available():
            model_result = self.validator.validate_weather(
                market_question=market.question,
                outcome="YES" if trade_side == "BUY_YES" else "NO",
                poly_price=poly_price,
                resolution_date=market.resolution_date.strftime("%Y-%m-%d"),
                forecast_data=prob_data.get("forecast_summary", ""),
                historical_data=prob_data.get("historical_summary", ""),
                our_probability=real_prob,
                ensemble_yes=prob_data.get("ensemble_yes", 0),
                ensemble_total=prob_data.get("ensemble_total", 0),
                base_rate=prob_data.get("historical_base_rate", 0.5),
            )

            # If validator disagrees strongly, skip
            if model_result:
                val_prob = model_result.get("real_probability", 0)
                val_conf = model_result.get("confidence", 0)

                # If validator has high confidence and disagrees, skip
                if val_conf >= 70 and abs(val_prob - real_prob) > 0.20:
                    logger.info(
                        "[AI DISAGREE] %s | model=%.3f vs "
                        "ours=%.3f | skipping",
                        market.city, val_prob, real_prob,
                    )
                    self._log_shadow(
                        market, prob_data, model_result, trade_side,
                        kelly_size, kelly_size, "VALIDATOR_REJECT",
                        time.time() - t0,
                    )
                    self.total_skipped += 1
                    return None

        # 6. Execute paper trade
        outcome = "YES" if trade_side == "BUY_YES" else "NO"
        token_id = ""
        for t in market.tokens:
            if t.get("outcome", "").upper() == outcome:
                token_id = t.get("token_id", "")
                break

        trade_id = self.trader.open_trade(
            market_question=market.question,
            condition_id=market.condition_id,
            token_id=token_id,
            outcome=outcome,
            side=trade_side,
            entry_price=trade_price,
            size_usd=kelly_size,
            signal_source="weather",
            city=market.city,
            resolution_date=market.resolution_date,
        )

        if trade_id:
            self.total_traded += 1
            self.risk.record_trade_open(kelly_size)
            bot_action = "PAPER_BUY"
        else:
            bot_action = "EXECUTION_FAILED"

        # Shadow log
        latency = time.time() - t0
        self._log_shadow(
            market, prob_data, model_result, trade_side,
            kelly_size, kelly_size, bot_action, latency,
        )

        # Return signal for dashboard
        return {
            "trade_id": trade_id,
            "city": market.city,
            "question": market.question,
            "side": trade_side,
            "real_prob": real_prob,
            "poly_price": poly_price,
            "mispricing": mispricing,
            "size_usd": kelly_size,
            "action": bot_action,
        }

    # ── Shadow Logging ───────────────────────────────────────────

    def _log_shadow(
        self,
        market: WeatherMarket,
        prob_data: dict,
        model_result: Optional[dict],
        trade_side: str,
        position_size: float,
        kelly_size: float,
        bot_action: str,
        latency_s: float,
    ) -> None:
        """Log signal to shadow CSV."""
        self.shadow.log_weather_signal(
            city=market.city,
            market_question=market.question,
            market_slug=market.market_slug,
            condition_id=market.condition_id,
            token_id=(
                market.tokens[0].get("token_id", "")
                if market.tokens else ""
            ),
            outcome="YES" if "YES" in trade_side else "NO",
            poly_price=market.yes_price,
            forecast_probability=prob_data.get(
                "combined_probability", 0),
            model_result=model_result,
            trade_side=trade_side,
            position_size_usd=position_size,
            kelly_size=kelly_size,
            market_liquidity_usd=market.liquidity_usd,
            resolution_date=(
                market.resolution_date.isoformat()
                if market.resolution_date else ""
            ),
            hours_to_resolution=market.hours_to_resolution,
            ensemble_yes=prob_data.get("ensemble_yes", 0),
            ensemble_total=prob_data.get("ensemble_total", 0),
            historical_base_rate=prob_data.get(
                "historical_base_rate", 0),
            bot_action=bot_action,
            latency_ms=latency_s * 1000,
        )

    # ── Status ───────────────────────────────────────────────────

    def status(self) -> dict:
        return {
            "total_scanned": self.total_scanned,
            "total_mispriced": self.total_mispriced,
            "total_traded": self.total_traded,
            "total_skipped": self.total_skipped,
            "last_scan": (
                self.last_scan_time.isoformat()
                if self.last_scan_time else None
            ),
        }
