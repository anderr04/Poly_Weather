"""
probability_validator.py – IA probability validator using Ollama.

Validates trade signals (weather + whale) against a local LLM.
Runs synchronously (called per-signal) — the caller decides
whether to run it in a background thread.

Uses the exact JSON format:
    {"real_probability": float, "confidence": int,
     "explanation": "...", "key_sources": [...]}
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any, Optional

import requests

import config

logger = logging.getLogger(__name__)


# =====================================================================
#  Ollama Client
# =====================================================================

class OllamaClient:
    """Minimal Ollama HTTP client for structured JSON output."""

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "phi3:mini",
        timeout: float = 30.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    def is_available(self) -> bool:
        """Check if Ollama is running and the model is loaded."""
        try:
            r = requests.get(
                f"{self.base_url}/api/tags",
                timeout=5,
            )
            if r.status_code != 200:
                return False
            models = r.json().get("models", [])
            model_names = [m.get("name", "") for m in models]
            # Check if our model (or a variant) is present
            return any(
                self.model.split(":")[0] in name
                for name in model_names
            )
        except Exception:
            return False

    def generate(self, prompt: str) -> Optional[dict]:
        """
        Send prompt to Ollama, parse JSON response.
        Returns parsed dict or None on failure.
        """
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "options": {
                "temperature": 0.3,
                "num_predict": 400,
            },
        }

        try:
            r = requests.post(
                f"{self.base_url}/api/generate",
                json=payload,
                timeout=self.timeout,
            )
            if r.status_code != 200:
                logger.warning(
                    "Ollama returned %d: %s",
                    r.status_code, r.text[:200],
                )
                return None

            raw = r.json().get("response", "")
            return self._parse_json(raw)

        except requests.exceptions.ConnectionError:
            logger.warning("Ollama not reachable at %s", self.base_url)
            return None
        except requests.exceptions.Timeout:
            logger.warning("Ollama timeout after %.0fs", self.timeout)
            return None
        except Exception as e:
            logger.warning("Ollama error: %s", e)
            return None

    @staticmethod
    def _parse_json(raw: str) -> Optional[dict]:
        """Try to parse JSON from Ollama's response."""
        # Direct parse
        try:
            data = json.loads(raw)
            if "real_probability" in data:
                # Validate and clamp
                data["real_probability"] = max(0, min(1,
                    float(data.get("real_probability", 0))))
                data["confidence"] = max(0, min(100,
                    int(data.get("confidence", 0))))
                return data
        except (json.JSONDecodeError, ValueError):
            pass

        # Try to find JSON block in the response
        match = re.search(
            r"\{[^{}]*\"real_probability\"[^{}]*\}", raw)
        if match:
            try:
                data = json.loads(match.group())
                data["real_probability"] = max(0, min(1,
                    float(data.get("real_probability", 0))))
                data["confidence"] = max(0, min(100,
                    int(data.get("confidence", 0))))
                return data
            except (json.JSONDecodeError, ValueError):
                pass

        logger.debug("Could not parse Ollama response: %s", raw[:200])
        return None


# =====================================================================
#  Prompt Templates
# =====================================================================

_WEATHER_PROMPT = """You are a prediction market analyst specializing in WEATHER markets.

Given real weather data from meteorological models and historical records, estimate the TRUE probability of this weather event.

EVENT: {market_question}
OUTCOME: {outcome}
CURRENT POLYMARKET PRICE: {poly_price:.4f} (= market's implied probability)
RESOLUTION DATE: {resolution_date}
TODAY: {today}

WEATHER FORECAST DATA (from GFS ensemble model):
{forecast_data}

HISTORICAL DATA (past {historical_years} years for same date range):
{historical_data}

OUR STATISTICAL ESTIMATE: {our_probability:.4f}
(based on {ensemble_yes}/{ensemble_total} ensemble members + historical base rate {base_rate:.4f})

MISPRICING vs Polymarket: {mispricing:+.4f} ({mispricing_pct:+.1f}%)

INSTRUCTIONS:
1. Evaluate if our statistical estimate is reasonable given the data.
2. Consider the forecast data quality, ensemble agreement, and historical patterns.
3. Factor in any edge cases (unusual weather patterns, seasonal anomalies).
4. You can adjust our estimate up or down based on your analysis.

Respond with ONLY this JSON (no other text):
{{"real_probability": 0.XX, "confidence": XX, "explanation": "max 3 lines", "key_sources": ["source1", "source2"]}}"""


_WHALE_PROMPT = """You are a prediction market probability analyst.

A whale trader has made a significant bet. Evaluate the TRUE probability of this event based on available data.

EVENT: {market_question}
OUTCOME BEING BET ON: {outcome}
CURRENT POLYMARKET PRICE: {poly_price:.4f} (= market's implied probability)
WHALE BET: ${whale_usd:.2f} on {outcome} (conviction: {conviction:.2f}% of portfolio)
TODAY: {today}

REAL-WORLD CONTEXT:
{context_str}

INSTRUCTIONS:
1. Based on public data and your knowledge, estimate the true probability of {outcome}.
2. Consider whether the Polymarket price seems too high or too low.
3. A whale with high conviction has bet on {outcome} — factor this in but don't blindly follow.

Respond with ONLY this JSON (no other text):
{{"real_probability": 0.XX, "confidence": XX, "explanation": "max 3 lines", "key_sources": ["source1", "source2"]}}"""


