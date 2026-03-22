"""
paper_trader.py – Paper trading simulator.

Simulates order execution with realistic slippage and fee models.
Tracks open positions, PnL, and trade history in-memory and to CSV.
"""

from __future__ import annotations

import csv
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import config

logger = logging.getLogger(__name__)


@dataclass
class Position:
    """An open paper position."""
    trade_id: str
    market_question: str
    condition_id: str
    token_id: str
    outcome: str
    side: str              # BUY_YES / BUY_NO
    entry_price: float
    size_usd: float
    shares: float          # shares = size_usd / entry_price
    entry_time: datetime
    signal_source: str     # "weather" / "whale_copy"
    city: str              # city (weather) or whale label
    resolution_date: Optional[datetime] = None


@dataclass
class ClosedTrade:
    """A completed trade with PnL."""
    trade_id: str
    market_question: str
    outcome: str
    side: str
    entry_price: float
    exit_price: float
    size_usd: float
    shares: float
    pnl: float
    pnl_pct: float
    entry_time: datetime
    exit_time: datetime
    signal_source: str
    exit_reason: str       # "resolved_win" / "resolved_loss" / "manual"


TRADES_COLUMNS = [
    "trade_id", "market_question", "outcome", "side",
    "entry_price", "exit_price", "size_usd", "shares",
    "pnl", "pnl_pct", "entry_time", "exit_time",
    "signal_source", "city", "exit_reason",
]


