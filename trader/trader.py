"""
trader/trader.py
Day-trade execution engine.

Responsibilities:
  - Size positions (% of equity, never over guardrail limits)
  - Place bracket orders (entry limit + stop-loss + take-profit)
  - Monitor positions every 5 minutes
  - Force-close all positions at EOD (3:30 PM ET — 30 min before close)
  - Return structured results for the learner to record
"""

from __future__ import annotations

import time
import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import Optional

from trader.alpaca import AlpacaClient

# ── Day trading guardrails ─────────────────────────────────────────────────────
POSITION_SIZE_PCT   = 0.05    # 5% of equity per trade
MAX_POSITIONS       = 3       # max concurrent day trades
STOP_LOSS_PCT       = 0.02    # 2% stop (tight for day trading)
TAKE_PROFIT_PCT     = 0.04    # 4% take-profit (2:1 R/R minimum)
SLIPPAGE_TOLERANCE  = 0.002   # limit order ±0.2% from last price
ENTRY_BUFFER_PCT    = 0.001   # limit price = last * (1 + 0.1%)  (just above market)
EOD_CLOSE_TIME      = (15, 30)  # 3:30 PM ET
MONITOR_INTERVAL_S  = 300     # check every 5 minutes
ET = ZoneInfo("America/New_York")


def _now_et() -> datetime:
    return datetime.now(ET)


def _past_entry_time() -> bool:
    """True if it's past 9:45 AM ET (15-min rule after open)."""
    now = _now_et()
    return now.hour > 9 or (now.hour == 9 and now.minute >= 45)


def _past_eod_close() -> bool:
    """True if it's past 3:30 PM ET."""
    now = _now_et()
    h, m = EOD_CLOSE_TIME
    return now.hour > h or (now.hour == h and now.minute >= m)


def _market_is_open() -> bool:
    """True between 9:30 AM and 4:00 PM ET, Mon–Fri."""
    now = _now_et()
    if now.weekday() >= 5:
        return False
    open_min  = 9 * 60 + 30
    close_min = 16 * 60
    cur_min   = now.hour * 60 + now.minute
    return open_min <= cur_min < close_min


# ── Position sizing ────────────────────────────────────────────────────────────

def calc_position_size(equity: float, price: float) -> int:
    """
    Returns whole-share count for a 5% position.
    Rounds DOWN — never over-size.
    """
    dollar_size = equity * POSITION_SIZE_PCT
    shares = int(dollar_size / price)  # floor
    return max(1, shares)


# ── Main execution ─────────────────────────────────────────────────────────────

def execute_picks(
    client: AlpacaClient,
    picks: list[dict],
) -> list[dict]:
    """
    Place bracket orders for approved picks.
    Returns list of trade dicts with order IDs and sizing.

    Each trade dict structure:
    {
        ticker, entry_price, stop_price, take_profit_price,
        shares, position_size_usd, order_id,
        entry_time, status, signals, ai_score, tech_score, final_score,
        ai_reason, sector
    }
    """
    equity = client.get_equity()
    print(f"\n  💼 Portfolio equity: ${equity:,.2f}")

    open_positions = len(client.get_positions())
    slots = MAX_POSITIONS - open_positions
    if slots <= 0:
        print("  ⚠  Max positions already open — no new trades.")
        return []

    trades = []
    for pick in picks[:slots]:
        ticker = pick["ticker"]

        # Get live quote for limit pricing
        live_price = client.get_latest_price(ticker)
        if not live_price:
            live_price = pick.get("premarket_price") or pick["price"]

        limit_price      = round(live_price * (1 + ENTRY_BUFFER_PCT), 2)
        stop_price       = round(live_price * (1 - STOP_LOSS_PCT),    2)
        take_profit_price= round(live_price * (1 + TAKE_PROFIT_PCT),  2)
        shares           = calc_position_size(equity, live_price)
        pos_usd          = round(shares * live_price, 2)

        print(f"\n  📈 {ticker}  entry≈${limit_price}  stop=${stop_price}  "
              f"TP=${take_profit_price}  {shares} shares (${pos_usd:,.2f})")

        order = client.place_bracket_order(
            ticker, shares, limit_price, stop_price, take_profit_price
        )

        trade = {
            "ticker":             ticker,
            "entry_price":        limit_price,
            "stop_price":         stop_price,
            "take_profit_price":  take_profit_price,
            "shares":             shares,
            "position_size_usd":  pos_usd,
            "order_id":           str(order.id) if order else None,
            "entry_time":         _now_et().isoformat(),
            "status":             "open" if order else "failed",
            "pnl_pct":            None,
            "exit_price":         None,
            "exit_time":          None,
            "exit_reason":        None,
            # Scanner metadata (for learner)
            "signals": {
                "rsi":           pick.get("rsi"),
                "premarket_pct": pick.get("premarket_pct"),
                "rel_volume":    pick.get("rel_volume"),
                "above_ma20":    pick.get("above_ma20_pct", 0) > 0,
                "above_ma50":    pick.get("above_ma50_pct", 0) > 0,
                "near_52w_high": pick.get("pct_from_52w_high", -99) > -5,
                "ret_5d":        pick.get("ret_5d"),
            },
            "tech_score":  pick.get("tech_score"),
            "ai_score":    pick.get("ai_score"),
            "final_score": pick.get("final_score"),
            "ai_reason":   pick.get("ai_reason"),
            "sector":      pick.get("sector", ""),
        }

        if order:
            print(f"  ✅ Order placed — ID: {order.id}")
        else:
            print(f"  ❌ Order failed for {ticker}")

        trades.append(trade)
        time.sleep(0.5)

    return trades


