"""
scanner/data.py
Fetches and calculates market data for the universe using yfinance.

Flow:
  1. fetch_universe_data()  — bulk 30-day daily OHLCV for all tickers (batched)
  2. build_stock_features()  — RSI, MA, returns, relative volume per ticker
  3. get_premarket_info()    — live pre-market price/change for shortlisted tickers
"""

import warnings
import logging
warnings.filterwarnings("ignore")
# Silence yfinance's own noisy download messages about delisted tickers
logging.getLogger("yfinance").setLevel(logging.CRITICAL)

import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timezone
from typing import Optional
import time

BATCH_SIZE   = 50    # tickers per yf.download() call
HISTORY_DAYS = "45d" # enough for 20-day MA and RSI(14)
RETRY_DELAY  = 2     # seconds between retries on failure


# ── RSI ────────────────────────────────────────────────────────────────────────

def calc_rsi(closes: pd.Series, period: int = 14) -> float:
    """Standard Wilder RSI. Returns NaN if not enough data."""
    if len(closes) < period + 1:
        return float("nan")
    delta = closes.diff().dropna()
    gain  = delta.clip(lower=0).ewm(com=period - 1, adjust=False).mean()
    loss  = (-delta.clip(upper=0)).ewm(com=period - 1, adjust=False).mean()
    rs    = gain / loss.replace(0, np.nan)
    rsi   = 100 - (100 / (1 + rs))
    return round(float(rsi.iloc[-1]), 2)


# ── Bulk download ──────────────────────────────────────────────────────────────

def fetch_universe_data(tickers: list[str]) -> dict[str, pd.DataFrame]:
    """
    Download 45 days of daily OHLCV for every ticker in batches.
    Returns {ticker: DataFrame(Open/High/Low/Close/Volume)} — tickers with
    insufficient data are silently skipped.
    """
    result: dict[str, pd.DataFrame] = {}

    for i in range(0, len(tickers), BATCH_SIZE):
        batch = tickers[i : i + BATCH_SIZE]
        for attempt in range(2):
            try:
                raw = yf.download(
                    tickers=batch,
                    period=HISTORY_DAYS,
                    interval="1d",
                    auto_adjust=True,
                    progress=False,
                    threads=True,
                )
                if raw.empty:
                    break

                # yf returns multi-level columns when >1 ticker
                if isinstance(raw.columns, pd.MultiIndex):
                    for ticker in batch:
                        try:
                            df = raw.xs(ticker, axis=1, level=1).dropna(how="all")
                            if len(df) >= 15:
                                result[ticker] = df
                        except KeyError:
                            pass
                else:
                    # Single ticker in batch
                    df = raw.dropna(how="all")
                    if len(df) >= 15 and batch:
                        result[batch[0]] = df

                break  # success
            except Exception as e:
                if attempt == 0:
                    time.sleep(RETRY_DELAY)
                else:
                    print(f"  ⚠ Batch {i//BATCH_SIZE + 1} failed: {e}")

    return result


# ── Feature calculation ────────────────────────────────────────────────────────

def build_stock_features(ticker: str, df: pd.DataFrame) -> Optional[dict]:
    """
    Given a ticker's OHLCV DataFrame, compute all features needed for scoring.
    Returns None if data is insufficient.
    """
    if df is None or len(df) < 15:
        return None

    closes  = df["Close"]
    volumes = df["Volume"]

    rsi = calc_rsi(closes)
    if np.isnan(rsi):
        return None

    # Moving averages
    ma50  = float(closes.rolling(min(50, len(closes))).mean().iloc[-1])
    ma20  = float(closes.rolling(20).mean().iloc[-1])
    price = float(closes.iloc[-1])

    above_ma20_pct  = round((price / ma20  - 1) * 100, 2) if ma20  else 0
    above_ma50_pct  = round((price / ma50  - 1) * 100, 2) if ma50  else 0

    # 52-week high/low
    high52 = float(closes.tail(min(252, len(closes))).max())
    low52  = float(closes.tail(min(252, len(closes))).min())
    pct_from_high = round((price / high52 - 1) * 100, 2)
    pct_from_low  = round((price / low52  - 1) * 100, 2)

    # Short-term returns
    ret_1d  = round((closes.iloc[-1] / closes.iloc[-2]  - 1) * 100, 2) if len(closes) >= 2  else 0
    ret_5d  = round((closes.iloc[-1] / closes.iloc[-6]  - 1) * 100, 2) if len(closes) >= 6  else 0
    ret_20d = round((closes.iloc[-1] / closes.iloc[-21] - 1) * 100, 2) if len(closes) >= 21 else 0

    # Relative volume (today vs 20-day avg)
    avg_vol = float(volumes.tail(21).iloc[:-1].mean()) if len(volumes) > 1 else 1
    rel_vol = round(float(volumes.iloc[-1]) / avg_vol, 2) if avg_vol > 0 else 1.0

    return {
        "ticker":          ticker,
        "price":           round(price, 2),
        "rsi":             rsi,
        "ma20":            round(ma20, 2),
        "ma50":            round(ma50, 2),
        "above_ma20_pct":  above_ma20_pct,
        "above_ma50_pct":  above_ma50_pct,
        "pct_from_52w_high": pct_from_high,
        "pct_from_52w_low":  pct_from_low,
        "ret_1d":          ret_1d,
        "ret_5d":          ret_5d,
        "ret_20d":         ret_20d,
        "avg_volume":      int(avg_vol),
        "rel_volume":      rel_vol,
        "premarket_pct":   0.0,   # filled in by get_premarket_info()
        "premarket_price": price,  # placeholder
    }


