"""
backtest/swing_optimizer.py
Grid-search optimizer for the swing (multi-day hold) backtest.

Sweeps stop x TP x trail x max_hold_days combinations.
Uses walk-forward validation (70% train / 30% holdout) to avoid overfitting.

Only needs daily bars -- no 5-min Alpaca data, so it's fast.
Typical run: under 30 seconds for a 6-year period.

Usage:
    python run_day.py --swing-optimize 2020-01-01 2026-01-01
    python run_day.py --swing-optimize 2020-01-01 2026-01-01 --strategy all
    python run_day.py --swing-optimize 2020-01-01 2026-01-01 --apply-best
"""

from __future__ import annotations

import os
import json
import warnings
warnings.filterwarnings("ignore")

import numpy as np
from datetime import date, datetime
from collections import defaultdict
from itertools import product

_BASE        = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PARAMS_PATH = os.path.join(_BASE, "memory", "swing_best_params.json")
_REPORT_DIR  = os.path.join(_BASE, "memory", "swing_results")

# ── Parameter grid ─────────────────────────────────────────────────────────────
# 4 x 5 x 3 x 3 = 180 combinations; minus where tp <= stop -> ~140 valid
STOP_OPTIONS     = [0.03, 0.05, 0.07, 0.10]
TP_OPTIONS       = [0.06, 0.08, 0.10, 0.15, 0.20]
TRAIL_OPTIONS    = [0.03, 0.05, 0.07]
MAX_HOLD_OPTIONS = [3, 5, 7]

TRAIN_PCT        = 0.70
MIN_TRAIN_TRADES = 8


# ── Fast simulation (no I/O) ───────────────────────────────────────────────────

def _simulate_combo(
    daily_candidates: dict[date, list[dict]],
    hist_data:        dict,
    strategy:         str,
    n_picks:          int,
    stop_pct:         float,
    tp_pct:           float,
    trail_pct:        float,
    max_hold_days:    int,
    days_subset:      list[date],
) -> dict:
    """
    Run one parameter combo on a subset of days. Pure math, no I/O.
    Re-uses already-loaded hist_data so scanning is fast.
    """
    from backtest.swing_backtest import (
        _simulate_swing_trade, STARTING_CAPITAL, POSITION_SIZE,
    )

    days_set = set(days_subset)
    trades   = []

    for day, cands in sorted(daily_candidates.items()):
        if day not in days_set:
            continue
        for feat in cands:
            ticker      = feat["ticker"]
            entry_price = feat["_today_open"]

            full_df = hist_data.get(ticker)
            if full_df is None:
                continue
            df_future = full_df[full_df.index.date > day]
            if df_future.empty:
                continue

            result = _simulate_swing_trade(
                df_future, entry_price,
                stop_pct, tp_pct, trail_pct, max_hold_days,
            )
            if result:
                trades.append({
                    "entry_date":  day.isoformat(),
                    "pnl_pct":     result["pnl_pct"],
                    "exit_reason": result["exit_reason"],  # trail_profit / stop_loss / take_profit / max_hold
                    "hold_days":   result.get("hold_days", 0),
                })

    if len(trades) < MIN_TRAIN_TRADES:
        return {}

    wins   = [t for t in trades if t["pnl_pct"] > 0]
    losses = [t for t in trades if t["pnl_pct"] <= 0]
    n      = len(trades)
    wr     = len(wins) / n * 100

    avg_win  = sum(t["pnl_pct"] for t in wins)   / max(len(wins),   1)
    avg_loss = sum(t["pnl_pct"] for t in losses) / max(len(losses), 1)

    exits = defaultdict(int)
    for t in trades:
        exits[t["exit_reason"]] += 1

    # Portfolio simulation
    by_date: dict[str, list[float]] = defaultdict(list)
    for t in trades:
        by_date[t["entry_date"]].append(t["pnl_pct"])

    portfolio = STARTING_CAPITAL
    for day in sorted(days_subset):
        for pnl in by_date.get(day.isoformat(), []):
            portfolio += portfolio * POSITION_SIZE * (pnl / 100)

    port_return = (portfolio / STARTING_CAPITAL - 1) * 100

    # Sharpe
    daily_pnl: dict[str, float] = defaultdict(float)
    for t in trades:
        daily_pnl[t["entry_date"]] += t["pnl_pct"] / max(n_picks, 1)
    daily_pnls = list(daily_pnl.values())
    m = np.mean(daily_pnls)
    s = np.std(daily_pnls, ddof=1) if len(daily_pnls) > 1 else 0.0
    sharpe = float(m / s * np.sqrt(252)) if s > 0 else 0.0

    bkev = (abs(avg_loss) / (avg_win + abs(avg_loss)) * 100
            if avg_win > 0 else 50.0)

    return {
        "n_trades":    n,
        "win_rate":    round(wr,          1),
        "breakeven":   round(bkev,        1),
        "avg_win":     round(avg_win,     3),
        "avg_loss":    round(avg_loss,    3),
        "tp_rate":     round(exits["take_profit"] / n * 100, 1),
        "sharpe":      round(sharpe,      2),
        "port_return": round(port_return, 2),
        "port_end":    round(portfolio,   2),
    }


