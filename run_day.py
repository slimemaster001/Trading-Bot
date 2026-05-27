"""
run_day.py — Day Trading Bot Runner
====================================
The one script you need. Open your laptop, run this, approve picks, walk away.

USAGE
-----
Full day (scan → approve → trade → monitor → close → learn):
    python run_day.py

Scan only — see picks but don't place any trades:
    python run_day.py --scan-only

Backtest the strategy over a historical period:
    python run_day.py --backtest 2025-01-01 2025-06-01
    python run_day.py --backtest 2025-01-01 2025-06-01 --picks 5
    python run_day.py --backtest 2025-01-01 2025-06-01 --strategy gap_and_go

    Strategies: momentum (default), mean_reversion, gap_and_go, breakout

Run the learner (analyse results, update scoring weights):
    python run_day.py --learn

Check bot stats (win rate, P&L history):
    python run_day.py --stats

Full backtest report (all saved results, explained in plain English):
    python run_day.py --report

ENVIRONMENT VARIABLES REQUIRED (set before running)
-----------------------------------------------------
    ALPACA_API_KEY      — your Alpaca paper/live key
    ALPACA_SECRET_KEY   — your Alpaca secret
    OLLAMA_MODEL        — (optional) override model, e.g. "llama3.2"
    OLLAMA_URL          — (optional) default http://localhost:11434
    DISCORD_BOT_TOKEN   — (optional) for Discord DM notifications
    DISCORD_USER_ID     — (optional) your Discord user ID
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

# Force UTF-8 output so emojis and special characters work in Windows CMD/PowerShell
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Auto-load .env from project root so you never have to set env vars manually
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

ET = ZoneInfo("America/New_York")

_BASE = os.path.dirname(os.path.abspath(__file__))

BANNER = """
╔══════════════════════════════════════════════════════════════╗
║           📈  DAY TRADING BOT  v2.0  (Powered by Ollama)    ║
║           Scan → AI Score → Approve → Auto-Trade → Learn    ║
╚══════════════════════════════════════════════════════════════╝
"""


# ── Helpers ────────────────────────────────────────────────────────────────────

def _now_et() -> datetime:
    return datetime.now(ET)


def _check_market_timing() -> str:
    """
    Returns:
      'premarket'  — before 9:30 AM ET (best time to scan)
      'open'       — market is open
      'closed'     — after hours / weekend
    """
    now = _now_et()
    if now.weekday() >= 5:
        return "closed"
    m = now.hour * 60 + now.minute
    if m < 9 * 60 + 30:
        return "premarket"
    if m < 16 * 60:
        return "open"
    return "closed"


def _wait_for_market(target_h: int, target_m: int) -> None:
    """Block until clock reaches target_h:target_m ET."""
    while True:
        now = _now_et()
        cur_m = now.hour * 60 + now.minute
        tgt_m = target_h * 60 + target_m
        if cur_m >= tgt_m:
            break
        wait = tgt_m - cur_m
        print(f"  ⏳ Waiting {wait} minute(s) until {target_h:02d}:{target_m:02d} ET…",
              end="\r", flush=True)
        time.sleep(30)
    print()


def _confirm_pending_picks(approved: list[dict]) -> list[dict]:
    """
    At market open, re-check volume for picks that were flagged [HIGH?] or [GOOD?]
    during the premarket scan (low volume at scan time).

    Picks with RV >= 1.2x at scan time pass straight through.
    Picks with RV < 1.2x are re-checked with live intraday volume.
    If the 15-min volume pace is >= 1.5x normal, the move is confirmed.
    If still thin, the pick is dropped and the user is told why.
    """
    from scorer.scorer import _conf_label
    from scanner.data import confirm_volume_at_open

    ready   = [p for p in approved if "?" not in _conf_label(
                   p.get("final_score", 0), p.get("rel_volume", 1.0))]
    pending = [p for p in approved if "?" in  _conf_label(
                   p.get("final_score", 0), p.get("rel_volume", 1.0))]

    if not pending:
        return ready   # nothing to re-check

    print(f"\n  Volume check: {len(pending)} pending pick(s) had low premarket volume")
    tickers = [p["ticker"] for p in pending]
    vol     = confirm_volume_at_open(tickers)

    for p in pending:
        result = vol.get(p["ticker"], {})
        pace   = result.get("rv_pace", 0.0)
        if result.get("confirmed"):
            print(f"    {p['ticker']:<6}  RV pace {pace:.1f}x  -- confirmed  -> trading")
            ready.append(p)
        else:
            print(f"    {p['ticker']:<6}  RV pace {pace:.1f}x  -- dropped    "
                  f"(volume not there at open)")

    return ready


def _get_approved_picks(scored: list[dict]) -> list[dict]:
    """
    Show the ranked table and ask which picks to trade.
    User can type pick numbers (e.g. "1 2 3") or 'all' for top 3, or 'none'.
    """
    from scorer.scorer import format_picks_table
    print(format_picks_table(scored, top_n=10))

    print("\n  ┌─────────────────────────────────────────────────┐")
    print("  │  Which picks do you want to trade?              │")
    print("  │  Enter numbers (e.g. '1 2 3')  or  'all' (top 3) │")
    print("  │  or 'none' to skip trading today                │")
    print("  │  Pressing Enter = 'all' (top 3)                 │")
    print("  └─────────────────────────────────────────────────┘")

    raw = input("  > ").strip().lower()

    if raw in ("none", "n", "skip", "0"):
        return []

    if raw in ("", "all", "a"):
        return scored[:3]

    try:
        indices = [int(x) - 1 for x in raw.split()]
        picks = [scored[i] for i in indices if 0 <= i < len(scored)]
        return picks
    except (ValueError, IndexError):
        print("  ⚠  Couldn't parse input — defaulting to top 3")
        return scored[:3]


def _notify_discord(message: str) -> None:
    """Send a Discord DM if env vars are set. Silent if not configured."""
    token   = os.environ.get("DISCORD_BOT_TOKEN")
    user_id = os.environ.get("DISCORD_USER_ID")
    if not token or not user_id:
        return
    try:
        import requests
        headers = {"Authorization": f"Bot {token}", "Content-Type": "application/json"}
        # Open DM channel
        dm_r = requests.post(
            "https://discord.com/api/v10/users/@me/channels",
            headers=headers,
            json={"recipient_id": user_id},
            timeout=10,
        )
        channel_id = dm_r.json().get("id")
        if channel_id:
            requests.post(
                f"https://discord.com/api/v10/channels/{channel_id}/messages",
                headers=headers,
                json={"content": message[:1999]},
                timeout=10,
            )
    except Exception:
        pass  # Discord is optional


# ── Sub-commands ───────────────────────────────────────────────────────────────

def cmd_stats() -> None:
    """Print historical win rate and P&L stats."""
    from learner.logger import get_stats, get_recent_results
    stats = get_stats()
    if stats.get("total", 0) == 0:
        print("\n  📭 No closed trades yet. Run the bot for a few days first!\n")
        return
    print("\n" + "═" * 50)
    print("  📊  BOT PERFORMANCE STATS")
    print("═" * 50)
    print(f"  Total closed trades : {stats['total']}")
    print(f"  Win rate            : {stats['win_rate']}%")
    print(f"  Average win         : +{stats['avg_win']}%")
    print(f"  Average loss        : {stats['avg_loss']}%")
    print(f"  Combined P&L        : {stats['total_pnl']:+.2f}%")
    print("─" * 50)
    recent = get_recent_results(5)
    if recent:
        print("  Last 5 trades:")
        for t in reversed(recent):
            e = "✅" if t.get("pnl_pct", 0) > 0 else "❌"
            print(f"    {e} {t['date']}  {t['ticker']:<6}  {t.get('pnl_pct', 0):+.2f}%  [{t.get('exit_reason', '?')}]")
    print("═" * 50)


def cmd_learn() -> None:
    """Run a learning cycle and print the report."""
    from learner.learner import run_learning_cycle, print_report
    report = run_learning_cycle(verbose=True)
    print_report(report)


def cmd_backtest(
    start: str, end: str, n_picks: int,
    strategy: str = "momentum", compare: bool = False,
) -> None:
    """Run and save a backtest (single strategy or compare all)."""
    if compare:
        from backtest.backtest import run_backtest_compare, save_compare_results
        all_results = run_backtest_compare(start, end, n_picks=n_picks)
        if all_results:
            save_compare_results(all_results)
    else:
        from backtest.backtest import run_backtest, save_backtest_results
        results = run_backtest(start, end, n_picks=n_picks, strategy=strategy)
        if results:
            save_backtest_results(results)


def cmd_report() -> None:
    """Print the full human-readable report of all saved backtest results."""
    from backtest.report import print_full_report
    print_full_report()


def cmd_swing_backtest(
    start: str, end: str,
    strategy:   str  = "momentum",
    n_picks:    int  = 3,
) -> None:
    """
    Multi-day swing backtest using daily OHLC bars.
    Enter at open, hold up to 5 days, exit on stop/TP/max-hold.
    No 5-min Alpaca data needed -- runs on the same daily bars as everything else.
    """
    from backtest.swing_backtest import run_swing_backtest
    run_swing_backtest(start, end, strategy=strategy, n_picks=n_picks, verbose=True)


def cmd_robust_optimize(
    start: str, end: str,
    strategy:   str  = "momentum",
    apply_best: bool = False,
) -> None:
    """
    Robust optimizer: validates params across random stock splits.

    Proves the params work on stocks the optimizer has NEVER seen —
    the strongest possible evidence against overfitting.

    How it works:
      1. Randomly splits ~290 tickers into 60% train / 40% test (5 times)
      2. Fine grid-searches ~960 stop/TP/trail/hold combos on train stocks
      3. Validates best combos on test stocks (never seen during training)
      4. Finds "consensus params" = consistently good across all splits
      5. Runs a robustness map: ±1 step shows if you're on a plateau (safe)
         or a spike (overfit)

    Typical runtime: 2-4 minutes on a 6-year range.
    Results saved to memory/swing_results/robust_STRATEGY_TIMESTAMP.txt
    """
    from backtest.robust_optimizer import run_robust_optimizer
    run_robust_optimizer(
        start, end,
        strategy=strategy,
        apply_best=apply_best,
        verbose=True,
    )


def cmd_swing_optimize(
    start: str, end: str,
    strategy:   str  = "momentum",
    n_picks:    int  = 3,
    apply_best: bool = False,
) -> None:
    """
    Grid-search optimizer for swing trading: stop x TP x trail x max_hold_days.
    strategy="all" compares all 4 strategies with one download, shows the winner.
    Runs entirely on daily bars -- typically under 30 seconds.
    """
    if strategy == "all":
        from backtest.swing_optimizer import run_swing_compare
        run_swing_compare(
            start, end,
            n_picks=n_picks,
            apply_best=apply_best,
            verbose=True,
        )
    else:
        from backtest.swing_optimizer import run_swing_optimizer
        run_swing_optimizer(
            start, end,
            strategy=strategy,
            n_picks=n_picks,
            apply_best=apply_best,
            verbose=True,
        )


def cmd_optimize(
    start: str, end: str, strategy: str = "momentum",
    n_picks: int = 3, apply_best: bool = False,
) -> None:
    """
    Grid-search optimizer: tests combinations of stop/TP/score to find
    what actually makes money. Uses walk-forward validation so results
    aren't just fitted to one lucky time period.
    """
    from backtest.optimizer import run_optimizer
    run_optimizer(
        start, end,
        strategy=strategy,
        n_picks=n_picks,
        apply_best=apply_best,
        verbose=True,
    )


def cmd_rec_report(days: int = 30) -> None:
    """
    Print the recommendation accuracy report.
    Shows how well the bot's picks have done, broken down by confidence tier.
    """
    from learner.rec_tracker import print_performance_report
    print_performance_report(days=days)


def cmd_scan_only() -> None:
    """Scan and score but don't trade."""
    print("\n  Running scan (no trades will be placed)\n")
    from scanner.scanner import run_scan
    from scorer.scorer import rank_candidates, format_picks_table

    candidates = run_scan(verbose=True)
    if not candidates:
        print("  No candidates found today.")
        return

    ranked = rank_candidates(candidates, verbose=True)

    # Log recommendations (traded_tickers=[] → all marked as not traded in scan-only mode)
    try:
        from learner.rec_tracker import log_recommendations
        log_recommendations(ranked, traded_tickers=[])
    except Exception:
        pass

    print(format_picks_table(ranked, top_n=10))
    print(f"\n  Scan complete.\n")


