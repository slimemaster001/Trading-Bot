"""
backtest/robust_optimizer.py
============================
"Will these params still work next month, on stocks the optimizer never saw?"

STANDARD optimizer: train 70% of dates → validate on last 30% (same ~220 stocks).
ROBUST  optimizer: randomly split the stock universe 60/40, then:
  - Train   on 60% of tickers (+ first 70% of the date range)
  - Validate on the OTHER 40% of tickers (never seen during training)
  - Repeat 5× with different random splits
  - Only params that rank top-3 in most folds get the "consensus" label

After consensus is found, runs a ROBUSTNESS MAP:
  - Tests ±1 step in each direction from the consensus params
  - Plateau (neighbours also profitable) → real edge, safe to live-trade
  - Spike (only the exact combo works)  → likely overfit, treat with caution

PERFORMANCE NOTE — why this is now fast:
  The expensive operation is scanning ~300 stocks × 1567 days to find candidates
  (computes RSI, volume ratios, trend signals, etc.).
  Old approach: scanned once per fold per group = 5 folds × 2 groups = 10 scans.
  New approach: scan ALL tickers ONCE, store all candidates, then per fold just
  filter that list by train/test ticker set (a simple list comprehension).
  Result: 10× less scanning work → ~2-4 min instead of 1+ hour.

Fine-grained grid:
  stop  2.0% – 5.0%   (0.5% steps) — 6 options
  TP    5.0% – 12.0%  (1.0% steps) — 7 options
  trail 2.0% – 5.0%   (0.5% steps) — 6 options
  hold  2 – 5 days                  — 4 options
  → ~984 valid combos (vs 140 in the standard optimizer)

Usage:
    python run_day.py --robust-optimize 2020-01-01 2026-01-01
    python run_day.py --robust-optimize 2020-01-01 2026-01-01 --strategy breakout
    python run_day.py --robust-optimize 2020-01-01 2026-01-01 --apply-best
"""

from __future__ import annotations

import os
import json
import random
import time
import threading
import warnings
import concurrent.futures
warnings.filterwarnings("ignore")

import numpy as np
from datetime import date, datetime
from collections import defaultdict
from itertools import product

_BASE        = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PARAMS_PATH = os.path.join(_BASE, "memory", "swing_best_params.json")
_REPORT_DIR  = os.path.join(_BASE, "memory", "swing_results")

# ── Fine-grained parameter grid ────────────────────────────────────────────────
FINE_STOP_OPTIONS  = [0.020, 0.025, 0.030, 0.035, 0.040, 0.050]
FINE_TP_OPTIONS    = [0.050, 0.060, 0.070, 0.080, 0.090, 0.100, 0.120]
FINE_TRAIL_OPTIONS = [0.020, 0.025, 0.030, 0.035, 0.040, 0.050]
FINE_HOLD_OPTIONS  = [2, 3, 4, 5]

N_FOLDS          = 5
TRAIN_STOCK_PCT  = 0.60
TIME_TRAIN_PCT   = 0.70
MIN_TRAIN_TRADES = 5
TOP_N_VALIDATE   = 5
MAX_PARALLEL     = 4


# ── Thread-safe progress bar ───────────────────────────────────────────────────

class _ProgressBar:
    """
    In-place progress bar for Windows CMD (uses \\r overwrite).
    Thread-safe: update() and print_above() can be called from any thread.

    Example output:
      Scanning  [████████████░░░░░░░░░░░░░░░░░░░░░░░░░░]  31.4%  (492/1567)  ETA 47s
    """
    _BAR_WIDTH = 38

    def __init__(self, total: int, label: str = "") -> None:
        self._total  = max(total, 1)
        self._done   = 0
        self._label  = label
        self._lock   = threading.Lock()
        self._start  = time.monotonic()
        self._active = True
        self._draw()

    def update(self, n: int = 1) -> None:
        """Increment counter and redraw (thread-safe)."""
        with self._lock:
            if not self._active:
                return
            self._done = min(self._done + n, self._total)
            self._draw()

    def print_above(self, msg: str) -> None:
        """
        Clear the bar line, print msg (with newline), then redraw the bar.
        Call from the main thread to log a fold result without breaking the bar.
        """
        with self._lock:
            print(f"\r{' ' * 84}\r", end="", flush=True)
            print(msg, flush=True)
            if self._active:
                self._draw()

    def finish(self, msg: str = "") -> None:
        """Fill bar to 100% and end with a newline."""
        with self._lock:
            self._active = False
            self._done   = self._total
            self._draw()
        print(flush=True)
        if msg:
            print(f"  {msg}", flush=True)

    def _draw(self) -> None:
        """Overwrite the current terminal line. Always called inside self._lock."""
        done    = self._done
        total   = self._total
        pct     = done / total
        filled  = int(self._BAR_WIDTH * pct)
        bar     = "█" * filled + "░" * (self._BAR_WIDTH - filled)
        elapsed = time.monotonic() - self._start
        if done > 0 and pct < 1.0:
            eta     = elapsed / pct * (1.0 - pct)
            eta_str = f"{eta:.0f}s" if eta < 90 else f"{eta / 60:.1f}m"
        elif pct >= 1.0:
            eta_str = f"{elapsed:.0f}s"
        else:
            eta_str = "---"
        line = (f"\r  {self._label}  [{bar}] {pct * 100:5.1f}%"
                f"  ({done}/{total})  ETA {eta_str}   ")
        print(line, end="", flush=True)


