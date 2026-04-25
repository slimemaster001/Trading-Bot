"""
research.py — Free market research helpers for Claude routines

Two data sources (both free):
  - yfinance: stock prices, fundamentals, Yahoo Finance news (no API key needed)
  - Tavily:   web search for macro news, sector trends (1,000 free/month)

Usage:
    from bot.research import get_stock_snapshot, get_news, web_search

    snap = get_stock_snapshot("AAPL")
    news = get_news(["AAPL", "MSFT"])
    macro = web_search("Federal Reserve interest rate decision this week")
"""

import os
import json
from datetime import datetime, timedelta

import yfinance as yf
import requests


# ── yfinance helpers ───────────────────────────────────────────────────────────

def get_stock_snapshot(ticker: str) -> dict:
    """
    Return a concise snapshot of a stock: price, change, key stats, recent news.
    No API key needed.
    """
    try:
        t = yf.Ticker(ticker)
        info = t.info
        hist = t.history(period="5d")

        if hist.empty:
            return {"ticker": ticker, "error": "No price data found"}

        current_price = round(float(hist["Close"].iloc[-1]), 2)
        prev_price    = round(float(hist["Close"].iloc[-2]), 2) if len(hist) > 1 else current_price
        day_change    = round(((current_price - prev_price) / prev_price) * 100, 2)

        # 50-day and 200-day moving averages
        hist_long = t.history(period="1y")
        ma50  = round(float(hist_long["Close"].tail(50).mean()), 2)  if len(hist_long) >= 50  else None
        ma200 = round(float(hist_long["Close"].tail(200).mean()), 2) if len(hist_long) >= 200 else None

        # RSI (14-day)
        rsi = None
        if len(hist_long) >= 15:
            closes = hist_long["Close"].tail(15)
            delta  = closes.diff()
            gain   = delta.clip(lower=0).mean()
            loss   = (-delta.clip(upper=0)).mean()
            rsi    = round(100 - (100 / (1 + gain / loss)), 1) if loss != 0 else 100.0

        # Recent news (last 5 headlines)
        news = []
        for article in (t.news or [])[:5]:
            news.append({
                "title":     article.get("title", ""),
                "publisher": article.get("publisher", ""),
                "link":      article.get("link", ""),
            })

        return {
            "ticker":        ticker,
            "price":         current_price,
            "day_change_pct": day_change,
            "ma50":          ma50,
            "ma200":         ma200,
            "rsi":           rsi,
            "above_ma50":    current_price > ma50  if ma50  else None,
            "above_ma200":   current_price > ma200 if ma200 else None,
            "volume":        info.get("averageVolume", 0),
            "market_cap":    info.get("marketCap", 0),
            "sector":        info.get("sector", "Unknown"),
            "news":          news,
            "fetched_at":    datetime.now().isoformat(),
        }

    except Exception as e:
        return {"ticker": ticker, "error": str(e)}


def get_portfolio_snapshot(tickers: list[str]) -> list[dict]:
    """Get snapshots for all tickers in the portfolio."""
    return [get_stock_snapshot(t) for t in tickers]


def get_market_overview() -> dict:
    """
    Get a quick macro overview using SPY, QQQ, VIX proxies.
    """
    snapshots = {}
    for symbol, label in [("SPY", "S&P 500"), ("QQQ", "Nasdaq"), ("^VIX", "VIX")]:
        try:
            t    = yf.Ticker(symbol)
            hist = t.history(period="5d")
            if not hist.empty:
                price    = round(float(hist["Close"].iloc[-1]), 2)
                prev     = round(float(hist["Close"].iloc[-2]), 2) if len(hist) > 1 else price
                chg      = round(((price - prev) / prev) * 100, 2)
                snapshots[label] = {"price": price, "day_change_pct": chg}
        except Exception as e:
            snapshots[label] = {"error": str(e)}

    return snapshots


def get_news(tickers: list[str], max_per_ticker: int = 3) -> dict:
    """
    Pull Yahoo Finance news for a list of tickers.
    Returns dict: {ticker: [list of news articles]}
    """
    result = {}
    for ticker in tickers:
        try:
            t    = yf.Ticker(ticker)
            news = []
            for article in (t.news or [])[:max_per_ticker]:
                news.append({
                    "title":     article.get("title", ""),
                    "publisher": article.get("publisher", ""),
                    "link":      article.get("link", ""),
                })
            result[ticker] = news
        except Exception as e:
            result[ticker] = [{"title": f"Error: {e}"}]
    return result


# ── Tavily web search ──────────────────────────────────────────────────────────

