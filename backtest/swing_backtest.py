"""
backtest/swing_backtest.py
Multi-day (swing) backtest using existing daily OHLC bars.

Why swing beats intraday for trend-following strategies
--------------------------------------------------------
Intraday problem: you have 6 hours, a 1-2% target, and the opening 15 minutes
are pure chaos -- HFT algos, wide spreads, institutional order imbalances.
Even a perfect setup gets stopped out by noise before the move develops.

Swing solution:
  - Enter at today's open (same morning scan, same signals)
  - Hold up to 5 trading days -- ride the actual trend
  - 5% stop on a 10% target makes commission (0.1%) irrelevant
  - Breakeven WR drops from ~46% (intraday) to ~40% (swing)
  - No opening-hour noise, no 5-min bar data needed

Which strategies work best for swing:
  momentum      -- stocks with strong upward momentum continue for days
  breakout      -- fresh 52-week high breakouts often run 5-15% over 3-7 days
  gap_and_go    -- large premarket gaps that hold tend to trend for 2-3 days
  mean_reversion-- oversold bounces often take 2-3 days to fully play out

Usage:
    python run_day.py --swing-backtest 2020-01-01 2026-01-01
    python run_day.py --swing-backtest 2020-01-01 2026-01-01 --strategy breakout
    python run_day.py --swing-optimize 2020-01-01 2026-01-01 --strategy all
"""

from __future__ import annotations

import os
import sys
import time
import json
import warnings
warnings.filterwarnings("ignore")

import pandas as pd
import numpy as np
from datetime import datetime, date, timedelta
from collections import defaultdict

_BASE        = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_RESULTS_DIR = os.path.join(_BASE, "memory", "swing_results")
_WEIGHTS_PATH= os.path.join(_BASE, "memory", "scoring_weights.json")

# ── Trade parameters ───────────────────────────────────────────────────────────
STARTING_CAPITAL = 10_000
POSITION_SIZE    = 0.10    # 10% per trade -- fewer, larger positions than intraday
COMMISSION       = 0.001   # 0.1% round-trip (less relevant at 5-15% targets)

# Per-strategy defaults -- wider than intraday to match multi-day volatility
SWING_PARAMS = {
    "momentum": {
        "stop": 0.05, "tp": 0.10, "trail": 0.05, "max_hold": 5,
    },
    "breakout": {
        "stop": 0.05, "tp": 0.12, "trail": 0.05, "max_hold": 5,
    },
    "gap_and_go": {
        "stop": 0.05, "tp": 0.10, "trail": 0.05, "max_hold": 3,
    },
    "mean_reversion": {
        "stop": 0.05, "tp": 0.08, "trail": 0.04, "max_hold": 5,
    },
}

MIN_TECH_SCORE    = 5.5
_TREND_STRATEGIES = {"momentum", "gap_and_go", "breakout"}

# ── Auto-load best params if optimizer was ever run with --apply-best ──────────
_BEST_PARAMS_PATH = os.path.join(_BASE, "memory", "swing_best_params.json")
if os.path.exists(_BEST_PARAMS_PATH):
    try:
        with open(_BEST_PARAMS_PATH) as _f:
            for _strat, _p in json.load(_f).items():
                if _strat in SWING_PARAMS:
                    SWING_PARAMS[_strat].update(_p)
    except Exception:
        pass


# ── Helpers ────────────────────────────────────────────────────────────────────

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


# ── Single-trade swing simulation ─────────────────────────────────────────────