def cmd_full_day() -> None:
    """
    Full day: scan -> approve -> trade -> monitor all day -> EOD close -> learn.
    """
    timing = _check_market_timing()

    if timing == "closed":
        now = _now_et()
        print(f"\n  Market is closed ({now.strftime('%A %H:%M ET')}).")
        resp = input("  Run in SCAN-ONLY mode instead? [y/N] ").strip().lower()
        if resp == "y":
            cmd_scan_only()
        return

    print(f"\n  Market: {timing.upper()}")

    # ── Step 1: Scan ──
    print("\n" + "-" * 60)
    print("  STEP 1 / 4  --  MARKET SCAN")
    print("-" * 60)
    from scanner.scanner import run_scan
    from scorer.scorer import rank_candidates

    candidates = run_scan(verbose=True)
    if not candidates:
        print("  No candidates found. Exiting.")
        return

    ranked = rank_candidates(candidates, verbose=True)

    # Log every recommendation before the user sees the table
    try:
        from learner.rec_tracker import log_recommendations
        log_recommendations(ranked)
    except Exception:
        pass

    # ── Step 2: User approval ──
    print("\n" + "-" * 60)
    print("  STEP 2 / 4  --  REVIEW & APPROVE")
    print("-" * 60)
    approved = _get_approved_picks(ranked)

    if not approved:
        print("\n  No picks approved. No trades placed today.\n")
        return

    print(f"\n  Approved {len(approved)} pick(s): "
          f"{', '.join(p['ticker'] for p in approved)}")

    # ── Step 3: Wait for entry time (9:45 AM ET) ──
    print("\n" + "-" * 60)
    print("  STEP 3 / 4  --  TRADE EXECUTION")
    print("-" * 60)

    if timing == "premarket":
        print("  Waiting for 9:45 AM ET (15-min open rule)...")
        _wait_for_market(9, 45)

    # ── Volume confirmation for [HIGH?] / [GOOD?] picks ──
    # Picks with '?' were approved in premarket but had low volume.
    # Now that the market has been open 15 min, re-check whether real
    # volume has come in to confirm the move. Drop picks that are still thin.
    approved = _confirm_pending_picks(approved)
    if not approved:
        print("\n  No picks survived volume confirmation. Nothing to trade today.\n")
        return

    # Record which picks we're actually placing orders for
    try:
        from learner.rec_tracker import mark_traded
        mark_traded([p["ticker"] for p in approved])
    except Exception:
        pass

    # ── Connect Alpaca ──
    try:
        from trader.alpaca import AlpacaClient
        from trader.trader import execute_picks, monitor_loop, eod_summary
        client = AlpacaClient()
    except EnvironmentError as e:
        print(f"\n  [ERROR] {e}")
        print("  Set ALPACA_API_KEY and ALPACA_SECRET_KEY then try again.\n")
        return
    except Exception as e:
        print(f"\n  [ERROR] Could not connect to Alpaca: {e}\n")
        return

    trades = execute_picks(client, approved)

    if not trades or all(t["status"] == "failed" for t in trades):
        print("\n  All orders failed. Nothing to monitor.\n")
        return

    placed = [t for t in trades if t["status"] == "open"]
    tickers_str = ", ".join(t["ticker"] for t in placed)
    _notify_discord(
        f"Day trade orders placed: {tickers_str}\n"
        + "\n".join(
            f"  {t['ticker']} -- entry ${t['entry_price']}"
            f"  stop ${t['stop_price']}  TP ${t['take_profit_price']}"
            for t in placed
        )
    )

    # ── Step 4: Monitor + EOD close ──
    print("\n" + "-" * 60)
    print("  STEP 4 / 4  --  MONITORING  (auto-closes at 3:30 PM ET)")
    print("-" * 60)

    trades = monitor_loop(client, trades)

    summary_text = eod_summary(trades)
    print(summary_text)

    _notify_discord("Day trading EOD summary:\n" + summary_text)

    # ── Auto-learn after the day ──
    print("\n  Running learning cycle...")
    from learner.logger import log_trades
    from learner.learner import run_learning_cycle

    log_trades(trades)
    report = run_learning_cycle(verbose=False)

    wins  = report.get("wins",          0)
    total = report.get("total_trades",  0)
    pnl   = report.get("total_pnl_pct", 0)
    if total > 0:
        print(f"  Lifetime: {wins}/{total} wins  --  P&L {pnl:+.2f}%")

    print("\n  Day complete.\n")


