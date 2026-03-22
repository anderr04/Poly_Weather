"""
market_scanner.py - Polymarket weather market scanner.

Finds active weather-related markets on Polymarket using
DIRECT EVENT SLUG lookups. The Gamma API search is unreliable,
but event slugs follow predictable patterns:

    highest-temperature-in-{city}-on-{month}-{day}

This scanner generates slug candidates for target cities across
the next N days and fetches events directly.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import requests

import config
from src.polymarket_api import PolymarketAPI

logger = logging.getLogger(__name__)


@dataclass
class WeatherMarket:
    """A parsed weather market from Polymarket."""

    # Polymarket identifiers
    condition_id: str
    market_slug: str
    question: str
    description: str

    # Token info
    tokens: list[dict]       # [{"token_id": str, "outcome": str, "price": float}]

    # Parsed weather details
    city: str
    metric: str              # "temp_max", "temp_min", "precipitation"
    operator: str            # "above", "below"
    threshold: float         # e.g., 90.0
    threshold_unit: str      # "F" or "C"

    # Market meta
    resolution_date: Optional[datetime]
    volume_usd: float
    liquidity_usd: float

    # Event context
    event_slug: str = ""
    event_title: str = ""

    @property
    def yes_price(self) -> float:
        """Price of YES token."""
        for t in self.tokens:
            if t.get("outcome", "").upper() in ("YES", "Y"):
                return t.get("price", 0)
        return 0.0

    @property
    def no_price(self) -> float:
        """Price of NO token."""
        for t in self.tokens:
            if t.get("outcome", "").upper() in ("NO", "N"):
                return t.get("price", 0)
        return 0.0

    @property
    def hours_to_resolution(self) -> float:
        """Hours until resolution."""
        if not self.resolution_date:
            return 9999
        delta = self.resolution_date - datetime.now(timezone.utc)
        return max(0, delta.total_seconds() / 3600)


# -- Slug generation patterns --

# Month names for slug generation
_MONTH_NAMES = {
    1: "january", 2: "february", 3: "march", 4: "april",
    5: "may", 6: "june", 7: "july", 8: "august",
    9: "september", 10: "october", 11: "november", 12: "december",
}

# City name to slug format
_CITY_SLUGS = {
    "Madrid": "madrid",
    "Berlin": "berlin",
    "Sydney": "sydney",
    "Singapore": "singapore",
    "Mexico City": "mexico-city",
    "Warsaw": "warsaw",
    "Athens": "athens",
    "Lisbon": "lisbon",
    "Istanbul": "istanbul",
    "Buenos Aires": "buenos-aires",
    "Prague": "prague",
    "Budapest": "budapest",
    "Vienna": "vienna",
    "Dublin": "dublin",
    "Helsinki": "helsinki",
    "London": "london",
    "Paris": "paris",
    "Munich": "munich",
    "Milan": "milan",
    "Rome": "rome",
    "Tokyo": "tokyo",
    "New York": "new-york",
    "Los Angeles": "los-angeles",
    "Chicago": "chicago",
    "Miami": "miami",
    "Houston": "houston",
    "Denver": "denver",
    "Seattle": "seattle",
    "Toronto": "toronto",
    "Vancouver": "vancouver",
    "Seoul": "seoul",
    "Bangkok": "bangkok",
    "Mumbai": "mumbai",
    "Cairo": "cairo",
    "Johannesburg": "johannesburg",
    "Lagos": "lagos",
    "Stockholm": "stockholm",
    "Oslo": "oslo",
    "Copenhagen": "copenhagen",
    "Amsterdam": "amsterdam",
    "Brussels": "brussels",
    "Zurich": "zurich",
}

# Slug patterns for weather events (only confirmed working patterns)
# CRITICAL: 2026+ events require the year suffix (e.g., -2026)
_EVENT_SLUG_TEMPLATES = [
    "highest-temperature-in-{city}-on-{month}-{day}-{year}",
]


class WeatherMarketScanner:
    """
    Scans Polymarket for weather markets using direct event slug lookups.

    Strategy:
    1. Generate slug candidates for each city x next N days
    2. Fetch events via Gamma API slug lookup
    3. Parse sub-markets from each event
    4. Filter to only include markets with sufficient volume/liquidity
    """

    def __init__(self):
        self.api = PolymarketAPI()
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Poly_Weather/1.0",
            "Accept": "application/json",
        })
        self._found_slugs: set[str] = set()

    def scan(self, days_ahead: int = 4) -> list[WeatherMarket]:
        """
        Scan Polymarket for weather markets across target cities.

        Args:
            days_ahead: How many days ahead to scan for markets.

        Returns:
            List of parsed WeatherMarket objects.
        """
        all_markets: list[WeatherMarket] = []
        events_checked = 0
        events_found = 0

        now = datetime.now(timezone.utc)

        # Generate slug candidates
        slug_candidates = self._generate_slugs(now, days_ahead)
        logger.info(
            "[SCANNER] Generated %d slug candidates for %d cities x %d days",
            len(slug_candidates), len(config.ALL_WEATHER_CITIES), days_ahead,
        )

        for slug, city, target_date, metric in slug_candidates:
            events_checked += 1
            markets = self._fetch_event_markets(slug, city, target_date, metric)

            if markets:
                events_found += 1
                all_markets.extend(markets)
                logger.info(
                    "[FOUND] %s | %d sub-markets | city=%s",
                    slug, len(markets), city,
                )

            # Rate limit (fast — slug lookups are lightweight)
            time.sleep(0.1)

        logger.info(
            "[SCANNER] Checked %d slugs | Found %d events | %d total markets",
            events_checked, events_found, len(all_markets),
        )

        return all_markets

    def _generate_slugs(
        self,
        now: datetime,
        days_ahead: int,
    ) -> list[tuple[str, str, datetime, str]]:
        """
        Generate event slug candidates.

        Returns list of (slug, city, target_date, metric).
        """
        candidates = []

        for day_offset in range(0, days_ahead + 1):
            target = now + timedelta(days=day_offset)
            month_name = _MONTH_NAMES[target.month]
            day_num = target.day

            for city_name in config.ALL_WEATHER_CITIES:
                city_slug = _CITY_SLUGS.get(city_name, city_name.lower().replace(" ", "-"))

                for template in _EVENT_SLUG_TEMPLATES:
                    slug = template.format(
                        city=city_slug,
                        month=month_name,
                        day=day_num,
                        year=target.year,
                    )

                    # Determine metric from template
                    if "highest" in template or "high-temp" in template:
                        metric = "temp_max"
                    elif "lowest" in template or "low-temp" in template:
                        metric = "temp_min"
                    elif "rain" in template:
                        metric = "precipitation"
                    else:
                        metric = "temp_max"

                    candidates.append((slug, city_name, target, metric))

        return candidates

    def _fetch_event_markets(
        self,
        event_slug: str,
        city: str,
        target_date: datetime,
        metric: str,
    ) -> list[WeatherMarket]:
        """
        Fetch an event by slug and parse its sub-markets.
        """
        try:
            r = self.session.get(
                f"https://gamma-api.polymarket.com/events",
                params={"slug": event_slug},
                timeout=10,
            )

            if r.status_code != 200:
                return []

            data = r.json()
            if not data:
                return []

            # Response can be a list or a single event
            events = data if isinstance(data, list) else [data]

            markets = []
            for event in events:
                event_title = event.get("title", "")
                event_markets = event.get("markets", [])

                if not event_markets:
                    continue

                self._found_slugs.add(event_slug)

                for mkt in event_markets:
                    wm = self._parse_sub_market(
                        mkt, city, target_date, metric,
                        event_slug, event_title,
                    )
                    if wm:
                        markets.append(wm)

            return markets

        except Exception as e:
            logger.debug("Error fetching %s: %s", event_slug, e)
            return []

    def _parse_sub_market(
        self,
        mkt: dict,
        city: str,
        target_date: datetime,
        metric: str,
        event_slug: str,
        event_title: str,
    ) -> Optional[WeatherMarket]:
        """Parse a single sub-market from an event."""
        question = mkt.get("question", "") or mkt.get("groupItemTitle", "")
        condition_id = mkt.get("conditionId", "") or mkt.get("condition_id", "")

        if not question or not condition_id:
            return None

        # Extract tokens with prices
        tokens = self._extract_tokens(mkt)

        # Parse threshold from question
        threshold, unit, operator = self._parse_threshold(question, event_title)

        # Resolution date
        resolution = PolymarketAPI.parse_resolution_date(mkt)
        if not resolution:
            # Use target_date as fallback
            resolution = target_date.replace(hour=23, minute=59, tzinfo=timezone.utc)

        # Volume
        volume = PolymarketAPI.get_market_volume(mkt)

        # Estimated liquidity
        liquidity = volume * 0.3 if volume > 0 else 0

        slug = mkt.get("slug", "") or mkt.get("market_slug", "")

        return WeatherMarket(
            condition_id=condition_id,
            market_slug=slug,
            question=question,
            description=mkt.get("description", "")[:500],
            tokens=tokens,
            city=city,
            metric=metric,
            operator=operator,
            threshold=threshold,
            threshold_unit=unit,
            resolution_date=resolution,
            volume_usd=volume,
            liquidity_usd=liquidity,
            event_slug=event_slug,
            event_title=event_title,
        )

    def _extract_tokens(self, mkt: dict) -> list[dict]:
        """Extract token info (token_id, outcome, price) from a market dict."""
        tokens = []

        # Method 1: "tokens" field
        market_tokens = mkt.get("tokens", [])
        if isinstance(market_tokens, list):
            for t in market_tokens:
                if isinstance(t, dict):
                    tokens.append({
                        "token_id": t.get("token_id", ""),
                        "outcome": t.get("outcome", ""),
                        "price": float(t.get("price", 0)),
                    })

        # Method 2: clobTokenIds + outcomePrices
        if not tokens:
            clob_ids = mkt.get("clobTokenIds", "")
            outcome_prices = mkt.get("outcomePrices", "")
            outcomes = mkt.get("outcomes", "")

            if isinstance(clob_ids, str) and clob_ids:
                try:
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

    def _parse_threshold(
        self,
        question: str,
        event_title: str,
    ) -> tuple[float, str, str]:
        """
        Parse threshold, unit, and operator from market question.

        Examples:
            "Will the highest temperature in London be 53F or below on March 23?"
            -> (53.0, "F", "below")

            "Will the highest temperature in London be between 52-53F on March 23?"
            -> (52.5, "F", "between")
        """
        combined = question + " " + event_title

        # Pattern: "XX°F or below" / "XX°C or below"
        match = re.search(r"(\d+\.?\d*)\s*[°]?\s*(F|C)\s+or\s+(below|above|less|more)", combined, re.IGNORECASE)
        if match:
            value = float(match.group(1))
            unit = match.group(2).upper()
            direction = match.group(3).lower()
            operator = "below" if direction in ("below", "less") else "above"
            return value, unit, operator

        # Pattern: "between XX-YYF" -> midpoint
        match = re.search(r"between\s+(\d+)\s*[-–]\s*(\d+)\s*[°]?\s*(F|C)", combined, re.IGNORECASE)
        if match:
            low = float(match.group(1))
            high = float(match.group(2))
            unit = match.group(3).upper()
            midpoint = (low + high) / 2
            return midpoint, unit, "between"

        # Pattern: "be XXF or above" / "reach XXF"
        match = re.search(r"(?:be|reach|hit|exceed)\s+(\d+\.?\d*)\s*[°]?\s*(F|C)", combined, re.IGNORECASE)
        if match:
            value = float(match.group(1))
            unit = match.group(2).upper()
            return value, unit, "above"

        # Pattern: just a number + degree sign
        match = re.search(r"(\d+\.?\d*)\s*[°]\s*(F|C)", combined, re.IGNORECASE)
        if match:
            value = float(match.group(1))
            unit = match.group(2).upper()
            return value, unit, "above"

        # Pattern: just a number + C/F
        match = re.search(r"(\d+\.?\d*)\s*(F|C)\b", combined, re.IGNORECASE)
        if match:
            value = float(match.group(1))
            unit = match.group(2).upper()
            return value, unit, "above"

        # Fallback
        return 0.0, "F", "above"
