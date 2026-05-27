"""
backtest/optimizer.py
Auto-optimize strategy parameters to find the best stop/TP/filter settings.

The Problem:
  Choosing stop-loss % and take-profit % by guessing is unreliable.
  Small changes (e.g. stop 2% → 1.5%) can flip a strategy from losing to
  profitable. This tool tests every combination systematically.

How it works:
  1. Downloads historical data ONCE
  2. Pre-computes all technical features for every (ticker, date) pair
     — this is the slow part, but only happens once
  3. Rapidly tests 100+ parameter combinations using the cached features
     — each combo takes <0.1s once features are cached
  4. Uses WALK-FORWARD validation:
       Training set  = first 70% of days (in-sample, used to find best params)
       Validation set = last 30% of days (out-of-sample, checks they still work)
     This prevents "overfitting" — picking params that only worked in the past
     because of luck rather than real signal.
  5. Shows a ranked results table, grades each combo A–F
  6. Optionally applies the best settings to memory/scoring_weights.json

Target:
  We want cum_pnl_pct > ~8% on the training set and cum_pnl_pct > ~3% on the
  validation set. Combined with 5% position sizing (3 trades), this translates
  to roughly 20%+ annual portfolio return.

Usage:
  python run_day.py --optimize 2025-01-01 2026-01-01
  python run_day.py --optimize 2025-01-01 2026-01-01 --strategy momentum
  python run_day.py --optimize 2025-01-01 2026-01-01 --strategy momentum --apply-best
"""

from __future__ import annotations

import json
import os
import itertools
import warnings
warnings.filterwarnings("ignore")

import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, date
from collections import defaultdict

from scanner.universe import get_all_tickers
from scanner.data import build_stock_features
from scanner.scanner import technical_score, passes_filter

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_WEIGHTS_PATH  = os.path.join(_BASE, "memory", "scoring_weights.json")
_OPT_LOG_PATH  = os.path.join(_BASE, "memory", "optimizer_results.json")

# ── Parameter grid ─────────────────────────────────────────────────────────────
# Total combinations: 4 × 6 × 5 = 120 per strategy
# Each runs in <0.1s after pre-computation → full grid done in ~12s
#
# Why small TPs?
# On daily OHLC bars, a 6% intraday move rarely happens. Most large/mid-cap
# stocks move ±1-2% on a normal day. The previous 4.5-6% TPs were never
# reached, so the theoretical 3:1 R:R never materialised — most exits were
# EOD closes at ±0.5%, making the win/loss math almost coin-flip.
# Smaller TPs (1.5-3%) are actually achievable in a day and create real
# asymmetry when combined with tight stops.

STOP_OPTIONS  = [0.010, 0.015, 0.020, 0.025, 0.030]             # 1.0% → 3.0%
                                                                 # wider stops needed for mean_reversion
TP_OPTIONS    = [0.020, 0.025, 0.030, 0.035, 0.040, 0.050]      # 2.0% → 5.0%
                                                                 # removed 1.5% (too small after commission)
SCORE_OPTIONS = [5.0,   5.5,   6.0,   6.5,   7.0]               # minimum tech_score gate

TRAIN_SPLIT   = 0.70   # 70% of days for optimisation, 30% for validation
COMMISSION_PCT = 0.001  # 0.1% round-trip per trade — same as backtest.py
                        # EOD exits at flat now cost -0.1%, penalising dead trades

# Target for "good enough" → 20% annual portfolio return
MIN_CUM_PNL_TRAIN = 5.0    # training P&L must be positive to qualify
MIN_CUM_PNL_VALID = 2.0    # validation P&L must be positive (avoids overfitting)
MIN_WIN_RATE      = 35.0   # minimum acceptable win rate
MIN_TP_HIT_RATE   = 15.0   # at least 15% of trades must hit the profit target
                            # below this = strategy has no real edge, just drift


