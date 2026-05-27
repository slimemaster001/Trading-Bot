"""
backtest/backtest.py
Historical simulation of the day-trading strategy.

Strategies supported (pass --strategy <name>):
  momentum      (default) — scan for strong technical setups, ride momentum
  mean_reversion          — buy yesterday's big losers expecting a bounce
  gap_and_go              — only stocks with >2.5% open gap AND high relative volume
  breakout                — stocks consolidating near 52-week high, breaking out

Run a single strategy:
    python run_day.py --backtest 2025-01-01 2026-01-01 --strategy momentum

Compare all 4 strategies on the same date range (downloads data once):
    python run_day.py --backtest 2025-01-01 2026-01-01 --compare

After backtesting, run learning to let the bot improve from simulated results:
    python run_day.py --learn
"""

from __future__ import annotations

import glob
import json
import os
import time
import warnings
warnings.filterwarnings("ignore")

import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, date


def _pbar(done: int, total: int, t0: float, label: str = "", width: int = 22) -> str:
    """
    Build a compact in-place progress bar string.
    Use with print(..., end='', flush=True) and \\r to overwrite.

    Example:  Downloading  [===============       ]  5/7 batches  42s
    """
    pct    = done / max(total, 1)
    filled = int(width * pct)
    bar    = "=" * filled + " " * (width - filled)
    elapsed = time.time() - t0
    suffix = f"{done}/{total}   {elapsed:.0f}s"
    return f"\r  {label}  [{bar}]  {suffix}  "

from scanner.universe import get_all_tickers
from scanner.data import build_stock_features, calc_rsi
from scanner.scanner import technical_score, passes_filter, TOP_N_FOR_AI

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_WEIGHTS_PATH = os.path.join(_BASE, "memory", "scoring_weights.json")

MAX_PICKS       = 3
MIN_TECH_SCORE  = 5.5   # don't trade anything scoring below this — avoids weak setups

# ── Paper-trading account settings ────────────────────────────────────────────
STARTING_CAPITAL  = 10_000   # $10,000 starting balance — change to whatever you like
POSITION_SIZE_PCT = 0.05     # 5% of portfolio per trade (so 3 picks = 15% deployed)
COMMISSION_PCT    = 0.001    # 0.1% round-trip cost per trade (spread + slippage)
                             # This makes EOD exits at "flat" actually cost money,
                             # which is realistic and penalises weak setups naturally.


# ── Strategy definitions ───────────────────────────────────────────────────────
# Each strategy defines its own stop/TP levels and candidate selection logic.
#
# Stop/TP — calibrated to what DAILY OHLC bars can actually achieve.
# Key insight from backtesting: 5-6% TPs are almost never hit in a single day
# on large/mid-cap stocks.  Realistic intraday moves are 1.5-3.5%.
#
#   momentum:       1.0% stop / 2.5% TP  → R:R 2.5:1,  break-even WR = 29%
#   mean_reversion: 2.5% stop / 3.5% TP  → R:R 1.4:1,  break-even WR = 42%
#                   (we observed 46% WR → should be profitable once TP is reachable)
#   gap_and_go:     1.2% stop / 3.0% TP  → R:R 2.5:1,  break-even WR = 29%
#   breakout:       1.2% stop / 3.5% TP  → R:R 2.9:1,  break-even WR = 26%

STRATEGIES = {
    "momentum": {
        "desc":    "Strong RSI, 1%+ gap, above both MAs — confirmed trend continuation",
        "stop":    0.010,  # 1.0% — if the gap-up fails quickly, exit fast
        "tp":      0.025,  # 2.5% — achievable intraday; needs only 29% WR
    },
    "mean_reversion": {
        "desc":    "Buy yesterday's -3%+ losers that are oversold — expect a bounce today",
        "stop":    0.025,  # 2.5% — tighter than before; if it keeps falling, cut it
        "tp":      0.035,  # 3.5% — realistic 1-day bounce target; backtests show 46% WR
    },                     #        which easily covers the 42% break-even
    "gap_and_go": {
        "desc":    "Clean 2.5-8% gap with strong volume, in uptrend — ride the momentum",
        "stop":    0.012,  # 1.2% — gap plays fail fast; cut quickly
        "tp":      0.030,  # 3.0% — quick target; gaps that hold 30min often close at +3%
    },
    "breakout": {
        "desc":    "Near 52-week high, above both MAs, rising volume — about to break out",
        "stop":    0.012,  # 1.2% — false breakouts reverse immediately; tight exit
        "tp":      0.035,  # 3.5% — real breakouts push through; 2.9:1 R:R
    },
}