def cmd_swing_live() -> None:
    """
    Swing trading live mode.

    How it's different from Full Day:
      - Stop loss  3%  (not 2%)
      - Take profit 8% (not 4%)
      - Does NOT force-close at 3:30 PM -- holds for up to 3 days
      - Alpaca watches your stops and TPs even when your PC is off
      - Morning check shows current P&L and flags positions held 3+ days

    Daily routine (15-20 minutes):
      1. Run this each morning
      2. See open positions + current P&L
      3. Close any day-3 positions (prompted automatically)
      4. Approve new picks for today
      5. Orders placed -- PC can be off for the rest of the day
    """
    timing = _check_market_timing()
    memory_path = os.path.join(_BASE, "memory")

    if timing == "closed":
        now = _now_et()
        print(f"\n  Market is closed ({now.strftime('%A %H:%M ET')}).")
        resp = input("  Run morning check on open positions anyway? [y/N] ").strip().lower()
        if resp != "y":
            return

    try:
        from trader.alpaca import AlpacaClient
        from trader.swing_trader import (
            morning_check, execute_swing_picks,
            close_expired_positions, swing_eod_summary,
        )
        client = AlpacaClient()
    except EnvironmentError as e:
        print(f"\n  {e}")
        print("  Set ALPACA_API_KEY and ALPACA_SECRET_KEY in your .env file.\n")
        return
    except Exception as e:
        print(f"\n  Could not connect to Alpaca: {e}\n")
        return

    # ── Step 1: Morning check on existing positions ──
    print("\n" + "-" * 60)
    print("  STEP 1 / 3  --  OPEN POSITIONS CHECK")
    print("-" * 60)

    expired = morning_check(client, memory_path)

    if expired:
        resp = input(f"\n  Close {len(expired)} expired position(s) now? [Y/n] ").strip().lower()
        if resp not in ("n", "no"):
            close_expired_positions(client, expired, memory_path)

    # ── Step 2: Scan for new setups ──
    print("\n" + "-" * 60)
    print("  STEP 2 / 3  --  SCAN FOR NEW MOMENTUM SETUPS")
    print("-" * 60)

    from scanner.scanner import run_scan
    from scorer.scorer import rank_candidates

    candidates = run_scan(verbose=True)
    if not candidates:
        print("  No candidates found today. Check back tomorrow.")
        return

    ranked = rank_candidates(candidates, verbose=True)

    # Log every recommendation before the user sees the table
    try:
        from learner.rec_tracker import log_recommendations
        log_recommendations(ranked)
    except Exception:
        pass

    # ── Step 3: Approve and place ──
    print("\n" + "-" * 60)
    print("  STEP 3 / 3  --  APPROVE PICKS & PLACE ORDERS")
    print("-" * 60)
    print("  These will be held for up to 3 days.")
    print("  Stop loss: 3%  |  Take profit: 8%  |  Alpaca watches overnight.")
    print()

    approved = _get_approved_picks(ranked)
    if not approved:
        print("\n  No picks approved. Existing positions are still running.\n")
        return

    # Wait for open if in premarket, then confirm volume on [HIGH?]/[GOOD?] picks
    if timing == "premarket":
        print("  Waiting for 9:45 AM ET...")
        _wait_for_market(9, 45)

    approved = _confirm_pending_picks(approved)
    if not approved:
        print("\n  No picks survived volume confirmation. Nothing to trade today.\n")
        return

    # Record which picks we're actually placing orders for
    try:
        from learner.rec_tracker import mark_traded
        mark_traded([p["ticker"] for p in approved])
    except Exception:
        pass

    new_trades = execute_swing_picks(client, approved, memory_path)

    summary = swing_eod_summary(new_trades, [])
    print(summary)

    placed = [t for t in new_trades if t["status"] == "open"]
    if placed:
        _notify_discord(
            "Swing trades placed:\n"
            + "\n".join(
                f"  {t['ticker']} -- entry ${t['entry_price']}  "
                f"stop ${t['stop_price']}  TP ${t['take_profit_price']}  (3-day hold)"
                for t in placed
            )
        )

    print("\n  Done. Your stop/TP orders are live on Alpaca's servers.")
    print("  PC can be off. Come back tomorrow morning and run option 9 again.\n")