def _simulate_swing_trade(
    df_future:     pd.DataFrame,
    entry_price:   float,
    stop_pct:      float,
    tp_pct:        float,
    trail_pct:     float,
    max_hold_days: int,
) -> dict:
    """
    Simulate one swing trade on daily OHLC bars.

    Entry    : entry_price (open of signal day)
    Stop     : fixed initially, then trails upward as highs are made
    TP       : fixed take-profit
    Max hold : exit at close after max_hold_days if nothing triggered

    Within-bar logic (conservative):
      1. If the day's Low <= current stop   → stop hit, exit at stop
      2. Else if the day's High >= TP       → TP hit, exit at TP
      3. Otherwise                          → hold another day

    Trailing stop:
      After each day's High, stop = max(stop, High x (1 - trail_pct))
      Stop can only move UP -- locks in gains as price rises.
    """
    if df_future.empty:
        return {}

    stop    = entry_price * (1 - stop_pct)
    tp      = entry_price * (1 + tp_pct)
    highest = entry_price

    bars = df_future.head(max_hold_days)

    for i, (ts, row) in enumerate(bars.iterrows()):
        high  = float(row.get("High",  row.get("high",  entry_price)))
        low   = float(row.get("Low",   row.get("low",   entry_price)))
        close = float(row.get("Close", row.get("close", entry_price)))

        # Update trailing stop on new intraday high
        if high > highest:
            highest   = high
            new_trail = highest * (1 - trail_pct)
            stop      = max(stop, new_trail)

        # Stop loss first (worst case)
        if low <= stop:
            pnl = (stop / entry_price - 1) * 100 - COMMISSION * 100
            # Distinguish a profitable trailing exit from a real loss
            exit_label = "trail_profit" if stop > entry_price else "stop_loss"
            return {
                "pnl_pct":     round(pnl, 3),
                "exit_reason": exit_label,
                "hold_days":   i + 1,
                "exit_date":   str(ts.date() if hasattr(ts, "date") else ts),
            }

        # Take profit
        if high >= tp:
            pnl = (tp / entry_price - 1) * 100 - COMMISSION * 100
            return {
                "pnl_pct":     round(pnl, 3),
                "exit_reason": "take_profit",
                "hold_days":   i + 1,
                "exit_date":   str(ts.date() if hasattr(ts, "date") else ts),
            }

    # Max hold days reached -- exit at last bar's close
    last       = bars.iloc[-1]
    last_close = float(last.get("Close", last.get("close", entry_price)))
    pnl        = (last_close / entry_price - 1) * 100 - COMMISSION * 100
    last_ts    = bars.index[-1]
    return {
        "pnl_pct":     round(pnl, 3),
        "exit_reason": "max_hold",
        "hold_days":   len(bars),
        "exit_date":   str(last_ts.date() if hasattr(last_ts, "date") else last_ts),
    }


# ── Daily candidate scan ───────────────────────────────────────────────────────