# ── Shared data loader ─────────────────────────────────────────────────────────

def _prepare_shared_data(
    start_date: str,
    end_date:   str,
    verbose:    bool = True,
) -> dict:
    """Download daily bars once. Result is shared across all strategy runs."""
    from backtest.swing_backtest import _get_trading_days, _load_weights
    from backtest.backtest import _download_hist_data, _build_market_regime
    from scanner.universe import get_all_tickers

    trading_days = _get_trading_days(start_date, end_date)
    if len(trading_days) < 40:
        print("  Need at least 40 trading days.")
        return {}

    tickers = get_all_tickers()
    weights = _load_weights()

    hist_data = _download_hist_data(trading_days, tickers, verbose=verbose)

    market_regime = _build_market_regime(hist_data, trading_days)

    return {
        "trading_days":  trading_days,
        "hist_data":     hist_data,
        "market_regime": market_regime,
        "weights":       weights,
    }


# ── Main optimizer ─────────────────────────────────────────────────────────────

def run_swing_optimizer(
    start_date:  str,
    end_date:    str,
    strategy:    str  = "momentum",
    n_picks:     int  = 3,
    apply_best:  bool = False,
    verbose:     bool = True,
    print_table: bool = True,
    shared_data: dict | None = None,
) -> dict:
    """
    Grid-search over stop x TP x trail x max_hold_days.
    Walk-forward: trains on first 70% of days, validates on last 30%.
    """
    from backtest.swing_backtest import (
        _scan_day_candidates, _TREND_STRATEGIES,
    )

    # ── 1. Data ────────────────────────────────────────────────────────────────
    if shared_data:
        sd            = shared_data
        trading_days  = sd["trading_days"]
        hist_data     = sd["hist_data"]
        market_regime = sd["market_regime"]
        weights       = sd["weights"]
    else:
        sd = _prepare_shared_data(start_date, end_date, verbose)
        if not sd:
            return {}
        trading_days  = sd["trading_days"]
        hist_data     = sd["hist_data"]
        market_regime = sd["market_regime"]
        weights       = sd["weights"]

    split_idx  = int(len(trading_days) * TRAIN_PCT)
    train_days = trading_days[:split_idx]
    val_days   = trading_days[split_idx:]

    if verbose:
        if not shared_data:
            print(f"\n  Swing optimizer: {start_date} to {end_date}")
        print(f"  Strategy : {strategy}   Picks/day: {n_picks}")
        if not shared_data:
            print(f"  Split    : {len(train_days)} train  +  {len(val_days)} validation (70/30)")

    # ── 2. Candidates ──────────────────────────────────────────────────────────
    if verbose:
        print(f"  Scanning {len(trading_days)} days for {strategy} candidates...",
              end=" ", flush=True)

    daily_candidates: dict[date, list[dict]] = {}
    for day in trading_days:
        market_up = market_regime.get(day, True)
        cands = _scan_day_candidates(
            day, hist_data, weights, strategy, n_picks, market_up
        )
        if cands:
            daily_candidates[day] = cands

    total_cands = sum(len(v) for v in daily_candidates.values())
    if verbose:
        print(f"{total_cands} candidates across {len(daily_candidates)} days")

    if not daily_candidates:
        print(f"  No {strategy} candidates found.")
        return {}

    # ── 3. Parameter sweep ─────────────────────────────────────────────────────
    all_combos = [
        (stop, tp, trail, hold)
        for stop, tp, trail, hold
        in product(STOP_OPTIONS, TP_OPTIONS, TRAIL_OPTIONS, MAX_HOLD_OPTIONS)
        if tp > stop
    ]

    if verbose:
        print(f"  Testing {len(all_combos)} combos...", end=" ", flush=True)

    train_results = []
    for stop, tp, trail, hold in all_combos:
        r = _simulate_combo(
            daily_candidates, hist_data,
            strategy, n_picks,
            stop, tp, trail, hold,
            train_days,
        )
        if r:
            train_results.append({
                "stop": stop, "tp": tp, "trail": trail, "max_hold": hold,
                **r,
            })

    if verbose:
        print(f"{len(train_results)} valid")

    if not train_results:
        print("  No combinations produced enough trades on training data.")
        return {}

    train_results.sort(
        key=lambda x: (x["port_return"] > 0, x["sharpe"], x["port_return"]),
        reverse=True,
    )

    # ── 4. Validate top 10 ─────────────────────────────────────────────────────
    top_n = min(10, len(train_results))
    if verbose:
        print(f"  Validating top {top_n} on holdout...", end=" ", flush=True)

    final_results = []
    for combo in train_results[:top_n]:
        v = _simulate_combo(
            daily_candidates, hist_data,
            strategy, n_picks,
            combo["stop"], combo["tp"], combo["trail"], combo["max_hold"],
            val_days,
        )
        final_results.append({
            **combo,
            "strategy":   strategy,
            "val_n":      v.get("n_trades",    0)      if v else 0,
            "val_wr":     v.get("win_rate",    0.0)    if v else 0.0,
            "val_tp":     v.get("tp_rate",     0.0)    if v else 0.0,
            "val_ret":    v.get("port_return", 0.0)    if v else 0.0,
            "val_end":    v.get("port_end",  10_000)   if v else 10_000,
            "val_sharpe": v.get("sharpe",      0.0)    if v else 0.0,
            "val_bkev":   v.get("breakeven",  50.0)    if v else 50.0,
        })

    final_results.sort(key=lambda x: x["val_ret"], reverse=True)

    if verbose:
        best_ret = final_results[0]["val_ret"] if final_results else 0
        best_end = final_results[0]["val_end"] if final_results else 10_000
        sign     = "+" if best_ret >= 0 else ""
        print(f"best ${best_end:,.0f} ({sign}{best_ret:.1f}%)")

    if print_table:
        _print_results(final_results, strategy, start_date, end_date,
                       len(train_days), len(val_days), len(train_results))

    best = final_results[0] if final_results else {}

    if apply_best and best:
        _save_best(strategy, best)

    return {
        "strategy":      strategy,
        "start_date":    start_date,
        "end_date":      end_date,
        "train_days":    len(train_days),
        "val_days":      len(val_days),
        "combos_tested": len(train_results),
        "results":       final_results,
        "best":          best,
    }


