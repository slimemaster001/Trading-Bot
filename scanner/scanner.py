"""
scanner/scanner.py
Main scanning pipeline.

Steps:
  1. Load universe (~200 tickers)
  2. Bulk-fetch 45 days of historical data
  3. Compute technical features for each ticker
  4. Hard-filter: price, volume, basic guardrails
  5. Technical pre-score each passing ticker
  6. Return top N candidates for AI scoring
"""

from __future__ import annotations

import os
import json
from typing import Optional

from scanner.universe import get_all_tickers, get_filters, get_sector
from scanner.data import fetch_universe_data, build_stock_features, get_premarket_info

# ── Config ─────────────────────────────────────────────────────────────────────

TOP_N_FOR_AI = 25          # send this many to Ollama for deep scoring
MIN_REL_VOL  = 1.2         # relative volume must be ≥ this (unusual activity)
RSI_MIN      = 35          # below this = in freefall, skip
RSI_MAX      = 75          # above this = overbought, skip
MIN_PRICE    = 10.0        # raised from $5 — penny stocks have wide spreads
MAX_PRICE    = 300.0
MIN_AVG_VOL  = 500_000     # 500k shares/day minimum — thinner stocks hard to exit

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_WEIGHTS_PATH = os.path.join(_BASE, "memory", "scoring_weights.json")


def _load_weights() -> dict:
    try:
        with open(_WEIGHTS_PATH) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


# ── Technical pre-scorer ───────────────────────────────────────────────────────

def technical_score(stock: dict, weights: dict) -> float:
    """
    Rule-based score 0–10 based on technical signals.
    Uses weights from scoring_weights.json (updated by learner).
    Higher = more favourable setup for an intraday bounce.
    """
    score = 5.0  # neutral baseline

    w = weights.get("technical", {})

    # RSI sweet spot 50–65 scores highest; extremes deduct
    rsi = stock.get("rsi", 50)
    if   50 <= rsi <= 65:  score += w.get("rsi_sweet", 1.5)
    elif 40 <= rsi <  50:  score += w.get("rsi_mid",   0.5)
    elif 65 <  rsi <= 75:  score -= w.get("rsi_high",  0.5)
    elif rsi > 75:         score -= w.get("rsi_ob",    2.0)
    elif rsi < 35:         score -= w.get("rsi_os",    2.0)

    # Pre-market momentum (small positive gap = best for intraday follow-through)
    pm = stock.get("premarket_pct", 0)
    if   0.5 <= pm <= 3.0: score += w.get("pm_ideal",  2.0)
    elif 3.0 <  pm <= 5.0: score += w.get("pm_large",  0.5)
    elif pm > 5.0:         score -= w.get("pm_gap",    1.0)  # too big — gap risk
    elif pm < -1.0:        score -= w.get("pm_neg",    1.5)

    # Relative volume (unusual interest = momentum fuel)
    rv = stock.get("rel_volume", 1.0)
    if   rv >= 3.0: score += w.get("rv_high",  2.0)
    elif rv >= 2.0: score += w.get("rv_med",   1.0)
    elif rv >= 1.5: score += w.get("rv_slight",0.5)
    elif rv < 0.8:  score -= w.get("rv_low",   1.0)

    # Trend: above both MAs = uptrend, favours longs
    ma20_pct = stock.get("above_ma20_pct", 0)
    ma50_pct = stock.get("above_ma50_pct", 0)
    if ma20_pct > 0 and ma50_pct > 0:  score += w.get("above_both_ma", 1.0)
    elif ma20_pct > 0:                 score += w.get("above_ma20",    0.5)
    elif ma20_pct < -5:                score -= w.get("below_ma20",    1.0)

    # Near recent high but not AT high (breakout zone)
    from_high = stock.get("pct_from_52w_high", -50)
    if  -5 <= from_high <= 0:   score += w.get("near_high",     1.5)  # breakout candidate
    elif -15 <= from_high < -5: score += w.get("mid_range",     0.5)
    elif from_high < -30:       score -= w.get("deep_pullback", 0.5)

    # Short-term momentum (5-day return)
    ret5 = stock.get("ret_5d", 0)
    if   ret5 >= 5:  score += w.get("ret5_strong", 1.0)
    elif ret5 >= 2:  score += w.get("ret5_ok",     0.5)
    elif ret5 < -5:  score -= w.get("ret5_weak",   1.0)

    return round(max(0.0, min(10.0, score)), 2)


