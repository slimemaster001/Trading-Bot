"""
backtest/report.py
Human-readable report of all backtest results stored in memory/.

Shows:
  - Summary table of every backtest run (strategy, period, win rate, P&L)
  - Best and worst individual trades
  - Sector breakdown
  - What the numbers mean and how to improve

Usage:
  python run_day.py --report
"""

from __future__ import annotations

import json
import os
import glob
from datetime import datetime

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MEMORY = os.path.join(_BASE, "memory")


def _load_all_backtests() -> list[dict]:
    pattern = os.path.join(_MEMORY, "backtest_*.json")
    files   = sorted(glob.glob(pattern))
    results = []
    for f in files:
        try:
            with open(f) as fh:
                data = json.load(fh)
            data["_file"] = os.path.basename(f)
            results.append(data)
        except Exception:
            pass
    return results


def _load_live_trades() -> list[dict]:
    path = os.path.join(_MEMORY, "trade_results.json")
    try:
        with open(path) as f:
            return [t for t in json.load(f) if t.get("status") == "closed"]
    except Exception:
        return []


def _grade(win_rate: float, cum_pnl: float, sharpe: float) -> str:
    if cum_pnl > 10 and win_rate > 50 and sharpe > 0.5:
        return "A  (Excellent)"
    if cum_pnl > 0 and win_rate > 45:
        return "B  (Good)"
    if cum_pnl > -5 and win_rate > 40:
        return "C  (Marginal)"
    if cum_pnl > -15:
        return "D  (Losing — needs work)"
    return "F  (Failing — strategy broken)"


def _explain_results(r: dict) -> list[str]:
    """Plain-English explanation of what the numbers mean."""
    lines = []
    wr  = r.get("win_rate", 0)
    aw  = r.get("avg_win_pct", 0)
    al  = r.get("avg_loss_pct", 0)
    pnl = r.get("cum_pnl_pct", 0)
    sh  = r.get("sharpe_ratio", 0)
    strat = r.get("strategy", "momentum")

    # Break-even win rate = |avg_loss| / (avg_win + |avg_loss|)
    if aw > 0 and al < 0:
        breakeven_wr = abs(al) / (aw + abs(al)) * 100
    else:
        breakeven_wr = 50.0

    lines.append(f"  Strategy : {strat}")
    lines.append(f"  Win rate : {wr}%  (need >{breakeven_wr:.0f}% to break even with this R/R)")
    lines.append(f"  Avg win  : +{aw}%   Avg loss: {al}%")
    lines.append(f"  P&L      : {pnl:+.2f}%  over {r.get('trading_days',0)} days")
    lines.append(f"  Sharpe   : {sh:.2f}  (>1.0 is good, >2.0 is great, <0 is bad)")
    lines.append(f"  Grade    : {_grade(wr, pnl, sh)}")
    lines.append("")

    # Diagnosis
    if wr < breakeven_wr:
        gap = breakeven_wr - wr
        lines.append(f"  DIAGNOSIS: Win rate is {gap:.0f}% below break-even.")
        lines.append(f"  The strategy is picking too many losers.")
        if strat == "momentum":
            lines.append("  FIX IDEAS:")
            lines.append("    - Raise minimum score threshold (only trade final_score >= 7.5)")
            lines.append("    - Add relative volume filter (only RV >= 1.5x, not 1.1x)")
            lines.append("    - Widen take-profit to 5-6% (let winners run more)")
            lines.append("    - Only trade when S&P 500 is in uptrend (above 50-day MA)")
        elif strat == "mean_reversion":
            lines.append("  FIX IDEAS:")
            lines.append("    - Require bigger prior-day drop (e.g. -5% not -3%)")
            lines.append("    - Add volume confirmation (selloff must have been on high volume)")
            lines.append("    - Tighten RSI requirement (only buy when RSI < 35, not < 45)")
            lines.append("    - Avoid in strong downtrends (check if S&P below 200-day MA)")
        elif strat == "gap_and_go":
            lines.append("  FIX IDEAS:")
            lines.append("    - Raise gap threshold (try >3% gap, not >2%)")
            lines.append("    - Only enter after gap holds for first 15 minutes")
            lines.append("    - Require catalyst: earnings/news (not random gaps)")
        elif strat == "breakout":
            lines.append("  FIX IDEAS:")
            lines.append("    - Require higher RV (try >= 1.5x instead of 1.2x)")
            lines.append("    - Only enter when stock is within 1% of ATH (tighter zone)")
            lines.append("    - Avoid breakouts during broad market weakness")
            lines.append("    - Wait for first 30-min candle to confirm breakout holds")
    elif pnl > 0:
        lines.append("  DIAGNOSIS: Strategy is profitable! Keep testing.")
        lines.append("  Consider running live in paper mode to confirm with real data.")

    return lines