# ── Print table ────────────────────────────────────────────────────────────────

def _print_results(
    results:    list[dict],
    strategy:   str,
    start_date: str,
    end_date:   str,
    n_train:    int,
    n_val:      int,
    n_tested:   int,
) -> None:

    def grade(ret: float) -> str:
        if ret >= 30: return "A+"
        if ret >= 20: return "A"
        if ret >= 10: return "B+"
        if ret >=  5: return "B"
        if ret >=  0: return "C"
        if ret >= -5: return "D"
        return "F"

    print("\n" + "=" * 76)
    print(f"  SWING OPTIMIZER  [{strategy.upper()}]  (daily bars)")
    print("=" * 76)
    print(f"  Period : {start_date}  to  {end_date}")
    print(f"  Split  : {n_train} train days  |  {n_val} validation days  (70/30)")
    print(f"  Tested : {n_tested} valid combos  (stop x TP x trail x max_hold_days)")
    print()
    print(f"  {'#':<3}  {'Stop':>5}  {'TP':>5}  {'Trail':>6}  {'Hold':>5}  "
          f"{'TrnWR':>6}  {'ValWR':>6}  {'$10k->':>8}  {'Grd':>4}")
    print(f"  {'---':<3}  {'-----':>5}  {'-----':>5}  {'------':>6}  {'-----':>5}  "
          f"{'------':>6}  {'------':>6}  {'--------':>8}  {'----':>4}")

    for i, r in enumerate(results[:10], 1):
        flag = " <" if i == 1 else ""
        print(
            f"  {i:<3}  "
            f"{r['stop']*100:.0f}%    "
            f"{r['tp']*100:.0f}%    "
            f"{r['trail']*100:.0f}%     "
            f"{r['max_hold']}d     "
            f"{r['win_rate']:>5.1f}%  "
            f"{r['val_wr']:>5.1f}%  "
            f"${r['val_end']:>7,.0f}  "
            f"{grade(r['val_ret']):>4}{flag}"
        )

    print()
    best   = results[0]
    profit = best["val_end"] - 10_000
    psign  = "+" if profit >= 0 else "-"

    print("=" * 76)
    print(f"  BEST VALIDATED SETTINGS:")
    print(f"     Stop: {best['stop']*100:.0f}%   "
          f"TP: {best['tp']*100:.0f}%   "
          f"Trail: {best['trail']*100:.0f}%   "
          f"Max hold: {best['max_hold']} days")
    print(f"     Validation: {best['val_wr']:.1f}% WR  |  "
          f"TP hit {best['val_tp']:.0f}%  |  "
          f"Sharpe {best['val_sharpe']:.2f}  |  "
          f"$10,000 -> ${best['val_end']:,.0f}  ({psign}${abs(profit):,.0f})")
    print()

    ret = best["val_ret"]
    g   = grade(ret)
    if ret >= 5:
        print(f"  PROFITABLE on validation: {ret:+.1f}%  (grade {g})")
        print(f"  Run with --apply-best to save these settings.")
    elif ret >= 0:
        print(f"  Marginal ({ret:+.1f}%). Try a longer date range.")
    else:
        print(f"  All combinations lose ({ret:+.1f}% best) on validation.")
        print(f"  The {strategy} strategy may not suit this period.")
        print(f"  Try a longer range (e.g. 2020-2026) covering multiple regimes.")

    print("=" * 76)
    print()