# ── Hard filters ───────────────────────────────────────────────────────────────

def passes_filter(stock: dict) -> tuple[bool, str]:
    """Returns (True, '') or (False, reason)."""
    if stock["price"] < MIN_PRICE:
        return False, f"price ${stock['price']} < ${MIN_PRICE}"
    if stock["price"] > MAX_PRICE:
        return False, f"price ${stock['price']} > ${MAX_PRICE}"
    if stock["avg_volume"] < MIN_AVG_VOL:
        return False, f"avg volume {stock['avg_volume']:,} < {MIN_AVG_VOL:,}"
    if stock["rsi"] > RSI_MAX:
        return False, f"RSI {stock['rsi']} overbought (>{RSI_MAX})"
    if stock["rsi"] < RSI_MIN:
        return False, f"RSI {stock['rsi']} freefall (<{RSI_MIN})"
    return True, ""


# ── Main pipeline ──────────────────────────────────────────────────────────────

def check_market_regime() -> dict:
    """
    Check overall market direction (SPY 50-day MA) and fear level (VIX).

    VIX interpretation:
      < 15  Low fear  — momentum setups work best
      15-25 Normal    — trade normally
      25-35 Elevated  — consider halving position size
      > 35  High fear — avoid momentum; consider skipping

    Returns regime dict with spy + vix data.
    """
    import yfinance as yf

    result: dict = {"regime": "unknown", "above_ma50": True, "vix": None}

    # ── SPY trend ──
    try:
        spy = yf.download("SPY", period="70d", interval="1d",
                          auto_adjust=True, progress=False)
        if not spy.empty and len(spy) >= 20:
            closes = spy["Close"].squeeze()
            price  = float(closes.iloc[-1])
            ma50   = float(closes.rolling(min(50, len(closes))).mean().iloc[-1])
            ma20   = float(closes.rolling(min(20, len(closes))).mean().iloc[-1])
            above  = price > ma50
            result.update({
                "regime":        "bull" if above else "bear",
                "spy_price":     round(price, 2),
                "ma50":          round(ma50,  2),
                "ma20":          round(ma20,  2),
                "above_ma50":    above,
                "pct_from_ma50": round((price / ma50 - 1) * 100, 2),
                "spy_1d":        round((price / float(closes.iloc[-2]) - 1) * 100, 2)
                                 if len(closes) >= 2 else 0,
            })
    except Exception:
        pass

    # ── VIX fear level ──
    try:
        vix_df = yf.download("^VIX", period="5d", interval="1d",
                              auto_adjust=True, progress=False)
        if not vix_df.empty:
            vix_closes = vix_df["Close"].squeeze()
            vix = round(float(vix_closes.iloc[-1]), 1)
            result["vix"] = vix
            if vix > 35:
                result["vix_regime"] = "high_fear"
            elif vix > 25:
                result["vix_regime"] = "elevated"
            elif vix < 15:
                result["vix_regime"] = "low_fear"
            else:
                result["vix_regime"] = "normal"
    except Exception:
        pass

    return result