# ── Grid helpers ───────────────────────────────────────────────────────────────

def _fine_grid() -> list[tuple]:
    return [
        (stop, tp, trail, hold)
        for stop, tp, trail, hold
        in product(FINE_STOP_OPTIONS, FINE_TP_OPTIONS, FINE_TRAIL_OPTIONS, FINE_HOLD_OPTIONS)
        if tp > stop
    ]


def _nearest_idx(lst: list, val: float) -> int:
    return min(range(len(lst)), key=lambda i: abs(lst[i] - val))


# ── Stock-fold generator ───────────────────────────────────────────────────────

def _stock_kfold(
    tickers: list[str],
    n_folds: int,
) -> list[tuple[list[str], list[str]]]:
    folds = []
    for i in range(n_folds):
        rng = random.Random(42 + i * 13)
        shuffled = list(tickers)
        rng.shuffle(shuffled)
        split = int(len(shuffled) * TRAIN_STOCK_PCT)
        folds.append((shuffled[:split], shuffled[split:]))
    return folds


# ── Candidate pre-computation (called ONCE for all tickers) ───────────────────

def _precompute_all_candidates(
    trading_days:  list[date],
    hist_data:     dict,
    market_regime: dict,
    weights:       dict,
    strategy:      str,
    bar:           "_ProgressBar | None" = None,
) -> dict[date, list[dict]]:
    """
    Scan ALL tickers across ALL days exactly once.

    Returns {day: [all qualifying candidates sorted by score]}.

    Previously we called _scan_day_candidates once per fold per group (10x total).
    Now we call it once for the full universe and let each fold filter the result.
    10x less scanning = the dominant performance win.
    """
    from backtest.swing_backtest import _scan_day_candidates
    # Use a large n_picks so we capture every qualifying candidate.
    # Each fold will then slice to its own top-n_picks after filtering by ticker.
    max_per_day = max(len(hist_data), 50)
    all_cands: dict[date, list[dict]] = {}
    for day in trading_days:
        market_up = market_regime.get(day, True)
        cands = _scan_day_candidates(day, hist_data, weights, strategy, max_per_day, market_up)
        if cands:
            all_cands[day] = cands
        if bar is not None:
            bar.update()
    return all_cands


def _filter_candidates(
    all_cands:  dict[date, list[dict]],
    ticker_set: set[str],
    n_picks:    int,
) -> dict[date, list[dict]]:
    """
    From pre-computed candidates, keep only those from ticker_set.
    Takes top n_picks per day (original score order is preserved).
    Pure list comprehension — takes milliseconds.
    """
    filtered: dict[date, list[dict]] = {}
    for day, cands in all_cands.items():
        day_cands = [c for c in cands if c["ticker"] in ticker_set][:n_picks]
        if day_cands:
            filtered[day] = day_cands
    return filtered


# ── Single fold runner ─────────────────────────────────────────────────────────

