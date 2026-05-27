"""
scanner/news.py  —  Controlled private news pipe for local Ollama scoring.

Privacy architecture
────────────────────
Your local Ollama model NEVER connects to the internet itself.
It only knows what you pass to it in the prompt.

This module is the ONLY thing that fetches external data, and it does so
in a strictly controlled way:
  - Only stock ticker symbols are sent externally (e.g. "AAPL")
  - No account info, positions, trade history, or personal data leaves this machine
  - You can read this file line by line to verify exactly what is sent
  - Results are passed to Ollama as plain text — Ollama processes them locally

Data sources
────────────
1. yfinance (Yahoo Finance news)   — free, no API key, always available
2. NewsAPI                         — optional; set NEWSAPI_KEY in .env for richer coverage
                                     Free tier: 1,000 requests/day (never auto-enabled)

What Ollama sees (example):
    "Recent news for AAPL: Apple reports record iPhone sales | Morgan Stanley
     raises price target to $230 | Apple announces $110B buyback program"

Ollama uses this as context when scoring a candidate — the same way you'd
glance at headlines before deciding whether to trade a stock.

Usage:
    from scanner.news import get_news_context
    news = get_news_context(["AAPL", "MSFT", "NVDA"])
    # {"AAPL": "Headline 1 | Headline 2", "MSFT": "...", "NVDA": ""}
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta

_NEWSAPI_KEY = os.environ.get("NEWSAPI_KEY", "")
_MAX_HEADLINES_PER_TICKER = 4
_MAX_HEADLINE_CHARS       = 100
_NEWS_MAX_AGE_DAYS        = 3


# ── yfinance source (always available, no key needed) ─────────────────────────

def _yf_headlines(ticker: str) -> list[str]:
    """
    Fetch up to 5 recent headlines from Yahoo Finance via yfinance.
    Sends: ticker symbol only.  Returns: list of headline strings.
    """
    try:
        import yfinance as yf
        items = yf.Ticker(ticker).news or []
        cutoff = datetime.now() - timedelta(days=_NEWS_MAX_AGE_DAYS)
        results = []
        for item in items[:8]:
            ts = item.get("providerPublishTime", 0)
            if ts and datetime.fromtimestamp(ts) < cutoff:
                continue
            title = (item.get("title") or "").strip()
            if len(title) > 10:
                results.append(title)
            if len(results) >= _MAX_HEADLINES_PER_TICKER:
                break
        return results
    except Exception:
        return []


# ── NewsAPI source (optional, richer coverage) ────────────────────────────────

def _newsapi_headlines(ticker: str) -> list[str]:
    """
    Fetch from NewsAPI if NEWSAPI_KEY is configured.
    Set NEWSAPI_KEY=your_key in .env to enable.
    Free tier: 1,000 req/day — https://newsapi.org
    Sends: ticker symbol only (used as the search query).
    """
    if not _NEWSAPI_KEY:
        return []
    try:
        import requests
        r = requests.get(
            "https://newsapi.org/v2/everything",
            params={
                "q":        ticker,
                "sortBy":   "publishedAt",
                "pageSize": 3,
                "language": "en",
                "from":     (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d"),
                "apiKey":   _NEWSAPI_KEY,
            },
            timeout=5,
        )
        r.raise_for_status()
        return [(a.get("title") or "").strip()
                for a in r.json().get("articles", [])
                if a.get("title")]
    except Exception:
        return []


# ── Public interface ──────────────────────────────────────────────────────────

def get_news_context(
    tickers: list[str],
    verbose: bool = False,
) -> dict[str, str]:
    """
    Fetch recent headlines for each ticker.

    Returns {ticker: "Headline 1 | Headline 2 | ..."} — one string per ticker.
    Empty string means no recent news was found (not an error — just no news).

    Pass the result to scorer/ollama.py as `news_context` so your local
    Ollama model can incorporate current events into its scoring.

    Nothing in this function ever reveals your account, positions, or
    personal data to any external service.
    """
    results: dict[str, str] = {}

    for ticker in tickers:
        headlines = _yf_headlines(ticker)

        # Supplement with NewsAPI if configured and yfinance gave few results
        if _NEWSAPI_KEY and len(headlines) < 2:
            headlines += _newsapi_headlines(ticker)

        # Truncate each headline for a clean prompt
        clipped = [h[:_MAX_HEADLINE_CHARS] for h in headlines[:_MAX_HEADLINES_PER_TICKER]]
        results[ticker] = " | ".join(clipped)

        if verbose and results[ticker]:
            print(f"  {ticker}: {results[ticker][:80]}...")

        time.sleep(0.05)   # ~20 req/sec max — polite rate limiting

    found = sum(1 for v in results.values() if v)
    if verbose:
        print(f"  News: {found}/{len(tickers)} tickers have recent headlines")

    return results


def news_enabled() -> bool:
    """True if at least one news source is available (always True — yfinance is free)."""
    return True