class PaperTrader:
    """
    Paper trading engine with position tracking and PnL calculation.

    Usage:
        trader = PaperTrader(capital=100.0)
        trader.open_trade(...)
        trader.close_trade(trade_id, exit_price, reason)
    """

    def __init__(self, capital: float = 100.0):
        self.initial_capital = capital
        self.capital = capital
        self.positions: dict[str, Position] = {}  # trade_id → Position
        self.closed_trades: list[ClosedTrade] = []
        self._trade_counter = 0
        self._lock = threading.Lock()
        self._init_csv()

    def _init_csv(self) -> None:
        """Ensure trades CSV has headers."""
        csv_path = config.TRADES_CSV
        if not csv_path.exists():
            csv_path.parent.mkdir(parents=True, exist_ok=True)
            with open(csv_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=TRADES_COLUMNS)
                writer.writeheader()

    # ── Trade Execution ──────────────────────────────────────────

    def open_trade(
        self,
        market_question: str,
        condition_id: str,
        token_id: str,
        outcome: str,
        side: str,
        entry_price: float,
        size_usd: float,
        signal_source: str = "weather",
        city: str = "",
        resolution_date: Optional[datetime] = None,
    ) -> Optional[str]:
        """
        Open a new paper position.

        Returns:
            trade_id if successful, None if insufficient capital.
        """
        with self._lock:
            # Apply slippage
            slippage = entry_price * config.SLIPPAGE_PCT
            effective_price = entry_price + slippage  # worse fill

            if effective_price >= 1.0:
                logger.warning("Price after slippage >= $1.00, skipping")
                return None

            if size_usd > self.capital:
                logger.warning(
                    "Insufficient capital: $%.2f needed, $%.2f available",
                    size_usd, self.capital,
                )
                return None

            self._trade_counter += 1
            trade_id = f"{signal_source}_{self._trade_counter:04d}"

            shares = size_usd / effective_price

            pos = Position(
                trade_id=trade_id,
                market_question=market_question,
                condition_id=condition_id,
                token_id=token_id,
                outcome=outcome,
                side=side,
                entry_price=effective_price,
                size_usd=size_usd,
                shares=shares,
                entry_time=datetime.now(timezone.utc),
                signal_source=signal_source,
                city=city,
                resolution_date=resolution_date,
            )

            self.positions[trade_id] = pos
            self.capital -= size_usd

            logger.info(
                "[PAPER BUY] %s | %s %s @ %.4f | $%.2f | "
                "%.1f shares | capital=$%.2f",
                trade_id, side, outcome, effective_price,
                size_usd, shares, self.capital,
            )

            return trade_id

    def close_trade(
        self,
        trade_id: str,
        exit_price: float,
        reason: str = "manual",
    ) -> Optional[ClosedTrade]:
        """
        Close a paper position and calculate PnL.

        For prediction markets:
        - If resolved YES and we bought YES: exit_price = 1.0 (WIN)
        - If resolved NO and we bought YES: exit_price = 0.0 (LOSS)
        """
        with self._lock:
            pos = self.positions.pop(trade_id, None)
            if not pos:
                logger.warning("Trade %s not found", trade_id)
                return None

            # PnL = (exit_price - entry_price) × shares
            pnl = (exit_price - pos.entry_price) * pos.shares
            pnl_pct = pnl / pos.size_usd if pos.size_usd > 0 else 0

            closed = ClosedTrade(
                trade_id=trade_id,
                market_question=pos.market_question,
                outcome=pos.outcome,
                side=pos.side,
                entry_price=pos.entry_price,
                exit_price=exit_price,
                size_usd=pos.size_usd,
                shares=pos.shares,
                pnl=pnl,
                pnl_pct=pnl_pct,
                entry_time=pos.entry_time,
                exit_time=datetime.now(timezone.utc),
                signal_source=pos.signal_source,
                exit_reason=reason,
            )

            self.closed_trades.append(closed)
            self.capital += pos.size_usd + pnl  # return capital + PnL

            # Log to CSV
            self._write_trade_csv(closed, pos.city)

            tag = "WIN" if pnl >= 0 else "LOSS"
            logger.info(
                "[PAPER CLOSE][%s] %s | %s @ %.4f->%.4f | "
                "PnL: $%+.2f (%+.1f%%) | %s | capital=$%.2f",
                tag, trade_id, pos.outcome,
                pos.entry_price, exit_price,
                pnl, pnl_pct * 100, reason, self.capital,
            )

            return closed

    def _write_trade_csv(self, trade: ClosedTrade, city: str) -> None:
        """Append closed trade to CSV."""
        try:
            with open(config.TRADES_CSV, "a", newline="",
                      encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=TRADES_COLUMNS)
                writer.writerow({
                    "trade_id": trade.trade_id,
                    "market_question": trade.market_question[:200],
                    "outcome": trade.outcome,
                    "side": trade.side,
                    "entry_price": f"{trade.entry_price:.6f}",
                    "exit_price": f"{trade.exit_price:.6f}",
                    "size_usd": f"{trade.size_usd:.2f}",
                    "shares": f"{trade.shares:.4f}",
                    "pnl": f"{trade.pnl:.4f}",
                    "pnl_pct": f"{trade.pnl_pct:.4f}",
                    "entry_time": trade.entry_time.isoformat(),
                    "exit_time": trade.exit_time.isoformat(),
                    "signal_source": trade.signal_source,
                    "city": city,
                    "exit_reason": trade.exit_reason,
                })
        except Exception as e:
            logger.warning("Failed to write trades CSV: %s", e)

    # ── Status ───────────────────────────────────────────────────

    @property
    def total_pnl(self) -> float:
        """Total realised PnL across all closed trades."""
        return sum(t.pnl for t in self.closed_trades)

    @property
    def unrealized_pnl(self) -> float:
        """
        Rough unrealized PnL (would need current prices for accuracy).
        Returns 0 as we don't have live prices here.
        """
        return 0.0

    @property
    def win_rate(self) -> float:
        """Win rate of closed trades."""
        if not self.closed_trades:
            return 0.0
        wins = sum(1 for t in self.closed_trades if t.pnl > 0)
        return wins / len(self.closed_trades)

    def status(self) -> dict:
        """Current paper trading status."""
        return {
            "initial_capital": self.initial_capital,
            "current_capital": round(self.capital, 2),
            "total_pnl": round(self.total_pnl, 4),
            "roi_pct": round(
                self.total_pnl / self.initial_capital * 100, 2
            ) if self.initial_capital > 0 else 0,
            "open_positions": len(self.positions),
            "closed_trades": len(self.closed_trades),
            "win_rate": round(self.win_rate * 100, 1),
        }
