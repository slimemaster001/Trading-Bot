"""
scorer/scorer.py
Combines technical pre-score + Ollama AI score into a final ranking.

Final score = weighted blend of tech_score and ai_score.
Weights come from memory/scoring_weights.json (updated by learner over time).
"""

from __future__ import annotations

import json
import os

from scorer.ollama import score_with_ollama

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_WEIGHTS_PATH = os.path.join(_BASE, "memory", "scoring_weights.json")

# Default blend weights (learner updates these over time)
DEFAULT_TECH_WEIGHT = 0.35
DEFAULT_AI_WEIGHT   = 0.65


def _load_blend_weights() -> tuple[float, float]:
    try:
        with open(_WEIGHTS_PATH) as f:
            w = json.load(f)
        tech_w = w.get("blend", {}).get("tech_weight", DEFAULT_TECH_WEIGHT)
        ai_w   = w.get("blend", {}).get("ai_weight",   DEFAULT_AI_WEIGHT)
        return tech_w, ai_w
    except (FileNotFoundError, KeyError):
        return DEFAULT_TECH_WEIGHT, DEFAULT_AI_WEIGHT


def rank_candidates(candidates: list[dict], verbose: bool = True) -> list[dict]:
    """
    Full scoring pipeline:
      1. Send candidates to Ollama for AI scoring
      2. Blend AI score with technical score
      3. Return final ranked list

    Each candidate dict will have:
      tech_score   — rule-based technical score (0–10)
      ai_score     — Ollama AI score (0–10)
      final_score  — weighted blend (0–10)
      ai_reason    — Ollama's one-line explanation
      ai_risk      — Ollama's identified downside risk
    """
    if not candidates:
        return []

    # Extract news context if scanner already fetched it (stored in candidate dicts)
    news_context: dict[str, str] = {}
    for c in candidates:
        headline = c.get("news_headline", "")
        if headline:
            news_context[c["ticker"]] = headline

    # AI scoring (adds ai_score, ai_reason, ai_risk to each dict)
    scored = score_with_ollama(candidates, news_context=news_context or None)

    tech_w, ai_w = _load_blend_weights()

    for stock in scored:
        t = stock.get("tech_score", 5.0)
        a = stock.get("ai_score",   5.0)
        stock["final_score"] = round(tech_w * t + ai_w * a, 2)

    # Sort by final score
    scored.sort(key=lambda x: x["final_score"], reverse=True)

    if verbose:
        print(f"  Scoring complete  --  "
              f"tech {tech_w:.0%} / AI {ai_w:.0%}")

    return scored


def _conf_label(score: float, rv: float = 1.0) -> str:
    """
    Map a final score + relative volume to a confidence label.

    Volume matters because a big premarket move on thin volume often reverses.
    Any pick with RV < 1.2x gets a '?' flag — score looks good but volume
    doesn't confirm it yet. Re-check at the real market open.

    [HIGH ] score >= 8.5  AND  RV >= 1.2x  -- strong score + real volume
    [HIGH?] score >= 8.5  BUT  RV <  1.2x  -- good score, volume unconfirmed
    [GOOD ] score >= 7.0  AND  RV >= 1.2x  -- solid setup with volume
    [GOOD?] score >= 7.0  BUT  RV <  1.2x  -- solid score, volume unconfirmed
    [OK   ] score >= 5.5                    -- marginal
    [WEAK ] score <  5.5                    -- skip
    """
    low_vol = rv < 1.2
    if score >= 8.5:
        return "[HIGH?]" if low_vol else "[HIGH ]"
    if score >= 7.0:
        return "[GOOD?]" if low_vol else "[GOOD ]"
    if score >= 5.5:
        return "[OK   ]"
    return     "[WEAK ]"


def format_picks_table(scored: list[dict], top_n: int = 10) -> str:
    """
    Pretty-print the top picks as a terminal table.

    CONF column at a glance:
      [HIGH ] score >= 8.5 + RV >= 1.2x  -- strong setup, volume confirms it
      [HIGH?] score >= 8.5 but low volume -- score good, wait for open to confirm
      [GOOD ] score >= 7.0 + RV >= 1.2x  -- solid setup
      [GOOD?] score >= 7.0 but low volume -- solid score, unconfirmed volume
      [OK   ] score >= 5.5               -- marginal
      [WEAK ] score  < 5.5               -- skip

    '?' means the score looks good but volume (RV < 1.2x) doesn't back the
    premarket move yet. These should be re-evaluated at the 9:45 AM open.
    Score = 35% technical signals + 65% AI (Ollama) judgment, 0-10 scale.
    """
    rows = scored[:top_n]
    if not rows:
        return "No candidates found."

    DIV = "-" * 96

    header = (
        f"\n{'#':<3} {'Ticker':<7} {'Price':>7} {'PM%':>6} {'RV':>5} "
        f"{'RSI':>5} {'Score':>6}  {'Conf':<8}  Reason\n"
        + DIV
    )
    lines = [header]
    for i, s in enumerate(rows, 1):
        score  = s.get("final_score", 0)
        rv     = s.get("rel_volume", 1.0)
        conf   = _conf_label(score, rv)
        reason = (s.get("ai_reason") or "")[:52]
        lines.append(
            f"{i:<3} {s['ticker']:<7} ${s['price']:>6.2f} "
            f"{s.get('premarket_pct', 0):>+5.1f}% "
            f"{rv:>4.1f}x "
            f"{s.get('rsi', 0):>5.1f} "
            f"{score:>6.2f}  {conf}  {reason}"
        )

    lines.append(DIV)
    lines.append(
        "  Conf: [HIGH/GOOD] strong setup"
        "   [HIGH?/GOOD?] good score but low volume -- verify at open"
        "   [OK] marginal   [WEAK] skip"
    )
    return "\n".join(lines)