def _load_weights() -> dict:
    try:
        with open(_WEIGHTS_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


def _get_trading_days(start: str, end: str) -> list[date]:
    s = datetime.strptime(start, "%Y-%m-%d").date()
    e = datetime.strptime(end,   "%Y-%m-%d").date()
    days, cur = [], s
    while cur <= e:
        if cur.weekday() < 5:
            days.append(cur)
        cur += timedelta(days=1)
    return days


# ── Strategy selectors (same logic as backtest.py) ────────────────────────────

def _select_momentum(feat: dict) -> bool:
    pm        = feat.get("premarket_pct",  0)
    rv        = feat.get("rel_volume",     1.0)
    rsi       = feat.get("rsi",            50)
    above_ma20 = feat.get("above_ma20_pct", 0) > 0
    above_ma50 = feat.get("above_ma50_pct", 0) > 0
    ret5      = feat.get("ret_5d",         0)
    # Real gap (>=1%) + above-avg volume + uptrend confirmed + healthy RSI + recent momentum
    return (pm >= 1.0 and rv >= 1.3
            and above_ma20 and above_ma50
            and 50 <= rsi <= 70
            and ret5 > 0)

def _select_mean_reversion(feat: dict) -> bool:
    # Loosened: -3% drop (was -4%), RSI<45 (was <40) → ~2x more qualifying trades
    # Data showed 46% WR at old thresholds — loosening slightly should preserve this
    return (feat.get("ret_1d", 0) < -3.0
            and feat.get("rsi", 50) < 45
            and feat.get("rel_volume", 1.0) >= 0.8)

def _select_gap_and_go(feat: dict) -> bool:
    # Added 8% cap (removes earnings/biotech disasters) + MA filter + RSI range
    # Previous filter got 15.6% WR — this should push to 30-40%
    pm        = feat.get("premarket_pct",  0)
    rv        = feat.get("rel_volume",     1.0)
    rsi       = feat.get("rsi",            50)
    above_ma20 = feat.get("above_ma20_pct", 0) > 0
    return 2.5 < pm <= 8.0 and rv >= 2.0 and 45 <= rsi <= 72 and above_ma20

def _select_breakout(feat: dict) -> bool:
    # Added both-MA confirmation + RSI 55-75 + positive 5d return
    # Previous 31.9% WR was buying stocks near old highs that were actually in downtrends
    fh         = feat.get("pct_from_52w_high", -50)
    rv         = feat.get("rel_volume",      1.0)
    rsi        = feat.get("rsi",             50)
    above_ma20 = feat.get("above_ma20_pct",  0) > 0
    above_ma50 = feat.get("above_ma50_pct",  0) > 0
    ret5       = feat.get("ret_5d",          0)
    return (-3.0 <= fh <= 0.5
            and rv >= 1.5
            and 55 <= rsi <= 75
            and above_ma20 and above_ma50
            and ret5 > 1.0)

_SELECTORS = {
    "momentum":       (_select_momentum,       False),  # (fn, reverse_sort)
    "mean_reversion": (_select_mean_reversion, False),  # sort ascending (worst ret_1d first)
    "gap_and_go":     (_select_gap_and_go,     True),
    "breakout":       (_select_breakout,       True),
}

def _sort_key(feat: dict, strategy: str) -> float:
    if strategy == "momentum":       return feat.get("tech_score", 0)
    if strategy == "mean_reversion": return feat.get("ret_1d", 0)    # ascending → negate below
    if strategy == "gap_and_go":     return feat.get("premarket_pct", 0)
    if strategy == "breakout":       return feat.get("pct_from_52w_high", -50)
    return feat.get("tech_score", 0)


# ── Pre-computation of features ────────────────────────────────────────────────

def _build_market_regime(
    hist_data:    dict[str, pd.DataFrame],
    trading_days: list[date],
) -> dict[date, bool]:
    """True = SPY above 50-day MA = bull market = safe for trend strategies."""
    spy_df = hist_data.get("SPY")
    if spy_df is None:
        return {day: True for day in trading_days}
    regime: dict[date, bool] = {}
    for day in trading_days:
        before = spy_df[spy_df.index.date < day]
        if len(before) < 50:
            regime[day] = True
            continue
        closes = before["Close"]
        # yfinance may return a DataFrame with MultiIndex even for a single ticker
        if isinstance(closes, pd.DataFrame):
            closes = closes.squeeze()          # single-column → Series
        if isinstance(closes, pd.DataFrame):   # still a DataFrame? take first column
            closes = closes.iloc[:, 0]
        price = float(closes.iloc[-1])
        ma50  = float(closes.rolling(50).mean().iloc[-1])
        regime[day] = price > ma50
    return regime


def _precompute_features(
    trading_days:   list[date],
    hist_data:      dict[str, pd.DataFrame],
    weights:        dict,
    strategy:       str  = "momentum",
    verbose:        bool = True,
) -> dict[date, list[dict]]:
    """
    For each trading day, compute features for ALL qualifying tickers.
    Result is a cache: {date → [list of feature dicts with today's OHLC attached]}.

    Bear-market days are automatically excluded for trend-following strategies
    (momentum, gap_and_go, breakout). This is the key fix that stops the optimizer
    from being poisoned by 2022-style bear-market data.

    This is the slow step (~20-60 seconds). After this, running 100 different
    parameter combinations takes only a few seconds total.
    """
    # Build market regime to exclude bear-market days for trend strategies
    trend_strategies = {"momentum", "gap_and_go", "breakout"}
    market_regime    = _build_market_regime(hist_data, trading_days)
    bull_days        = {d for d, up in market_regime.items() if up}
    bear_days        = len(trading_days) - len(bull_days)

    if verbose:
        print(f"\n  ⚙  Pre-computing features for {len(trading_days)} days "
              f"× {len(hist_data)} tickers…")
        if strategy in trend_strategies:
            print(f"  📈 Market filter: {len(bull_days)} bull days / "
                  f"{bear_days} bear days excluded")
        print("  (This runs once — the grid-search will be fast after this)\n")

    cache: dict[date, list[dict]] = {}
    n_done = 0

    for day in trading_days:
        # Skip bear-market days for trend-following strategies
        if strategy in trend_strategies and day not in bull_days:
            cache[day] = []
            n_done += len(hist_data)
            continue
        day_candidates = []

        for ticker, full_df in hist_data.items():
            df_before = full_df[full_df.index.date < day]
            df_today  = full_df[full_df.index.date == day]

            if len(df_before) < 15 or df_today.empty:
                n_done += 1
                continue

            feat = build_stock_features(ticker, df_before)
            if feat is None:
                n_done += 1
                continue

            ok, _ = passes_filter(feat)
            if not ok:
                n_done += 1
                continue

            entry_proxy = float(df_before["Close"].iloc[-1])
            today_open  = float(df_today["Open"].iloc[0])  if "Open"  in df_today.columns else entry_proxy

            feat["premarket_pct"] = round((today_open / entry_proxy - 1) * 100, 2)
            feat["tech_score"]    = float(technical_score(feat, weights))
            feat["entry_price"]   = round(today_open * 1.001, 2)  # 0.1% slippage

            # Attach today's OHLC for stop/TP simulation
            feat["_today_high"]   = float(df_today["High"].iloc[0])  if "High"  in df_today.columns else feat["entry_price"]
            feat["_today_low"]    = float(df_today["Low"].iloc[0])   if "Low"   in df_today.columns else feat["entry_price"]
            feat["_today_close"]  = float(df_today["Close"].iloc[0]) if "Close" in df_today.columns else feat["entry_price"]
            feat["_date"]         = day

            day_candidates.append(feat)
            n_done += 1

        cache[day] = day_candidates

        if verbose and (list(trading_days).index(day) + 1) % 30 == 0:
            n_day = list(trading_days).index(day) + 1
            qualified = sum(len(v) for v in cache.values())
            print(f"  … {n_day}/{len(trading_days)} days  "
                  f"({qualified} qualified candidates so far)")

    if verbose:
        total_qual = sum(len(v) for v in cache.values())
        print(f"  ✅ Pre-computation done: {total_qual} qualified (ticker, day) pairs cached\n")

    return cache


# ── Single parameter combo simulation ─────────────────────────────────────────

def _simulate_combo(
    cache:       dict[date, list[dict]],
    trading_days: list[date],
    strategy:    str,
    stop_pct:    float,
    tp_pct:      float,
    min_score:   float,
    n_picks:     int,
) -> dict:
    """
    Run simulation for one parameter combination using pre-computed feature cache.
    Very fast since features are already calculated.
    """
    selector, _ = _SELECTORS.get(strategy, _SELECTORS["momentum"])
    reverse_sort = strategy != "mean_reversion"

    all_trades  = []
    daily_pnl:  dict[str, float] = defaultdict(float)
    exit_counts: dict[str, int]  = {"tp": 0, "stop": 0, "eod": 0}

    for day in trading_days:
        candidates = [
            f for f in cache.get(day, [])
            if f["tech_score"] >= min_score and selector(f)
        ]
        if not candidates:
            continue

        # Sort by strategy-specific key
        key_fn = lambda f: _sort_key(f, strategy)
        candidates.sort(key=key_fn, reverse=reverse_sort)
        picks = candidates[:n_picks]

        for pick in picks:
            entry = pick["entry_price"]
            stop  = entry * (1 - stop_pct)
            tp    = entry * (1 + tp_pct)
            high  = pick["_today_high"]
            low   = pick["_today_low"]
            close = pick["_today_close"]

            if low <= stop:
                exit_p     = stop
                exit_type  = "stop"
            elif high >= tp:
                exit_p     = tp
                exit_type  = "tp"
            else:
                exit_p     = close
                exit_type  = "eod"

            # Apply round-trip commission — EOD exits at flat now cost -0.1%
            pnl = (exit_p / entry - 1) * 100 - COMMISSION_PCT * 100
            all_trades.append(pnl)
            daily_pnl[day.isoformat()] += pnl / n_picks

            # Track exit reasons for signal quality metrics
            exit_counts[exit_type] += 1

    if not all_trades:
        return {}

    wins     = [p for p in all_trades if p > 0]
    losses   = [p for p in all_trades if p <= 0]
    wr       = len(wins) / len(all_trades) * 100
    avg_win  = sum(wins)   / len(wins)   if wins   else 0
    avg_loss = sum(losses) / len(losses) if losses else 0

    daily_pnls = list(daily_pnl.values())
    cum_pnl    = sum(daily_pnls)
    sharpe     = _sharpe(daily_pnls)
    max_dd     = _max_drawdown(daily_pnls)

    # Break-even WR
    breakeven = abs(avg_loss) / (avg_win + abs(avg_loss)) * 100 if avg_win > 0 else 50.0

    # ── Portfolio simulation ($10,000 starting, 5% per trade, compounding) ──
    # daily_pnl[date] = sum(trade_pnls) / n_picks
    # Portfolio daily return = daily_pnl × n_picks × 5%
    # (e.g. 3 trades averaging +1%, each at 5% → portfolio +0.15%)
    STARTING = 10_000.0
    POS_SIZE  = 0.05        # 5% per trade
    portfolio = STARTING
    for dpnl in daily_pnl.values():
        portfolio *= (1 + dpnl * n_picks * POS_SIZE / 100)
    port_end_value  = round(portfolio, 2)
    port_return_pct = round((portfolio / STARTING - 1) * 100, 2)
    port_profit     = round(portfolio - STARTING, 2)

    # Annualised portfolio return (for comparison across different period lengths)
    days_in_set   = len(trading_days)
    annual_port_pct = port_return_pct * (252 / max(days_in_set, 1))

    n_total   = max(len(all_trades), 1)
    tp_rate   = round(exit_counts["tp"]  / n_total * 100, 1)
    eod_rate  = round(exit_counts["eod"] / n_total * 100, 1)
    stop_rate = round(exit_counts["stop"]/ n_total * 100, 1)

    return {
        "stop_pct":         stop_pct,
        "tp_pct":           tp_pct,
        "min_score":        min_score,
        "n_trades":         len(all_trades),
        "win_rate":         round(wr, 1),
        "avg_win_pct":      round(avg_win, 3),
        "avg_loss_pct":     round(avg_loss, 3),
        "breakeven_wr":     round(breakeven, 1),
        "cum_pnl_pct":      round(cum_pnl, 2),
        "sharpe":           round(sharpe, 2),
        "max_drawdown":     round(max_dd, 2),
        "tp_hit_rate":      tp_rate,    # % of trades that hit the profit target — KEY METRIC
        "eod_rate":         eod_rate,   # % that drifted to end-of-day — should be LOW
        "stop_rate":        stop_rate,  # % that hit the stop-loss
        "port_end_value":   port_end_value,
        "port_return_pct":  port_return_pct,
        "port_profit":      port_profit,
        "annual_port_est":  round(annual_port_pct, 1),
    }


def _sharpe(daily_pnls: list[float]) -> float:
    if len(daily_pnls) < 2:
        return 0.0
    mean = np.mean(daily_pnls)
    std  = np.std(daily_pnls, ddof=1)
    return float(mean / std * np.sqrt(252)) if std > 0 else 0.0


def _max_drawdown(daily_pnls: list[float]) -> float:
    peak, cum, dd = 0.0, 0.0, 0.0
    for p in daily_pnls:
        cum  += p
        peak  = max(peak, cum)
        dd    = min(dd, cum - peak)
    return dd


# ── Grade ─────────────────────────────────────────────────────────────────────

def _grade(r: dict) -> str:
    pnl    = r.get("cum_pnl_pct", 0)
    wr     = r.get("win_rate", 0)
    sharpe = r.get("sharpe", 0)
    est    = r.get("annual_port_est", 0)

    if est >= 20 and sharpe >= 1.0 and wr > 40:   return "A  ★★★  (target reached)"
    if est >= 15 and sharpe >= 0.5:               return "B  ★★   (close to target)"
    if est >= 8  and pnl > 0:                     return "C  ★    (profitable, not target)"
    if pnl > 0:                                   return "D       (just profitable)"
    return "F       (losing money)"


# ── Main optimiser ─────────────────────────────────────────────────────────────

def run_optimizer(
    start_date:  str,
    end_date:    str,
    strategy:    str  = "momentum",
    n_picks:     int  = 3,
    apply_best:  bool = False,
    verbose:     bool = True,
) -> list[dict]:
    """
    Grid-search optimisation with walk-forward validation.

    Steps:
      1. Download data for the full period
      2. Pre-compute features (the slow part)
      3. Split into 70% train / 30% validation
      4. Test all 100 stop/TP/score combinations on TRAIN set
      5. Validate the top 5 winners on VALIDATION set
      6. Print results table
      7. Optionally save best params

    Returns: sorted list of result dicts (best first)
    """
    from backtest.backtest import _download_hist_data

    if strategy not in ("momentum", "mean_reversion", "gap_and_go", "breakout"):
        print(f"  ⚠  Unknown strategy '{strategy}' — defaulting to momentum")
        strategy = "momentum"

    all_days = _get_trading_days(start_date, end_date)
    if len(all_days) < 20:
        print("  ⚠  Need at least 20 trading days to optimise.")
        return []

    split_idx  = int(len(all_days) * TRAIN_SPLIT)
    train_days = all_days[:split_idx]
    valid_days = all_days[split_idx:]

    tickers = get_all_tickers()
    weights = _load_weights()

    if verbose:
        print(f"\n  📅 Optimise: {start_date} → {end_date}  ({len(all_days)} days)")
        print(f"  📋 Strategy: {strategy}  |  n_picks: {n_picks}")
        print(f"  🔀 Train: {train_days[0]}→{train_days[-1]} ({len(train_days)} days)  |  "
              f"Validate: {valid_days[0]}→{valid_days[-1]} ({len(valid_days)} days)")
        n_combos = len(STOP_OPTIONS) * len(TP_OPTIONS) * len(SCORE_OPTIONS)
        print(f"  🔢 Testing {n_combos} parameter combinations")

    # ── Download data once ──
    print()
    hist_data = _download_hist_data(all_days, tickers, verbose)

    # ── Pre-compute features ──
    cache_train = _precompute_features(train_days, hist_data, weights, strategy, verbose)
    cache_valid = _precompute_features(valid_days, hist_data, weights, strategy, verbose=False)

    # ── Grid search on training set ──
    if verbose:
        print("  🔍 Running grid search…")

    param_combos = list(itertools.product(STOP_OPTIONS, TP_OPTIONS, SCORE_OPTIONS))
    train_results = []

    for i, (stop, tp, score) in enumerate(param_combos):
        # Skip combos where TP <= stop (guaranteed to lose)
        if tp <= stop * 1.5:
            continue
        r = _simulate_combo(cache_train, train_days, strategy, stop, tp, score, n_picks)
        if r:
            r["grade"] = _grade(r)
            train_results.append(r)

        if verbose and (i + 1) % 20 == 0:
            print(f"  … {i+1}/{len(param_combos)} combos done", end="\r")

    if not train_results:
        print("  ⚠  No valid results from grid search.")
        return []

    # Sort by: TP hit rate first (signal quality), then Sharpe, then P&L
    # A combo with high TP rate but lower Sharpe is BETTER than a combo
    # with 3% TP hits and slightly higher Sharpe — the latter is just noise.
    train_results.sort(
        key=lambda x: (x["tp_hit_rate"] >= MIN_TP_HIT_RATE,
                       x["port_return_pct"] > 0,
                       x["sharpe"],
                       x["cum_pnl_pct"]),
        reverse=True,
    )

    if verbose:
        print(f"  ✅ Grid search complete: {len(train_results)} valid combinations\n")

    # ── Validate top 10 on validation set ──
    top_combos    = train_results[:10]
    valid_results = []

    for r in top_combos:
        v = _simulate_combo(
            cache_valid, valid_days, strategy,
            r["stop_pct"], r["tp_pct"], r["min_score"], n_picks,
        )
        if v:
            v["grade"] = _grade(v)
            valid_results.append({
                "stop_pct":  r["stop_pct"],
                "tp_pct":    r["tp_pct"],
                "min_score": r["min_score"],
                "train":     r,
                "valid":     v,
                "robust":    v["cum_pnl_pct"] > 0,  # profitable on unseen data
            })

    # ── Print results ──
    if verbose:
        _print_results(train_results, valid_results, strategy, train_days, valid_days)

    # ── Apply best params ──
    if apply_best and valid_results:
        best_robust = [v for v in valid_results if v["robust"]]
        best = (best_robust or valid_results)[0]
        _apply_best_params(best, strategy, weights)

    # ── Save log ──
    _save_log(train_results, valid_results, strategy, start_date, end_date)

    return train_results


def _print_results(
    train_results: list[dict],
    valid_results: list[dict],
    strategy:      str,
    train_days:    list[date],
    valid_days:    list[date],
) -> None:
    print("\n" + "=" * 80)
    print(f"  OPTIMISER RESULTS  [{strategy.upper()}]")
    print("=" * 80)

    print(f"\n  TOP 15 on TRAINING SET  ({train_days[0]} → {train_days[-1]})")
    print(f"  ($10,000 start • 5%/trade • commission included)\n")
    print(f"  {'Stop':>6} {'TP':>6} {'Score':>6} {'Trades':>7} "
          f"{'TP Hit%':>8} {'EOD%':>6} {'$10k→':>10} {'Profit':>9} {'Sharpe':>7}")
    print("  " + "-" * 84)

    target_hit = False
    for r in train_results[:15]:
        end_val  = r.get("port_end_value",  10_000)
        profit   = r.get("port_profit",     0)
        ret_pct  = r.get("port_return_pct", 0)
        tp_rate  = r.get("tp_hit_rate",     0)
        eod_rate = r.get("eod_rate",        0)
        est      = r.get("annual_port_est", 0)

        # Signal quality label next to TP hit rate
        if tp_rate >= 25:  tp_flag = "✅"
        elif tp_rate >= 15: tp_flag = "⚡"
        else:               tp_flag = "❌"

        flag = " ← TARGET" if ret_pct > 0 and est >= 15 and not target_hit else ""
        if flag: target_hit = True

        sign = "+" if profit >= 0 else "-"
        print(f"  {r['stop_pct']*100:>5.1f}% {r['tp_pct']*100:>5.1f}% "
              f"{r['min_score']:>6.1f} {r['n_trades']:>7} "
              f"{tp_flag}{tp_rate:>5.0f}% "
              f"{eod_rate:>5.0f}% "
              f"${end_val:>8,.0f} "
              f"{sign}${abs(profit):>7,.0f} "
              f"{r['sharpe']:>7.2f}{flag}")

    # ── Walk-forward validation ──
    print(f"\n  WALK-FORWARD VALIDATION  ({valid_days[0]} → {valid_days[-1]})")
    print("  (Do the top training params still work on data they've never seen?)")
    print()
    print(f"  {'Stop':>6} {'TP':>6} {'MinScore':>9}  "
          f"{'Train $':>9}  {'Valid $':>9}  {'Robust?':>8}  Grade")
    print("  " + "-" * 72)

    for v in valid_results:
        tr    = v["train"]
        val   = v["valid"]
        ok    = "✓ YES" if v["robust"] else "✗ NO"
        tr_p  = tr.get("port_profit",  0)
        val_p = val.get("port_profit", 0)
        tr_s  = ("+" if tr_p  >= 0 else "") + f"${abs(tr_p):,.0f}"
        val_s = ("+" if val_p >= 0 else "-") + f"${abs(val_p):,.0f}"
        print(f"  {tr['stop_pct']*100:>5.1f}% {tr['tp_pct']*100:>5.1f}% "
              f"{tr['min_score']:>9.1f}  "
              f"{tr_s:>9}  "
              f"{val_s:>9}  {ok:>8}  {val['grade']}")

    robust = [v for v in valid_results if v["robust"]]

    print()
    if robust:
        best = robust[0]
        print("=" * 80)
        print("  RECOMMENDATION (profitable on BOTH training and validation)")
        print("=" * 80)
        tr  = best["train"]
        val = best["valid"]
        est = tr.get("annual_port_est", 0)
        tr_profit  = tr.get("port_profit",  0)
        val_profit = val.get("port_profit", 0)
        tr_end     = tr.get("port_end_value",  10_000)
        val_end    = val.get("port_end_value", 10_000)

        print(f"\n  Best robust setting:")
        print(f"    Stop-loss  :  -{best['stop_pct']*100:.1f}%")
        print(f"    Take-profit:  +{best['tp_pct']*100:.1f}%")
        print(f"    Min score  :   {best['min_score']:.1f}")
        print(f"\n  With $10,000 starting capital (5% per trade):")
        tr_sign  = "+" if tr_profit  >= 0 else ""
        val_sign = "+" if val_profit >= 0 else ""
        print(f"    Training period  : $10,000 → ${tr_end:,.0f}  "
              f"({tr_sign}${abs(tr_profit):,.0f})  WR {tr['win_rate']}%  Sharpe {tr['sharpe']:.2f}")
        print(f"    Validation period: $10,000 → ${val_end:,.0f}  "
              f"({val_sign}${abs(val_profit):,.0f})  WR {val['win_rate']}%  Sharpe {val['sharpe']:.2f}")
        print(f"\n  Estimated annual portfolio return: ~{est:+.1f}%")
        print()
        print(f"  Grade: {tr['grade']}")
        print()
        print("  To apply these settings, re-run with --apply-best flag")
        print("  or use option 3 (Backtest → Optimise) in the launcher.")
    else:
        print("=" * 80)
        print("  NOTE: No combo was profitable on BOTH training AND validation.")
        print("  The top training results overfit to that specific time period.")
        print()
        print("  What to try:")
        print("    - Test a different/longer date range (more data = more reliable)")
        print("    - Try a different strategy (mean_reversion, gap_and_go, breakout)")
        print("    - The market conditions in this period may not favour this strategy")
        print("=" * 80)

    print()
    _print_20pct_guide(train_results)


def _print_20pct_guide(results: list[dict]) -> None:
    """Dollar-focused guide: what does this strategy actually make on $10,000?"""
    best    = results[0] if results else {}
    est     = best.get("annual_port_est",  0)
    end_val = best.get("port_end_value",   10_000)
    profit  = best.get("port_profit",      0)
    wr      = best.get("win_rate",         0)

    print("─" * 80)
    print("  WHAT DOES THIS MEAN IN REAL MONEY?  ($10,000 account)")
    print("─" * 80)
    print()
    sign = "+" if profit >= 0 else ""
    print(f"  Best combo result:  $10,000  →  ${end_val:,.0f}  "
          f"({sign}${abs(profit):,.0f} over the test period)")
    print()

    # Annual projection
    if est > 0:
        annual_profit = 10_000 * est / 100
        print(f"  Annualised estimate: {est:+.1f}% per year  ≈  ${annual_profit:,.0f}/yr on $10k")
        if est >= 20:
            print(f"  ✅ Hits the 20% target!  That's +$2,000/yr on every $10,000 invested.")
        elif est >= 10:
            print(f"  ⚡ Decent — better than most savings accounts, but short of 20% target.")
            print(f"     Double-check on a longer date range before trusting this result.")
        else:
            print(f"  ⚠  Too small to be worth the risk and time. Keep optimising.")
    elif est == 0:
        print("  ⚠  Break-even is NOT profit. Fees, tax, and your time make this a loss.")
    else:
        print(f"  ✗ LOSING money: {est:.1f}%/yr = -${abs(10_000 * est / 100):,.0f}/yr on $10k.")
        print("     Do not trade this strategy as-is.")

    print()
    print("  To get to +$2,000/yr on $10,000 you need:")
    print("    • Win rate ≥ 45%  AND  avg win ≥ 2× avg loss")
    print("    • OR: win rate ≥ 35%  AND  avg win ≥ 3× avg loss (tight stops)")
    print()
    print("  What to try next:")
    print("    1. Run 'Compare all strategies' (Backtest → option 2)")
    print("       mean_reversion often outperforms in the same period")
    print("    2. HIGHER MIN SCORE (6.5+) → trade only your best setups")
    print("    3. LONGER DATE RANGE → 2 years of data gives more reliable numbers")
    print("─" * 80)
    print()


# ── Apply best params ─────────────────────────────────────────────────────────

def _apply_best_params(best: dict, strategy: str, weights: dict) -> None:
    """
    Write the best-found stop/TP/min_score into scoring_weights.json
    so the bot uses them going forward.
    """
    stop  = best["stop_pct"]
    tp    = best["tp_pct"]
    score = best["min_score"]

    weights.setdefault("strategy_params", {})
    weights["strategy_params"][strategy] = {
        "stop_pct":  stop,
        "tp_pct":    tp,
        "min_score": score,
        "set_by":    "optimizer",
        "set_at":    datetime.now().isoformat(),
    }
    weights["last_updated"] = datetime.now().isoformat()

    with open(_WEIGHTS_PATH, "w") as f:
        json.dump(weights, f, indent=2)

    tr  = best.get("train", {})
    print(f"\n  ✅ Best params applied to memory/scoring_weights.json")
    print(f"     Strategy  : {strategy}")
    print(f"     Stop-loss : -{stop*100:.1f}%")
    print(f"     Take-profit: +{tp*100:.1f}%")
    print(f"     Min score : {score:.1f}")
    print(f"     Training P&L: {tr.get('cum_pnl_pct', 0):+.1f}%  "
          f"WR: {tr.get('win_rate', 0):.1f}%\n")


def _save_log(
    train_results: list[dict],
    valid_results: list[dict],
    strategy:      str,
    start_date:    str,
    end_date:      str,
) -> None:
    """Save optimizer results to memory for the report viewer."""
    entry = {
        "run_at":    datetime.now().isoformat(),
        "strategy":  strategy,
        "start":     start_date,
        "end":       end_date,
        "top_train": train_results[:5],
        "validation": [
            {k: v for k, v in r.items() if k != "train"}
            for r in valid_results
        ],
    }
    # Append to log
    log = []
    try:
        with open(_OPT_LOG_PATH) as f:
            log = json.load(f)
    except Exception:
        pass
    log.append(entry)
    with open(_OPT_LOG_PATH, "w") as f:
        json.dump(log[-20:], f, indent=2)  # keep last 20 runs
