"""
learner/learner.py
Analyses past trade results and updates scoring_weights.json.

Run after each trading day (auto) or on demand:
    python run_day.py --learn

What it does:
  1. Reads trade_results.json (real live/paper trades)
  2. Optionally supplements with backtest_trades_*.json files
  3. Finds which signals correlate with winning trades
  4. Updates memory/scoring_weights.json with adjusted weights
  5. Prints an insight report

The learner needs ≥20 closed trades before it adjusts weights
(too few samples = noise, not signal).

Learning hierarchy:
  - Real trades (from actual Alpaca paper/live orders) = ground truth
  - Backtest trades (simulated) = bootstrap data when real trades < 20
"""

from __future__ import annotations

import glob
import json
import os
from collections import defaultdict
from datetime import datetime

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_RESULTS_PATH  = os.path.join(_BASE, "memory", "trade_results.json")
_WEIGHTS_PATH  = os.path.join(_BASE, "memory", "scoring_weights.json")
_LESSONS_PATH  = os.path.join(_BASE, "memory", "agent_lessons.md")
_BT_TRADES_DIR = os.path.join(_BASE, "memory")

MIN_TRADES_TO_LEARN = 20   # minimum records before adjusting weights


# ── Data loaders ───────────────────────────────────────────────────────────────

def _load_live_results() -> list[dict]:
    """Load real paper/live trade results from trade_results.json."""
    try:
        with open(_RESULTS_PATH) as f:
            return [r for r in json.load(f) if r.get("status") == "closed"]
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _load_backtest_trades() -> list[dict]:
    """
    Load all individual trade records saved by save_backtest_results().
    These are in backtest_trades_{strategy}_{timestamp}.json files.
    Returns all trades tagged with source='backtest'.
    """
    pattern = os.path.join(_BT_TRADES_DIR, "backtest_trades_*.json")
    files   = sorted(glob.glob(pattern))
    trades  = []
    for path in files:
        try:
            with open(path) as f:
                batch = json.load(f)
            for t in batch:
                t.setdefault("source", "backtest")
            trades.extend(batch)
        except Exception:
            pass
    return trades


