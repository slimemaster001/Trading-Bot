"""
learner/rec_tracker.py
Recommendation journal — tracks every pick the bot suggests and measures
how accurate those suggestions actually are over time.

What it does
------------
1. Logs every recommendation at scan time (all 10 candidates, not just top 3)
2. Records whether you traded it or not
3. Auto-links outcomes from memory/trade_results.json when positions close
4. Prints a performance report broken down by confidence tier

Why this matters
----------------
After a few weeks you can answer real questions:
  - Does [HIGH] actually win more often than [GOOD?]?
  - Is the AI score adding value over pure technical signals?
  - Which types of setups go wrong (news? sector selloff? timing?)
  - What would have happened to picks you decided to skip?

Data file: memory/rec_log.jsonl  (append-only, one JSON object per line)
"""

from __future__ import annotations

import json
import os
from datetime import date, timedelta
from collections import defaultdict
from typing import Optional

_BASE   = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LOG    = os.path.join(_BASE, "memory", "rec_log.jsonl")
_TRADES = os.path.join(_BASE, "memory", "trade_results.json")


# ── Write ──────────────────────────────────────────────────────────────────────

def log_recommendations(
    ranked:         list[dict],
    traded_tickers: list[str] | None = None,
    scan_date:      str | None       = None,
) -> None:
    """
    Append today's recommendations to the journal.
    Call this right after rank_candidates() returns, before showing the table.

    ranked          : full ranked list from rank_candidates()
    traded_tickers  : tickers actually approved and traded today (or None if unknown yet)
    scan_date       : override today's date (YYYY-MM-DD), useful for testing
    """
    today      = scan_date or date.today().isoformat()
    traded_set = set(traded_tickers or [])

    # Don't duplicate — skip if we already logged for today
    existing = {r["date"] for r in _load_log(days=1)}
    if today in existing:
        return

    os.makedirs(os.path.dirname(_LOG), exist_ok=True)

    from scorer.scorer import _conf_label

    with open(_LOG, "a", encoding="utf-8") as f:
        for rank, s in enumerate(ranked, 1):
            ticker = s["ticker"]
            score  = s.get("final_score", 0)
            rv     = s.get("rel_volume", 1.0)
            conf   = _conf_label(score, rv).strip("[] ").rstrip()

            rec = {
                "date":       today,
                "rank":       rank,
                "ticker":     ticker,
                "price":      round(s.get("price", 0), 2),
                "pm_pct":     round(s.get("premarket_pct", 0), 2),
                "rv":         round(rv, 2),
                "rsi":        round(s.get("rsi", 50), 1),
                "tech_score": round(s.get("tech_score", 0), 2),
                "ai_score":   round(s.get("ai_score", 0), 2),
                "score":      round(score, 2),
                "conf":       conf,
                "reason":     (s.get("ai_reason") or "")[:120],
                "risk":       (s.get("ai_risk")   or "")[:80],
                # outcome fields — filled in later
                "traded":     (ticker in traded_set) if traded_tickers is not None else None,
                "outcome":    None,   # "win" / "loss" / "flat"
                "pnl_pct":   None,
                "hold_days":  None,
                "exit_date":  None,
                "exit_reason": None,
            }
            f.write(json.dumps(rec) + "\n")


def mark_traded(tickers: list[str], scan_date: str | None = None) -> None:
    """
    After the user approves and orders are placed, mark those tickers as
    traded=True and everything else from today as traded=False.
    Call this right before execute_picks().
    """
    today      = scan_date or date.today().isoformat()
    ticker_set = set(tickers)

    def is_today(r: dict) -> bool:
        return r.get("date") == today

    def mark(r: dict) -> None:
        r["traded"] = r["ticker"] in ticker_set

    _rewrite_log(lambda r: is_today(r), mark)


