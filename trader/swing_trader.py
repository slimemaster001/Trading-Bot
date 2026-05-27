"""
trader/swing_trader.py
Swing-trade execution engine (multi-day holds).

Differences from day trader:
  - Stop loss  : 3%  (vs 2% intraday)
  - Take profit: 8%  (vs 4% intraday)
  - Position   : 10% of equity per trade (vs 5% intraday)
  - Max holds  : 3 trading days
  - NO 3:30 PM force-close — Alpaca bracket orders stay live overnight
  - Morning check mode: see how existing swing positions are doing

How it works day-to-day:
  Morning (~9:15 AM ET):
    1. Run this script — it shows any open swing positions with current P&L
    2. Scans for new momentum setups
    3. You approve picks
    4. Orders placed at 9:30-9:45 AM open

  After placing orders:
    - Alpaca watches your stop and TP 24/7 — PC can be off
    - Come back next morning, repeat

  Day 3:
    - Script detects positions held 3+ days and asks if you want to close them
"""

from __future__ import annotations

import time
import os
import json
from datetime import datetime, date, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import Optional

from trader.alpaca import AlpacaClient

ET = ZoneInfo("America/New_York")

# ── Swing-specific parameters ──────────────────────────────────────────────────
STOP_LOSS_PCT    = 0.03    # 3% stop — survives normal intraday noise
TAKE_PROFIT_PCT  = 0.08    # 8% TP  — rarely hit, trailing stop handles most exits
POSITION_SIZE_PCT= 0.10    # 10% of equity per position
MAX_POSITIONS    = 3       # max concurrent swing positions
MAX_HOLD_DAYS    = 3       # close on the morning of day 4 if not already closed
ENTRY_BUFFER_PCT = 0.001   # limit order +0.1% above last price


def _now_et() -> datetime:
    return datetime.now(ET)


def _today_et() -> date:
    return _now_et().date()


# ── Morning position check ─────────────────────────────────────────────────────

def morning_check(client: AlpacaClient, memory_path: str) -> list[dict]:
    """
    Show current open swing positions with P&L and days held.
    Flags positions that have been held for MAX_HOLD_DAYS and should be closed.
    Returns list of positions that need manual close (held too long).
    """
    positions = client.get_positions()
    if not positions:
        print("\n  No open positions.")
        return []

    # Load trade log to find entry dates
    open_trades = _load_open_trades(memory_path)
    today = _today_et()

    print(f"\n  {'='*58}")
    print(f"  OPEN SWING POSITIONS  ({_now_et().strftime('%Y-%m-%d %H:%M ET')})")
    print(f"  {'='*58}")

    to_close = []
    for pos in positions:
        ticker   = pos.symbol
        qty      = int(pos.qty)
        entry    = float(pos.avg_entry_price)
        current  = float(pos.current_price)
        pnl_pct  = float(pos.unrealized_plpc) * 100
        pnl_usd  = float(pos.unrealized_pl)

        # Find entry date from trade log
        entry_date = open_trades.get(ticker, {}).get("entry_date")
        days_held  = (today - date.fromisoformat(entry_date)).days if entry_date else "?"

        flag = ""
        if isinstance(days_held, int) and days_held >= MAX_HOLD_DAYS:
            flag = "  <-- DAY 3: CLOSE TODAY"
            to_close.append(pos)

        emoji = "+" if pnl_pct >= 0 else "-"
        print(f"\n  {ticker}  ({qty} shares)")
        print(f"    Entry  : ${entry:.2f}")
        print(f"    Current: ${current:.2f}  ({pnl_pct:+.2f}%  ${pnl_usd:+.2f}){flag}")
        print(f"    Held   : {days_held} day(s)")

    print(f"\n  {'='*58}")

    if to_close:
        print(f"\n  {len(to_close)} position(s) have reached the {MAX_HOLD_DAYS}-day limit.")

    return to_close


# ── Place new swing orders ─────────────────────────────────────────────────────