def _load_weights() -> dict:
    try:
        with open(_WEIGHTS_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_weights(weights: dict) -> None:
    with open(_WEIGHTS_PATH, "w") as f:
        json.dump(weights, f, indent=2)


# ── Signal analysis ────────────────────────────────────────────────────────────

def _analyse_signals(records: list[dict]) -> dict:
    """
    For each boolean signal in the 'signals' dict, compute win-rate
    when the signal is True vs False.
    Edge = win_rate_true - win_rate_false (positive = signal helps).
    """
    signal_keys = set()
    for r in records:
        signal_keys.update(r.get("signals", {}).keys())

    analysis = {}
    for sig in signal_keys:
        with_sig    = [r for r in records if r.get("signals", {}).get(sig)]
        without_sig = [r for r in records if not r.get("signals", {}).get(sig)]

        def wr(lst):
            if not lst:
                return None
            return round(sum(1 for r in lst if r.get("pnl_pct", 0) > 0) / len(lst) * 100, 1)

        wt = wr(with_sig)
        wf = wr(without_sig)
        edge = round((wt or 0) - (wf or 0), 1) if wt is not None and wf is not None else 0

        analysis[sig] = {
            "win_rate_when_true":  wt,
            "win_rate_when_false": wf,
            "n_true":  len(with_sig),
            "n_false": len(without_sig),
            "edge":    edge,
        }
    return analysis


def _analyse_score_correlation(records: list[dict]) -> dict:
    """How well does tech_score predict actual winning?"""
    buckets: dict[int, list] = defaultdict(list)
    for r in records:
        score  = r.get("tech_score") or r.get("final_score") or r.get("ai_score") or 5
        bucket = max(1, min(10, int(score)))
        buckets[bucket].append(r.get("pnl_pct", 0))

    result = {}
    for b in sorted(buckets.keys()):
        pnls = buckets[b]
        wins = sum(1 for p in pnls if p > 0)
        result[f"score_{b}"] = {
            "n":        len(pnls),
            "win_rate": round(wins / len(pnls) * 100, 1),
            "avg_pnl":  round(sum(pnls) / len(pnls), 3),
        }
    return result


def _analyse_sectors(records: list[dict]) -> dict:
    sectors: dict[str, list] = defaultdict(list)
    for r in records:
        sectors[r.get("sector", "unknown")].append(r.get("pnl_pct", 0))

    result = {}
    for sec, pnls in sectors.items():
        wins = sum(1 for p in pnls if p > 0)
        result[sec] = {
            "n":        len(pnls),
            "win_rate": round(wins / len(pnls) * 100, 1),
            "avg_pnl":  round(sum(pnls) / len(pnls), 3),
        }
    return result


def _analyse_strategies(records: list[dict]) -> dict:
    """Which strategy is performing best? (Only useful on backtest data.)"""
    by_strat: dict[str, list] = defaultdict(list)
    for r in records:
        s = r.get("strategy", "momentum")
        by_strat[s].append(r.get("pnl_pct", 0))

    result = {}
    for strat, pnls in by_strat.items():
        wins = sum(1 for p in pnls if p > 0)
        result[strat] = {
            "n":        len(pnls),
            "win_rate": round(wins / len(pnls) * 100, 1),
            "avg_pnl":  round(sum(pnls) / len(pnls), 3),
        }
    return result


def _analyse_exit_reasons(records: list[dict]) -> dict:
    """
    Break down results by exit reason.
    If stop_loss exits are far more common than take_profit, the entry bar
    is too low or the stop is too tight.
    """
    reasons: dict[str, list] = defaultdict(list)
    for r in records:
        reasons[r.get("exit_reason", "unknown")].append(r.get("pnl_pct", 0))

    result = {}
    for reason, pnls in reasons.items():
        result[reason] = {
            "n":       len(pnls),
            "avg_pnl": round(sum(pnls) / len(pnls), 3),
            "pct_of_total": 0,  # filled below
        }
    total = sum(v["n"] for v in result.values())
    for reason in result:
        result[reason]["pct_of_total"] = round(
            result[reason]["n"] / max(total, 1) * 100, 1
        )
    return result


# ── Weight update ──────────────────────────────────────────────────────────────

def _update_weights(
    signal_analysis: dict,
    score_corr:      dict,
    exit_analysis:   dict,
) -> dict:
    """
    Adjust scoring_weights.json based on what signals are actually working.
    Uses a conservative ±10% nudge so weights change slowly each cycle.
    """
    weights = _load_weights()
    tech_w  = weights.get("technical", {})
    NUDGE   = 0.1  # 10% adjustment per cycle

    # ── Signal → weight key mapping ──
    # Maps trade signal names to the weight keys in scoring_weights.json
    key_map = {
        "above_ma20":    "above_ma20",
        "above_ma50":    "above_both_ma",
        "near_52w_high": "near_high",
        "high_rel_vol":  "rv_high",
        "strong_pm":     "pm_ideal",
        "pos_5d":        "ret5_ok",
        "pos_20d":       "ret5_strong",  # proxy for sustained trend
    }

    for sig, info in signal_analysis.items():
        edge = info.get("edge", 0)
        n    = info.get("n_true", 0)
        if n < 5:
            continue  # not enough data to draw conclusions

        wkey    = key_map.get(sig)
        current = tech_w.get(wkey, 1.0) if wkey else None
        if current is None:
            continue

        if edge > 10:     # signal clearly helps → boost its weight
            tech_w[wkey] = round(current * (1 + NUDGE), 3)
        elif edge < -10:  # signal actually hurts → reduce its weight
            tech_w[wkey] = round(current * (1 - NUDGE), 3)

    # ── Stop/TP feedback ──
    # If stop_loss is the most common exit, the entry bar might be too low.
    # Log this as insight but don't auto-adjust stop (controlled elsewhere).
    tp_exits  = exit_analysis.get("take_profit", {}).get("pct_of_total", 0)
    sl_exits  = exit_analysis.get("stop_loss",   {}).get("pct_of_total", 0)
    if sl_exits > 50:
        # More than 50% of exits are stop-outs → boost RV and RSI sweet-spot weights
        tech_w["rv_high"]   = round(tech_w.get("rv_high",   2.0) * (1 + NUDGE), 3)
        tech_w["rsi_sweet"] = round(tech_w.get("rsi_sweet", 1.5) * (1 + NUDGE), 3)

    # ── AI vs tech blend ──
    high_score_wins = sum(
        1 for k, v in score_corr.items()
        if int(k.split("_")[1]) >= 7 and v["win_rate"] >= 60
    )
    if high_score_wins >= 2:
        blend = weights.get("blend", {})
        blend["ai_weight"]   = min(0.80, blend.get("ai_weight",   0.65) + 0.03)
        blend["tech_weight"] = max(0.20, blend.get("tech_weight", 0.35) - 0.03)
        weights["blend"] = blend

    weights["technical"]     = tech_w
    weights["last_updated"]  = datetime.now().isoformat()
    return weights


# ── Lesson writer ──────────────────────────────────────────────────────────────

def _append_lesson(insight: str, source: str = "live") -> None:
    today = datetime.now().strftime("%Y-%m-%d")
    entry = (f"\n**{today} — Learner Auto-Update ({source})**\n"
             f"- {insight}\n- Applied to: scanner scoring\n")
    try:
        with open(_LESSONS_PATH, "a", encoding="utf-8") as f:
            f.write(entry)
    except Exception:
        pass


# ── Main entry point ───────────────────────────────────────────────────────────

def run_learning_cycle(verbose: bool = True) -> dict:
    """
    Full learning cycle. Returns an insights dict.

    Learning hierarchy:
      1. If real trades >= MIN_TRADES_TO_LEARN: use real trades only
      2. If real trades < MIN_TRADES_TO_LEARN AND backtest trades exist:
         use backtest trades to bootstrap the learner
      3. If neither has enough data: show stats only, no weight change

    Call this after each trading day, or on demand with:  python run_day.py --learn
    """
    live_records     = _load_live_results()
    backtest_records = _load_backtest_trades()

    n_live = len(live_records)
    n_bt   = len(backtest_records)

    if verbose:
        print(f"\n  🧠 Learner:")
        print(f"     Real trades:      {n_live}")
        print(f"     Backtest trades:  {n_bt}")

    # ── Choose which dataset to learn from ──
    if n_live >= MIN_TRADES_TO_LEARN:
        records        = live_records
        data_source    = "live"
        update_weights = True
        if verbose:
            print(f"  ✅ Using real trade data ({n_live} trades) for weight updates")

    elif n_bt >= MIN_TRADES_TO_LEARN:
        records        = backtest_records
        data_source    = "backtest"
        update_weights = True
        if verbose:
            print(f"  📚 Using backtest data ({n_bt} trades) to bootstrap learning")
            print(f"  ℹ  Weights will be updated once you have {MIN_TRADES_TO_LEARN}+ real trades")

    else:
        # Not enough of either
        records        = live_records + backtest_records
        data_source    = "insufficient"
        update_weights = False
        need_live = MIN_TRADES_TO_LEARN - n_live
        need_bt   = MIN_TRADES_TO_LEARN - n_bt
        if verbose:
            if n_bt == 0:
                print(f"  ℹ  Run a backtest first to generate {MIN_TRADES_TO_LEARN}+ simulated trades,")
                print(f"     or trade live for {need_live} more days. Showing stats only.")
            else:
                print(f"  ℹ  Need {need_live} more real trades OR {need_bt} more backtest trades.")
                print(f"     Showing stats only (no weight changes yet).")
        stats_only = _build_report(records, {}, {}, {}, {}, {}, update_weights=False,
                                   data_source=data_source)
        return stats_only

    sig_analysis  = _analyse_signals(records)
    score_corr    = _analyse_score_correlation(records)
    sector_perf   = _analyse_sectors(records)
    strat_perf    = _analyse_strategies(records)
    exit_analysis = _analyse_exit_reasons(records)

    if update_weights:
        new_weights = _update_weights(sig_analysis, score_corr, exit_analysis)
        _save_weights(new_weights)
        if verbose:
            print("  💾 Scoring weights updated in memory/scoring_weights.json")

    report = _build_report(
        records, sig_analysis, score_corr, sector_perf, strat_perf, exit_analysis,
        update_weights=update_weights, data_source=data_source,
    )

    # Write top insight to lessons file
    if sig_analysis:
        best_sig = max(sig_analysis.items(),
                       key=lambda x: x[1].get("edge", 0), default=(None, {}))
        if best_sig[0] and best_sig[1].get("edge", 0) > 15:
            _append_lesson(
                f"Best predictive signal: '{best_sig[0]}' "
                f"(edge +{best_sig[1]['edge']}% win-rate when true, "
                f"n={best_sig[1]['n_true']})",
                source=data_source,
            )

    return report


def _build_report(
    records, sig_analysis, score_corr, sector_perf, strat_perf,
    exit_analysis, update_weights: bool, data_source: str,
) -> dict:
    wins     = [r for r in records if r.get("pnl_pct", 0) > 0]
    losses   = [r for r in records if r.get("pnl_pct", 0) <= 0]
    avg_win  = round(sum(r["pnl_pct"] for r in wins)   / max(len(wins),   1), 3)
    avg_loss = round(sum(r["pnl_pct"] for r in losses)  / max(len(losses), 1), 3)

    return {
        "total_trades":       len(records),
        "wins":               len(wins),
        "win_rate":           round(len(wins) / max(len(records), 1) * 100, 1),
        "avg_win_pct":        avg_win,
        "avg_loss_pct":       avg_loss,
        "total_pnl_pct":      round(sum(r.get("pnl_pct", 0) for r in records), 3),
        "data_source":        data_source,
        "signal_analysis":    sig_analysis,
        "score_correlation":  score_corr,
        "sector_performance": sector_perf,
        "strategy_performance": strat_perf,
        "exit_analysis":      exit_analysis,
        "weights_updated":    update_weights,
    }


def print_report(report: dict) -> None:
    """Print a human-readable learning report."""
    src = report.get("data_source", "")
    src_label = f"  [{src.upper()} DATA]" if src not in ("live", "") else ""

    print("\n" + "═" * 62)
    print(f"  🧠  LEARNING REPORT{src_label}")
    print("═" * 62)
    print(f"  Total trades: {report['total_trades']}")
    print(f"  Win rate:     {report['win_rate']}%")
    print(f"  Avg win:      +{report['avg_win_pct']}%")
    print(f"  Avg loss:     {report['avg_loss_pct']}%")
    print(f"  Total P&L:    {report['total_pnl_pct']:+.2f}%")

    # ── Exit breakdown ──
    exits = report.get("exit_analysis", {})
    if exits:
        print("\n  Exit reasons:")
        for reason, info in sorted(exits.items(),
                                    key=lambda x: x[1]["n"], reverse=True):
            print(f"    {reason:<14}  {info['n']:>4} trades  "
                  f"({info['pct_of_total']:>4.0f}%)  avg {info['avg_pnl']:>+5.2f}%")

    # ── Signal analysis ──
    if report.get("signal_analysis"):
        print("\n  Signal performance (edge = win-rate boost when signal is True):")
        for sig, info in sorted(report["signal_analysis"].items(),
                                 key=lambda x: abs(x[1].get("edge", 0)), reverse=True):
            indicator = "✓" if info.get("edge", 0) > 0 else "✗"
            print(f"    {indicator} {sig:<20}  edge: {info['edge']:>+6.1f}%  "
                  f"(n={info['n_true']:>4} true  /  n={info['n_false']:>4} false)")

    # ── Strategy performance ──
    strat_perf = report.get("strategy_performance", {})
    if strat_perf and len(strat_perf) > 1:
        print("\n  Strategy performance:")
        for strat, perf in sorted(strat_perf.items(),
                                   key=lambda x: x[1]["avg_pnl"], reverse=True):
            print(f"    {strat:<18}  win {perf['win_rate']:>4.1f}%  "
                  f"avg {perf['avg_pnl']:>+5.2f}%  (n={perf['n']})")

    # ── Sector performance ──
    if report.get("sector_performance"):
        print("\n  Sector performance:")
        for sec, perf in sorted(report["sector_performance"].items(),
                                  key=lambda x: x[1]["win_rate"], reverse=True)[:8]:
            print(f"    {sec:<28}  win {perf['win_rate']:>4.1f}%  "
                  f"avg {perf['avg_pnl']:>+5.2f}%  (n={perf['n']})")

    if report.get("weights_updated"):
        print("\n  ✅ Scoring weights updated.")
    else:
        need = max(0, 20 - report["total_trades"])
        if need > 0:
            print(f"\n  ℹ  Need {need} more trades before weights can be updated.")

    print("═" * 62)