def sync_outcomes() -> int:
    """
    Cross-reference rec_log.jsonl with memory/trade_results.json and fill in
    outcome fields for any traded recommendations that have since closed.
    Returns the number of entries updated.
    """
    if not os.path.exists(_TRADES):
        return 0

    try:
        with open(_TRADES, encoding="utf-8") as f:
            trades = json.load(f)
    except (json.JSONDecodeError, OSError):
        return 0

    # Build lookup: (ticker, entry_date_str[:10]) -> trade record
    trade_map: dict[tuple, dict] = {}
    for t in trades:
        if t.get("status") == "closed" and t.get("pnl_pct") is not None:
            key = (t["ticker"], (t.get("entry_date") or "")[:10])
            trade_map[key] = t

    updated = 0

    def needs_outcome(r: dict) -> bool:
        return (
            r.get("traded") is True
            and r.get("outcome") is None
            and (r["ticker"], r["date"]) in trade_map
        )

    def fill(r: dict) -> None:
        nonlocal updated
        t   = trade_map[(r["ticker"], r["date"])]
        pnl = float(t.get("pnl_pct", 0))
        r.update({
            "outcome":     "win" if pnl > 0 else ("flat" if pnl == 0 else "loss"),
            "pnl_pct":    round(pnl, 3),
            "hold_days":  t.get("hold_days"),
            "exit_date":  (t.get("exit_date") or "")[:10],
            "exit_reason": t.get("exit_reason", ""),
        })
        updated += 1

    _rewrite_log(needs_outcome, fill)
    return updated


# ── Read ───────────────────────────────────────────────────────────────────────