def _precompute_features_cache(
    hist_data:    dict[str, pd.DataFrame],
    trading_days: list[date],
) -> dict[str, dict]:
    """
    Precompute technical features for every (ticker, trading_day) pair once.

    Returns {ticker: {date: features_dict}}

    ~40x faster than computing inside the day-loop because:
      - Rolling window ops (RSI, MAs, returns) run once per ticker as vectorised
        pandas operations over the full series.
      - The simulation loop then does a plain dict lookup per (ticker, date).

    No lookahead: features for day D use data strictly before D (close of D-1).
    """
    from scanner.universe import get_sector

    def _safe(val: float, default: float = 0.0) -> float:
        try:
            f = float(val)
            return default if (np.isnan(f) or np.isinf(f)) else f
        except Exception:
            return default

    cache: dict[str, dict] = {}

    for ticker, df in hist_data.items():
        if ticker in ("SPY", "^VIX") or len(df) < 20:
            continue
        try:
            closes  = df["Close"].astype(float)
            volumes = df["Volume"].astype(float)
            opens   = (df["Open"] if "Open" in df.columns else closes).astype(float)

            # ── Rolling series (all causal — each value uses only past data) ──
            delta   = closes.diff()
            gain    = delta.clip(lower=0).ewm(com=13, adjust=False).mean()
            loss    = (-delta.clip(upper=0)).ewm(com=13, adjust=False).mean()
            rsi_s   = 100 - (100 / (1 + gain / loss.replace(0, np.nan)))

            ma20_s  = closes.rolling(min(20,  len(closes))).mean()
            ma50_s  = closes.rolling(min(50,  len(closes))).mean()
            h52_s   = closes.rolling(min(252, len(closes))).max()
            l52_s   = closes.rolling(min(252, len(closes))).min()

            # shift(1): at index i, avg_vol = mean of volumes[i-20 .. i-1]
            # matches build_stock_features: volumes.tail(21).iloc[:-1].mean()
            avgvol_s = volumes.shift(1).rolling(20).mean()

            ret1_s  = closes.pct_change(1).mul(100)
            ret5_s  = closes.pct_change(5).mul(100)
            ret20_s = closes.pct_change(20).mul(100)

            sector = get_sector(ticker)
            ticker_cache: dict = {}

            for day in trading_days:
                ts = pd.Timestamp(day)

                # prev_pos: last index strictly before this day
                prev_pos = int(df.index.searchsorted(ts, side="left")) - 1
                if prev_pos < 14:
                    continue

                # today_pos: the row for this exact trading day (must exist)
                today_pos = int(df.index.searchsorted(ts, side="left"))
                if today_pos >= len(df) or df.index[today_pos].date() != day:
                    continue

                rsi_val = rsi_s.iloc[prev_pos]
                ma20_val = ma20_s.iloc[prev_pos]
                if pd.isna(rsi_val) or pd.isna(ma20_val):
                    continue

                price  = float(closes.iloc[prev_pos])
                rsi    = float(rsi_val)
                ma20   = float(ma20_val)
                ma50   = _safe(ma50_s.iloc[prev_pos],  default=price)
                h52    = _safe(h52_s.iloc[prev_pos],   default=price)
                l52    = _safe(l52_s.iloc[prev_pos],   default=price)
                av     = _safe(avgvol_s.iloc[prev_pos], default=1.0)
                if av <= 0:
                    av = 1.0
                rv     = round(_safe(volumes.iloc[prev_pos]) / av, 2)

                t_open = _safe(opens.iloc[today_pos], default=price)
                pm_pct = round((t_open / price - 1) * 100, 2) if price > 0 else 0.0

                ticker_cache[day] = {
                    "ticker":            ticker,
                    "price":             round(price, 2),
                    "rsi":               round(rsi,   2),
                    "ma20":              round(ma20,  2),
                    "ma50":              round(ma50,  2),
                    "above_ma20_pct":    round((price / ma20 - 1) * 100, 2) if ma20 else 0.0,
                    "above_ma50_pct":    round((price / ma50 - 1) * 100, 2) if ma50 else 0.0,
                    "pct_from_52w_high": round((price / h52  - 1) * 100, 2) if h52  else -50.0,
                    "pct_from_52w_low":  round((price / l52  - 1) * 100, 2) if l52  else 0.0,
                    "ret_1d":            round(_safe(ret1_s.iloc[prev_pos]),  2),
                    "ret_5d":            round(_safe(ret5_s.iloc[prev_pos]),  2),
                    "ret_20d":           round(_safe(ret20_s.iloc[prev_pos]), 2),
                    "avg_volume":        int(av),
                    "rel_volume":        rv,
                    "premarket_pct":     pm_pct,
                    "premarket_price":   round(t_open, 2),
                    "sector":            sector,
                    "_prev_close":       round(price,  4),
                    "_today_open":       round(t_open, 4),
                }

            if ticker_cache:
                cache[ticker] = ticker_cache

        except Exception:
            pass   # skip tickers with data gaps or type issues

    return cache


