"""
shadow_logger.py – Unified CSV logger for ALL trade signals.

Logs weather and whale signals to shadow_trades.csv with full
metadata for post-hoc analysis.
"""

from __future__ import annotations

import csv
import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import config

logger = logging.getLogger(__name__)

_csv_lock = threading.Lock()


class ShadowLogger:
    """Thread-safe CSV logger for shadow trades."""

    def __init__(self, csv_path: Path | None = None):
        self.csv_path = csv_path or config.SHADOW_CSV
        self._ensure_csv()
        self.total_logged = 0

    def _ensure_csv(self) -> None:
        """Create CSV with headers if it doesn't exist."""
        if not self.csv_path.exists():
            self.csv_path.parent.mkdir(parents=True, exist_ok=True)
            with _csv_lock:
                with open(self.csv_path, "w", newline="", encoding="utf-8") as f:
                    writer = csv.DictWriter(
                        f, fieldnames=config.SHADOW_COLUMNS)
                    writer.writeheader()
            logger.info("[CSV] Created %s", self.csv_path)

    def log_signal(self, data: dict[str, Any]) -> None:
        """
        Log a trade signal to shadow CSV.

        Args:
            data: Dict with keys matching SHADOW_COLUMNS.
                  Missing keys are filled with empty strings.
        """
        row = {col: "" for col in config.SHADOW_COLUMNS}
        row["timestamp"] = datetime.now(timezone.utc).isoformat()

        # Fill known fields
        for key, val in data.items():
            if key in row:
                if isinstance(val, (list, dict)):
                    row[key] = json.dumps(val, default=str)
                elif isinstance(val, float):
                    row[key] = f"{val:.6f}"
                else:
                    row[key] = str(val)

        try:
            with _csv_lock:
                with open(self.csv_path, "a", newline="",
                          encoding="utf-8") as f:
                    writer = csv.DictWriter(
                        f, fieldnames=config.SHADOW_COLUMNS)
                    writer.writerow(row)
            self.total_logged += 1
        except Exception as e:
            logger.warning("Failed to write shadow CSV: %s", e)

    def log_weather_signal(
        self,
        city: str,
        market_question: str,
        market_slug: str,
        condition_id: str,
        token_id: str,
        outcome: str,
        poly_price: float,
        forecast_probability: float,
        model_result: dict | None,
        trade_side: str,
        position_size_usd: float,
        kelly_size: float,
        market_liquidity_usd: float,
        resolution_date: str,
        hours_to_resolution: float,
        ensemble_yes: int,
        ensemble_total: int,
        historical_base_rate: float,
        bot_action: str,
        latency_ms: float = 0,
    ) -> None:
        """Convenience method for weather signals."""
        self.log_signal({
            "signal_source": "weather",
            "city": city,
            "market_question": market_question[:200],
            "market_slug": market_slug,
            "condition_id": condition_id,
            "token_id": token_id,
            "outcome": outcome,
            "poly_price": poly_price,
            "forecast_probability": forecast_probability,
            "mispricing": forecast_probability - poly_price,
            "model_probability": (
                model_result.get("real_probability", 0)
                if model_result else 0
            ),
            "model_confidence": (
                model_result.get("confidence", 0)
                if model_result else 0
            ),
            "model_explanation": (
                str(model_result.get("explanation", ""))[:500]
                if model_result else ""
            ),
            "model_sources": (
                model_result.get("key_sources", [])
                if model_result else []
            ),
            "would_trade": bot_action in ("PAPER_BUY", "LIVE_BUY"),
            "trade_side": trade_side,
            "position_size_usd": position_size_usd,
            "kelly_size": kelly_size,
            "market_liquidity_usd": market_liquidity_usd,
            "resolution_date": resolution_date,
            "hours_to_resolution": hours_to_resolution,
            "ensemble_members_yes": ensemble_yes,
            "ensemble_total_members": ensemble_total,
            "historical_base_rate": historical_base_rate,
            "bot_action": bot_action,
            "model_name": config.VALIDATOR_MODEL if model_result else "",
            "latency_ms": latency_ms,
        })

    def log_whale_signal(
        self,
        whale_label: str,
        market_question: str,
        outcome: str,
        poly_price: float,
        whale_usd: float,
        conviction: float,
        model_result: dict | None,
        bot_action: str,
        position_size_usd: float = 0,
        latency_ms: float = 0,
    ) -> None:
        """Convenience method for whale copy-trade signals."""
        self.log_signal({
            "signal_source": "whale_copy",
            "city": whale_label,
            "market_question": market_question[:200],
            "outcome": outcome,
            "poly_price": poly_price,
            "forecast_probability": conviction,
            "mispricing": 0,
            "model_probability": (
                model_result.get("real_probability", 0)
                if model_result else 0
            ),
            "model_confidence": (
                model_result.get("confidence", 0)
                if model_result else 0
            ),
            "model_explanation": (
                str(model_result.get("explanation", ""))[:500]
                if model_result else ""
            ),
            "model_sources": (
                model_result.get("key_sources", [])
                if model_result else []
            ),
            "would_trade": bot_action in ("COPIED", "PAPER_BUY"),
            "trade_side": f"WHALE_{outcome}",
            "position_size_usd": position_size_usd,
            "bot_action": bot_action,
            "model_name": config.VALIDATOR_MODEL if model_result else "",
            "latency_ms": latency_ms,
        })
