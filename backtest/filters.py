"""
backtest/filters.py
Pre-trade quality filters — applied before entering any position.

What each filter does
─────────────────────
1. Earnings calendar filter
   We hold swing trades OVERNIGHT. If a company reports earnings while we're
   holding, a bad surprise gaps the stock down 15-25% at the open — completely
   bypassing our 2% stop loss. This is the main cause of large unexpected losses.
   → Skip any stock with earnings in the next 3 calendar days.
   → LIVE TRADING ONLY: yfinance only gives us future dates, not past ones.
     In backtesting, the historical OHLC already captures what actually happened
     (good/bad earnings are baked into the price action). The filter removes
     *forward-looking* binary risk that a backtest can't represent.

2. Sector concentration limit
   If all 3 picks are tech stocks and tech sells off, all 3 lose together.
   → Max 1 pick per sector per day.
   → Applied in BOTH live trading AND backtests.

Privacy note
────────────
The earnings calendar fetches publicly available dates from Yahoo Finance.
Only ticker symbols (e.g. "AAPL") are sent — never account info, positions,
trade history, or any personal data. Results are cached on disk for the day
so repeated scanner runs don't re-fetch.
"""

from __future__ import annotations

import os
import json
from datetime import date, timedelta
from collections import defaultdict
from typing import Optional

_BASE       = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CACHE_PATH = os.path.join(_BASE, "memory", "earnings_cache.json")

EARNINGS_BUFFER_DAYS = 3   # skip if earnings fall within this many calendar days


# ── Earnings Calendar ─────────────────────────────────────────────────────────

class EarningsCalendar:
    """
    Fetches and caches upcoming earnings dates for the universe.
    Source: yfinance (Yahoo Finance).  Cached on disk until end of today.

    Usage:
        cal = EarningsCalendar.load(tickers)
        if cal.is_safe("AAPL"):
            # safe to enter today
    """

    def __init__(self) -> None:
        self._dates: dict[str, list[str]] = {}  # {ticker: ["2024-01-15", ...]}

    # ── Load ─────────────────────────────────────────────────────────────────

    @classmethod
    def load(cls, tickers: list[str], verbose: bool = True) -> "EarningsCalendar":
        """Use today's disk cache if available; otherwise fetch from yfinance."""
        cal = cls()

        if os.path.exists(_CACHE_PATH):
            try:
                with open(_CACHE_PATH, encoding="utf-8") as f:
                    cached = json.load(f)
                if cached.get("_cached_on") == date.today().isoformat():
                    cal._dates = {k: v for k, v in cached.items() if not k.startswith("_")}
                    if verbose:
                        n = sum(1 for v in cal._dates.values() if v)
                        print(f"  Earnings calendar: {n} tickers with dates (cached)")
                    return cal
            except Exception:
                pass

        cal._fetch(tickers, verbose=verbose)
        return cal

    # ── Fetch ─────────────────────────────────────────────────────────────────

    def _fetch(self, tickers: list[str], verbose: bool = True) -> None:
        """Fetch from yfinance. Only ticker symbols leave this machine."""
        import yfinance as yf

        if verbose:
            print(f"  Fetching earnings calendar ({len(tickers)} tickers)...",
                  end=" ", flush=True)

        fetched = 0
        for ticker in tickers:
            try:
                t = yf.Ticker(ticker)
                dates: list[str] = []

                # Strategy 1: earnings_dates (newer yfinance — gives past + upcoming)
                try:
                    df = t.earnings_dates
                    if df is not None and not df.empty:
                        for idx in df.index:
                            try:
                                d = (idx.date() if hasattr(idx, "date")
                                     else date.fromisoformat(str(idx)[:10]))
                                dates.append(d.isoformat())
                            except Exception:
                                pass
                except Exception:
                    pass

                # Strategy 2: calendar dict (fallback for older yfinance builds)
                if not dates:
                    try:
                        cal = t.calendar
                        if isinstance(cal, dict):
                            ed = cal.get("Earnings Date", [])
                            for e in (ed if isinstance(ed, list) else [ed]):
                                try:
                                    d = (e.date() if hasattr(e, "date")
                                         else date.fromisoformat(str(e)[:10]))
                                    dates.append(d.isoformat())
                                except Exception:
                                    pass
                    except Exception:
                        pass

                if dates:
                    self._dates[ticker] = sorted(set(dates))
                    fetched += 1

            except Exception:
                pass

        if verbose:
            print(f"got {fetched}/{len(tickers)} with earnings data")

        # Persist to disk
        try:
            os.makedirs(os.path.dirname(_CACHE_PATH), exist_ok=True)
            with open(_CACHE_PATH, "w", encoding="utf-8") as f:
                json.dump({"_cached_on": date.today().isoformat(), **self._dates}, f)
        except Exception:
            pass

    # ── Queries ───────────────────────────────────────────────────────────────

    def is_safe(
        self,
        ticker:      str,
        trade_date:  Optional[date] = None,
        buffer_days: int = EARNINGS_BUFFER_DAYS,
    ) -> bool:
        """
        True  → safe to enter (no earnings within buffer_days).
        True  → no data found (don't over-filter on missing data).
        False → earnings too close — skip this trade.
        """
        if trade_date is None:
            trade_date = date.today()

        dates = self._dates.get(ticker, [])
        if not dates:
            return True  # no data → assume safe

        deadline = trade_date + timedelta(days=buffer_days)
        for d_str in dates:
            try:
                d = date.fromisoformat(d_str)
                if trade_date <= d <= deadline:
                    return False
            except ValueError:
                pass
        return True

    def days_to_next(
        self,
        ticker:     str,
        trade_date: Optional[date] = None,
    ) -> Optional[int]:
        """Days until next earnings date, or None if unknown."""
        if trade_date is None:
            trade_date = date.today()
        future = []
        for d_str in self._dates.get(ticker, []):
            try:
                d = date.fromisoformat(d_str)
                if d >= trade_date:
                    future.append(d)
            except ValueError:
                pass
        return (min(future) - trade_date).days if future else None