def _scan_day_candidates(
    target_date:    date,
    hist_data:      dict,
    weights:        dict,
    strategy:       str,
    n_picks:        int,
    market_up:      bool,
    features_cache: dict | None = None,
) -> list[dict]:
    """
    Identify top-N swing candidates for one trading day.

    features_cache: when provided, use precomputed {ticker: {date: feat}} instead
    of calling build_stock_features from scratch — ~40x faster for backtests.
    """
    if strategy in _TREND_STRATEGIES and not market_up:
        return []

    from scanner.scanner import technical_score, passes_filter
    from backtest.backtest import (
        _select_momentum, _select_mean_reversion,
        _select_gap_and_go, _select_breakout,
    )
    from backtest.filters import apply_sector_limit

    selector_map = {
        "momentum":       _select_momentum,
        "mean_reversion": _select_mean_reversion,
        "gap_and_go":     _select_gap_and_go,
        "breakout":       _select_breakout,
    }
    selector = selector_map.get(strategy, _select_momentum)
    reverse  = strategy != "mean_reversion"

    candidates = []

    if features_cache is not None:
        # ── Fast path: precomputed features (plain dict lookup) ───────────
        for ticker, day_map in features_cache.items():
            feat = day_map.get(target_date)
            if feat is None:
                continue
            feat = dict(feat)   # shallow copy — don't mutate the cache

            ok, _ = passes_filter(feat)
            if not ok:
                continue

            feat["tech_score"] = float(technical_score(feat, weights))
            if feat["tech_score"] < MIN_TECH_SCORE:
                continue
            if not selector(feat):
                continue

            candidates.append(feat)

    else:
        # ── Slow path: recompute features from scratch (kept for compat) ──
        from scanner.data import build_stock_features
        from scanner.universe import get_sector

        for ticker, full_df in hist_data.items():
            if ticker in ("SPY", "^VIX"):
                continue
            df_before = full_df[full_df.index.date < target_date]
            df_today  = full_df[full_df.index.date == target_date]
            if len(df_before) < 15 or df_today.empty:
                continue

            feat = build_stock_features(ticker, df_before)
            if feat is None:
                continue

            ok, _ = passes_filter(feat)
            if not ok:
                continue

            prev_close = float(df_before["Close"].iloc[-1])
            today_open = (float(df_today["Open"].iloc[0])
                          if "Open" in df_today.columns else prev_close)
            feat["premarket_pct"] = round((today_open / prev_close - 1) * 100, 2)
            feat["tech_score"]    = float(technical_score(feat, weights))

            if feat["tech_score"] < MIN_TECH_SCORE:
                continue
            if not selector(feat):
                continue

            feat["sector"]      = get_sector(ticker)
            feat["_prev_close"] = prev_close
            feat["_today_open"] = today_open
            candidates.append(feat)

    if not candidates:
        return []

    sort_keys = {
        "momentum":       lambda f: f["tech_score"],
        "mean_reversion": lambda f: f["ret_1d"],        # most negative first
        "gap_and_go":     lambda f: f["premarket_pct"],
        "breakout":       lambda f: f["pct_from_52w_high"],
    }
    candidates.sort(
        key=sort_keys.get(strategy, lambda f: f["tech_score"]),
        reverse=reverse,
    )

    return apply_sector_limit(candidates, max_per_sector=1, n_picks=n_picks)


# ── Compile stats ──────────────────────────────────────────────────────────────

