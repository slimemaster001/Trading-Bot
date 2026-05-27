"""
scanner/universe.py
Loads and manages the stock universe from config/universe.json.
"""

import json
import os
from typing import Optional

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_UNIVERSE_PATH = os.path.join(_BASE, "config", "universe.json")

_cache: Optional[dict] = None


def _load() -> dict:
    global _cache
    if _cache is None:
        with open(_UNIVERSE_PATH, "r", encoding="utf-8") as f:
            _cache = json.load(f)
    return _cache


def get_all_tickers() -> list[str]:
    """Return every unique ticker in the universe (deduped)."""
    data = _load()
    seen = set()
    tickers = []
    for sector_tickers in data["sectors"].values():
        for t in sector_tickers:
            if t not in seen:
                seen.add(t)
                tickers.append(t)
    return tickers


def get_tickers_by_sector(sector: str) -> list[str]:
    """Return tickers for a specific sector key."""
    data = _load()
    return data["sectors"].get(sector, [])


def get_sector(ticker: str) -> str:
    """Look up which sector a ticker belongs to."""
    data = _load()
    for sector, tickers in data["sectors"].items():
        if ticker in tickers:
            return sector
    return "unknown"


def get_filters() -> dict:
    """Return the filter thresholds defined in universe.json."""
    return _load()["filters"]


def add_ticker(ticker: str, sector: str) -> None:
    """Persist a new ticker to the universe file."""
    data = _load()
    if sector not in data["sectors"]:
        data["sectors"][sector] = []
    if ticker not in data["sectors"][sector]:
        data["sectors"][sector].append(ticker)
        with open(_UNIVERSE_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        global _cache
        _cache = data  # refresh cache


def remove_ticker(ticker: str) -> bool:
    """Remove a ticker from the universe. Returns True if found & removed."""
    data = _load()
    found = False
    for sector in data["sectors"]:
        if ticker in data["sectors"][sector]:
            data["sectors"][sector].remove(ticker)
            found = True
    if found:
        with open(_UNIVERSE_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        global _cache
        _cache = data
    return found
