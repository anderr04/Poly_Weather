"""
polymarket_api.py – Polymarket API client (Gamma + CLOB).

Provides methods to search markets, get market details, prices,
orderbook depth, and liquidity. Uses only public endpoints
(no authentication needed for reads).
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Optional

import requests

import config

logger = logging.getLogger(__name__)

# Rate limiting
_last_request_time = 0.0
_MIN_REQUEST_INTERVAL = 0.25  # 250ms between requests


def _rate_limit():
    """Simple rate limiter to avoid hammering the API."""
    global _last_request_time
    elapsed = time.time() - _last_request_time
    if elapsed < _MIN_REQUEST_INTERVAL:
        time.sleep(_MIN_REQUEST_INTERVAL - elapsed)
    _last_request_time = time.time()


class PolymarketAPI:
    """Client for Polymarket's public APIs."""

    def __init__(self):
        self.gamma_url = config.POLYMARKET_GAMMA_URL
        self.clob_url = config.POLYMARKET_CLOB_URL
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Poly_Weather/1.0",
            "Accept": "application/json",
        })

    # ── Market Search (Gamma API) ────────────────────────────────

    def search_markets(
        self,
        query: str = "",
        tag: str = "",
        closed: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """
        Search for markets on Polymarket via the Gamma API.

        Args:
            query: Search string (matched against title/description)
            tag: Filter by tag (e.g., "weather", "politics")
            closed: Include closed/resolved markets
            limit: Max results
            offset: Pagination offset

        Returns:
            List of market dicts with full details.
        """
        _rate_limit()
        params: dict[str, Any] = {
            "limit": limit,
            "offset": offset,
            "closed": str(closed).lower(),
            "order": "volume24hr",
            "ascending": "false",
        }
        if query:
            params["_q"] = query
        if tag:
            params["tag"] = tag

        try:
            r = self.session.get(
                f"{self.gamma_url}/markets",
                params=params,
                timeout=10,
            )
            if r.status_code != 200:
                logger.warning("Gamma API %d: %s", r.status_code, r.text[:200])
                return []
            return r.json() if isinstance(r.json(), list) else []
        except Exception as e:
            logger.error("Gamma API error: %s", e)
            return []

    def get_events(
        self,
        query: str = "",
        tag: str = "",
        closed: bool = False,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Search events (groups of related markets)."""
        _rate_limit()
        params: dict[str, Any] = {
            "limit": limit,
            "closed": str(closed).lower(),
        }
        if query:
            params["_q"] = query
        if tag:
            params["tag"] = tag

        try:
            r = self.session.get(
                f"{self.gamma_url}/events",
                params=params,
                timeout=10,
            )
            if r.status_code != 200:
                return []
            return r.json() if isinstance(r.json(), list) else []
        except Exception as e:
            logger.error("Gamma events error: %s", e)
            return []

    # ── Market Details ───────────────────────────────────────────

    def get_market(self, condition_id: str) -> Optional[dict[str, Any]]:
        """Get detailed market info by condition ID."""
        _rate_limit()
        try:
            r = self.session.get(
                f"{self.gamma_url}/markets/{condition_id}",
                timeout=10,
            )
            if r.status_code != 200:
                return None
            return r.json()
        except Exception as e:
            logger.error("Market detail error: %s", e)
            return None

    # ── Token Prices (CLOB API) ──────────────────────────────────

    def get_token_price(self, token_id: str) -> Optional[float]:
        """Get the current mid-price for a token from the CLOB."""
        _rate_limit()
        try:
            r = self.session.get(
                f"{self.clob_url}/price",
                params={"token_id": token_id},
                timeout=10,
            )
            if r.status_code != 200:
                return None
            data = r.json()
            return float(data.get("price", 0))
        except Exception as e:
            logger.debug("Price fetch error: %s", e)
            return None

    def get_midpoint(self, token_id: str) -> Optional[float]:
        """Get the orderbook midpoint for a token."""
        _rate_limit()
        try:
            r = self.session.get(
                f"{self.clob_url}/midpoint",
                params={"token_id": token_id},
                timeout=10,
            )
            if r.status_code != 200:
                return None
            data = r.json()
            return float(data.get("mid", 0))
        except Exception as e:
            logger.debug("Midpoint fetch error: %s", e)
            return None

    # ── Orderbook ────────────────────────────────────────────────

    def get_orderbook(self, token_id: str) -> Optional[dict]:
        """
        Get the full orderbook for a token.
        Returns: {"bids": [[price, size], ...], "asks": [[price, size], ...]}
        """
        _rate_limit()
        try:
            r = self.session.get(
                f"{self.clob_url}/book",
                params={"token_id": token_id},
                timeout=10,
            )
            if r.status_code != 200:
                return None
            return r.json()
        except Exception as e:
            logger.debug("Orderbook fetch error: %s", e)
            return None

    def estimate_liquidity(self, token_id: str) -> float:
        """
        Estimate total liquidity (USD) available in the orderbook.
        Sum of (price × size) for all bid and ask levels.
        """
        book = self.get_orderbook(token_id)
        if not book:
            return 0.0

        total = 0.0
        for side in ("bids", "asks"):
            levels = book.get(side, [])
            for level in levels:
                try:
                    price = float(level.get("price", 0))
                    size = float(level.get("size", 0))
                    total += price * size
                except (ValueError, TypeError):
                    continue
        return total

    # ── Helpers ──────────────────────────────────────────────────

    @staticmethod
    def parse_resolution_date(market: dict) -> Optional[datetime]:
        """Parse the resolution/end date from a market dict."""
        for field in ("end_date_iso", "endDate", "end_date",
                      "resolution_date", "close_time"):
            val = market.get(field)
            if val:
                try:
                    if isinstance(val, str):
                        # Handle various ISO formats
                        val = val.replace("Z", "+00:00")
                        return datetime.fromisoformat(val)
                except (ValueError, TypeError):
                    continue
        return None

    @staticmethod
    def extract_tokens(market: dict) -> list[dict]:
        """
        Extract token info from a market dict.
        Returns list of {"token_id": str, "outcome": str, "price": float}.
        """
        tokens = []

        # Gamma API format: "tokens" field or "clobTokenIds"
        market_tokens = market.get("tokens", [])
        if isinstance(market_tokens, list):
            for t in market_tokens:
                if isinstance(t, dict):
                    tokens.append({
                        "token_id": t.get("token_id", ""),
                        "outcome": t.get("outcome", ""),
                        "price": float(t.get("price", 0)),
                    })

        # Alternative: clobTokenIds + outcomePrices
        if not tokens:
            clob_ids = market.get("clobTokenIds", "")
            outcome_prices = market.get("outcomePrices", "")
            outcomes = market.get("outcomes", "")

            if isinstance(clob_ids, str) and clob_ids:
                try:
                    import json
                    ids = json.loads(clob_ids) if clob_ids.startswith("[") else [clob_ids]
                    prices = json.loads(outcome_prices) if outcome_prices else []
                    outcome_labels = json.loads(outcomes) if outcomes else ["Yes", "No"]

                    for i, tid in enumerate(ids):
                        tokens.append({
                            "token_id": tid,
                            "outcome": outcome_labels[i] if i < len(outcome_labels) else f"Outcome_{i}",
                            "price": float(prices[i]) if i < len(prices) else 0.0,
                        })
                except (json.JSONDecodeError, ValueError, IndexError):
                    pass

        return tokens

    @staticmethod
    def get_market_volume(market: dict) -> float:
        """Get the total volume (USD) traded on a market."""
        for field in ("volume", "volume24hr", "volumeNum"):
            val = market.get(field)
            if val is not None:
                try:
                    return float(val)
                except (ValueError, TypeError):
                    continue
        return 0.0