def _load_weights() -> dict:
    try:
        with open(_WEIGHTS_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _get_trading_days(start: str, end: str) -> list[date]:
    """Return Mon–Fri trading days between start and end (YYYY-MM-DD strings)."""
    s = datetime.strptime(start, "%Y-%m-%d").date()
    e = datetime.strptime(end,   "%Y-%m-%d").date()
    days = []
    cur  = s
    while cur <= e:
        if cur.weekday() < 5:
            days.append(cur)
        cur += timedelta(days=1)
    return days


# ── Per-strategy candidate selection ──────────────────────────────────────────

def _select_momentum(feat: dict) -> bool:
    """
    Momentum: meaningful gap-up WITH confirmed multiday trend strength.

    Why stricter thresholds?
    - A 0.3% gap with normal volume catches ~40% of all stocks on any bull day.
      That's not a signal — it's noise. Most gaps that small FADE by EOD.
    - Requiring pm >= 1.0% + rv >= 1.3x filters to stocks with a real catalyst
      and actual buying interest, which are much more likely to hold their gains.
    - Above both MAs confirms the stock is in an uptrend, not just bouncing.
    - ret_5d > 0 means the momentum is real (has been building), not one-day spike.
    """
    pm        = feat.get("premarket_pct",  0)
    rv        = feat.get("rel_volume",     1.0)
    rsi       = feat.get("rsi",            50)
    above_ma20 = feat.get("above_ma20_pct", 0) > 0
    above_ma50 = feat.get("above_ma50_pct", 0) > 0
    ret5      = feat.get("ret_5d",         0)
    # Real gap (≥1%) + above-avg volume + uptrend + healthy RSI + recent momentum
    return (pm >= 1.0 and rv >= 1.3
            and above_ma20 and above_ma50
            and 50 <= rsi <= 70
            and ret5 > 0)


def _select_mean_reversion(feat: dict) -> bool:
    """
    Mean reversion: stock dropped 3%+ yesterday and is oversold — buy the bounce.

    What the data showed:
    - Previous filter (-4%, RSI<40) was too strict → only 0.3 trades/day, barely trades
    - -3% drop + RSI<45 roughly doubles qualifying trades to ~0.6-0.8/day
    - Strategy already has ~46% WR — the ONLY one above break-even in backtests
    - Just needed the TP calibrated (3.5% instead of 6%) to make money

    Why this works:
    - Academic literature confirms "short-term reversal premium" — stocks that
      drop hard in a day tend to bounce the next day as panic sellers close out
    - 46% WR > 42% break-even with 2.5/3.5 stop/TP → positive EV
    """
    ret1d = feat.get("ret_1d", 0)
    rsi   = feat.get("rsi",   50)
    rv    = feat.get("rel_volume", 1.0)
    # Dropped 3%+ yesterday AND oversold AND had actual volume (not just a thin stock)
    return ret1d < -3.0 and rsi < 45 and rv >= 0.8


def _select_gap_and_go(feat: dict) -> bool:
    """
    Gap and go: clean gap-up 2.5-8% with strong volume, in a confirmed uptrend.

    What the data showed:
    - Previous filter (pm>2.5, rv>=1.8) got 15.6% WR — catastrophically bad
    - Why: caught earnings disasters, biotech crashes, anything gapping up including
      stocks in free fall that gapped up 10-15%+ and kept collapsing
    - Fix: cap gap at 8% (removes disaster plays), require stock already in uptrend

    Why this works better:
    - Moderate gaps (2.5-8%) in stocks above their MAs are continuation plays
    - The uptrend context means institutional buyers are already interested
    - Extreme gaps (>8%) are usually news events too unpredictable for a system
    """
    pm        = feat.get("premarket_pct",  0)
    rv        = feat.get("rel_volume",     1.0)
    rsi       = feat.get("rsi",            50)
    above_ma20 = feat.get("above_ma20_pct", 0) > 0
    # Gap must be real but not extreme + above MA confirms uptrend context
    return 2.5 < pm <= 8.0 and rv >= 2.0 and 45 <= rsi <= 72 and above_ma20


def _select_breakout(feat: dict) -> bool:
    """
    Breakout: near 52-week high WITH confirmed uptrend AND strong volume.

    What the data showed:
    - Previous filter got 31.9% WR — buying stocks near highs that then failed
    - Why: a stock can be near its 52-week high BUT in a downtrend if the high
      was set months ago and the stock has since drifted down 30-40%
    - Fix: require BOTH MAs confirm uptrend + RSI showing genuine strength

    Why this works better:
    - Stocks making new highs in uptrends (above both MAs) have momentum behind them
    - High volume near a high = institutional accumulation, not distribution
    - RSI 55-75 means strong but not yet exhausted
    """
    from_high  = feat.get("pct_from_52w_high", -50)
    rv         = feat.get("rel_volume",      1.0)
    rsi        = feat.get("rsi",             50)
    above_ma20 = feat.get("above_ma20_pct",  0) > 0
    above_ma50 = feat.get("above_ma50_pct",  0) > 0
    ret5       = feat.get("ret_5d",          0)
    # Within 3% of high + both MAs confirming uptrend + volume + RSI strength + recent momentum
    return (-3.0 <= from_high <= 0.5
            and rv >= 1.5
            and 55 <= rsi <= 75
            and above_ma20 and above_ma50
            and ret5 > 1.0)


def _strategy_sort_key(feat: dict, strategy: str):
    """Sort candidates by the most relevant signal per strategy."""
    if strategy == "momentum":
        return feat.get("tech_score", 0)
    elif strategy == "mean_reversion":
        return feat.get("ret_1d", 0)          # most negative first (ascending)
    elif strategy == "gap_and_go":
        return feat.get("premarket_pct", 0)   # biggest gap first
    elif strategy == "breakout":
        return feat.get("pct_from_52w_high", -50)  # closest to ATH first
    return feat.get("tech_score", 0)


_SELECTORS = {
    "momentum":       _select_momentum,
    "mean_reversion": _select_mean_reversion,
    "gap_and_go":     _select_gap_and_go,
    "breakout":       _select_breakout,
}

# Strategies that need a bull market — skip when SPY is below 50-day MA
_TREND_STRATEGIES = {"momentum", "gap_and_go", "breakout"}


# ── Market regime detection ────────────────────────────────────────────────────

def _build_market_regime(
    hist_data:    dict[str, pd.DataFrame],
    trading_days: list[date],
) -> dict[date, bool]:
    """
    Build {date: is_uptrend} using SPY as the market proxy.
    is_uptrend = True  →  SPY above 50-day MA  →  safe to run trend strategies
    is_uptrend = False →  SPY below 50-day MA  →  bear market, skip momentum/breakout/gap

    Why this matters: in 2022, the S&P 500 fell -27%. Momentum strategies
    lose catastrophically in bear markets because even "good" setups get
    sold down with everything else. Filtering to bull days alone can push
    win rate from 22% → 40%+.
    """
    spy_df = hist_data.get("SPY")
    if spy_df is None:
        return {day: True for day in trading_days}  # no data → don't filter

    regime: dict[date, bool] = {}
    for day in trading_days:
        spy_before = spy_df[spy_df.index.date < day]
        if len(spy_before) < 50:
            regime[day] = True  # not enough history yet
            continue
        closes = spy_before["Close"]
        # yfinance may return a DataFrame with MultiIndex even for a single ticker
        if isinstance(closes, pd.DataFrame):
            closes = closes.squeeze()          # single-column → Series
        if isinstance(closes, pd.DataFrame):   # still a DataFrame? take first column
            closes = closes.iloc[:, 0]
        price = float(closes.iloc[-1])
        ma50  = float(closes.rolling(50).mean().iloc[-1])
        regime[day] = price > ma50
    return regime


def _build_vix_regime(
    hist_data:    dict[str, pd.DataFrame],
    trading_days: list[date],
) -> dict[date, float]:
    """
    Build {date: vix_level} for each trading day using ^VIX data.

    VIX interpretation:
      < 15  Low fear  — momentum setups historically work best
      15-25 Normal    — trade at full position size
      25-35 Elevated  — consider reducing position size to 50%
      > 35  High fear — consider skipping momentum trades entirely

    Returns an empty dict if VIX data is unavailable (graceful degradation).
    VIX is downloaded by _download_hist_data() alongside SPY automatically.
    """
    vix_df = hist_data.get("^VIX")
    if vix_df is None:
        return {}

    vix_levels: dict[date, float] = {}
    for day in trading_days:
        vix_before = vix_df[vix_df.index.date <= day]
        if vix_before.empty:
            continue
        closes = vix_before["Close"]
        if isinstance(closes, pd.DataFrame):
            closes = closes.squeeze()
        if isinstance(closes, pd.DataFrame):
            closes = closes.iloc[:, 0]
        try:
            vix_levels[day] = round(float(closes.iloc[-1]), 2)
        except Exception:
            pass
    return vix_levels


def _compute_signals(feat: dict) -> dict:
    """
    Convert continuous features into boolean signals.
    These are stored with each trade so the learner can analyse
    which signals actually correlate with wins.

    NOTE: All values wrapped in bool() to convert numpy.bool_ → Python bool
    so json.dump() doesn't throw a TypeError.
    """
    return {
        "above_ma20":    bool(feat.get("above_ma20_pct", 0) > 0),
        "above_ma50":    bool(feat.get("above_ma50_pct", 0) > 0),
        "near_52w_high": bool(feat.get("pct_from_52w_high", -50) >= -5),
        "high_rel_vol":  bool(feat.get("rel_volume", 0) >= 1.5),
        "strong_pm":     bool(0.5 <= feat.get("premarket_pct", 0) <= 3.5),
        "pos_5d":        bool(feat.get("ret_5d", 0) > 0),
        "pos_20d":       bool(feat.get("ret_20d", 0) > 0),
    }


# ── Data download ──────────────────────────────────────────────────────────────

def _download_hist_data(
    trading_days: list[date],
    tickers:      list[str],
    verbose:      bool = True,
) -> dict[str, pd.DataFrame]:
    """Download historical OHLCV data for the full backtest period."""
    bt_start = (trading_days[0]  - timedelta(days=60)).strftime("%Y-%m-%d")
    bt_end   = (trading_days[-1] + timedelta(days=1)).strftime("%Y-%m-%d")

    hist_data: dict[str, pd.DataFrame] = {}
    batch_size   = 50
    n_batches    = (len(tickers) + batch_size - 1) // batch_size
    batches_done = 0
    t0           = time.time()

    if verbose:
        print(f"  Downloading {len(tickers)} tickers  "
              f"({bt_start} to {bt_end})  --  {n_batches} batches")

    # Always download SPY + ^VIX first — used for market/volatility regime filtering.
    for special in (["SPY"] if "SPY" not in tickers else [],
                    ["^VIX"] if "^VIX" not in tickers else []):
        if not special:
            continue
        sym = special[0]
        try:
            raw = yf.download(
                tickers=special, start=bt_start, end=bt_end,
                interval="1d", auto_adjust=True, progress=False, threads=False,
            )
            if not raw.empty:
                if isinstance(raw.columns, pd.MultiIndex):
                    try:
                        raw = raw.xs(sym, axis=1, level=1)
                    except KeyError:
                        raw.columns = raw.columns.droplevel(1)
                hist_data[sym] = raw.dropna(how="all")
        except Exception:
            pass  # failed — regime filter will degrade gracefully

    for i in range(0, len(tickers), batch_size):
        batch = tickers[i : i + batch_size]

        if verbose:
            print(_pbar(batches_done, n_batches, t0, "  Downloading"), end="", flush=True)

        try:
            raw = yf.download(
                tickers=batch,
                start=bt_start,
                end=bt_end,
                interval="1d",
                auto_adjust=True,
                progress=False,
                threads=True,
            )
            if not raw.empty:
                if isinstance(raw.columns, pd.MultiIndex):
                    for ticker in batch:
                        try:
                            df = raw.xs(ticker, axis=1, level=1).dropna(how="all")
                            if len(df) >= 20:
                                hist_data[ticker] = df
                        except KeyError:
                            pass
                else:
                    if batch and len(raw) >= 20:
                        hist_data[batch[0]] = raw.dropna(how="all")
        except Exception as e:
            if verbose:
                print(f"\n  [!] Batch {batches_done + 1} error: {e}")

        batches_done += 1

    elapsed = time.time() - t0
    if verbose:
        # Overwrite the progress bar with a clean final line
        print(_pbar(n_batches, n_batches, t0, "  Downloading"), end="", flush=True)
        print(f"\r  Downloaded {len(hist_data)} tickers in {elapsed:.0f}s" + " " * 30)

    return hist_data


# ── Single-day simulation ──────────────────────────────────────────────────────

def _simulate_day(
    target_date:    date,
    hist_data:      dict[str, pd.DataFrame],
    weights:        dict,
    n_picks:        int,
    strategy:       str = "momentum",
    market_uptrend: dict[date, bool] | None = None,
) -> list[dict]:
    """
    Simulate one trading day for a given strategy.
    Returns a list of simulated trade dicts.

    market_uptrend: pre-computed {date: is_bull_market}.
      Trend-following strategies (momentum/gap_and_go/breakout) are skipped
      on bear-market days. Mean reversion can trade any day.
    """
    # ── Market regime gate ──
    if market_uptrend is not None:
        is_up = market_uptrend.get(target_date, True)
        if strategy in _TREND_STRATEGIES and not is_up:
            return []  # never trade momentum/breakout/gap in a bear market

    cfg      = STRATEGIES.get(strategy, STRATEGIES["momentum"])
    selector = _SELECTORS.get(strategy, _select_momentum)
    reverse_sort = strategy != "mean_reversion"

    candidates = []

    for ticker, full_df in hist_data.items():
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

        # Today's open represents the actual entry gap (vs yesterday's close)
        entry_proxy = float(df_before["Close"].iloc[-1])
        today_open  = float(df_today["Open"].iloc[0]) if "Open" in df_today.columns else entry_proxy

        feat["premarket_pct"] = round((today_open / entry_proxy - 1) * 100, 2)
        feat["tech_score"]    = technical_score(feat, weights)

        # ── Minimum score gate — skip weak setups ──
        if feat["tech_score"] < MIN_TECH_SCORE:
            continue

        # ── Strategy-specific filter ──
        if not selector(feat):
            continue

        feat["entry_price"] = round(today_open * 1.001, 2)  # 0.1% slippage
        candidates.append(feat)

    if not candidates:
        return []

    candidates.sort(
        key=lambda x: _strategy_sort_key(x, strategy),
        reverse=reverse_sort,
    )
    picks = candidates[:n_picks]

    results = []
    for pick in picks:
        entry = pick["entry_price"]
        stop  = round(entry * (1 - cfg["stop"]), 2)
        tp    = round(entry * (1 + cfg["tp"]),   2)

        ticker = pick["ticker"]
        df_today = hist_data[ticker][hist_data[ticker].index.date == target_date]
        if df_today.empty:
            continue

        today_high  = float(df_today["High"].iloc[0])  if "High"  in df_today.columns else entry
        today_low   = float(df_today["Low"].iloc[0])   if "Low"   in df_today.columns else entry
        today_close = float(df_today["Close"].iloc[0]) if "Close" in df_today.columns else entry

        # Determine exit: stop → TP → EOD close
        if today_low <= stop:
            exit_price  = stop
            exit_reason = "stop_loss"
        elif today_high >= tp:
            exit_price  = tp
            exit_reason = "take_profit"
        else:
            exit_price  = today_close
            exit_reason = "eod_close"

        # Subtract round-trip commission (entry spread + exit spread)
        # This means a flat EOD exit costs -0.1%, not 0.0%
        pnl_pct = round((exit_price / entry - 1) * 100 - COMMISSION_PCT * 100, 3)

        results.append({
            "date":          target_date.isoformat(),
            "ticker":        ticker,
            "entry_price":   float(entry),
            "exit_price":    float(exit_price),
            "pnl_pct":       float(pnl_pct),
            "exit_reason":   exit_reason,
            "tech_score":    float(pick.get("tech_score", 0)),
            "rsi":           float(pick.get("rsi") or 0),
            "rel_volume":    float(pick.get("rel_volume") or 0),
            "premarket_pct": float(pick.get("premarket_pct") or 0),
            "sector":        str(pick.get("sector", "")),
            "strategy":      strategy,
            "status":        "closed",
            "source":        "backtest",
            "signals":       _compute_signals(pick),
        })

    return results


# ── Strategy simulation (uses pre-downloaded data) ────────────────────────────

def _run_strategy_on_data(
    hist_data:    dict[str, pd.DataFrame],
    trading_days: list[date],
    weights:      dict,
    n_picks:      int,
    strategy:     str,
    verbose:      bool = True,
) -> dict:
    """Simulate a single strategy using already-downloaded data."""
    cfg = STRATEGIES.get(strategy, STRATEGIES["momentum"])

    if verbose:
        print(f"\n  📋 Strategy : {strategy}  —  {cfg['desc']}")
        print(f"  🎯 Stop: -{cfg['stop']*100:.1f}%   Take-profit: +{cfg['tp']*100:.1f}%")
        print(f"  📊 Min tech_score gate: {MIN_TECH_SCORE}")

    # ── Market regime filter ──
    market_regime = _build_market_regime(hist_data, trading_days)
    if verbose and strategy in _TREND_STRATEGIES:
        bull_days = sum(1 for v in market_regime.values() if v)
        bear_days = len(trading_days) - bull_days
        pct_bull  = bull_days / max(len(trading_days), 1) * 100
        print(f"  📈 Market filter: {bull_days} bull days ({pct_bull:.0f}%)  "
              f"/ {bear_days} bear days skipped")
        if pct_bull < 40:
            print(f"  ⚠  Mostly bear-market period — few tradeable days for this strategy.")
            print(f"     For best momentum results, use a bull-market range (e.g. 2023+).")

    all_trades: list[dict] = []
    for i, day in enumerate(trading_days):
        day_trades = _simulate_day(
            day, hist_data, weights, n_picks, strategy, market_regime
        )
        all_trades.extend(day_trades)
        if verbose and (i + 1) % 20 == 0:
            done_w = [t for t in all_trades if t["pnl_pct"] > 0]
            pct    = len(done_w) / max(len(all_trades), 1) * 100
            print(f"  … {i + 1}/{len(trading_days)} days  "
                  f"({len(all_trades)} trades, {pct:.0f}% winners so far)")

    return _compile_results(all_trades, trading_days, n_picks, strategy, verbose)


# ── Public entry points ────────────────────────────────────────────────────────

def run_backtest(
    start_date: str,
    end_date:   str,
    n_picks:    int  = MAX_PICKS,
    strategy:   str  = "momentum",
    verbose:    bool = True,
) -> dict:
    """
    Run a single-strategy backtest.
    Downloads data then simulates all trading days.
    """
    if strategy not in STRATEGIES:
        print(f"  ⚠  Unknown strategy '{strategy}'. "
              f"Valid: {', '.join(STRATEGIES)}")
        strategy = "momentum"

    trading_days = _get_trading_days(start_date, end_date)
    if not trading_days:
        print("  ⚠ No trading days in range.")
        return {}

    if verbose:
        print(f"\n  📅 Backtest: {start_date} → {end_date}  "
              f"({len(trading_days)} days, {n_picks} picks/day)")

    tickers   = get_all_tickers()
    weights   = _load_weights()
    hist_data = _download_hist_data(trading_days, tickers, verbose)

    return _run_strategy_on_data(
        hist_data, trading_days, weights, n_picks, strategy, verbose
    )


def run_backtest_compare(
    start_date:  str,
    end_date:    str,
    n_picks:     int        = MAX_PICKS,
    strategies:  list[str] | None = None,
    verbose:     bool       = True,
) -> dict[str, dict]:
    """
    Run ALL (or selected) strategies on the SAME downloaded data.
    Because data is downloaded once, all strategies see identical market conditions
    — making comparison apples-to-apples.

    Returns a dict of {strategy_name: results_dict}.
    """
    if strategies is None:
        strategies = list(STRATEGIES.keys())

    trading_days = _get_trading_days(start_date, end_date)
    if not trading_days:
        print("  ⚠ No trading days in range.")
        return {}

    tickers = get_all_tickers()
    weights = _load_weights()

    if verbose:
        print(f"\n  📅 Strategy comparison: {start_date} → {end_date}")
        print(f"  🗓  {len(trading_days)} trading days | {n_picks} picks/day | "
              f"comparing {len(strategies)} strategies")
        print(f"\n  Downloading data once for all strategies…")

    hist_data = _download_hist_data(trading_days, tickers, verbose)

    all_results: dict[str, dict] = {}
    for strategy in strategies:
        if verbose:
            print(f"\n  {'='*60}")
            print(f"  Running: {strategy.upper()}")
            print(f"  {'='*60}")
        results = _run_strategy_on_data(
            hist_data, trading_days, weights, n_picks, strategy, verbose=True
        )
        if results:
            all_results[strategy] = results

    if verbose and all_results:
        _print_comparison(all_results)

    return all_results


# ── Results compilation ────────────────────────────────────────────────────────

def _compile_results(
    trades:       list[dict],
    trading_days: list[date],
    n_picks:      int,
    strategy:     str,
    verbose:      bool,
) -> dict:
    if not trades:
        if verbose:
            print(f"  ⚠ No trades simulated for '{strategy}'.")
            print("  Hint: this strategy may have no qualifying setups in this period.")
        return {}

    wins    = [t for t in trades if t["pnl_pct"] > 0]
    losses  = [t for t in trades if t["pnl_pct"] <= 0]
    win_rate = round(len(wins) / len(trades) * 100, 1)

    avg_win  = round(sum(t["pnl_pct"] for t in wins)   / max(len(wins),   1), 3)
    avg_loss = round(sum(t["pnl_pct"] for t in losses)  / max(len(losses), 1), 3)

    # Break-even win rate
    breakeven = abs(avg_loss) / (avg_win + abs(avg_loss)) * 100 if avg_win > 0 else 50.0

    from collections import defaultdict
    daily_pnl: dict[str, float] = defaultdict(float)
    for t in trades:
        daily_pnl[t["date"]] += t["pnl_pct"] / n_picks

    daily_pnls = list(daily_pnl.values())
    cum_pnl    = sum(daily_pnls)
    max_dd     = _max_drawdown(daily_pnls)
    sharpe     = _sharpe(daily_pnls)

    # Exit reason breakdown
    exit_counts: dict[str, int] = defaultdict(int)
    for t in trades:
        exit_counts[t["exit_reason"]] += 1

    portfolio = _simulate_portfolio(trades, trading_days, n_picks)

    results = {
        "strategy":         strategy,
        "start_date":       trading_days[0].isoformat(),
        "end_date":         trading_days[-1].isoformat(),
        "trading_days":     len(trading_days),
        "total_trades":     len(trades),
        "wins":             len(wins),
        "losses":           len(losses),
        "win_rate":         win_rate,
        "avg_win_pct":      avg_win,
        "avg_loss_pct":     avg_loss,
        "breakeven_wr":     round(breakeven, 1),
        "cum_pnl_pct":      round(cum_pnl, 2),
        "max_drawdown":     round(max_dd, 2),
        "sharpe_ratio":     round(sharpe, 2),
        "stop_pct":         STRATEGIES[strategy]["stop"] * 100,
        "tp_pct":           STRATEGIES[strategy]["tp"]   * 100,
        "exit_breakdown":   dict(exit_counts),
        "portfolio":        portfolio,
        "trades":           trades,
    }

    if verbose:
        _print_results(results)

    return results


def _max_drawdown(daily_pnls: list[float]) -> float:
    if not daily_pnls:
        return 0.0
    peak = 0.0
    cum  = 0.0
    dd   = 0.0
    for p in daily_pnls:
        cum  += p
        peak  = max(peak, cum)
        dd    = min(dd, cum - peak)
    return dd


def _simulate_portfolio(
    trades:       list[dict],
    trading_days: list[date],
    n_picks:      int,
    capital:      float = STARTING_CAPITAL,
    pos_size:     float = POSITION_SIZE_PCT,
) -> dict:
    """
    Simulate an actual dollar account — compounding wins and losses.

    Each trade uses `pos_size` (default 5%) of the CURRENT portfolio.
    Wins grow the pot; losses shrink it — same as real trading.

    Returns a dict with dollar figures so you can see
    '$10,000 → $11,234  (+12.3%)' instead of abstract percentages.
    """
    from collections import defaultdict as _dd
    trades_by_date: dict[str, list[float]] = _dd(list)
    for t in trades:
        trades_by_date[t["date"]].append(t["pnl_pct"])

    portfolio = capital
    peak      = capital
    max_dd    = 0.0          # in dollars (will be ≤ 0)
    equity:   list[tuple[str, float]] = []

    for day in trading_days:
        day_pnls = trades_by_date.get(day.isoformat(), [])
        for pnl_pct in day_pnls:
            # This trade used pos_size of current portfolio
            portfolio += portfolio * pos_size * (pnl_pct / 100)

        equity.append((day.isoformat(), round(portfolio, 2)))
        peak   = max(peak, portfolio)
        max_dd = min(max_dd, portfolio - peak)   # most negative point

    total_return = (portfolio / capital - 1) * 100

    return {
        "start":            round(capital,   2),
        "end":              round(portfolio, 2),
        "peak":             round(peak,      2),
        "total_return_pct": round(total_return, 2),
        "profit_dollars":   round(portfolio - capital, 2),
        "max_dd_dollars":   round(max_dd,   2),
        "max_dd_pct":       round(max_dd / capital * 100, 2),
        "pos_size_pct":     round(pos_size * 100, 1),
        "equity":           equity,   # (date, value) list — useful for charting later
    }


def _sharpe(daily_pnls: list[float], rf_daily: float = 0.0) -> float:
    if len(daily_pnls) < 2:
        return 0.0
    excess = [p - rf_daily for p in daily_pnls]
    mean   = np.mean(excess)
    std    = np.std(excess, ddof=1)
    if std == 0:
        return 0.0
    return float(mean / std * np.sqrt(252))


def _print_results(r: dict) -> None:
    strat = r.get("strategy", "momentum")
    cfg   = STRATEGIES.get(strat, {})
    wr    = r.get("win_rate", 0)
    bkev  = r.get("breakeven_wr", 50)
    gap   = round(wr - bkev, 1)
    gap_str = f"+{gap:.1f}% ABOVE break-even ✓" if gap >= 0 else f"{gap:.1f}% BELOW break-even ✗"

    print("\n" + "═" * 62)
    print(f"  BACKTEST  [{strat.upper()}]")
    print("═" * 62)
    print(f"  Strategy:     {strat}  —  {cfg.get('desc','')}")
    print(f"  Period:       {r['start_date']}  →  {r['end_date']}")
    print(f"  Trading days: {r['trading_days']}")
    print(f"  Total trades: {r['total_trades']}  "
          f"({r['wins']}W / {r['losses']}L)")
    print(f"  Win rate:     {wr}%  (break-even: {bkev}%  →  {gap_str})")
    print(f"  Avg win:      +{r['avg_win_pct']}%   "
          f"Avg loss: {r['avg_loss_pct']}%")
    exits = r.get("exit_breakdown", {})
    if exits:
        tp_n  = exits.get("take_profit", 0)
        sl_n  = exits.get("stop_loss",  0)
        eod_n = exits.get("eod_close",  0)
        tot   = max(sum(exits.values()), 1)
        tp_rate  = tp_n  / tot * 100
        sl_rate  = sl_n  / tot * 100
        eod_rate = eod_n / tot * 100

        # TP hit rate is the KEY signal-quality metric
        if tp_rate >= 25:
            tp_label = f"✅ {tp_rate:.0f}%  (good — targets being reached)"
        elif tp_rate >= 12:
            tp_label = f"⚡ {tp_rate:.0f}%  (ok — could be higher)"
        else:
            tp_label = f"❌ {tp_rate:.0f}%  (bad — targets almost never hit)"

        print(f"  TP hit rate:  {tp_label}")
        print(f"  Stop hit:     {sl_rate:.0f}%   EOD exit: {eod_rate:.0f}%  "
              f"(commission applied — EOD at flat = -0.1%)")
        if eod_rate > 60:
            print(f"  ⚠  {eod_rate:.0f}% of trades drifted to EOD with no clear move.")
            print(f"     These are dead trades — tying up capital for tiny/no gain.")
            print(f"     Fix: tighter TP (1.5-2%) so targets are actually reachable.")

    # ── Portfolio simulation ──────────────────────────────────────
    p = r.get("portfolio", {})
    if p:
        start  = p["start"]
        end    = p["end"]
        profit = p["profit_dollars"]
        ret    = p["total_return_pct"]
        dd_d   = p["max_dd_dollars"]
        dd_pct = p["max_dd_pct"]
        pos    = p["pos_size_pct"]
        arrow  = "📈" if ret >= 0 else "📉"
        sign   = "+" if profit >= 0 else ""
        print(f"  {'─'*58}")
        print(f"  {arrow} PORTFOLIO SIMULATION  (${start:,.0f} starting capital)")
        print(f"     Start : ${start:>9,.2f}")
        print(f"     End   : ${end:>9,.2f}   ({ret:+.1f}%)")
        print(f"     Profit: {sign}${abs(profit):>8,.2f}")
        print(f"     Worst drawdown: -${abs(dd_d):,.2f}  ({dd_pct:.1f}% of account)")
        print(f"     Position size : {pos:.0f}% per trade  "
              f"({r.get('total_trades',0) // max(r.get('trading_days',1),1)} avg trades/day)")

    print(f"  {'─'*58}")
    print(f"  Sharpe ratio: {r['sharpe_ratio']:.2f}   "
          f"Max drawdown: {r['max_drawdown']:.2f}%")
    print("═" * 62)


def _print_comparison(all_results: dict[str, dict]) -> None:
    """Print a side-by-side comparison table of all strategies."""
    print("\n\n" + "=" * 78)
    print("  STRATEGY COMPARISON  (same data, same period)")
    print("=" * 78)
    print(f"  {'Strategy':<18} {'Trades':>7} {'WinRate':>8} {'BreakEven':>10} "
          f"{'Gap':>7} {'P&L':>8} {'Sharpe':>7}")
    print("  " + "-" * 70)

    # Sort by P&L descending
    sorted_r = sorted(all_results.items(),
                      key=lambda x: x[1].get("cum_pnl_pct", -999), reverse=True)

    for strat, r in sorted_r:
        wr    = r.get("win_rate", 0)
        bkev  = r.get("breakeven_wr", 50)
        gap   = wr - bkev
        pnl   = r.get("cum_pnl_pct", 0)
        sh    = r.get("sharpe_ratio", 0)
        tot   = r.get("total_trades", 0)
        flag  = "✓" if pnl > 0 else "✗"
        print(f"  {strat:<18} {tot:>7} {wr:>7.1f}% {bkev:>9.1f}% "
              f"{gap:>+6.1f}% {pnl:>+7.1f}% {sh:>7.2f}  {flag}")

    print("=" * 78)
    best = sorted_r[0][0] if sorted_r else "?"
    print(f"\n  Best strategy for this period: {best.upper()}")
    print("  Note: test multiple periods before trusting one result.\n")


# ── Save results ───────────────────────────────────────────────────────────────

def save_backtest_results(results: dict, filename: str | None = None) -> str:
    """
    Save backtest summary JSON (for the report viewer).
    ALSO saves individual trades to a separate file so the learner can learn
    from simulated data without waiting weeks for real trades.
    """
    strat = results.get("strategy", "momentum")
    today = datetime.now().strftime("%Y%m%d_%H%M")

    # ── Summary JSON (no individual trades — keeps it readable) ──
    if not filename:
        filename = os.path.join(_BASE, "memory", f"backtest_{strat}_{today}.json")
    with open(filename, "w") as f:
        summary = {k: v for k, v in results.items() if k != "trades"}
        summary["n_trades_in_detail"] = len(results.get("trades", []))
        json.dump(summary, f, indent=2)
    print(f"  💾 Backtest summary saved: {os.path.basename(filename)}")

    # ── Individual trades JSON (for learning) ──
    trades = results.get("trades", [])
    if trades:
        trades_file = os.path.join(
            _BASE, "memory", f"backtest_trades_{strat}_{today}.json"
        )
        with open(trades_file, "w") as f:
            json.dump(trades, f, indent=2)
        print(f"  📚 Trade details saved for learning: "
              f"{os.path.basename(trades_file)}  ({len(trades)} trades)")

    return filename


def save_compare_results(all_results: dict[str, dict]) -> None:
    """Save all strategy results from a compare run."""
    for strat, results in all_results.items():
        if results:
            save_backtest_results(results)
