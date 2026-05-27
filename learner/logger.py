"""
learner/logger.py
Persists every day's trades to memory/trade_results.json.

The file is a flat JSON array of trade dicts — append-only.
The learner reads this to find patterns over time.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_RESULTS_PATH = os.path.join(_BASE, "memory", "trade_results.json")


def _load() -> list[dict]:
    if not os.path.exists(_RESULTS_PATH):
        return []
    with open(_RESULTS_PATH, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return []


def _save(records: list[dict]) -> None:
    with open(_RESULTS_PATH, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2)


# ── Public API ─────────────────────────────────────────────────────────────────

def log_trades(trades: list[dict]) -> None:
    """
    Append today's completed trades to trade_results.json.
    Only logs trades with status='closed' and a real pnl_pct.
    """
    records = _load()
    today   = datetime.now(ET).strftime("%Y-%m-%d")

    for trade in trades:
        if trade.get("status") != "closed":
            continue
        if trade.get("pnl_pct") is None:
            continue

        record = {
            "date":          today,
            "ticker":        trade["ticker"],
            "entry_price":   trade.get("entry_price"),
            "exit_price":    trade.get("exit_price"),
            "shares":        trade.get("shares"),
            "pnl_pct":       trade.get("pnl_pct"),
            "exit_reason":   trade.get("exit_reason"),
            "entry_time":    trade.get("entry_time"),
            "exit_time":     trade.get("exit_time"),
            "tech_score":    trade.get("tech_score"),
            "ai_score":      trade.get("ai_score"),
            "final_score":   trade.get("final_score"),
            "ai_reason":     trade.get("ai_reason"),
            "sector":        trade.get("sector", ""),
            "signals":       trade.get("signals", {}),
            "status":        "closed",
        }
        records.append(record)

    _save(records)
    closed = [t for t in trades if t.get("status") == "closed"]
    print(f"  💾 Logged {len(closed)} trade(s) to trade_results.json "
          f"(total records: {len(records)})")


def get_all_results() -> list[dict]:
    """Return all historical trade records."""
    return _load()


def get_recent_results(n: int = 50) -> list[dict]:
    """Return the N most recent closed trades."""
    all_r = _load()
    closed = [r for r in all_r if r.get("status") == "closed"]
    return closed[-n:]


def get_stats() -> dict:
    """Quick summary stats for the terminal."""
    records = [r for r in _load() if r.get("status") == "closed"]
    if not records:
        return {"total": 0}

    wins    = [r for r in records if r.get("pnl_pct", 0) > 0]
    losses  = [r for r in records if r.get("pnl_pct", 0) <= 0]
    avg_win  = round(sum(r["pnl_pct"] for r in wins)   / max(len(wins),   1), 3)
    avg_loss = round(sum(r["pnl_pct"] for r in losses)  / max(len(losses), 1), 3)
    win_rate = round(len(wins) / len(records) * 100, 1)

    return {
        "total":    len(records),
        "wins":     len(wins),
        "losses":   len(losses),
        "win_rate": win_rate,
        "avg_win":  avg_win,
        "avg_loss": avg_loss,
        "total_pnl": round(sum(r["pnl_pct"] for r in records), 3),
    }
