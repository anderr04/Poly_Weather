#!/usr/bin/env python3
"""
analysis.py – Shadow trade analysis with full performance metrics.

Reads shadow_trades.csv and computes:
    • Win-rate with/without IA filter
    • PnL simulation
    • % of losses eliminated by IA
    • Sharpe ratio (annualized)
    • Max drawdown (peak-to-trough)
    • Calibration plot (predicted prob vs actual outcome)
    • Breakdown by source (weather / whale) and by city

Usage:
    python analysis.py                          # Full report
    python analysis.py --min-trades 50          # Require N trades
    python analysis.py --csv data/shadow.csv    # Custom path
    python analysis.py --plot                   # Generate plots
"""

from __future__ import annotations

import argparse
import csv
import math
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

DATA_DIR = Path(__file__).parent / "data"


def load_csv(path: Path) -> list[dict]:
    """Load a CSV file into a list of dicts."""
    if not path.exists():
        print(f"  [!] File not found: {path}")
        return []
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def safe_float(val: str, default: float = 0.0) -> float:
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


# ── Core Metrics ─────────────────────────────────────────────────

def calc_sharpe(returns: list[float], annual_factor: float = 252) -> float:
    """
    Sharpe ratio (annualized, assuming daily returns).
    Returns 0 if insufficient data.
    """
    if len(returns) < 2:
        return 0.0
    avg = sum(returns) / len(returns)
    variance = sum((r - avg) ** 2 for r in returns) / (len(returns) - 1)
    std = math.sqrt(variance)
    if std == 0:
        return 0.0
    return (avg / std) * math.sqrt(annual_factor)


def calc_max_drawdown(equity_curve: list[float]) -> tuple[float, float]:
    """
    Max drawdown from an equity curve.
    Returns (max_drawdown_pct, max_drawdown_usd).
    """
    if not equity_curve:
        return 0.0, 0.0

    peak = equity_curve[0]
    max_dd = 0.0
    max_dd_usd = 0.0

    for eq in equity_curve:
        if eq > peak:
            peak = eq
        dd = (peak - eq) / peak if peak > 0 else 0
        dd_usd = peak - eq
        if dd > max_dd:
            max_dd = dd
            max_dd_usd = dd_usd

    return max_dd, max_dd_usd


def calc_profit_factor(pnls: list[float]) -> float:
    """Gross profit / gross loss. >1 = profitable."""
    gross_profit = sum(p for p in pnls if p > 0)
    gross_loss = abs(sum(p for p in pnls if p < 0))
    if gross_loss == 0:
        return float('inf') if gross_profit > 0 else 0.0
    return gross_profit / gross_loss


# ── Analysis ─────────────────────────────────────────────────────