def _run_fold(
    fold_idx:      int,
    train_tickers: list[str],
    test_tickers:  list[str],
    all_cands:     dict[date, list[dict]],   # pre-computed for all tickers
    trading_days:  list[date],
    hist_data:     dict,
    n_picks:       int,
    grid:          list[tuple],
    progress_cb    = None,                   # callable() or None — called after each combo
) -> dict:
    """
    One fold — filters pre-computed candidates instead of re-scanning.

    Train step : find best params on train tickers + first 70% of dates
    Test  step : validate top combos on test tickers + last 30% of dates
    """
    from backtest.swing_optimizer import _simulate_combo as _sim

    split_idx  = int(len(trading_days) * TIME_TRAIN_PCT)
    train_days = trading_days[:split_idx]
    val_days   = trading_days[split_idx:]

    # Fast filter — no scanning, just list comprehension
    train_set   = set(train_tickers)
    test_set    = set(test_tickers)
    train_cands = _filter_candidates(all_cands, train_set, n_picks)
    test_cands  = _filter_candidates(all_cands, test_set,  n_picks)

    train_hist = {t: hist_data[t] for t in train_tickers if t in hist_data}
    test_hist  = {t: hist_data[t] for t in test_tickers  if t in hist_data}

    # Grid search on train data
    train_rows = []
    for stop, tp, trail, hold in grid:
        r = _sim(
            train_cands, train_hist, "momentum", n_picks,
            stop, tp, trail, hold, train_days,
        )
        if r and r.get("n_trades", 0) >= MIN_TRAIN_TRADES:
            train_rows.append({"stop": stop, "tp": tp, "trail": trail, "max_hold": hold, **r})
        if progress_cb is not None:
            progress_cb()

    if not train_rows:
        return {
            "fold": fold_idx,
            "results": [],
            "train_combos": 0,
            "n_train": len(train_tickers),
            "n_test":  len(test_tickers),
            "train_cands": sum(len(v) for v in train_cands.values()),
            "test_cands":  sum(len(v) for v in test_cands.values()),
        }

    train_rows.sort(
        key=lambda x: (x["port_return"] > 0, x["sharpe"], x["port_return"]),
        reverse=True,
    )

    # Validate top N combos on unseen (test) stocks
    validated = []
    for combo in train_rows[:TOP_N_VALIDATE]:
        v = _sim(
            test_cands, test_hist, "momentum", n_picks,
            combo["stop"], combo["tp"], combo["trail"], combo["max_hold"],
            val_days,
        )
        validated.append({
            **combo,
            "test_ret":    v.get("port_return", 0.0) if v else 0.0,
            "test_wr":     v.get("win_rate",    0.0) if v else 0.0,
            "test_sharpe": v.get("sharpe",      0.0) if v else 0.0,
            "test_n":      v.get("n_trades",    0  ) if v else 0,
        })

    validated.sort(key=lambda x: x["test_ret"], reverse=True)

    return {
        "fold":         fold_idx,
        "results":      validated,
        "train_combos": len(train_rows),
        "n_train":      len(train_tickers),
        "n_test":       len(test_tickers),
        "train_cands":  sum(len(v) for v in train_cands.values()),
        "test_cands":   sum(len(v) for v in test_cands.values()),
    }


# ── Consensus algorithm ────────────────────────────────────────────────────────