# ── Save / apply best params ───────────────────────────────────────────────────

def _save_best(strategy: str, best: dict) -> None:
    os.makedirs(_REPORT_DIR, exist_ok=True)
    existing: dict = {}
    if os.path.exists(_PARAMS_PATH):
        try:
            with open(_PARAMS_PATH) as f:
                existing = json.load(f)
        except Exception:
            pass

    existing[strategy] = {
        "stop":     best["stop"],
        "tp":       best["tp"],
        "trail":    best["trail"],
        "max_hold": best["max_hold"],
    }
    with open(_PARAMS_PATH, "w") as f:
        json.dump(existing, f, indent=2)

    print(f"\n  Best params saved to memory/swing_best_params.json")
    print(f"  {strategy}: stop={best['stop']*100:.0f}%  "
          f"tp={best['tp']*100:.0f}%  "
          f"trail={best['trail']*100:.0f}%  "
          f"max_hold={best['max_hold']}d")
    print(f"  Next swing backtest will use these automatically.\n")


# ── Compare all strategies ─────────────────────────────────────────────────────

def run_swing_compare(
    start_date:  str,
    end_date:    str,
    n_picks:     int  = 3,
    apply_best:  bool = False,
    verbose:     bool = True,
) -> dict:
    """Run optimizer on all 4 strategies with one data download. Print ranked table."""
    strategies = ["momentum", "breakout", "gap_and_go", "mean_reversion"]

    print(f"\n  {'='*60}")
    print(f"  SWING OPTIMIZER  --  ALL STRATEGIES COMPARE")
    print(f"  {'='*60}")
    print(f"  Period  : {start_date}  to  {end_date}")
    print(f"  Testing : {len(strategies)} strategies  x  ~140 combos each")
    print(f"  No 5-min data needed -- runs entirely on daily bars.")
    print(f"  {'='*60}")

    shared = _prepare_shared_data(start_date, end_date, verbose=verbose)
    if not shared:
        return {}

    n_train = int(len(shared["trading_days"]) * TRAIN_PCT)
    n_val   = len(shared["trading_days"]) - n_train
    print(f"  Split   : {n_train} train  +  {n_val} validation (70/30)\n")

    all_results: dict[str, dict] = {}
    for i, strategy in enumerate(strategies, 1):
        print(f"  [{i}/4]  {strategy.upper()}", flush=True)
        r = run_swing_optimizer(
            start_date, end_date,
            strategy=strategy,
            n_picks=n_picks,
            apply_best=False,
            verbose=verbose,
            print_table=False,
            shared_data=shared,
        )
        if r and r.get("best"):
            all_results[strategy] = r
        else:
            print(f"       No valid results for {strategy} -- skipping.")

    if not all_results:
        print("\n  No strategies produced valid results.")
        return {}

    _print_comparison(all_results, start_date, end_date)

    if apply_best:
        winner = max(all_results, key=lambda s: all_results[s]["best"].get("val_ret", -999))
        _save_best(winner, all_results[winner]["best"])

    return all_results