# =====================================================================
#  Probability Validator
# =====================================================================

class ProbabilityValidator:
    """
    Validates trade signals against Ollama LLM.

    Can validate both weather and whale signals with
    specialised prompts for each.
    """

    def __init__(self):
        self.ollama = OllamaClient(
            base_url=config.VALIDATOR_OLLAMA_URL,
            model=config.VALIDATOR_MODEL,
            timeout=config.VALIDATOR_TIMEOUT_S,
        )
        self.enabled = config.VALIDATOR_ENABLED
        self.min_confidence = config.VALIDATOR_MIN_CONFIDENCE
        self.edge_threshold = config.VALIDATOR_EDGE_THRESHOLD

        # Stats
        self.total_validated = 0
        self.total_approved = 0
        self.total_rejected = 0
        self.total_errors = 0

    def is_available(self) -> bool:
        """Check if the validator is enabled and Ollama is running."""
        if not self.enabled:
            return False
        return self.ollama.is_available()

    # ── Weather Validation ───────────────────────────────────────

    def validate_weather(
        self,
        market_question: str,
        outcome: str,
        poly_price: float,
        resolution_date: str,
        forecast_data: str,
        historical_data: str,
        our_probability: float,
        ensemble_yes: int,
        ensemble_total: int,
        base_rate: float,
    ) -> Optional[dict]:
        """
        Validate a weather signal with Ollama.

        Returns:
            {"real_probability": float, "confidence": int,
             "explanation": str, "key_sources": list}
            or None if validation fails.
        """
        if not self.enabled:
            return None

        mispricing = our_probability - poly_price

        prompt = _WEATHER_PROMPT.format(
            market_question=market_question,
            outcome=outcome,
            poly_price=poly_price,
            resolution_date=resolution_date,
            today=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            forecast_data=forecast_data[:1500],
            historical_data=historical_data[:1000],
            our_probability=our_probability,
            ensemble_yes=ensemble_yes,
            ensemble_total=ensemble_total,
            base_rate=base_rate,
            mispricing=mispricing,
            mispricing_pct=mispricing * 100,
            historical_years=config.WEATHER_HISTORICAL_YEARS,
        )

        t0 = time.time()
        result = self.ollama.generate(prompt)
        latency = (time.time() - t0) * 1000

        self.total_validated += 1

        if result:
            prob = result.get("real_probability", 0)
            conf = result.get("confidence", 0)
            agreement = abs(prob - our_probability) < 0.1

            logger.info(
                "[AI WEATHER] %s | model=%.3f "
                "(our=%.3f, poly=%.3f) | conf=%d%% | "
                "%s | %.0fms",
                outcome,
                prob, our_probability, poly_price,
                conf,
                "AGREE" if agreement else "DISAGREE",
                latency,
            )

            # Pass/fail decision
            edge = abs(prob - poly_price)
            if conf >= self.min_confidence and edge >= self.edge_threshold:
                self.total_approved += 1
            else:
                self.total_rejected += 1

            result["_latency_ms"] = latency
            return result
        else:
            self.total_errors += 1
            logger.warning(
                "[AI WEATHER] Ollama error | %.0fms", latency)
            return None

    # ── Whale Validation ─────────────────────────────────────────

    def validate_whale(
        self,
        market_question: str,
        outcome: str,
        poly_price: float,
        whale_usd: float,
        conviction: float,
        context_str: str = "",
    ) -> Optional[dict]:
        """
        Validate a whale copy-trade signal with Ollama.

        Returns same format as validate_weather.
        """
        if not self.enabled:
            return None

        prompt = _WHALE_PROMPT.format(
            market_question=market_question,
            outcome=outcome,
            poly_price=poly_price,
            whale_usd=whale_usd,
            conviction=conviction * 100,
            today=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            context_str=context_str[:1500] if context_str else "No additional context available.",
        )

        t0 = time.time()
        result = self.ollama.generate(prompt)
        latency = (time.time() - t0) * 1000

        self.total_validated += 1

        if result:
            prob = result.get("real_probability", 0)
            conf = result.get("confidence", 0)

            logger.info(
                "[AI WHALE] %s | model=%.3f "
                "(poly=%.3f) | conf=%d%% | %.0fms",
                outcome, prob, poly_price, conf, latency,
            )

            edge = abs(prob - poly_price)
            if conf >= self.min_confidence and edge >= self.edge_threshold:
                self.total_approved += 1
            else:
                self.total_rejected += 1

            result["_latency_ms"] = latency
            return result
        else:
            self.total_errors += 1
            return None

    # ── Status ───────────────────────────────────────────────────

    def status(self) -> dict:
        return {
            "enabled": self.enabled,
            "model": self.ollama.model,
            "total_validated": self.total_validated,
            "approved": self.total_approved,
            "rejected": self.total_rejected,
            "errors": self.total_errors,
            "approval_rate": (
                round(self.total_approved / self.total_validated * 100, 1)
                if self.total_validated > 0 else 0
            ),
        }
