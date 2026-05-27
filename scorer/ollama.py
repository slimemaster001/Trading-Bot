"""
scorer/ollama.py
Talks to your local Ollama instance to score day-trading candidates.

Sends candidates in small batches to avoid overwhelming the model.
Uses /api/generate (more reliable than /api/chat for structured output).
Falls back to rule-based scoring if Ollama is unreachable or crashes.

Ollama API:  http://localhost:11434
"""

from __future__ import annotations

import json
import os
import time
import requests
from typing import Optional

OLLAMA_URL      = os.environ.get("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL    = os.environ.get("OLLAMA_MODEL", "")
WARMUP_TIMEOUT  = 300      # seconds for cold model load (first request after idle)
TIMEOUT         = 120      # seconds per batch once model is warm
BATCH_SIZE      = 10       # stocks per Ollama call — keeps prompt small and reliable
MAX_RETRIES     = 3        # retries on timeout/500 before giving up

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_RESULTS_PATH = os.path.join(_BASE, "memory", "trade_results.json")


# ── Model detection ────────────────────────────────────────────────────────────

def detect_model() -> Optional[str]:
    """Ask Ollama which models are installed. Picks the best available."""
    try:
        r = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
        r.raise_for_status()
        models = [m["name"] for m in r.json().get("models", [])]
        if not models:
            return None
        prefs = ["llama3", "deepseek", "mistral", "qwen", "phi", "gemma"]
        for pref in prefs:
            for m in models:
                if pref in m.lower():
                    return m
        return models[0]
    except Exception:
        return None


def is_ollama_running() -> bool:
    try:
        requests.get(f"{OLLAMA_URL}/api/tags", timeout=3)
        return True
    except Exception:
        return False


def _warmup_model(model: str) -> bool:
    """
    Send a 1-token request to force Ollama to load the model into VRAM.
    A cold 7B model can take 60-120 seconds to load — doing this before
    the real batches means the actual scoring uses a shorter timeout.

    Returns True if warm (or already warm), False if load timed out.
    """
    try:
        r = requests.post(
            f"{OLLAMA_URL}/api/generate",
            json={"model": model, "prompt": "Hi", "stream": False,
                  "options": {"num_predict": 1}},
            timeout=WARMUP_TIMEOUT,
        )
        return r.status_code == 200
    except Exception:
        return False


# ── Learned context ────────────────────────────────────────────────────────────

def _build_learned_context() -> str:
    try:
        with open(_RESULTS_PATH) as f:
            results = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return "No historical data yet."

    closed = [t for t in results if t.get("status") == "closed"][-30:]
    if not closed:
        return "No closed trades yet."

    winners  = [t for t in closed if t.get("pnl_pct", 0) > 0]
    win_rate = round(len(winners) / len(closed) * 100)
    avg_win  = round(sum(t["pnl_pct"] for t in winners) / max(len(winners), 1), 2)
    return f"Past {len(closed)} trades: {win_rate}% win rate, avg win +{avg_win}%."


# ── Single-batch scorer ────────────────────────────────────────────────────────

def _score_batch(
    batch:        list[dict],
    learned:      str,
    model:        str,
    news_context: dict[str, str] | None = None,
) -> list[dict] | None:
    """
    Score one batch of stocks with Ollama /api/generate.
    Returns parsed list of {ticker, score, reason, risk} or None on failure.

    news_context: optional {ticker: "headline1 | headline2"} dict from scanner/news.py.
    Your local Ollama processes this entirely on-device — news never goes to any AI cloud.
    """
    # Build a compact table — much smaller than JSON, works better for 7B models
    lines = ["ticker | pm% | relVol | RSI | aboveMA20% | from52wHigh% | ret5d | techScore"]
    for c in batch:
        lines.append(
            f"{c['ticker']} | {c['premarket_pct']:+.1f} | {c['rel_volume']:.1f}x | "
            f"{c['rsi']:.0f} | {c['above_ma20_pct']:+.1f}% | "
            f"{c['pct_from_52w_high']:.1f}% | {c['ret_5d']:+.1f}% | {c['tech_score']:.1f}"
        )
    table = "\n".join(lines)

    tickers = [c["ticker"] for c in batch]

    # Build news section (only included if news data was fetched)
    news_section = ""
    if news_context:
        news_lines = []
        for c in batch:
            ticker = c["ticker"]
            headline = (news_context.get(ticker) or c.get("news_headline") or "").strip()
            if headline:
                news_lines.append(f"  {ticker}: {headline[:120]}")
        if news_lines:
            news_section = "\nRecent news (use to adjust score up/down):\n" + "\n".join(news_lines)

    prompt = f"""You are a swing trading analyst. Score each stock for probability of gaining 5%+ over the next 2 days.

Scoring: 1=very unlikely, 5=neutral, 10=very likely to gain 5%+ in 2 days.
Key factors: premarket momentum (+1% to +5% is ideal), relative volume (higher=better),
RSI 50-65 (healthy momentum), near 52-week high (breakout zone), positive 5-day return.
Positive news catalyst (earnings beat, upgrade, buyback) raises score. Upcoming earnings = risk.
{learned}{news_section}

Technical data:
{table}

Reply with ONLY a JSON array, nothing else:
[{{"ticker":"XX","score":7,"reason":"brief reason","risk":"main risk"}}]
Score all {len(batch)} tickers: {', '.join(tickers)}"""

    for attempt in range(MAX_RETRIES):
        try:
            payload = {
                "model":  model,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.1, "num_predict": 800},
            }
            r = requests.post(
                f"{OLLAMA_URL}/api/generate",
                json=payload,
                timeout=TIMEOUT,
            )
            r.raise_for_status()
            raw = r.json().get("response", "").strip()

            # Strip markdown fences if present
            if "```" in raw:
                parts = raw.split("```")
                for p in parts:
                    p = p.strip()
                    if p.startswith("[") or p.startswith("json\n["):
                        raw = p.replace("json\n", "").strip()
                        break

            # Find the JSON array in the response
            start = raw.find("[")
            end   = raw.rfind("]") + 1
            if start == -1 or end == 0:
                raise ValueError("No JSON array found in response")
            raw = raw[start:end]

            return json.loads(raw)

        except Exception as e:
            if attempt < MAX_RETRIES - 1:
                wait = 3 * (attempt + 1)
                print(f"    attempt {attempt + 1}/{MAX_RETRIES} failed ({type(e).__name__})"
                      f" -- retrying in {wait}s...")
                time.sleep(wait)
            else:
                print(f"    all {MAX_RETRIES} attempts failed -- using rule-based fallback")
                return None

    return None