def _compile_results(
    trades:       list[dict],
    trading_days: list[date],
    n_picks:      int,
) -> dict:
    if not trades:
        return {}

    wins   = [t for t in trades if t["pnl_pct"] > 0]
    losses = [t for t in trades if t["pnl_pct"] <= 0]
    n      = len(trades)
    wr     = len(wins) / n * 100

    avg_win  = sum(t["pnl_pct"] for t in wins)   / max(len(wins),   1)
    avg_loss = sum(t["pnl_pct"] for t in losses) / max(len(losses), 1)
    bkev     = (abs(avg_loss) / (avg_win + abs(avg_loss)) * 100
                if avg_win > 0 else 50.0)

    exits = defaultdict(int)
    for t in trades:
        exits[t["exit_reason"]] += 1

    hold_days_all = [t.get("hold_days", 0) for t in trades]
    avg_hold      = sum(hold_days_all) / max(len(hold_days_all), 1)

    # ── Portfolio simulation with year/month milestones ───────────────────
    by_date: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for t in trades:
        size = t.get("pos_size", POSITION_SIZE)
        by_date[t["entry_date"]].append((t["pnl_pct"], size))

    portfolio  = STARTING_CAPITAL
    year_start: dict[str, float] = {}
    month_start: dict[str, float] = {}
    year_end:   dict[str, float] = {}
    month_end:  dict[str, float] = {}
    prev_y = prev_ym = None

    for day in trading_days:
        y  = str(day.year)
        ym = day.strftime("%Y-%m")
        if y  != prev_y:  year_start[y]   = portfolio; prev_y  = y
        if ym != prev_ym: month_start[ym] = portfolio; prev_ym = ym
        for pnl, size in by_date.get(day.isoformat(), []):
            portfolio += portfolio * size * (pnl / 100)
        year_end[y]   = portfolio
        month_end[ym] = portfolio

    port_return  = (portfolio / STARTING_CAPITAL - 1) * 100
    n_years      = len(trading_days) / 252.0
    cagr         = ((portfolio / STARTING_CAPITAL) ** (1.0 / max(n_years, 0.01)) - 1) * 100
    avg_daily    = port_return / max(len(trading_days), 1)

    # ── Sharpe ────────────────────────────────────────────────────────────
    daily_pnl: dict[str, float] = defaultdict(float)
    for t in trades:
        daily_pnl[t["entry_date"]] += t["pnl_pct"] / max(n_picks, 1)
    daily_pnls = list(daily_pnl.values())
    _m = np.mean(daily_pnls)
    _s = np.std(daily_pnls, ddof=1) if len(daily_pnls) > 1 else 0.0
    sharpe = float(_m / _s * np.sqrt(252)) if _s > 0 else 0.0

    # ── Per-year breakdown ────────────────────────────────────────────────
    trades_by_year: dict[str, list] = defaultdict(list)
    for t in trades:
        trades_by_year[t["entry_date"][:4]].append(t)

    by_year: dict[str, dict] = {}
    for y, yt in sorted(trades_by_year.items()):
        yw = [t for t in yt if t["pnl_pct"] > 0]
        yl = [t for t in yt if t["pnl_pct"] <= 0]
        s_p = year_start.get(y, STARTING_CAPITAL)
        e_p = year_end.get(y, s_p)
        by_year[y] = {
            "n":        len(yt),
            "wins":     len(yw),
            "losses":   len(yl),
            "wr":       round(len(yw) / len(yt) * 100, 1),
            "avg_win":  round(sum(t["pnl_pct"] for t in yw) / max(len(yw), 1), 2),
            "avg_loss": round(sum(t["pnl_pct"] for t in yl) / max(len(yl), 1), 2),
            "ret":      round((e_p / s_p - 1) * 100, 1),
            "cap_end":  round(e_p),
        }

    # ── Per-month breakdown ───────────────────────────────────────────────
    trades_by_month: dict[str, list] = defaultdict(list)
    for t in trades:
        trades_by_month[t["entry_date"][:7]].append(t)

    by_month: dict[str, dict] = {}
    for ym, mt in sorted(trades_by_month.items()):
        mw = [t for t in mt if t["pnl_pct"] > 0]
        s_p = month_start.get(ym, STARTING_CAPITAL)
        e_p = month_end.get(ym, s_p)
        by_month[ym] = {
            "n":   len(mt),
            "wr":  round(len(mw) / len(mt) * 100, 1),
            "ret": round((e_p / s_p - 1) * 100, 1),
        }

    return {
        "n_trades":     n,
        "n_wins":       len(wins),
        "n_losses":     len(losses),
        "win_rate":     round(wr,        1),
        "breakeven":    round(bkev,      1),
        "avg_win_pct":  round(avg_win,   3),
        "avg_loss_pct": round(avg_loss,  3),
        "trail_rate":   round(exits["trail_profit"] / n * 100, 1),
        "tp_rate":      round(exits["take_profit"]  / n * 100, 1),
        "stop_rate":    round(exits["stop_loss"]    / n * 100, 1),
        "maxhold_rate": round(exits["max_hold"]     / n * 100, 1),
        "avg_hold_days":round(avg_hold,  1),
        "sharpe":       round(sharpe,    2),
        "port_return":  round(port_return, 2),
        "port_end":     round(portfolio,   2),
        "cagr":         round(cagr,        1),
        "avg_daily_pct":round(avg_daily,   4),
        "by_year":      by_year,
        "by_month":     by_month,
    }