def analyze(rows: list[dict], generate_plots: bool = False) -> None:
    """Full analysis of shadow trades."""
    total = len(rows)
    if total == 0:
        print("  No trades to analyze.\n")
        return

    # ── Basic Stats ──────────────────────────────────────────
    print(f"\n  Total shadow entries    : {total}")

    # Source breakdown
    weather = [r for r in rows if r.get("signal_source") == "weather"]
    whale = [r for r in rows if r.get("signal_source") == "whale_copy"]
    print(f"  Weather signals        : {len(weather)}")
    print(f"  Whale copy signals     : {len(whale)}")
    print()

    # Trades vs skips
    traded = [r for r in rows if r.get("bot_action") in
              ("PAPER_BUY", "LIVE_BUY", "COPIED")]
    skipped = [r for r in rows if r.get("bot_action") not in
               ("PAPER_BUY", "LIVE_BUY", "COPIED", "")]
    print(f"  Trades executed        : {len(traded)}")
    print(f"  Signals skipped        : {len(skipped)}")

    # Skip reasons
    skip_reasons: dict[str, int] = {}
    for r in skipped:
        reason = r.get("bot_action", "unknown")
        skip_reasons[reason] = skip_reasons.get(reason, 0) + 1
    if skip_reasons:
        print(f"\n  Skip reasons:")
        for reason, count in sorted(skip_reasons.items(), key=lambda x: -x[1]):
            print(f"    {reason:<30s} {count:>4d}")
    print()

    # ── City Breakdown (Weather) ─────────────────────────────
    if weather:
        print("  " + "─" * 55)
        print("  CITY BREAKDOWN (Weather Signals)")
        print("  " + "─" * 55)

        cities: dict[str, list[dict]] = {}
        for r in weather:
            city = r.get("city", "unknown")
            cities.setdefault(city, []).append(r)

        print(f"  {'City':<20s} {'Signals':>8s} {'Traded':>8s} "
              f"{'Avg Misp':>10s} {'Avg Prob':>10s}")
        print(f"  {'─'*20} {'─'*8} {'─'*8} {'─'*10} {'─'*10}")

        for city, city_rows in sorted(cities.items(), key=lambda x: -len(x[1])):
            n_traded = sum(1 for r in city_rows
                          if r.get("bot_action") in ("PAPER_BUY", "LIVE_BUY"))
            avg_misp = sum(safe_float(r.get("mispricing", "0"))
                          for r in city_rows) / len(city_rows) if city_rows else 0
            avg_prob = sum(safe_float(r.get("forecast_probability", "0"))
                          for r in city_rows) / len(city_rows) if city_rows else 0
            print(f"  {city:<20s} {len(city_rows):>8d} {n_traded:>8d} "
                  f"{avg_misp:>+10.4f} {avg_prob:>10.4f}")
        print()

    # ── Probability Calibration ──────────────────────────────
    with_outcome = [r for r in rows
                    if r.get("actual_outcome") in ("WIN", "LOSS")]

    if with_outcome:
        print("  " + "─" * 55)
        print("  CALIBRATION (predicted probability vs actual outcome)")
        print("  " + "─" * 55)

        # Bin by predicted probability
        bins = [(0.0, 0.2), (0.2, 0.4), (0.4, 0.6),
                (0.6, 0.8), (0.8, 1.0)]
        print(f"  {'Pred Range':<15s} {'Count':>6s} {'Act WR':>8s} "
              f"{'Expected':>10s} {'Diff':>8s}")
        print(f"  {'─'*15} {'─'*6} {'─'*8} {'─'*10} {'─'*8}")

        cal_expected = []
        cal_actual = []

        for lo, hi in bins:
            in_bin = [r for r in with_outcome
                      if lo <= safe_float(r.get("forecast_probability", "0")) < hi]
            if not in_bin:
                continue
            wins = sum(1 for r in in_bin if r.get("actual_outcome") == "WIN")
            act_wr = wins / len(in_bin)
            expected = (lo + hi) / 2
            diff = act_wr - expected
            print(f"  {lo:.1f}–{hi:.1f}        {len(in_bin):>6d} "
                  f"{act_wr:>8.1%} {expected:>10.1%} {diff:>+8.1%}")
            cal_expected.append(expected)
            cal_actual.append(act_wr)
        print()

    # ── Performance (PnL Analysis) ───────────────────────────
    pnls = [safe_float(r.get("pnl_simulated", "0"))
            for r in with_outcome if r.get("pnl_simulated")]

    if len(with_outcome) >= 5:
        print("  " + "═" * 55)
        print("  PERFORMANCE METRICS")
        print("  " + "═" * 55)

        all_wins = sum(1 for r in with_outcome
                       if r.get("actual_outcome") == "WIN")
        all_losses = len(with_outcome) - all_wins
        wr = all_wins / len(with_outcome)

        print(f"\n  Total resolved trades  : {len(with_outcome)}")
        print(f"  Wins / Losses          : {all_wins} / {all_losses}")
        print(f"  Win Rate               : {wr:.1%}")

        if pnls:
            total_pnl = sum(pnls)
            avg_pnl = total_pnl / len(pnls)
            print(f"\n  Total PnL              : ${total_pnl:+.2f}")
            print(f"  Avg PnL per trade      : ${avg_pnl:+.4f}")
            print(f"  Profit Factor          : {calc_profit_factor(pnls):.2f}")

            # Sharpe
            sharpe = calc_sharpe(pnls)
            print(f"  Sharpe Ratio (ann.)    : {sharpe:.2f}")

            # Max Drawdown
            equity = [100.0]  # start at $100
            for p in pnls:
                equity.append(equity[-1] + p)
            dd_pct, dd_usd = calc_max_drawdown(equity)
            print(f"  Max Drawdown           : {dd_pct:.1%} (${dd_usd:.2f})")

            # Best/worst
            print(f"  Best Trade             : ${max(pnls):+.4f}")
            print(f"  Worst Trade            : ${min(pnls):+.4f}")

        # IA filter comparison
        ia_validated = [r for r in with_outcome
                        if r.get("model_confidence", "0") != "0"]
        if ia_validated:
            ia_approved = [r for r in ia_validated
                           if r.get("would_trade") == "True"]
            ia_rejected = [r for r in ia_validated
                           if r.get("would_trade") == "False"]

            print(f"\n  {'─'*55}")
            print(f"  IA FILTER COMPARISON")
            print(f"  {'─'*55}")
            print(f"  IA validated trades    : {len(ia_validated)}")
            print(f"  IA approved            : {len(ia_approved)}")
            print(f"  IA rejected            : {len(ia_rejected)}")

            if ia_approved:
                ia_wins = sum(1 for r in ia_approved
                              if r.get("actual_outcome") == "WIN")
                ia_wr = ia_wins / len(ia_approved)
                print(f"  IA Approved Win Rate   : {ia_wr:.1%}")

            if ia_rejected:
                rej_wins = sum(1 for r in ia_rejected
                               if r.get("actual_outcome") == "WIN")
                rej_losses = len(ia_rejected) - rej_wins
                print(f"  Rejected trades W/L    : {rej_wins}/{rej_losses}")
                if all_losses > 0:
                    pct_blocked = rej_losses / all_losses
                    print(f"  % Losses blocked by IA : {pct_blocked:.1%}")

        print()

    # ── Validator Stats ──────────────────────────────────────
    validated = [r for r in rows if r.get("model_confidence", "0") != "0"]
    if validated:
        print("  " + "─" * 55)
        print("  VALIDATOR STATS")
        print("  " + "─" * 55)

        confs = [int(r.get("model_confidence", "0")) for r in validated]
        avg_conf = sum(confs) / len(confs)
        print(f"  Total validated        : {len(validated)}")
        print(f"  Avg confidence         : {avg_conf:.1f}%")
        print(f"  Min / Max confidence   : {min(confs)}% / {max(confs)}%")

        edges = [safe_float(r.get("mispricing", "0")) for r in validated]
        if edges:
            avg_edge = sum(abs(e) for e in edges) / len(edges)
            print(f"  Avg |mispricing|       : {avg_edge:.4f}")

        latencies = [safe_float(r.get("latency_ms", "0")) for r in validated]
        if latencies:
            avg_lat = sum(latencies) / len(latencies)
            print(f"  Avg latency            : {avg_lat:.0f}ms")

        models: dict[str, int] = {}
        for r in validated:
            m = r.get("model_name", "unknown")
            models[m] = models.get(m, 0) + 1
        if models:
            print(f"  Models used            : {dict(models)}")
        print()

    # ── Generate Plots ───────────────────────────────────────
    if generate_plots and with_outcome:
        _generate_plots(with_outcome, pnls)