# ── Module-level singleton ────────────────────────────────────────────────────

_GLOBAL_CALENDAR: Optional[EarningsCalendar] = None


def get_earnings_calendar(
    tickers: list[str],
    verbose: bool = True,
) -> EarningsCalendar:
    """
    Return the session earnings calendar (fetches once, then reuses).
    Repeated calls are free — reads from the in-memory instance.
    """
    global _GLOBAL_CALENDAR
    if _GLOBAL_CALENDAR is None:
        _GLOBAL_CALENDAR = EarningsCalendar.load(tickers, verbose=verbose)
    return _GLOBAL_CALENDAR


# ── Sector concentration limit ────────────────────────────────────────────────

def apply_sector_limit(
    candidates:     list[dict],
    max_per_sector: int = 1,
    n_picks:        int = 3,
    sector_key:     str = "sector",
) -> list[dict]:
    """
    Enforce a maximum number of picks per sector.
    Candidates MUST already be sorted best-first — this preserves that order,
    it only skips candidates that would exceed the sector cap.

    Example:
        3 candidates: AAPL (tech 8.5), MSFT (tech 8.2), JPM (finance 7.9)
        max_per_sector=1, n_picks=3  →  [AAPL, JPM]   (MSFT skipped, same sector as AAPL)
        This diversifies across sectors instead of concentrating in one.
    """
    sector_count: dict[str, int] = defaultdict(int)
    result: list[dict] = []

    for c in candidates:
        if len(result) >= n_picks:
            break
        sector = (c.get(sector_key) or "unknown").lower().strip()
        if sector_count[sector] < max_per_sector:
            result.append(c)
            sector_count[sector] += 1

    return result