def _load_log(days: int = 90) -> list[dict]:
    if not os.path.exists(_LOG):
        return []
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    out = []
    with open(_LOG, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                if r.get("date", "") >= cutoff:
                    out.append(r)
            except json.JSONDecodeError:
                pass
    return out


def _rewrite_log(predicate, mutator) -> None:
    if not os.path.exists(_LOG):
        return
    records: list[dict] = []
    with open(_LOG, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                if predicate(r):
                    mutator(r)
                records.append(r)
            except json.JSONDecodeError:
                pass
    with open(_LOG, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


# ── Report ─────────────────────────────────────────────────────────────────────

def print_performance_report(days: int = 30) -> None:
    """
    Print a clear breakdown of recommendation accuracy.
    Run this from the launcher to see how well the bot is doing.
    """
    # Sync any new outcomes first
    updated = sync_outcomes()

    records = _load_log(days)

    W   = 72
    DIV = "-" * W

    print()
    print("=" * W)
    print(f"  RECOMMENDATION PERFORMANCE  (last {days} days)")
    print("=" * W)

    if not records:
        print()
        print("  No data yet.")
        print("  Recommendations are logged each time you run the daily scan.")
        print("  Check back after a few trading days.\n")
        return

    traded     = [r for r in records if r.get("traded") is True]
    not_traded = [r for r in records if r.get("traded") is False]
    pending    = [r for r in records if r.get("traded") is None]
    closed     = [r for r in traded  if r.get("outcome") is not None]
    wins       = [r for r in closed  if r["outcome"] == "win"]
    losses     = [r for r in closed  if r["outcome"] == "loss"]

    scan_days = len({r["date"] for r in records})

    # ── Overview ──
    print()
    print(f"  OVERVIEW  ({scan_days} scan days)")
    print(f"  {DIV}")
    print(f"  Total recommended  : {len(records):>4}  (avg {len(records)/max(scan_days,1):.1f}/day)")
    print(f"  Traded             : {len(traded):>4}  ({len(traded)/max(len(records),1)*100:.0f}%)")
    print(f"  Skipped            : {len(not_traded):>4}  ({len(not_traded)/max(len(records),1)*100:.0f}%)")
    if updated:
        print(f"  Outcomes synced    : {updated} new result(s) linked from trade log")

    # ── Traded accuracy ──
    if closed:
        wr       = len(wins) / len(closed) * 100
        avg_win  = sum(r["pnl_pct"] for r in wins)   / max(len(wins),   1)
        avg_loss = sum(r["pnl_pct"] for r in losses) / max(len(losses), 1)
        avg_hold = sum(r.get("hold_days") or 0 for r in closed) / len(closed)
        bkev     = abs(avg_loss) / (avg_win + abs(avg_loss)) * 100 if avg_win else 50

        print()
        print(f"  TRADED ACCURACY  ({len(closed)} closed trades)")
        print(f"  {DIV}")
        print(f"  Win rate      : {wr:.1f}%   "
              f"(breakeven needed: {bkev:.1f}%)  "
              f"{'ABOVE' if wr > bkev else 'BELOW'}")
        print(f"  Avg win       : {avg_win:>+.2f}%   Avg loss: {avg_loss:>+.2f}%")
        print(f"  Avg hold      : {avg_hold:.1f} days")

    # ── By confidence tier ──
    TIERS = ["HIGH ", "GOOD ", "HIGH?", "GOOD?", "OK   ", "WEAK "]
    tier_rows = []
    for tier in TIERS:
        t_all    = [r for r in records  if r.get("conf", "").ljust(5)[:5] == tier[:5]]
        t_closed = [r for r in t_all    if r.get("outcome") is not None]
        t_wins   = [r for r in t_closed if r["outcome"] == "win"]
        t_losses = [r for r in t_closed if r["outcome"] == "loss"]
        if not t_all:
            continue
        t_wr    = len(t_wins) / max(len(t_closed), 1) * 100
        t_avg_w = sum(r["pnl_pct"] for r in t_wins)   / max(len(t_wins),   1)
        t_avg_l = sum(r["pnl_pct"] for r in t_losses) / max(len(t_losses), 1) if t_losses else 0.0
        traded_n = sum(1 for r in t_all if r.get("traded") is True)
        tier_rows.append((tier.strip(), len(t_all), traded_n, len(t_closed), t_wr, t_avg_w, t_avg_l))

    if tier_rows:
        print()
        print(f"  BY CONFIDENCE TIER")
        print(f"  {DIV}")
        print(f"  {'Tier':<8}  {'Rec':>4}  {'Traded':>6}  {'Closed':>6}  "
              f"{'Win%':>5}  {'Avg W':>7}  {'Avg L':>7}")
        print(f"  {DIV}")
        for tier, n_all, n_traded, n_closed, t_wr, t_avg_w, t_avg_l in tier_rows:
            # Only show win rate if we have enough data
            if n_closed >= 3:
                wr_str   = f"{t_wr:>4.0f}%"
                avgw_str = f"{t_avg_w:>+6.2f}%"
                avgl_str = f"{t_avg_l:>+6.2f}%"
            else:
                wr_str   = "  --"
                avgw_str = "     --"
                avgl_str = "     --"
            print(f"  [{tier:<5}]  {n_all:>4}  {n_traded:>6}  {n_closed:>6}  "
                  f"{wr_str}  {avgw_str}  {avgl_str}")
        print(f"  {DIV}")
        print(f"  Note: win% only shown when >= 3 closed trades for that tier.")

    # ── Skipped picks at HIGH confidence ──
    high_skips = [r for r in not_traded
                  if r.get("conf", "").startswith("HIGH") and not r.get("conf", "").endswith("?")]
    if high_skips:
        print()
        print(f"  SKIPPED [HIGH] PICKS  (you passed on these)")
        print(f"  {DIV}")
        print(f"  {'Date':<12} {'Ticker':<7} {'Score':>5}  {'PM%':>5}  Reason")
        print(f"  {DIV}")
        for r in sorted(high_skips, key=lambda x: x["date"], reverse=True)[:8]:
            print(f"  {r['date']:<12} {r['ticker']:<7} {r['score']:>5.2f}"
                  f"  {r['pm_pct']:>+4.1f}%  {r.get('reason','')[:42]}")

    # ── Worst losses — learn from them ──
    worst = sorted(losses, key=lambda r: r.get("pnl_pct", 0))[:5]
    if worst:
        print()
        print(f"  WORST LOSSES  (learn from these)")
        print(f"  {DIV}")
        print(f"  {'Date':<12} {'Ticker':<7} {'Conf':<8} {'P&L':>6}  "
              f"{'Exit':<12}  Risk flagged at scan")
        print(f"  {DIV}")
        for r in worst:
            print(f"  {r['date']:<12} {r['ticker']:<7} [{r.get('conf','?'):<5}] "
                  f"  {r.get('pnl_pct',0):>+5.2f}%  "
                  f"{r.get('exit_reason','?'):<12}  "
                  f"{r.get('risk','')[:35]}")

    print()
    print("=" * W)
    print()