# ── Print results ──────────────────────────────────────────────────────────────

def _print_results(
    r:          dict,
    strategy:   str,
    start_date: str,
    end_date:   str,
    p:          dict,
) -> None:
    W     = 74
    DIV   = "-" * W
    HDIV  = "=" * W
    MONTHS = ["Jan","Feb","Mar","Apr","May","Jun",
              "Jul","Aug","Sep","Oct","Nov","Dec"]

    n_picks = r.get("n_picks", 3)

    # ── Header ────────────────────────────────────────────────────────────
    print()
    print(HDIV)
    title  = f"  SWING BACKTEST  [ {strategy.upper()} ]"
    period = f"{start_date}  to  {end_date}  "
    print(f"{title:<{W//2}}{period:>{W//2}}")
    params_str = (f"  Stop {p['stop']*100:.0f}%  TP {p['tp']*100:.0f}%  "
                  f"Trail {p['trail']*100:.0f}%  Hold {p['max_hold']}d")
    sizing_str = f"{POSITION_SIZE*100:.0f}%/trade   {n_picks} picks/day  "
    print(f"{params_str:<{W//2}}{sizing_str:>{W//2}}")
    print(HDIV)

    # ── Returns ───────────────────────────────────────────────────────────
    profit = r["port_end"] - STARTING_CAPITAL
    print()
    print(f"  RETURNS")
    print(f"  ${STARTING_CAPITAL:,.0f}  -->  ${r['port_end']:,.0f}"
          f"    {r['port_return']:>+.1f}%"
          f"    CAGR {r.get('cagr', 0):+.1f}%/yr"
          f"    {r.get('avg_daily_pct', 0):+.3f}%/day")

    # ── Edge ──────────────────────────────────────────────────────────────
    edge_pp = r["win_rate"] - r["breakeven"]
    rr      = abs(r["avg_win_pct"] / r["avg_loss_pct"]) if r["avg_loss_pct"] else 0.0
    print()
    print(f"  EDGE")
    print(f"  {r['n_trades']:,} trades"
          f"    {r.get('n_wins', '?')} wins  {r.get('n_losses', '?')} losses"
          f"    Win {r['win_rate']:.1f}%"
          f"    Breakeven {r['breakeven']:.1f}%"
          f"    Edge {edge_pp:+.1f} pp")
    print(f"  Avg win {r['avg_win_pct']:>+.2f}%"
          f"    Avg loss {r['avg_loss_pct']:>+.2f}%"
          f"    Reward:risk {rr:.1f}:1"
          f"    Hold {r['avg_hold_days']:.1f}d")
    print(f"  Sharpe {r['sharpe']:.2f}"
          f"    Trail {r.get('trail_rate', 0):.0f}%"
          f"    Stop {r.get('stop_rate', 0):.0f}%"
          f"    TP {r.get('tp_rate', 0):.0f}%"
          f"    Max-hold {r.get('maxhold_rate', 0):.0f}%")

    # ── Year by year ──────────────────────────────────────────────────────
    by_year = r.get("by_year", {})
    if by_year:
        print()
        print(f"  YEAR BY YEAR")
        print(f"  {DIV}")
        print(f"  {'Year':<6}  {'Trades':>6}  {'W':>4}  {'L':>4}  "
              f"{'Win%':>5}  {'Avg W':>6}  {'Avg L':>6}  "
              f"{'Return':>8}  {'Capital':>10}")
        print(f"  {DIV}")
        for y, s in sorted(by_year.items()):
            print(f"  {y:<6}  {s['n']:>6,}  {s['wins']:>4}  {s['losses']:>4}  "
                  f"{s['wr']:>4.0f}%  "
                  f"{s['avg_win']:>+5.2f}%  {s['avg_loss']:>+5.2f}%  "
                  f"  {s['ret']:>+6.1f}%  ${s['cap_end']:>9,.0f}")
        print(f"  {DIV}")

    # ── Month by month grid ───────────────────────────────────────────────
    by_month = r.get("by_month", {})
    if by_month:
        years = sorted({ym[:4] for ym in by_month})
        # header row
        hdr = "  Year  " + "".join(f"  {m:>5}" for m in MONTHS)
        print()
        print(f"  MONTH BY MONTH  (portfolio return %)")
        print(f"  {'-' * (len(hdr) - 2)}")
        print(hdr)
        print(f"  {'-' * (len(hdr) - 2)}")
        for y in years:
            row = f"  {y}  "
            for mo in range(1, 13):
                ym = f"{y}-{mo:02d}"
                md = by_month.get(ym)
                if md:
                    row += f"  {md['ret']:>+5.1f}"
                else:
                    row += "     --"
            print(row)
        print(f"  {'-' * (len(hdr) - 2)}")

    # ── Verdict ───────────────────────────────────────────────────────────
    ret = r["port_return"]
    wr  = r["win_rate"]
    bk  = r["breakeven"]
    print()
    print(HDIV)
    if ret >= 20:
        verdict = "STRONG"
    elif ret >= 5:
        verdict = "OK"
    elif ret >= 0:
        verdict = "MARGINAL"
    else:
        verdict = "LOSING"
    print(f"  [{verdict}]"
          f"  {ret:+.1f}%"
          f"  --  WR {wr:.1f}%"
          f"  --  Breakeven {bk:.1f}%"
          f"  --  Edge {wr - bk:+.1f} pp")
    print(HDIV)
    print()