# ── CLI ────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Day Trading Bot — scan, score, trade, learn.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--scan-only",  action="store_true",
                        help="Scan and score but don't place trades")
    parser.add_argument("--backtest",   nargs=2, metavar=("START", "END"),
                        help="Backtest over date range: --backtest 2025-01-01 2025-06-01")
    parser.add_argument("--picks",      type=int, default=3,
                        help="Number of picks per day for backtest (default 3)")
    parser.add_argument("--strategy",   type=str, default="momentum",
                        choices=["momentum", "mean_reversion", "gap_and_go", "breakout", "all"],
                        help="Strategy for backtest/optimize (default: momentum); use 'all' with --swing-optimize to compare all 4")
    parser.add_argument("--compare",    action="store_true",
                        help="Run ALL strategies on the same date range and compare")
    parser.add_argument("--optimize",   nargs=2, metavar=("START", "END"),
                        help="Grid-search best stop/TP/score settings: "
                             "--optimize 2025-01-01 2026-01-01")
    parser.add_argument("--apply-best", action="store_true",
                        help="With --optimize: save the best-found params to scoring_weights.json")
    parser.add_argument("--learn",      action="store_true",
                        help="Run learning cycle and update scoring weights")
    parser.add_argument("--stats",      action="store_true",
                        help="Show performance stats")
    parser.add_argument("--report",     action="store_true",
                        help="Show full backtest report with plain-English analysis")
    parser.add_argument("--swing-backtest", nargs=2, metavar=("START", "END"),
                        help="Swing backtest: enter at open, hold up to 5 days, daily bars only: "
                             "--swing-backtest 2020-01-01 2026-01-01")
    parser.add_argument("--swing-optimize", nargs=2, metavar=("START", "END"),
                        help="Auto-optimize swing params (stop/TP/trail/max-hold-days): "
                             "--swing-optimize 2020-01-01 2026-01-01")
    parser.add_argument("--robust-optimize", nargs=2, metavar=("START", "END"),
                        help="Robust optimizer: validates params on stocks never seen "
                             "during training (5-fold stock cross-validation + fine grid): "
                             "--robust-optimize 2020-01-01 2026-01-01")
    parser.add_argument("--swing-live", action="store_true",
                        help="Swing trading morning routine: check open positions, "
                             "scan for new setups, place GTC bracket orders. "
                             "PC can be off after this runs.")
    parser.add_argument("--rec-report", action="store_true",
                        help="Show recommendation accuracy report -- how well the AI "
                             "picks have done, broken down by confidence tier [HIGH/GOOD/OK]")
    parser.add_argument("--days",       type=int, default=30,
                        help="Lookback window in days for --rec-report (default 30)")

    args = parser.parse_args()

    print(BANNER)
    print(f"  📅 {_now_et().strftime('%A %Y-%m-%d  %H:%M %Z')}")

    if args.rec_report:
        cmd_rec_report(days=args.days)
    elif args.swing_live:
        cmd_swing_live()
    elif args.robust_optimize:
        cmd_robust_optimize(
            args.robust_optimize[0], args.robust_optimize[1],
            strategy=args.strategy,
            apply_best=args.apply_best,
        )
    elif args.report:
        cmd_report()
    elif args.stats:
        cmd_stats()
    elif args.learn:
        cmd_learn()
    elif args.swing_optimize:
        cmd_swing_optimize(
            args.swing_optimize[0], args.swing_optimize[1],
            strategy=args.strategy,
            n_picks=args.picks,
            apply_best=args.apply_best,
        )
    elif args.swing_backtest:
        cmd_swing_backtest(
            args.swing_backtest[0], args.swing_backtest[1],
            strategy=args.strategy,
            n_picks=args.picks,
        )
    elif args.optimize:
        cmd_optimize(
            args.optimize[0], args.optimize[1],
            strategy=args.strategy,
            n_picks=args.picks,
            apply_best=args.apply_best,
        )
    elif args.backtest:
        cmd_backtest(
            args.backtest[0], args.backtest[1],
            args.picks, args.strategy, args.compare,
        )
    elif args.scan_only:
        cmd_scan_only()
    else:
        cmd_full_day()


if __name__ == "__main__":
    main()