def execute_swing_picks(
    client:    AlpacaClient,
    picks:     list[dict],
    memory_path: str,
) -> list[dict]:
    """
    Place bracket orders for approved swing picks.

    Bracket order = entry limit + GTC stop loss + GTC take profit.
    After placing, Alpaca watches the stop and TP 24/7 even when PC is off.
    """
    equity = client.get_equity()
    print(f"\n  Portfolio equity: ${equity:,.2f}")

    # Check how many slots are free
    open_positions = len(client.get_positions())
    slots = MAX_POSITIONS - open_positions
    if slots <= 0:
        print(f"  Max positions ({MAX_POSITIONS}) already open. Wait for one to close.")
        return []

    trades = []
    for pick in picks[:slots]:
        ticker = pick["ticker"]

        live_price = client.get_latest_price(ticker)
        if not live_price:
            live_price = pick.get("premarket_price") or pick.get("price", 0)
        if not live_price:
            print(f"  Could not get price for {ticker} — skipping")
            continue

        limit_price = round(live_price * (1 + ENTRY_BUFFER_PCT), 2)
        stop_price  = round(live_price * (1 - STOP_LOSS_PCT),    2)
        tp_price    = round(live_price * (1 + TAKE_PROFIT_PCT),  2)
        shares      = max(1, int(equity * POSITION_SIZE_PCT / live_price))
        pos_usd     = round(shares * live_price, 2)

        print(f"\n  {ticker}")
        print(f"    Entry limit : ${limit_price}   ({shares} shares = ${pos_usd:,.2f})")
        print(f"    Stop loss   : ${stop_price}  (-{STOP_LOSS_PCT*100:.0f}%)")
        print(f"    Take profit : ${tp_price}   (+{TAKE_PROFIT_PCT*100:.0f}%)")
        print(f"    Max hold    : {MAX_HOLD_DAYS} days — close manually on day {MAX_HOLD_DAYS+1} morning if needed")

        order = client.place_bracket_order(
            ticker, shares, limit_price, stop_price, tp_price
        )

        trade = {
            "ticker":            ticker,
            "entry_date":        _today_et().isoformat(),
            "entry_price":       limit_price,
            "stop_price":        stop_price,
            "take_profit_price": tp_price,
            "shares":            shares,
            "position_size_usd": pos_usd,
            "order_id":          str(order.id) if order else None,
            "status":            "open" if order else "failed",
            "pnl_pct":           None,
            "exit_price":        None,
            "exit_time":         None,
            "exit_reason":       None,
            "hold_type":         "swing",
            "signals": {
                "rsi":           pick.get("rsi"),
                "premarket_pct": pick.get("premarket_pct"),
                "rel_volume":    pick.get("rel_volume"),
                "ret_5d":        pick.get("ret_5d"),
            },
            "tech_score": pick.get("tech_score"),
            "ai_score":   pick.get("ai_score"),
            "ai_reason":  pick.get("ai_reason"),
        }

        if order:
            print(f"    Order placed (ID: {order.id})")
            print(f"    Alpaca will watch this stop/TP even when your PC is off.")
        else:
            print(f"    Order failed for {ticker}")

        trades.append(trade)
        _save_open_trade(memory_path, trade)
        time.sleep(0.5)

    return trades


# ── Close positions held too long ──────────────────────────────────────────────

def close_expired_positions(
    client:     AlpacaClient,
    to_close:   list,
    memory_path: str,
) -> None:
    """
    Close positions that have been held for MAX_HOLD_DAYS.
    Called during the morning check if any positions are day 3+.
    """
    if not to_close:
        return

    print(f"\n  Closing {len(to_close)} expired swing position(s)...")
    for pos in to_close:
        ticker = pos.symbol
        current = float(pos.current_price)
        entry   = float(pos.avg_entry_price)
        pnl_pct = (current / entry - 1) * 100

        client.close_position(ticker)
        time.sleep(1)

        emoji = "+" if pnl_pct >= 0 else "-"
        print(f"  {ticker} closed @ ${current:.2f}  P&L: {pnl_pct:+.2f}%  [max_hold]")
        _mark_trade_closed(memory_path, ticker, current, "max_hold", pnl_pct)


# ── Daily summary ──────────────────────────────────────────────────────────────

def swing_eod_summary(
    new_trades:   list[dict],
    closed_today: list[dict],
) -> str:
    lines = [
        "\n" + "=" * 60,
        "  SWING TRADING SUMMARY",
        "=" * 60,
    ]

    if new_trades:
        lines.append(f"\n  New positions opened today: {len(new_trades)}")
        for t in new_trades:
            if t["status"] == "open":
                lines.append(
                    f"    {t['ticker']:<6}  entry ${t['entry_price']}  "
                    f"stop ${t['stop_price']}  TP ${t['take_profit_price']}"
                )
        lines.append("\n  Alpaca is watching your stops and TPs. PC can be off.")
        lines.append(f"  Come back in the morning to check positions.")

    if closed_today:
        lines.append(f"\n  Positions closed today: {len(closed_today)}")
        for t in closed_today:
            e = "+" if (t.get("pnl_pct") or 0) >= 0 else "-"
            lines.append(
                f"    {t['ticker']:<6}  "
                f"P&L: {t.get('pnl_pct', 0):+.2f}%  [{t.get('exit_reason', '?')}]"
            )

    lines.append("=" * 60)
    return "\n".join(lines)


# ── Persistence helpers ────────────────────────────────────────────────────────

_SWING_LOG = "memory/swing_open_positions.json"


def _log_path(memory_path: str) -> str:
    return os.path.join(memory_path, "swing_open_positions.json")


def _load_open_trades(memory_path: str) -> dict:
    path = _log_path(memory_path)
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_open_trade(memory_path: str, trade: dict) -> None:
    trades = _load_open_trades(memory_path)
    trades[trade["ticker"]] = {
        "entry_date":   trade["entry_date"],
        "entry_price":  trade["entry_price"],
        "stop_price":   trade["stop_price"],
        "tp_price":     trade["take_profit_price"],
        "shares":       trade["shares"],
        "order_id":     trade["order_id"],
    }
    path = _log_path(memory_path)
    os.makedirs(memory_path, exist_ok=True)
    with open(path, "w") as f:
        json.dump(trades, f, indent=2)


def _mark_trade_closed(
    memory_path: str,
    ticker: str,
    exit_price: float,
    reason: str,
    pnl_pct: float,
) -> None:
    trades = _load_open_trades(memory_path)
    if ticker in trades:
        del trades[ticker]
    path = _log_path(memory_path)
    with open(path, "w") as f:
        json.dump(trades, f, indent=2)
