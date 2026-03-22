"""
risk_manager.py – Centralised risk management & safeguards.

All trades (weather or whale) pass through these checks before
execution. Hard limits that can NEVER be overridden by strategy logic.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import Optional

import config

logger = logging.getLogger(__name__)


class RiskManager:
    """
    Enforces risk limits on every trade signal.

    Safeguards:
        - Max 3% capital per trade
        - Min $30K market liquidity
        - Min 24h to resolution
        - Max daily loss limit
        - Max total exposure
        - Max concurrent positions
    """

    def __init__(self, capital: float):
        self.capital = capital
        self.daily_pnl: float = 0.0
        self.open_positions: int = 0
        self.total_exposure: float = 0.0
        self._paused = False
        self._pause_reason = ""

    # ── Main Check ───────────────────────────────────────────────

    def check_trade(
        self,
        trade_size_usd: float,
        market_liquidity_usd: float,
        resolution_date: Optional[datetime],
        source: str = "weather",
    ) -> tuple[bool, str]:
        """
        Run ALL risk checks on a proposed trade.

        Returns:
            (passed: bool, reason: str)
            If passed=False, reason explains why.
        """
        # Kill switch
        if self._paused:
            return False, f"PAUSED: {self._pause_reason}"

        # 1. Max capital per trade
        max_size = self.capital * config.MAX_CAPITAL_PER_TRADE_PCT
        if trade_size_usd > max_size:
            return False, (
                f"Size ${trade_size_usd:.2f} exceeds "
                f"{config.MAX_CAPITAL_PER_TRADE_PCT*100:.0f}% cap "
                f"(${max_size:.2f})"
            )

        # 2. Minimum liquidity
        if market_liquidity_usd < config.MIN_LIQUIDITY_USD:
            return False, (
                f"Liquidity ${market_liquidity_usd:,.0f} < "
                f"min ${config.MIN_LIQUIDITY_USD:,.0f}"
            )

        # 3. Minimum time to resolution
        if resolution_date:
            now = datetime.now(timezone.utc)
            hours_left = (resolution_date - now).total_seconds() / 3600
            if hours_left < config.MIN_RESOLUTION_HOURS:
                return False, (
                    f"Resolution in {hours_left:.1f}h < "
                    f"min {config.MIN_RESOLUTION_HOURS}h"
                )

        # 4. Daily loss limit
        if self.daily_pnl < 0:
            max_daily_loss = -self.capital * config.MAX_DAILY_LOSS_PCT
            if self.daily_pnl <= max_daily_loss:
                self._pause("Daily loss limit hit")
                return False, (
                    f"Daily loss ${self.daily_pnl:.2f} exceeds "
                    f"{config.MAX_DAILY_LOSS_PCT*100:.0f}% limit"
                )

        # 5. Max total exposure
        new_exposure = self.total_exposure + trade_size_usd
        max_exposure = self.capital * config.MAX_TOTAL_EXPOSURE_PCT
        if new_exposure > max_exposure:
            return False, (
                f"Total exposure ${new_exposure:.2f} would exceed "
                f"{config.MAX_TOTAL_EXPOSURE_PCT*100:.0f}% cap "
                f"(${max_exposure:.2f})"
            )

        # 6. Max open positions
        if self.open_positions >= config.MAX_OPEN_POSITIONS:
            return False, (
                f"Open positions {self.open_positions} >= "
                f"max {config.MAX_OPEN_POSITIONS}"
            )

        return True, "OK"

    # ── Sizing ───────────────────────────────────────────────────

    def calculate_kelly_size(
        self,
        probability: float,
        poly_price: float,
    ) -> float:
        """
        Calculate half-Kelly optimal position size.

        Args:
            probability: Our estimated true probability (0-1)
            poly_price: Current Polymarket price (0-1)

        Returns:
            Position size in USD (capped at max per trade).
        """
        if probability <= 0 or probability >= 1:
            return 0.0
        if poly_price <= 0 or poly_price >= 1:
            return 0.0

        # Kelly formula: f* = (bp - q) / b
        # where b = odds = (1/price - 1), p = prob, q = 1-p
        b = (1.0 / poly_price) - 1.0
        if b <= 0:
            return 0.0

        p = probability
        q = 1.0 - p
        kelly_fraction = (b * p - q) / b

        if kelly_fraction <= 0:
            return 0.0  # No edge

        # Half-Kelly for safety
        half_kelly = kelly_fraction * config.KELLY_FRACTION
        size_usd = self.capital * half_kelly

        # Cap at max per trade
        max_size = self.capital * config.MAX_CAPITAL_PER_TRADE_PCT
        size_usd = min(size_usd, max_size)

        # Floor at min trade
        if size_usd < 0.5:
            return 0.0

        return round(size_usd, 2)

    # ── State Management ─────────────────────────────────────────

    def update_capital(self, new_capital: float) -> None:
        """Update current capital (e.g., after PnL)."""
        self.capital = new_capital

    def record_trade_open(self, size_usd: float) -> None:
        """Record a new position opened."""
        self.open_positions += 1
        self.total_exposure += size_usd

    def record_trade_close(self, size_usd: float, pnl: float) -> None:
        """Record a position closed."""
        self.open_positions = max(0, self.open_positions - 1)
        self.total_exposure = max(0, self.total_exposure - size_usd)
        self.daily_pnl += pnl
        self.capital += pnl

    def reset_daily(self) -> None:
        """Reset daily counters (call at midnight)."""
        self.daily_pnl = 0.0
        if self._paused and "Daily" in self._pause_reason:
            self._paused = False
            self._pause_reason = ""
            logger.info("[RISK] daily pause lifted")

    def _pause(self, reason: str) -> None:
        """Activate kill switch."""
        self._paused = True
        self._pause_reason = reason
        logger.warning("[RISK] PAUSED - %s", reason)

    def resume(self) -> None:
        """Manual resume (admin override)."""
        self._paused = False
        self._pause_reason = ""
        logger.info("[RISK] manually resumed")

    @property
    def is_paused(self) -> bool:
        return self._paused

    def status(self) -> dict:
        """Current risk status for dashboard/logging."""
        return {
            "capital": round(self.capital, 2),
            "daily_pnl": round(self.daily_pnl, 2),
            "open_positions": self.open_positions,
            "total_exposure": round(self.total_exposure, 2),
            "exposure_pct": round(
                self.total_exposure / self.capital * 100, 1
            ) if self.capital > 0 else 0,
            "paused": self._paused,
            "pause_reason": self._pause_reason,
        }