# ── Pre-market live data ───────────────────────────────────────────────────────

def get_premarket_info(tickers: list[str]) -> dict[str, dict]:
    """
    Fetch live pre-market price and % change for a short list of candidates.
    Uses yfinance Ticker.fast_info — one call per ticker.
    Falls back gracefully if data is unavailable (pre-market not active).
    """
    results = {}
    for ticker in tickers:
        try:
            t = yf.Ticker(ticker)
            fi = t.fast_info

            prev_close = getattr(fi, "previous_close", None) or getattr(fi, "regularMarketPreviousClose", None)
            last_price = getattr(fi, "last_price", None)

            # Try info dict for actual pre-market price (slower but more reliable)
            pm_price = None
            try:
                info = t.info
                pm_price = info.get("preMarketPrice") or info.get("regularMarketPrice")
                if not prev_close:
                    prev_close = info.get("previousClose") or info.get("regularMarketPreviousClose")
            except Exception:
                pm_price = last_price

            if pm_price and prev_close and prev_close > 0:
                pm_pct = round((pm_price / prev_close - 1) * 100, 2)
            else:
                pm_pct = 0.0
                pm_price = last_price or 0

            results[ticker] = {
                "premarket_price": round(float(pm_price or 0), 2),
                "premarket_pct":   pm_pct,
            }
            time.sleep(0.15)  # be polite to Yahoo Finance
        except Exception:
            results[ticker] = {"premarket_price": 0.0, "premarket_pct": 0.0}

    return results


def confirm_volume_at_open(
    tickers:    list[str],
    min_rv_pace: float = 1.5,
) -> dict[str, dict]:
    """
    At market open, re-check whether [HIGH?] picks have real volume backing them.

    How it works
    ------------
    A full trading day is ~390 minutes. The first 15 minutes (9:30-9:45) should
    account for roughly 1/26th of normal daily volume if activity were even.
    In reality the open is busier, so we use a slightly lower expected pace
    (1/20th) to avoid being too strict.

    If a stock's actual 15-min volume is >= 1.5x that expected pace, volume
    is confirming the premarket move. If it's still low, the premarket move
    was noise and the pick is dropped.

    Returns {ticker: {"confirmed": bool, "rv_pace": float, "vol_today": int}}
    """
    MINUTES_IN_DAY   = 390
    OPEN_WINDOW_MINS = 15
    # Expected fraction of daily volume in opening window (open is busier than average)
    EXPECTED_FRAC = OPEN_WINDOW_MINS / 200   # slightly generous — open gets ~double pace

    results: dict[str, dict] = {}
    for ticker in tickers:
        try:
            t  = yf.Ticker(ticker)
            fi = t.fast_info

            vol_today = int(getattr(fi, "regular_market_volume",      0) or 0)
            avg_daily = int(getattr(fi, "three_month_average_volume", 0)
                            or getattr(fi, "average_volume", 0) or 1)

            expected_now = avg_daily * EXPECTED_FRAC   # expected volume in window
            rv_pace      = round(vol_today / max(expected_now, 1), 2)
            confirmed    = rv_pace >= min_rv_pace

            results[ticker] = {
                "confirmed": confirmed,
                "rv_pace":   rv_pace,
                "vol_today": vol_today,
            }
            time.sleep(0.1)
        except Exception:
            # Fail open — don't block a trade because of a data error
            results[ticker] = {"confirmed": True, "rv_pace": 0.0, "vol_today": 0}

    return results