def _print_comparison(
    results:    dict[str, dict],
    start_date: str,
    end_date:   str,
) -> None:

    def grade(ret: float) -> str:
        if ret >= 30: return "A+"
        if ret >= 20: return "A"
        if ret >= 10: return "B+"
        if ret >=  5: return "B"
        if ret >=  0: return "C"
        if ret >= -5: return "D"
        return "F"

    rows = []
    for strategy, r in results.items():
        best = r.get("best", {})
        if best:
            rows.append({
                "strategy": strategy,
                "stop":     best.get("stop",      0),
                "tp":       best.get("tp",         0),
                "trail":    best.get("trail",      0),
                "max_hold": best.get("max_hold",   5),
                "train_wr": best.get("win_rate",   0.0),
                "val_wr":   best.get("val_wr",     0.0),
                "val_ret":  best.get("val_ret",  -999.0),
                "val_end":  best.get("val_end",  10_000),
                "val_tp":   best.get("val_tp",     0.0),
                "val_sharpe":best.get("val_sharpe",0.0),
            })

    rows.sort(key=lambda x: x["val_ret"], reverse=True)

    w = 76
    print()
    print("=" * w)
    print("  SWING COMPARE  --  Best validated result per strategy")
    print("=" * w)
    print(f"  {'Rank':<5}  {'Strategy':<16}  {'Stop':>4}  {'TP':>4}  "
          f"{'Trail':>5}  {'Hold':>4}  {'ValWR':>6}  {'$10k->':>8}  {'Grd':>4}")
    print(f"  {'-'*4}  {'-'*16}  {'-'*4}  {'-'*4}  "
          f"{'-'*5}  {'-'*4}  {'-'*6}  {'-'*8}  {'-'*4}")

    for rank, row in enumerate(rows, 1):
        flag = "  <-- BEST" if rank == 1 else ""
        g    = grade(row["val_ret"])
        print(
            f"  {rank:<5}  "
            f"{row['strategy']:<16}  "
            f"{row['stop']*100:.0f}%   "
            f"{row['tp']*100:.0f}%   "
            f"{row['trail']*100:.0f}%    "
            f"{row['max_hold']}d    "
            f"{row['val_wr']:>5.1f}%  "
            f"${row['val_end']:>7,.0f}  "
            f"{g:>4}{flag}"
        )

    print()
    print("=" * w)
    print()

    winner = rows[0]
    profit = winner["val_end"] - 10_000
    psign  = "+" if profit >= 0 else "-"
    ret    = winner["val_ret"]

    print(f"  WINNER: {winner['strategy'].upper()}")
    print(f"  " + "-" * 46)
    print(f"  Best params : stop={winner['stop']*100:.0f}%  "
          f"tp={winner['tp']*100:.0f}%  "
          f"trail={winner['trail']*100:.0f}%  "
          f"hold={winner['max_hold']} days")
    print(f"  Validation  : {winner['val_wr']:.1f}% WR  |  "
          f"TP hit {winner['val_tp']:.0f}%  |  "
          f"Sharpe {winner['val_sharpe']:.2f}  |  "
          f"$10,000 -> ${winner['val_end']:,.0f}  ({psign}${abs(profit):,.0f})")
    print()

    # Save results to text file so they're never lost
    _save_compare_txt(rows, start_date, end_date)

    if ret >= 5:
        print(f"  Profitable on validation: {ret:+.1f}%!")
        print(f"  Run with --apply-best to save params for future backtests.")
    elif ret >= 0:
        print(f"  Marginal ({ret:+.1f}%). Try a longer date range.")
    else:
        print(f"  No strategy profitable in this period ({ret:+.1f}% best).")
        print(f"  Try 2020-2026 for multiple market regimes.")

    print("=" * w)
    print()