def run_scan(verbose: bool = True) -> list[dict]:
    """
    Full scan pipeline. Returns top N candidates (dicts) ready for AI scoring.
    Each candidate dict includes all technical features + tech_score.
    """
    tickers = get_all_tickers()

    # ── Market direction + VIX check ──
    market     = check_market_regime()
    regime     = market.get("regime", "unknown")
    spy_p      = market.get("spy_price", 0)
    pct_ma     = market.get("pct_from_ma50", 0)
    spy_1d     = market.get("spy_1d", 0)
    vix        = market.get("vix")
    vix_regime = market.get("vix_regime", "normal")

    if verbose:
        regime_str = "BULL" if regime == "bull" else "BEAR"
        print(f"  Universe: {len(tickers)} tickers")
        print(f"  Market: {regime_str}  "
              f"(SPY ${spy_p}  {pct_ma:+.1f}% vs 50d MA  today {spy_1d:+.1f}%)")
        if regime == "bear":
            print(f"  [!] Bear market -- momentum/breakout setups likely to fail")

        if vix is not None:
            vix_labels = {
                "low_fear":  "Low fear    -- good for momentum",
                "normal":    "Normal",
                "elevated":  "Elevated    -- position size halved",
                "high_fear": "HIGH FEAR   -- momentum skipped",
            }
            note = vix_labels.get(vix_regime, "")
            print(f"  VIX: {vix:.1f}  -- {note}")

    # ── Step 1: bulk historical data ──
    if verbose:
        print(f"  Downloading market data...")
    raw_data = fetch_universe_data(tickers)

    # ── Step 2: compute features ──
    candidates: list[dict] = []
    skipped = 0
    for ticker, df in raw_data.items():
        feat = build_stock_features(ticker, df)
        if feat is None:
            skipped += 1
            continue
        ok, reason = passes_filter(feat)
        if not ok:
            skipped += 1
            continue
        feat["sector"] = get_sector(ticker)
        candidates.append(feat)

    if verbose:
        print(f"  {len(raw_data)} tickers fetched  --  "
              f"{len(candidates)} passed filters  ({skipped} skipped)")

    if not candidates:
        return []

    # ── Step 3: load learned weights & score ──
    weights = _load_weights()
    for stock in candidates:
        stock["tech_score"] = technical_score(stock, weights)

    candidates.sort(key=lambda x: x["tech_score"], reverse=True)

    # ── Step 4: enrich top-N with live pre-market data ──
    top_tickers = [s["ticker"] for s in candidates[:TOP_N_FOR_AI]]
    if verbose:
        print(f"  Fetching pre-market data for top {len(top_tickers)}...")
    pm_data = get_premarket_info(top_tickers)

    for stock in candidates[:TOP_N_FOR_AI]:
        pm = pm_data.get(stock["ticker"], {})
        stock["premarket_pct"]   = pm.get("premarket_pct", 0.0)
        stock["premarket_price"] = pm.get("premarket_price", stock["price"])
        stock["tech_score"] = technical_score(stock, weights)

    candidates[:TOP_N_FOR_AI] = sorted(
        candidates[:TOP_N_FOR_AI], key=lambda x: x["tech_score"], reverse=True
    )

    top = candidates[:TOP_N_FOR_AI]

    # ── Earnings filter ──
    try:
        from backtest.filters import get_earnings_calendar
        from datetime import date as _date
        cal = get_earnings_calendar([s["ticker"] for s in top], verbose=verbose)
        today = _date.today()
        before = len(top)
        top = [s for s in top if cal.is_safe(s["ticker"], today)]
        if verbose and before - len(top):
            print(f"  Earnings: removed {before - len(top)} pick(s) "
                  f"with earnings in next 3 days")
    except Exception:
        pass

    # ── Sector concentration limit ──
    try:
        from backtest.filters import apply_sector_limit
        before = len(top)
        top = apply_sector_limit(top, max_per_sector=1, n_picks=TOP_N_FOR_AI)
        if verbose and before - len(top):
            print(f"  Sector:   removed {before - len(top)} duplicate-sector pick(s)")
    except Exception:
        pass

    # ── Fetch news for Ollama context ──
    try:
        from scanner.news import get_news_context
        top_tickers = [s["ticker"] for s in top[:TOP_N_FOR_AI]]
        news = get_news_context(top_tickers, verbose=verbose)
        for s in top:
            s["news_headline"] = news.get(s["ticker"], "")
    except Exception:
        pass

    if verbose and top:
        best = top[0]
        print(f"  Top candidate: {best['ticker']}"
              f"  score {best['tech_score']:.1f}"
              f"  RSI {best['rsi']:.1f}"
              f"  PM {best.get('premarket_pct', 0):+.1f}%")

    return top