# ── Save results ───────────────────────────────────────────────────────────────

def _save_results(
    results: dict,
    trades:  list[dict],
    strategy: str,
) -> None:
    os.makedirs(_RESULTS_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    rp = os.path.join(_RESULTS_DIR, f"swing_{strategy}_{ts}.json")
    tp = os.path.join(_RESULTS_DIR, f"swing_trades_{strategy}_{ts}.json")
    with open(rp, "w") as f:
        json.dump(results, f, indent=2, default=str)
    with open(tp, "w") as f:
        json.dump(trades,  f, indent=2, default=str)
    print(f"  Saved to memory/swing_results/")


# ── Main entry point ───────────────────────────────────────────────────────────

def run_swing_backtest(
    start_date: str,
    end_date:   str,
    strategy:   str  = "momentum",
    n_picks:    int  = 3,
    verbose:    bool = True,
    params:     dict | None = None,
) -> dict:
    """
    Full swing backtest pipeline. Uses daily OHLC only -- no Alpaca 5-min data needed.

    Steps:
      1. Download daily bars (same source as daily backtest, already cached)
      2. Build market regime filter (SPY vs 50-day MA)
      3. Scan each day for top-N candidates using daily features
      4. Enter at today's open -- same signal as daily backtest
      5. Simulate holding up to max_hold_days with stop/TP/trailing stop
      6. Compile stats and print results
    """
    from backtest.backtest import _download_hist_data, _build_market_regime, _build_vix_regime, _pbar
    from scanner.universe import get_all_tickers

    if strategy not in SWING_PARAMS:
        print(f"  Unknown strategy '{strategy}' -- defaulting to momentum")
        strategy = "momentum"

    p = {**SWING_PARAMS[strategy], **(params or {})}

    trading_days = _get_trading_days(start_date, end_date)
    if not trading_days:
        print("  No trading days in range.")
        return {}

    tickers = get_all_tickers()
    weights = _load_weights()

    # ── Phase 1: Download data ─────────────────────────────────────────────
    t_total = time.time()
    hist_data = _download_hist_data(trading_days, tickers, verbose=verbose)

    market_regime = _build_market_regime(hist_data, trading_days)
    vix_regime    = _build_vix_regime(hist_data, trading_days)

    if verbose:
        n_tickers = len(hist_data) - sum(1 for k in hist_data if k in ("SPY", "^VIX"))
        bull      = sum(1 for v in market_regime.values() if v)
        high_vix  = sum(1 for v in vix_regime.values() if v > 25)
        print(f"  {n_tickers} tickers  --  "
              f"{bull} bull / {len(trading_days) - bull} bear days  --  "
              f"{high_vix} high-VIX days (position halved)")

    # ── Phase 2: Precompute features (vectorised — done once per ticker) ──
    if verbose:
        print("  Pre-computing features...", end="", flush=True)
    t_pre   = time.time()
    f_cache = _precompute_features_cache(hist_data, trading_days)
    if verbose:
        pre_secs = time.time() - t_pre
        print(f" {len(f_cache)} tickers in {pre_secs:.1f}s")

    # ── Phase 3: Simulate trades (fast — dict lookups only) ───────────────
    all_trades       = []
    days_with_trades = 0
    n_days           = len(trading_days)
    t_sim            = time.time()

    for day_idx, day in enumerate(trading_days):

        if verbose and day_idx % 50 == 0:
            print(_pbar(day_idx, n_days, t_sim, "  Simulating"), end="", flush=True)

        market_up = market_regime.get(day, True)
        vix_today = vix_regime.get(day, 20.0)

        # VIX-adjusted position sizing:
        #   VIX < 25  → full position (POSITION_SIZE)
        #   VIX 25-35 → half position  (elevated fear)
        #   VIX > 35  → skip momentum (extreme fear / market panic)
        if vix_today > 35 and strategy in _TREND_STRATEGIES:
            continue
        pos_size_today = (POSITION_SIZE * 0.5 if vix_today > 25 else POSITION_SIZE)

        cands = _scan_day_candidates(
            day, hist_data, weights, strategy, n_picks, market_up,
            features_cache=f_cache,
        )
        if not cands:
            continue
        days_with_trades += 1

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
                p["stop"], p["tp"], p["trail"], p["max_hold"],
            )
            if not result:
                continue

            all_trades.append({
                "ticker":       ticker,
                "entry_date":   day.isoformat(),
                "entry_price":  round(entry_price, 3),
                "strategy":     strategy,
                "pos_size":     round(pos_size_today, 4),
                "vix_at_entry": vix_today,
                **result,
            })

    if verbose:
        sim_secs = time.time() - t_sim
        print(_pbar(n_days, n_days, t_sim, "  Simulating"), end="", flush=True)
        print(f"\r  Simulation done in {sim_secs:.1f}s" + " " * 30)

    if not all_trades:
        print("  No trades generated.")
        return {}

    if verbose:
        print(f"  {len(all_trades):,} trades across {days_with_trades} active days")

    # ── Phase 3: Compile and display ───────────────────────────────────────
    t_compile = time.time()
    results = _compile_results(all_trades, trading_days, n_picks)
    results.update({
        "strategy":   strategy,
        "start_date": start_date,
        "end_date":   end_date,
        "n_picks":    n_picks,
        **p,
    })

    if verbose:
        total_secs = time.time() - t_total
        _print_results(results, strategy, start_date, end_date, p)
        print(f"  Total runtime: {total_secs:.0f}s")
        _save_results(results, all_trades, strategy)

    return results