def _consensus_params(fold_results: list[dict], top_n: int = 3) -> list[dict]:
    scores: dict[tuple, float] = defaultdict(float)
    counts: dict[tuple, int]   = defaultdict(int)

    for fold in fold_results:
        for rank, r in enumerate(fold.get("results", [])[:top_n], 1):
            key = (r["stop"], r["tp"], r["trail"], r["max_hold"])
            scores[key] += (top_n - rank + 1)
            counts[key] += 1

    min_folds = max(2, len(fold_results) // 2 + 1)

    consensus = [
        {
            "stop":            k[0],
            "tp":              k[1],
            "trail":           k[2],
            "max_hold":        k[3],
            "consensus_score": round(v, 1),
            "folds_in_top":    counts[k],
        }
        for k, v in scores.items()
        if counts[k] >= min_folds
    ]
    consensus.sort(key=lambda x: x["consensus_score"], reverse=True)
    return consensus


# ── Robustness map ─────────────────────────────────────────────────────────────

def _robustness_map(
    params:    dict,
    all_cands: dict[date, list[dict]],   # pre-computed, reuse here
    trading_days: list[date],
    hist_data: dict,
    n_picks:   int,
) -> list[dict]:
    """
    Test the consensus params PLUS ±1 step in each direction.
    Uses all tickers (full universe), validation window only.
    """
    from backtest.swing_optimizer import _simulate_combo as _sim

    split_idx = int(len(trading_days) * TIME_TRAIN_PCT)
    val_days  = trading_days[split_idx:]

    # Full-universe candidates filtered to val window
    val_cands = {d: c for d, c in all_cands.items() if d in set(val_days)}

    sc   = params["stop"]
    tp_c = params["tp"]
    tr_c = params["trail"]
    hc   = params["max_hold"]

    si  = _nearest_idx(FINE_STOP_OPTIONS,  sc)
    ti  = _nearest_idx(FINE_TP_OPTIONS,    tp_c)
    tri = _nearest_idx(FINE_TRAIL_OPTIONS, tr_c)

    stop_steps  = sorted({FINE_STOP_OPTIONS [max(0,si-1)],  sc,   FINE_STOP_OPTIONS [min(len(FINE_STOP_OPTIONS)-1,  si+1)]})
    tp_steps    = sorted({FINE_TP_OPTIONS   [max(0,ti-1)],  tp_c, FINE_TP_OPTIONS   [min(len(FINE_TP_OPTIONS)-1,    ti+1)]})
    trail_steps = sorted({FINE_TRAIL_OPTIONS[max(0,tri-1)], tr_c, FINE_TRAIL_OPTIONS[min(len(FINE_TRAIL_OPTIONS)-1,tri+1)]})
    hold_steps  = sorted({max(1, hc - 1), hc, min(7, hc + 1)})

    rows = []
    for stop, tp, trail, hold in product(stop_steps, tp_steps, trail_steps, hold_steps):
        if tp <= stop:
            continue
        r = _sim(val_cands, hist_data, "momentum", n_picks, stop, tp, trail, hold, val_days)
        if r:
            rows.append({
                "stop": stop, "tp": tp, "trail": trail, "max_hold": hold,
                "is_center": (
                    abs(stop  - sc)   < 1e-9 and
                    abs(tp    - tp_c) < 1e-9 and
                    abs(trail - tr_c) < 1e-9 and
                    hold == hc
                ),
                "ret":    r.get("port_return", 0.0),
                "wr":     r.get("win_rate",    0.0),
                "sharpe": r.get("sharpe",      0.0),
            })

    rows.sort(key=lambda x: x["ret"], reverse=True)
    return rows


# ── Print functions ────────────────────────────────────────────────────────────

def _print_fold_summary(fold_results: list[dict], strategy: str) -> None:
    W = 74
    print(f"\n  {'='*W}")
    print(f"  FOLD RESULTS  [{strategy.upper()}]")
    print(f"  {'='*W}")
    print(f"  {'Fold':<6}  {'Stks':>5}  {'Cands':>6}  {'Combos':>7}  "
          f"{'Best test params':>26}  {'Test ret':>9}")
    print(f"  {'-'*6}  {'-'*5}  {'-'*6}  {'-'*7}  {'-'*26}  {'-'*9}")

    for fold in sorted(fold_results, key=lambda x: x["fold"]):
        combos = fold.get("train_combos", 0)
        tc     = fold.get("train_cands", 0) + fold.get("test_cands", 0)
        if fold.get("results"):
            b = fold["results"][0]
            params = (f"s{b['stop']*100:.1f} t{b['tp']*100:.0f} "
                      f"tr{b['trail']*100:.1f} h{b['max_hold']}")
            ret = f"{b['test_ret']:+.1f}%"
        else:
            params = "no valid combos"
            ret    = "n/a"
        print(f"  {fold['fold']:<6}  "
              f"{fold.get('n_train',0)+fold.get('n_test',0):>5}  "
              f"{tc:>6}  "
              f"{combos:>7}  "
              f"{params:>26}  "
              f"{ret:>9}")

    print(f"  {'='*W}\n")


def _print_consensus(consensus: list[dict], strategy: str, n_folds: int) -> None:
    W = 74
    min_folds = max(2, n_folds // 2 + 1)
    print(f"  {'='*W}")
    print(f"  CONSENSUS PARAMS  [{strategy.upper()}]")
    print(f"  {'='*W}")
    print(f"  Only showing combos that ranked top-3 in >= {min_folds}/{n_folds} stock splits.")
    print(f"  These are the params that generalise — not tuned to specific tickers.")
    print()
    print(f"  {'#':<4}  {'Stop':>5}  {'TP':>5}  {'Trail':>6}  {'Hold':>5}  "
          f"{'Folds':>6}  {'Score':>7}")
    print(f"  {'-'*4}  {'-'*5}  {'-'*5}  {'-'*6}  {'-'*5}  "
          f"{'-'*6}  {'-'*7}")

    for i, c in enumerate(consensus[:10], 1):
        flag = "  <-- BEST" if i == 1 else ""
        print(f"  {i:<4}  "
              f"{c['stop']*100:.1f}%   "
              f"{c['tp']*100:.0f}%    "
              f"{c['trail']*100:.1f}%    "
              f"{c['max_hold']}d     "
              f"{c['folds_in_top']:>6}  "
              f"{c['consensus_score']:>7.1f}"
              f"{flag}")

    print(f"\n  {'='*W}")
    best = consensus[0]
    print(f"  RECOMMENDED PARAMS  (most consistent across all stock splits):")
    print(f"    stop  = {best['stop']*100:.1f}%   "
          f"tp = {best['tp']*100:.0f}%   "
          f"trail = {best['trail']*100:.1f}%   "
          f"max_hold = {best['max_hold']} days")
    print(f"    Appeared in top-3 for {best['folds_in_top']}/{n_folds} folds  "
          f"(score {best['consensus_score']})")
    print()


def _print_robustness_map(rmap: list[dict], best: dict) -> None:
    if not rmap:
        return

    profitable = sum(1 for r in rmap if r["ret"] > 0)
    total      = len(rmap)
    pct        = profitable / total * 100
    W          = 74

    print(f"  {'='*W}")
    print(f"  ROBUSTNESS MAP  (+-1 step around consensus params)")
    print(f"  {'='*W}")
    print(f"  Each row = a slightly different stop/TP/trail/hold near the best combo.")
    print(f"  Plateau (most neighbours profitable) = real edge, safe to trade.")
    print(f"  Spike (only the exact combo works)   = may be overfit, use caution.")
    print()
    print(f"  {'Stop':>5}  {'TP':>5}  {'Trail':>6}  {'Hold':>5}  "
          f"{'Return':>8}  {'WR':>6}  {'Sharpe':>7}  {'':>8}")
    print(f"  {'-'*5}  {'-'*5}  {'-'*6}  {'-'*5}  "
          f"{'-'*8}  {'-'*6}  {'-'*7}  {'-'*8}")

    for r in rmap[:16]:
        note = "* BEST *" if r["is_center"] else ""
        sign = "+" if r["ret"] >= 0 else ""
        print(f"  {r['stop']*100:.1f}%   "
              f"{r['tp']*100:.0f}%    "
              f"{r['trail']*100:.1f}%    "
              f"{r['max_hold']}d    "
              f"{sign}{r['ret']:.1f}%    "
              f"{r['wr']:.0f}%    "
              f"{r['sharpe']:>6.2f}  "
              f"  {note}")

    print()
    print(f"  {profitable}/{total} neighbours profitable ({pct:.0f}%)")
    if pct >= 70:
        verdict = "PLATEAU -- params are robust. Safe for live trading."
    elif pct >= 40:
        verdict = "MODERATE -- most neighbours work. Minor parameter sensitivity."
    else:
        verdict = "SPIKE -- only the exact combo works. Possible overfit. Use cautiously."
    print(f"  VERDICT: {verdict}")
    print(f"  {'='*W}\n")


# ── Save helpers ───────────────────────────────────────────────────────────────

def _save_robust_results(
    strategy:     str,
    best:         dict,
    consensus:    list[dict],
    fold_results: list[dict],
    rmap:         list[dict],
    start_date:   str,
    end_date:     str,
) -> None:
    os.makedirs(_REPORT_DIR, exist_ok=True)
    ts   = datetime.now().strftime("%Y%m%d_%H%M")
    path = os.path.join(_REPORT_DIR, f"robust_{strategy}_{ts}.txt")

    profitable = sum(1 for r in rmap if r["ret"] > 0)
    plateau    = (profitable / len(rmap) * 100) if rmap else 0

    lines = [
        f"ROBUST OPTIMIZER  [{strategy.upper()}]",
        f"Period  : {start_date} to {end_date}",
        f"Method  : {N_FOLDS}-fold stock cross-validation (60/40 train/test split)",
        f"Run at  : {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
        "CONSENSUS PARAMS (ranked top-3 in majority of stock splits):",
    ]
    for i, c in enumerate(consensus[:5], 1):
        lines.append(
            f"  {i}. stop={c['stop']*100:.1f}%  tp={c['tp']*100:.0f}%  "
            f"trail={c['trail']*100:.1f}%  hold={c['max_hold']}d  "
            f"(folds={c['folds_in_top']}  score={c['consensus_score']})"
        )

    lines += [
        "",
        f"ROBUSTNESS: {profitable}/{len(rmap)} neighbours profitable ({plateau:.0f}%)",
        f"VERDICT  : {'PLATEAU (robust)' if plateau >= 70 else 'MODERATE' if plateau >= 40 else 'SPIKE (fragile)'}",
        "",
        "FOLD RESULTS:",
    ]
    for fold in sorted(fold_results, key=lambda x: x["fold"]):
        if fold.get("results"):
            b = fold["results"][0]
            lines.append(
                f"  Fold {fold['fold']}: "
                f"stop={b['stop']*100:.1f}%  tp={b['tp']*100:.0f}%  "
                f"trail={b['trail']*100:.1f}%  hold={b['max_hold']}d  "
                f"test_ret={b['test_ret']:+.1f}%  test_wr={b['test_wr']:.0f}%"
            )
        else:
            lines.append(f"  Fold {fold['fold']}: no valid combinations")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print(f"  Results saved to memory/swing_results/robust_{strategy}_{ts}.txt")
    print(f"  (Open this file any time -- results survive window close)")


def _apply_consensus(strategy: str, best: dict) -> None:
    existing: dict = {}
    if os.path.exists(_PARAMS_PATH):
        try:
            with open(_PARAMS_PATH) as f:
                existing = json.load(f)
        except Exception:
            pass

    existing[strategy] = {
        "stop":     best["stop"],
        "tp":       best["tp"],
        "trail":    best["trail"],
        "max_hold": best["max_hold"],
    }
    os.makedirs(os.path.dirname(_PARAMS_PATH), exist_ok=True)
    with open(_PARAMS_PATH, "w") as f:
        json.dump(existing, f, indent=2)

    print(f"\n  Consensus params saved to memory/swing_best_params.json")
    print(f"  {strategy}: stop={best['stop']*100:.1f}%  "
          f"tp={best['tp']*100:.0f}%  "
          f"trail={best['trail']*100:.1f}%  "
          f"max_hold={best['max_hold']}d")
    print(f"  Future swing backtests and live trades will use these automatically.\n")


# ── Main entry point ───────────────────────────────────────────────────────────

def run_robust_optimizer(
    start_date:  str,
    end_date:    str,
    strategy:    str  = "momentum",
    n_folds:     int  = N_FOLDS,
    apply_best:  bool = False,
    verbose:     bool = True,
) -> dict:
    """
    Full robust cross-validation pipeline.
    Pre-computes candidates once, then runs parallel folds using fast filtering.
    """
    from backtest.swing_backtest import _get_trading_days, _load_weights
    from backtest.backtest import _download_hist_data, _build_market_regime
    from scanner.universe import get_all_tickers

    grid = _fine_grid()

    W = 68
    print(f"\n  {'='*W}")
    print(f"  ROBUST OPTIMIZER  [{strategy.upper()}]")
    print(f"  {'='*W}")
    print(f"  Period   : {start_date}  to  {end_date}")
    print(f"  Method   : {n_folds} random stock splits  (60% train / 40% test)")
    print(f"  Grid     : {len(grid)} fine-grained combos")
    print(f"             stop 2-5%  TP 5-12%  trail 2-5%  hold 2-5d")
    print(f"  Goal     : params that work on stocks the optimizer NEVER saw")
    print(f"  {'='*W}\n")

    # ── 1. Data ──────────────────────────────────────────────────────────────────
    trading_days = _get_trading_days(start_date, end_date)
    if len(trading_days) < 60:
        print("  Need at least 60 trading days. Try a longer date range.")
        return {}

    all_tickers = get_all_tickers()
    weights     = _load_weights()

    hist_data     = _download_hist_data(trading_days, all_tickers, verbose=True)
    market_regime = _build_market_regime(hist_data, trading_days)

    available = [t for t in all_tickers if t in hist_data]
    print(f"  {len(available)} tickers with data")

    if len(available) < 30:
        print("  Too few tickers with data. Check ALPACA_API_KEY in your .env file.")
        return {}

    split_idx = int(len(trading_days) * TIME_TRAIN_PCT)
    print(f"  Time split: {split_idx} train days / "
          f"{len(trading_days)-split_idx} val days  (70/30)")
    print(f"  Stock split: ~{int(len(available)*TRAIN_STOCK_PCT)} train / "
          f"~{len(available)-int(len(available)*TRAIN_STOCK_PCT)} test per fold")

    # ── 2. Pre-compute all candidates ONCE ───────────────────────────────────────
    print(f"\n  Scanning {len(available)} tickers × {len(trading_days)} days "
          f"for {strategy} signals...")
    scan_bar  = _ProgressBar(len(trading_days), "Scanning ")
    all_cands = _precompute_all_candidates(
        trading_days, hist_data, market_regime, weights, strategy, scan_bar
    )
    scan_bar.finish()
    total_cands = sum(len(v) for v in all_cands.values())
    print(f"  Found {total_cands} candidates on {len(all_cands)} active days")
    print(f"  (Each fold just filters this list — no re-scanning)\n")

    if not all_cands:
        print(f"  No {strategy} candidates found in this date range.")
        return {}

    # ── 3. Generate k-fold stock splits ──────────────────────────────────────────
    folds = _stock_kfold(available, n_folds)

    # ── 4. Run folds in parallel ──────────────────────────────────────────────────
    total_evals = n_folds * len(grid)
    print(f"  Running {n_folds} folds  ({len(grid):,} combos x {n_folds} folds"
          f" = {total_evals:,} evaluations)...")
    grid_bar = _ProgressBar(total_evals, "Grid  ")

    fold_results: list[dict | None] = [None] * n_folds

    def _submit_fold(args):
        idx, train_t, test_t = args
        result = _run_fold(
            fold_idx=idx,
            train_tickers=train_t,
            test_tickers=test_t,
            all_cands=all_cands,
            trading_days=trading_days,
            hist_data=hist_data,
            n_picks=3,
            grid=grid,
            progress_cb=grid_bar.update,
        )
        return idx, result

    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_PARALLEL) as pool:
        futures = {
            pool.submit(_submit_fold, (i, train, test)): i
            for i, (train, test) in enumerate(folds, 1)
        }
        for fut in concurrent.futures.as_completed(futures):
            fold_idx, fold = fut.result()
            fold_results[fold_idx - 1] = fold

            if fold.get("results"):
                b   = fold["results"][0]
                msg = (f"  Fold {fold_idx}/{n_folds} done -- "
                       f"train {fold['n_train']} / test {fold['n_test']} stks  |  "
                       f"best: s={b['stop']*100:.1f}%  tp={b['tp']*100:.0f}%  "
                       f"tr={b['trail']*100:.1f}%  h={b['max_hold']}d  "
                       f"ret={b['test_ret']:+.1f}%")
            else:
                msg = f"  Fold {fold_idx}/{n_folds} done -- no valid combinations found."
            grid_bar.print_above(msg)

    grid_bar.finish()

    fold_results = [f for f in fold_results if f is not None]

    # ── 5. Consensus ──────────────────────────────────────────────────────────────
    _print_fold_summary(fold_results, strategy)

    consensus = _consensus_params(fold_results)

    if not consensus:
        print("\n  No consensus params found.")
        print("  No combination consistently ranked top-3 across multiple stock splits.")
        print("  Possible causes:")
        print("    - Too short a date range (try 2020-01-01 to 2026-01-01)")
        print("    - Strategy doesn't have a stable edge in this period")
        return {}

    _print_consensus(consensus, strategy, n_folds)

    # ── 6. Robustness map ─────────────────────────────────────────────────────────
    best = consensus[0]
    print(f"  Running robustness map around best consensus params...", end=" ", flush=True)
    rmap = _robustness_map(best, all_cands, trading_days, hist_data, 3)
    print("done")
    _print_robustness_map(rmap, best)

    # ── 7. Save & optionally apply ────────────────────────────────────────────────
    _save_robust_results(strategy, best, consensus, fold_results, rmap, start_date, end_date)

    if apply_best:
        _apply_consensus(strategy, best)

    return {
        "strategy":       strategy,
        "start_date":     start_date,
        "end_date":       end_date,
        "n_folds":        n_folds,
        "n_tickers":      len(available),
        "consensus":      consensus,
        "best_consensus": best,
        "fold_results":   fold_results,
        "robustness_map": rmap,
    }