# ── Public API ─────────────────────────────────────────────────────────────────

def score_with_ollama(
    candidates:   list[dict],
    news_context: dict[str, str] | None = None,
) -> list[dict]:
    """
    Score all candidates with Ollama in small batches of BATCH_SIZE.
    Adds ai_score, ai_reason, ai_risk to each candidate.
    Falls back gracefully (uses tech_score) if Ollama fails.

    news_context: optional {ticker: headlines} from scanner/news.py.
    Ollama uses this as context when scoring — processed entirely on-device.
    Pass None to score on technical data only (default / backwards-compatible).
    """
    global OLLAMA_MODEL
    if not OLLAMA_MODEL:
        OLLAMA_MODEL = detect_model() or ""

    if not OLLAMA_MODEL or not is_ollama_running():
        print("  Ollama not available -- using rule-based scores")
        return _apply_fallback(candidates, "Ollama offline")

    has_news = bool(news_context and any(v for v in news_context.values()))

    # Warm up the model before batching — forces VRAM load so batches don't timeout
    print(f"  Scoring with Ollama ({OLLAMA_MODEL})"
          + ("  [+ news]" if has_news else "") + "  ...", end=" ", flush=True)
    _warmup_model(OLLAMA_MODEL)
    print("ready")

    learned = _build_learned_context()
    score_map: dict[str, dict] = {}
    total_batches = (len(candidates) + BATCH_SIZE - 1) // BATCH_SIZE

    for i in range(0, len(candidates), BATCH_SIZE):
        batch     = candidates[i : i + BATCH_SIZE]
        batch_num = i // BATCH_SIZE + 1
        print(f"    Batch {batch_num}/{total_batches} ({len(batch)} stocks)...",
              end=" ", flush=True)

        result = _score_batch(batch, learned, OLLAMA_MODEL, news_context=news_context)

        if result:
            for s in result:
                if isinstance(s, dict) and "ticker" in s:
                    score_map[s["ticker"]] = s
            print(f"done ({len(result)} scored)")
        else:
            fallback_batch = _apply_fallback(batch, "Ollama error")
            for c in fallback_batch:
                score_map[c["ticker"]] = {
                    "ticker": c["ticker"],
                    "score":  c["ai_score"],
                    "reason": c["ai_reason"],
                    "risk":   c["ai_risk"],
                }
            print("failed (rule-based fallback)")

    # Apply scores back to candidates
    for c in candidates:
        s = score_map.get(c["ticker"], {})
        raw_score = s.get("score", c.get("tech_score", 5.0))
        # Clamp to 1-10 in case model returns out-of-range values
        c["ai_score"]  = max(1.0, min(10.0, float(raw_score)))
        c["ai_reason"] = str(s.get("reason", "—"))[:80]
        c["ai_risk"]   = str(s.get("risk",   "—"))[:60]

    return sorted(candidates, key=lambda x: x["ai_score"], reverse=True)