def _save_compare_txt(rows: list[dict], start_date: str, end_date: str) -> None:
    """Save comparison table to a text file so results survive window close."""
    os.makedirs(_REPORT_DIR, exist_ok=True)
    ts   = datetime.now().strftime("%Y%m%d_%H%M")
    path = os.path.join(_REPORT_DIR, f"swing_compare_{ts}.txt")

    def grade(ret):
        if ret >= 30: return "A+"
        if ret >= 20: return "A"
        if ret >= 10: return "B+"
        if ret >=  5: return "B"
        if ret >=  0: return "C"
        if ret >= -5: return "D"
        return "F"

    lines = [
        f"SWING OPTIMIZER COMPARE  --  {start_date} to {end_date}",
        f"Run at: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
        f"{'Rank':<5}  {'Strategy':<16}  {'Stop':>4}  {'TP':>4}  "
        f"{'Trail':>5}  {'Hold':>4}  {'ValWR':>6}  {'$10k->':>9}  {'Grade':>5}",
        "-" * 72,
    ]
    for rank, row in enumerate(rows, 1):
        lines.append(
            f"{rank:<5}  {row['strategy']:<16}  "
            f"{row['stop']*100:.0f}%   {row['tp']*100:.0f}%   "
            f"{row['trail']*100:.0f}%    {row['max_hold']}d    "
            f"{row['val_wr']:>5.1f}%  ${row['val_end']:>8,.0f}  "
            f"{grade(row['val_ret']):>5}"
        )

    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")

    print(f"  Results also saved to memory/swing_results/swing_compare_{ts}.txt")
    print(f"  (Open this file any time to re-read the results)")
