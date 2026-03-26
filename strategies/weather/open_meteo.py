"""
open_meteo.py – Open-Meteo API client.

Provides weather forecast (GFS ensemble) and historical data for
probability calculation. All APIs are FREE, no key required.

APIs used:
    - Forecast: https://api.open-meteo.com/v1/forecast (GFS ensemble)
    - Historical: https://archive-api.open-meteo.com/v1/archive
    - Ensemble: https://ensemble-api.open-meteo.com/v1/ensemble
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import requests

import config

logger = logging.getLogger(__name__)

_TIMEOUT = 10  # seconds per API call


class OpenMeteoClient:
    """Client for Open-Meteo weather APIs."""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Poly_Weather/1.0",
        })
        self._cache_ensemble = {}
        self._cache_historical = {}

    def clear_cache(self):
        """Clear cache at the start of a new polling cycle."""
        self._cache_ensemble.clear()
        self._cache_historical.clear()

    # ── Ensemble Forecast ────────────────────────────────────────

    def get_ensemble_forecast(
        self,
        city: str,
        days: int = 16,
    ) -> Optional[dict[str, Any]]:
        """
        Get GFS ensemble forecast for a city.

        Returns dict with daily forecast data including
        ensemble spread for probability calculation.

        Args:
            city: City name (must be in config.ALL_WEATHER_CITIES)
            days: Number of forecast days (max 16)
        """
        coords = config.ALL_WEATHER_CITIES.get(city)
        if not coords:
            logger.warning("City '%s' not in config", city)
            return None

        cache_key = f"{city}_{days}"
        if cache_key in self._cache_ensemble:
            return self._cache_ensemble[cache_key]

        lat, lon, tz = coords

        try:
            # Use ensemble API for probability distributions
            r = self.session.get(
                "https://ensemble-api.open-meteo.com/v1/ensemble",
                params={
                    "latitude": lat,
                    "longitude": lon,
                    "daily": (
                        "temperature_2m_max,temperature_2m_min,"
                        "precipitation_sum,wind_speed_10m_max"
                    ),
                    "timezone": tz,
                    "forecast_days": min(days, 16),
                    "models": "gfs_seamless",
                },
                timeout=_TIMEOUT,
            )

            if r.status_code != 200:
                logger.warning(
                    "Ensemble API %d for %s: %s",
                    r.status_code, city, r.text[:200],
                )
                # Fallback to standard forecast
                return self._get_standard_forecast(lat, lon, tz, days)

            data = r.json()
            daily = data.get("daily", {})

            result = {
                "source": "open-meteo-ensemble",
                "model": "gfs_seamless",
                "city": city,
                "latitude": lat,
                "longitude": lon,
                "timezone": tz,
                "dates": daily.get("time", []),
                "temp_max_c": daily.get("temperature_2m_max", []),
                "temp_min_c": daily.get("temperature_2m_min", []),
                "precipitation_mm": daily.get("precipitation_sum", []),
                "wind_max_kmh": daily.get("wind_speed_10m_max", []),
            }
            self._cache_ensemble[cache_key] = result
            return result

        except Exception as e:
            logger.error("Ensemble forecast error for %s: %s", city, e)
            return self._get_standard_forecast(lat, lon, tz, days)

    def _get_standard_forecast(
        self, lat: float, lon: float, tz: str, days: int,
    ) -> Optional[dict]:
        """Fallback: standard forecast API (single deterministic run)."""
        try:
            r = self.session.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": lat,
                    "longitude": lon,
                    "daily": (
                        "temperature_2m_max,temperature_2m_min,"
                        "precipitation_sum,wind_speed_10m_max,"
                        "weathercode"
                    ),
                    "timezone": tz,
                    "forecast_days": min(days, 16),
                },
                timeout=_TIMEOUT,
            )
            if r.status_code != 200:
                return None

            data = r.json()
            daily = data.get("daily", {})
            result = {
                "source": "open-meteo-standard",
                "model": "best_match",
                "dates": daily.get("time", []),
                "temp_max_c": daily.get("temperature_2m_max", []),
                "temp_min_c": daily.get("temperature_2m_min", []),
                "precipitation_mm": daily.get("precipitation_sum", []),
                "wind_max_kmh": daily.get("wind_speed_10m_max", []),
                "weather_codes": daily.get("weathercode", []),
            }
            self._cache_ensemble[f"{lat}_{lon}_{days}"] = result
            return result
        except Exception as e:
            logger.error("Standard forecast error: %s", e)
            return None

    # ── Historical Data ──────────────────────────────────────────

    def get_historical(
        self,
        city: str,
        target_date: datetime,
        years_back: int = 5,
    ) -> Optional[dict[str, Any]]:
        """
        Get historical weather data for the same date range
        across multiple past years.

        Used to calculate base rates:
        "In the past N years on this date, how often was temp > X?"

        Args:
            city: City name
            target_date: The resolution date of the market
            years_back: How many years of history to fetch
        """
        coords = config.ALL_WEATHER_CITIES.get(city)
        if not coords:
            return None

        target_str = target_date.strftime('%Y-%m-%d')
        cache_key = f"{city}_{target_str}_{years_back}"
        if cache_key in self._cache_historical:
            return self._cache_historical[cache_key]

        lat, lon, tz = coords

        # Fetch historical data for each of the past N years
        # For the same month-day ± 3 days window
        all_temp_max = []
        all_temp_min = []
        all_precip = []
        yearly_data = []

        for y in range(1, years_back + 1):
            try:
                hist_date = target_date.replace(year=target_date.year - y)
                start = (hist_date - timedelta(days=3)).strftime("%Y-%m-%d")
                end = (hist_date + timedelta(days=3)).strftime("%Y-%m-%d")

                r = self.session.get(
                    "https://archive-api.open-meteo.com/v1/archive",
                    params={
                        "latitude": lat,
                        "longitude": lon,
                        "daily": (
                            "temperature_2m_max,temperature_2m_min,"
                            "precipitation_sum,wind_speed_10m_max"
                        ),
                        "timezone": tz,
                        "start_date": start,
                        "end_date": end,
                    },
                    timeout=_TIMEOUT,
                )

                if r.status_code == 200:
                    data = r.json().get("daily", {})
                    tmaxes = [
                        v for v in data.get("temperature_2m_max", [])
                        if v is not None
                    ]
                    tmins = [
                        v for v in data.get("temperature_2m_min", [])
                        if v is not None
                    ]
                    precips = [
                        v for v in data.get("precipitation_sum", [])
                        if v is not None
                    ]

                    all_temp_max.extend(tmaxes)
                    all_temp_min.extend(tmins)
                    all_precip.extend(precips)

                    yearly_data.append({
                        "year": target_date.year - y,
                        "avg_temp_max": (
                            sum(tmaxes) / len(tmaxes)
                            if tmaxes else None
                        ),
                        "avg_temp_min": (
                            sum(tmins) / len(tmins)
                            if tmins else None
                        ),
                        "total_precip_mm": sum(precips) if precips else None,
                    })

            except Exception as e:
                logger.debug(
                    "Historical fetch error year-%d for %s: %s", y, city, e)
                continue

        if not all_temp_max:
            return None

        result = {
            "source": "open-meteo-archive",
            "city": city,
            "target_date": target_date.strftime("%Y-%m-%d"),
            "years_analysed": len(yearly_data),
            "yearly_data": yearly_data,
            "all_temp_max_c": sorted(all_temp_max),
            "all_temp_min_c": sorted(all_temp_min),
            "all_precip_mm": sorted(all_precip),
            "stats": {
                "temp_max_mean": sum(all_temp_max) / len(all_temp_max),
                "temp_max_median": _median(all_temp_max),
                "temp_max_min": min(all_temp_max),
                "temp_max_max": max(all_temp_max),
                "temp_max_p10": _percentile(all_temp_max, 10),
                "temp_max_p25": _percentile(all_temp_max, 25),
                "temp_max_p75": _percentile(all_temp_max, 75),
                "temp_max_p90": _percentile(all_temp_max, 90),
                "precip_mean": (
                    sum(all_precip) / len(all_precip)
                    if all_precip else 0
                ),
                "precip_days_above_1mm": (
                    sum(1 for p in all_precip if p > 1.0)
                ),
                "total_observations": len(all_temp_max),
            },
        }
        self._cache_historical[cache_key] = result
        return result

    # ── Probability Calculation ──────────────────────────────────

    def calculate_probability(
        self,
        city: str,
        metric: str,
        operator: str,
        threshold: float,
        target_date: datetime,
        threshold_unit: str = "C",
    ) -> dict[str, Any]:
        """
        Calculate real probability from ensemble forecast + historical.

        Args:
            city: City name
            metric: "temp_max", "temp_min", "precipitation"
            operator: "above" or "below"
            threshold: Numeric threshold (e.g., 30.0)
            target_date: Resolution date
            threshold_unit: "C" or "F" (converts internally)

        Returns:
            Dict with ensemble_probability, historical_base_rate,
            combined_probability, and raw data.
        """
        # Convert Fahrenheit to Celsius if needed
        threshold_c = threshold
        if threshold_unit.upper() == "F" and metric.startswith("temp"):
            threshold_c = (threshold - 32) * 5 / 9

        # 1. Ensemble forecast
        forecast = self.get_ensemble_forecast(city)
        ensemble_prob = 0.0
        ensemble_yes = 0
        ensemble_total = 0
        forecast_str = "No forecast available"

        if forecast:
            dates = forecast.get("dates", [])
            target_str = target_date.strftime("%Y-%m-%d")

            # Find the target date in forecast
            metric_key = {
                "temp_max": "temp_max_c",
                "temp_min": "temp_min_c",
                "precipitation": "precipitation_mm",
            }.get(metric, "temp_max_c")

            values = forecast.get(metric_key, [])

            # If ensemble data has multiple members (nested lists)
            if values and isinstance(values[0], list):
                # Multi-member ensemble
                for i, d in enumerate(dates):
                    if d == target_str and i < len(values):
                        members = [v for v in values[i] if v is not None]
                        ensemble_total = len(members)
                        if operator == "above":
                            ensemble_yes = sum(
                                1 for v in members if v > threshold_c)
                        else:
                            ensemble_yes = sum(
                                1 for v in members if v < threshold_c)
                        ensemble_prob = (
                            ensemble_yes / ensemble_total
                            if ensemble_total > 0 else 0
                        )
                        break
            else:
                # Single deterministic forecast — use ±2°C window
                for i, d in enumerate(dates):
                    if d == target_str and i < len(values):
                        val = values[i]
                        if val is not None:
                            ensemble_total = 1
                            diff = val - threshold_c
                            if operator == "above":
                                # Probability based on distance from threshold
                                # Within 2°C = uncertain, >2°C = high prob
                                if diff > 2:
                                    ensemble_prob = 0.85
                                elif diff > 0:
                                    ensemble_prob = 0.5 + diff * 0.175
                                elif diff > -2:
                                    ensemble_prob = 0.5 + diff * 0.175
                                else:
                                    ensemble_prob = 0.15
                            else:
                                if diff < -2:
                                    ensemble_prob = 0.85
                                elif diff < 0:
                                    ensemble_prob = 0.5 - diff * 0.175
                                elif diff < 2:
                                    ensemble_prob = 0.5 - diff * 0.175
                                else:
                                    ensemble_prob = 0.15
                            ensemble_yes = 1 if ensemble_prob > 0.5 else 0
                        break

            forecast_str = (
                f"Forecast for {city} on {target_str}: "
                f"{metric_key}={values[dates.index(target_str)] if target_str in dates else 'N/A'}"
            )

        # 2. Historical base rate
        historical = self.get_historical(
            city, target_date, config.WEATHER_HISTORICAL_YEARS)
        base_rate = 0.5  # default/prior
        historical_str = "No historical data available"

        if historical:
            hist_key = {
                "temp_max": "all_temp_max_c",
                "temp_min": "all_temp_min_c",
                "precipitation": "all_precip_mm",
            }.get(metric, "all_temp_max_c")

            hist_values = historical.get(hist_key, [])
            if hist_values:
                if operator == "above":
                    above = sum(1 for v in hist_values if v > threshold_c)
                else:
                    above = sum(1 for v in hist_values if v < threshold_c)
                base_rate = above / len(hist_values)

            stats = historical.get("stats", {})
            historical_str = (
                f"Historical ({historical.get('years_analysed', 0)} years): "
                f"mean={stats.get('temp_max_mean', 0):.1f}°C, "
                f"median={stats.get('temp_max_median', 0):.1f}°C, "
                f"range=[{stats.get('temp_max_min', 0):.1f}, "
                f"{stats.get('temp_max_max', 0):.1f}]°C, "
                f"base_rate={base_rate:.3f}"
            )

        # 3. Combine: weighted average of ensemble + historical
        w = config.WEATHER_ENSEMBLE_WEIGHT
        combined = w * ensemble_prob + (1 - w) * base_rate

        # Clamp to [0.02, 0.98] — never be 100% certain
        combined = max(0.02, min(0.98, combined))

        return {
            "city": city,
            "metric": metric,
            "operator": operator,
            "threshold": threshold,
            "threshold_c": threshold_c,
            "threshold_unit": threshold_unit,
            "target_date": target_date.strftime("%Y-%m-%d"),
            "ensemble_probability": round(ensemble_prob, 4),
            "ensemble_yes": ensemble_yes,
            "ensemble_total": ensemble_total,
            "historical_base_rate": round(base_rate, 4),
            "combined_probability": round(combined, 4),
            "forecast_summary": forecast_str,
            "historical_summary": historical_str,
            "raw_forecast": forecast,
            "raw_historical": historical,
        }


# ── Helper Functions ─────────────────────────────────────────────

def _median(values: list[float]) -> float:
    """Calculate median of a sorted or unsorted list."""
    if not values:
        return 0.0
    s = sorted(values)
    n = len(s)
    if n % 2 == 1:
        return s[n // 2]
    return (s[n // 2 - 1] + s[n // 2]) / 2


def _percentile(values: list[float], pct: int) -> float:
    """Calculate percentile (simple nearest-rank method)."""
    if not values:
        return 0.0
    s = sorted(values)
    k = int(len(s) * pct / 100)
    return s[min(k, len(s) - 1)]