def print_full_report() -> None:
    backtests  = _load_all_backtests()
    live_trades = _load_live_trades()

    print("\n" + "=" * 68)
    print("  BACKTEST RESULTS REPORT")
    print("=" * 68)

    # ── Backtest summary table ──
    if not backtests:
        print("\n  No backtest results found yet.")
        print("  Run: python run_day.py --backtest 2025-01-01 2026-01-01")
    else:
        print(f"\n  Found {len(backtests)} backtest run(s):\n")
        print(f"  {'File':<28} {'Strategy':<16} {'Period':<22} {'Days':>5} "
              f"{'Trades':>7} {'WinRate':>8} {'P&L':>8} {'Sharpe':>7}")
        print("  " + "-" * 105)
        for r in backtests:
            strat   = r.get("strategy", "momentum")
            period  = f"{r.get('start_date','?')} to {r.get('end_date','?')}"
            days    = r.get("trading_days", 0)
            trades  = r.get("total_trades", 0)
            wr      = r.get("win_rate", 0)
            pnl     = r.get("cum_pnl_pct", 0)
            sharpe  = r.get("sharpe_ratio", 0)
            fname   = r.get("_file", "")[:26]
            pnl_str = f"{pnl:+.1f}%"
            wr_str  = f"{wr:.1f}%"
            print(f"  {fname:<28} {strat:<16} {period:<22} {days:>5} "
                  f"{trades:>7} {wr_str:>8} {pnl_str:>8} {sharpe:>7.2f}")

        # ── Detailed breakdown of most recent run ──
        latest = backtests[-1]
        strat_label = latest.get("strategy", "momentum").upper()
        print("\n" + "=" * 68)
        print(f"  DETAILED BREAKDOWN — {latest.get('_file','')}  [{strat_label}]")
        print("=" * 68)
        print()
        for line in _explain_results(latest):
            print(line)

        # ── Strategy comparison if multiple ──
        strategies = list({r.get("strategy", "momentum") for r in backtests})
        if len(strategies) > 1:
            print("=" * 68)
            print("  STRATEGY COMPARISON")
            print("=" * 68)
            # Group by strategy and show best run per strategy
            by_strat: dict[str, list] = {}
            for r in backtests:
                s = r.get("strategy", "momentum")
                by_strat.setdefault(s, []).append(r)

            for strat, runs in sorted(by_strat.items()):
                best = max(runs, key=lambda x: x.get("cum_pnl_pct", -999))
                print(f"\n  {strat.upper()}")
                print(f"    Best run P&L: {best.get('cum_pnl_pct',0):+.2f}%  "
                      f"Win rate: {best.get('win_rate',0):.1f}%  "
                      f"Sharpe: {best.get('sharpe_ratio',0):.2f}")
                print(f"    Grade: {_grade(best.get('win_rate',0), best.get('cum_pnl_pct',0), best.get('sharpe_ratio',0))}")

    # ── Live paper trade history ──
    if live_trades:
        wins   = [t for t in live_trades if t.get("pnl_pct", 0) > 0]
        losses = [t for t in live_trades if t.get("pnl_pct", 0) <= 0]
        print("\n" + "=" * 68)
        print(f"  LIVE PAPER TRADES ({len(live_trades)} closed)")
        print("=" * 68)
        print(f"  Win rate: {len(wins)/len(live_trades)*100:.1f}%  "
              f"Avg win: +{sum(t['pnl_pct'] for t in wins)/max(len(wins),1):.2f}%  "
              f"Total P&L: {sum(t.get('pnl_pct',0) for t in live_trades):+.2f}%")
        print("\n  Last 10 trades:")
        for t in live_trades[-10:]:
            mark = "WIN " if t.get("pnl_pct", 0) > 0 else "LOSS"
            print(f"    {mark}  {t.get('date','?')}  {t.get('ticker','?'):<6}  "
                  f"{t.get('pnl_pct',0):>+6.2f}%  [{t.get('exit_reason','?')}]  "
                  f"AI={t.get('ai_score','?')}")
    else:
        print("\n  No live paper trades yet.")
        print("  Run the bot on a market day to start building real results.")

    print("\n" + "=" * 68)
    print("  WHERE TO FIND THE RAW DATA FILES")
    print("=" * 68)
    print(f"\n  Backtest JSON files  :  {_MEMORY}\\backtest_*.json")
    print(f"  Live trade history   :  {_MEMORY}\\trade_results.json")
    print(f"  Scoring weights      :  {_MEMORY}\\scoring_weights.json")
    print()
    print("  To open in Excel:")
    print("    1. Open Excel → Data → Get Data → From JSON")
    print("    2. Browse to the memory folder and select a file")
    print()