def _generate_plots(
    with_outcome: list[dict],
    pnls: list[float],
) -> None:
    """Generate analysis plots (requires matplotlib)."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np
    except ImportError:
        print("  [!] matplotlib/numpy required for plots. "
              "pip install matplotlib numpy")
        return

    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    fig.suptitle("Poly_Weather — Shadow Trade Analysis",
                 fontsize=14, fontweight="bold")

    # 1. Calibration plot
    ax1 = axes[0, 0]
    bins = [(0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.0)]
    expected, actual, sizes = [], [], []
    for lo, hi in bins:
        in_bin = [r for r in with_outcome
                  if lo <= safe_float(r.get("forecast_probability", "0")) < hi]
        if in_bin:
            wins = sum(1 for r in in_bin if r.get("actual_outcome") == "WIN")
            expected.append((lo + hi) / 2)
            actual.append(wins / len(in_bin))
            sizes.append(len(in_bin))

    if expected:
        ax1.scatter(expected, actual, s=[s * 20 for s in sizes],
                    alpha=0.7, color="#2196F3")
        ax1.plot([0, 1], [0, 1], "--", color="#666", alpha=0.5)
        ax1.set_xlabel("Predicted Probability")
        ax1.set_ylabel("Actual Win Rate")
        ax1.set_title("Calibration Plot")
        ax1.set_xlim(0, 1)
        ax1.set_ylim(0, 1)
        ax1.grid(True, alpha=0.3)

    # 2. Equity curve
    ax2 = axes[0, 1]
    if pnls:
        equity = [100.0]
        for p in pnls:
            equity.append(equity[-1] + p)
        ax2.plot(equity, color="#4CAF50", linewidth=1.5)
        ax2.axhline(y=100, color="#666", linestyle="--", alpha=0.3)
        ax2.set_xlabel("Trade #")
        ax2.set_ylabel("Equity ($)")
        ax2.set_title("Equity Curve")
        ax2.grid(True, alpha=0.3)

    # 3. PnL distribution
    ax3 = axes[1, 0]
    if pnls:
        ax3.hist(pnls, bins=20, color="#FF9800", alpha=0.7, edgecolor="#333")
        ax3.axvline(x=0, color="#666", linestyle="--")
        ax3.set_xlabel("PnL ($)")
        ax3.set_ylabel("Count")
        ax3.set_title("PnL Distribution")
        ax3.grid(True, alpha=0.3)

    # 4. City performance
    ax4 = axes[1, 1]
    city_pnl: dict[str, float] = {}
    for r in with_outcome:
        city = r.get("city", "unknown")
        pnl = safe_float(r.get("pnl_simulated", "0"))
        city_pnl[city] = city_pnl.get(city, 0) + pnl
    if city_pnl:
        cities_sorted = sorted(city_pnl.items(), key=lambda x: x[1], reverse=True)
        names = [c[0][:12] for c in cities_sorted[:10]]
        vals = [c[1] for c in cities_sorted[:10]]
        colors = ["#4CAF50" if v >= 0 else "#F44336" for v in vals]
        ax4.barh(names, vals, color=colors, alpha=0.7)
        ax4.set_xlabel("Total PnL ($)")
        ax4.set_title("PnL by City")
        ax4.grid(True, alpha=0.3)

    plt.tight_layout()
    plot_path = DATA_DIR / "analysis_plots.png"
    plt.savefig(plot_path, dpi=150, bbox_inches="tight")
    print(f"\n  📊 Plots saved to: {plot_path}")
    plt.close()


# ── Main ─────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Analyze Poly_Weather shadow trades")
    parser.add_argument(
        "--csv", type=str, default=None,
        help="Path to shadow_trades.csv")
    parser.add_argument(
        "--min-trades", type=int, default=0,
        help="Minimum trades required for analysis")
    parser.add_argument(
        "--plot", action="store_true",
        help="Generate PNG analysis plots")
    args = parser.parse_args()

    shadow_path = Path(args.csv) if args.csv else DATA_DIR / "shadow_trades.csv"

    print()
    print("=" * 60)
    print("  POLY_WEATHER — SHADOW TRADE ANALYSIS")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)

    rows = load_csv(shadow_path)

    if not rows:
        print("\n  No data found. Run the bot first to generate shadow trades.")
        print(f"  Expected file: {shadow_path}")
        sys.exit(0)

    if len(rows) < args.min_trades:
        print(f"\n  Only {len(rows)} shadow trades "
              f"(need {args.min_trades}). Wait for more data.")
        sys.exit(0)

    analyze(rows, generate_plots=args.plot)

    print("=" * 60)
    print()


if __name__ == "__main__":
    main()