def _apply_fallback(candidates: list[dict], reason: str) -> list[dict]:
    """
    When Ollama is unavailable, compute a differentiated rule-based score
    so the ranking is still useful. Uses premarket momentum, relative volume,
    RSI quality, and trend alignment to break ties between candidates.
    """
    for c in candidates:
        score = 5.0  # neutral baseline

        # Pre-market momentum: ideal is +0.5% to +3%
        pm = c.get("premarket_pct", 0)
        if   0.5 <= pm <= 2.0:  score += 2.0   # perfect window
        elif 2.0 <  pm <= 3.5:  score += 1.0   # good but getting large
        elif pm > 3.5:          score -= 0.5   # gap risk
        elif pm > 0:            score += 0.5   # slight positive
        elif pm < -1.0:         score -= 1.5   # fading

        # Relative volume: higher = more fuel
        rv = c.get("rel_volume", 1.0)
        if   rv >= 3.0: score += 2.0
        elif rv >= 2.0: score += 1.5
        elif rv >= 1.5: score += 0.8
        elif rv <  0.8: score -= 1.0

        # RSI quality: 55-65 is the sweet spot for intraday longs
        rsi = c.get("rsi", 50)
        if   55 <= rsi <= 65:   score += 1.5
        elif 50 <= rsi <  55:   score += 0.5
        elif 65 <  rsi <= 72:   score -= 0.5
        elif rsi > 72:          score -= 1.5

        # Trend: above 20-day MA confirms uptrend
        if c.get("above_ma20_pct", 0) > 2:   score += 0.8
        elif c.get("above_ma20_pct", 0) < -3: score -= 0.8

        # Near 52-week high = breakout zone
        from_high = c.get("pct_from_52w_high", -50)
        if  -3 <= from_high <= 0:    score += 1.5   # right at ATH
        elif -8 <= from_high < -3:   score += 0.8   # close to ATH
        elif from_high < -25:        score -= 0.5   # deep hole

        # 5-day momentum
        ret5 = c.get("ret_5d", 0)
        if   ret5 >= 5:  score += 0.8
        elif ret5 >= 2:  score += 0.4
        elif ret5 < -5:  score -= 0.8

        c["ai_score"]  = round(max(1.0, min(10.0, score)), 2)
        c["ai_reason"] = (
            f"[No AI] PM={pm:+.1f}% RV={rv:.1f}x RSI={rsi:.0f} "
            f"ATH={from_high:.1f}% 5d={ret5:+.1f}%"
        )
        c["ai_risk"] = "Ollama offline — rule-based score only"

    return sorted(candidates, key=lambda x: x["ai_score"], reverse=True)