def web_search(query: str, max_results: int = 5) -> list[dict]:
    """
    Search the web using Tavily API (free tier: 1,000 searches/month).
    Returns list of {title, url, content} dicts.

    Requires: TAVILY_API_KEY environment variable.
    """
    api_key = os.environ.get("TAVILY_API_KEY")
    if not api_key:
        return [{"error": "TAVILY_API_KEY not set — skipping web search"}]

    try:
        response = requests.post(
            "https://api.tavily.com/search",
            json={
                "api_key":      api_key,
                "query":        query,
                "max_results":  max_results,
                "search_depth": "basic",   # "basic" uses fewer credits than "advanced"
                "include_answer": True,
            },
            timeout=15,
        )
        response.raise_for_status()
        data = response.json()

        results = []
        if data.get("answer"):
            results.append({"type": "answer", "content": data["answer"]})
        for r in data.get("results", []):
            results.append({
                "type":    "result",
                "title":   r.get("title", ""),
                "url":     r.get("url", ""),
                "content": r.get("content", "")[:500],  # trim long snippets
            })
        return results

    except Exception as e:
        return [{"error": f"Tavily search failed: {e}"}]


# ── Combined pre-market report ─────────────────────────────────────────────────

def generate_premarket_brief(held_tickers: list[str], watchlist_tickers: list[str]) -> str:
    """
    Generate a structured pre-market research brief as a Markdown string.
    Used by the pre-market Claude routine.
    """
    lines = [f"# Pre-Market Brief — {datetime.now().strftime('%Y-%m-%d')}", ""]

    # Market overview
    lines.append("## Market Overview")
    overview = get_market_overview()
    for label, data in overview.items():
        if "error" not in data:
            arrow = "▲" if data["day_change_pct"] > 0 else "▼"
            lines.append(f"- **{label}:** ${data['price']} {arrow} {data['day_change_pct']}%")
    lines.append("")

    # VIX assessment
    vix = overview.get("VIX", {}).get("price", 0)
    if vix > 30:
        lines.append("⚠️ **VIX > 30 — High volatility. Reduce position sizes, widen stops.**")
    elif vix > 20:
        lines.append("🟡 **VIX elevated (20–30) — Proceed with caution.**")
    else:
        lines.append("🟢 **VIX normal — Standard operation.**")
    lines.append("")

    # Macro news
    lines.append("## Macro News")
    macro_results = web_search("stock market macro news today Federal Reserve economic data")
    for r in macro_results[:3]:
        if r.get("type") == "answer":
            lines.append(r["content"])
        elif r.get("title"):
            lines.append(f"- [{r['title']}]({r['url']})")
    lines.append("")

    # Held positions
    if held_tickers:
        lines.append("## Held Positions — Pre-Market Check")
        for snap in get_portfolio_snapshot(held_tickers):
            if "error" in snap:
                lines.append(f"- **{snap['ticker']}:** Error — {snap['error']}")
                continue
            arrow  = "▲" if snap["day_change_pct"] > 0 else "▼"
            ma_str = f"MA50: {'✅' if snap['above_ma50'] else '❌'}  MA200: {'✅' if snap['above_ma200'] else '❌'}"
            rsi_str = f"RSI: {snap['rsi']}" if snap["rsi"] else "RSI: N/A"
            lines.append(f"- **{snap['ticker']}:** ${snap['price']} {arrow}{snap['day_change_pct']}%  |  {ma_str}  |  {rsi_str}")
            for article in snap.get("news", [])[:2]:
                lines.append(f"  - 📰 {article['title']}")
        lines.append("")

    # Watchlist
    if watchlist_tickers:
        lines.append("## Watchlist — Signal Check")
        for snap in get_portfolio_snapshot(watchlist_tickers):
            if "error" in snap:
                lines.append(f"- **{snap['ticker']}:** Error — {snap['error']}")
                continue
            signals = 0
            signal_details = []
            if snap.get("above_ma50") and snap.get("above_ma200"):
                signals += 1; signal_details.append("Trend ✅")
            rsi = snap.get("rsi")
            if rsi and 45 <= rsi <= 70:
                signals += 1; signal_details.append(f"RSI ✅ ({rsi})")
            lines.append(f"- **{snap['ticker']}:** ${snap['price']}  |  Signals: {signals}/5  |  {', '.join(signal_details)}")
        lines.append("")

    lines.append(f"*Generated at {datetime.now().strftime('%H:%M')} CT*")
    return "\n".join(lines)


if __name__ == "__main__":
    # Quick test
    print(json.dumps(get_stock_snapshot("AAPL"), indent=2))
    print("\n--- Market Overview ---")
    print(json.dumps(get_market_overview(), indent=2))