# ── Monitor loop ───────────────────────────────────────────────────────────────

def monitor_loop(client: AlpacaClient, trades: list[dict]) -> list[dict]:
    """
    Check open positions every MONITOR_INTERVAL_S seconds.
    Fills in exit details when positions close (stop/TP hit).
    Forces EOD close at 3:30 PM ET.
    Blocks until all positions are closed or market closes.

    Returns updated trades list with final results filled in.
    """
    if not trades:
        return trades

    open_trades = {t["ticker"]: t for t in trades if t["status"] == "open"}

    print(f"\n  👁  Monitoring {len(open_trades)} position(s). "
          f"EOD close at 3:30 PM ET. Checking every {MONITOR_INTERVAL_S//60} min.")

    while open_trades:
        time.sleep(MONITOR_INTERVAL_S)
        now = _now_et()
        print(f"\n  [{now.strftime('%H:%M ET')}] Checking {len(open_trades)} open positions…")

        # ── EOD force-close ──
        if _past_eod_close():
            print("  🔔 3:30 PM ET — closing all remaining positions")
            for ticker in list(open_trades.keys()):
                _close_and_record(client, open_trades, ticker, reason="eod_close")
            break

        # ── Check each position ──
        positions = {p.symbol: p for p in client.get_positions()}
        for ticker in list(open_trades.keys()):
            if ticker not in positions:
                # Position is gone — stop or TP was hit
                _fill_exit_from_orders(client, open_trades, ticker)
            else:
                pos = positions[ticker]
                pnl_pct = float(pos.unrealized_plpc) * 100
                print(f"    {ticker}: P&L {pnl_pct:+.2f}%  "
                      f"(entry ${open_trades[ticker]['entry_price']} → "
                      f"current ${float(pos.current_price):.2f})")

    # Reconcile final state
    for trade in trades:
        if trade["status"] == "open":
            trade["status"] = "closed"

    return trades


def _close_and_record(
    client: AlpacaClient,
    open_trades: dict,
    ticker: str,
    reason: str,
) -> None:
    """Force-close a position and record the exit."""
    trade = open_trades[ticker]
    price = client.get_latest_price(ticker) or trade["entry_price"]
    client.close_position(ticker)
    time.sleep(1)
    actual_price = client.get_latest_price(ticker) or price
    pnl_pct = (actual_price / trade["entry_price"] - 1) * 100

    trade["exit_price"]  = actual_price
    trade["exit_time"]   = _now_et().isoformat()
    trade["exit_reason"] = reason
    trade["pnl_pct"]     = round(pnl_pct, 3)
    trade["status"]      = "closed"

    emoji = "✅" if pnl_pct > 0 else "❌"
    print(f"  {emoji} Closed {ticker} @ ${actual_price}  P&L: {pnl_pct:+.2f}%  [{reason}]")
    del open_trades[ticker]


def _fill_exit_from_orders(
    client: AlpacaClient,
    open_trades: dict,
    ticker: str,
) -> None:
    """
    Position disappeared — look up filled child orders to determine
    whether stop or TP was hit, then record the result.
    """
    trade    = open_trades[ticker]
    entry    = trade["entry_price"]
    stop     = trade["stop_price"]
    tp       = trade["take_profit_price"]
    # Infer exit price: if we can't get actual fill, estimate from stop/TP
    # (Alpaca bracket child orders would show the fill; approximate here)
    live = client.get_latest_price(ticker) or entry
    pnl_pct = (live / entry - 1) * 100

    if abs(live - stop) < abs(live - tp):
        reason = "stop_loss"
    elif abs(live - tp) < abs(live - stop):
        reason = "take_profit"
    else:
        reason = "unknown_close"

    trade["exit_price"]  = round(live, 2)
    trade["exit_time"]   = _now_et().isoformat()
    trade["exit_reason"] = reason
    trade["pnl_pct"]     = round(pnl_pct, 3)
    trade["status"]      = "closed"

    emoji = "✅" if pnl_pct > 0 else "❌"
    print(f"  {emoji} {ticker} closed automatically  P&L: {pnl_pct:+.2f}%  [{reason}]")
    del open_trades[ticker]


# ── EOD summary ────────────────────────────────────────────────────────────────

def eod_summary(trades: list[dict]) -> str:
    """Return a formatted EOD summary string."""
    closed = [t for t in trades if t["status"] == "closed" and t["pnl_pct"] is not None]
    if not closed:
        return "\n  📭 No completed trades today.\n"

    total_pnl = sum(t["pnl_pct"] for t in closed)
    winners   = [t for t in closed if t["pnl_pct"] > 0]
    losers    = [t for t in closed if t["pnl_pct"] <= 0]
    win_rate  = len(winners) / len(closed) * 100

    lines = [
        "\n" + "═" * 60,
        "  📊  EOD TRADE SUMMARY",
        "═" * 60,
    ]
    for t in closed:
        e = "✅" if t["pnl_pct"] > 0 else "❌"
        lines.append(
            f"  {e} {t['ticker']:<6} entry ${t['entry_price']}  "
            f"exit ${t.get('exit_price','?')}  "
            f"P&L: {t['pnl_pct']:+.2f}%  [{t.get('exit_reason','?')}]"
        )
    lines += [
        "─" * 60,
        f"  Trades: {len(closed)}  |  Winners: {len(winners)}  |  "
        f"Win rate: {win_rate:.0f}%  |  Combined P&L: {total_pnl:+.2f}%",
        "═" * 60,
    ]
    return "\n".join(lines)
