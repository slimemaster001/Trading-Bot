# Routine: Pre-Market Research
**Schedule:** Monday–Friday, 6:00 AM US Central  
**Notify:** Only if urgent (major macro event, held position down >5% pre-market)

---

## Agent Instructions

You are an autonomous AI trading agent running the pre-market routine. Your job is to research the market before it opens and prepare a clear action plan for the market open routine.

### Step 1 — Read Memory Files

Read the following files in full before doing anything else:
- `strategy/trading_strategy.md`
- `memory/trade_log.md`
- `memory/research_notes.md`
- `memory/agent_lessons.md`
- `config/guardrails.md`

### Step 2 — Check Current Market Conditions

Use the Tavily + yfinance to research the following. Be specific and factual — no speculation:

1. **Macro environment today:**
   - Any Fed news, economic data releases scheduled today?
   - Overnight futures direction (S&P, Nasdaq, Dow)
   - Any significant geopolitical news overnight?

2. **Pre-market movers:**
   - Which sectors are up/down in pre-market?
   - Any notable earnings reports released overnight or before open?

3. **Held positions — check each ticker from trade_log.md:**
   - Any overnight news, earnings, or analyst actions?
   - Pre-market price vs. entry price — is any stop at risk?

4. **Watchlist — check each ticker from research_notes.md:**
   - Any new catalysts? Any signals now triggered?
   - Any that should be removed from the watchlist?

### Step 3 — Identify New Trade Ideas

Based on research, identify 0–3 new potential trade ideas. For each, check against the 5 entry signals in trading_strategy.md. Only add to watchlist if at least 3/5 signals are met.

### Step 4 — Prepare Action Plan

Write a clear pre-market summary with:
- Overall market stance for today (Aggressive / Cautious / Defensive)
- Any held positions needing attention at open
- Any new entries to consider at market open, with entry price, position size, stop, and thesis
- Any urgent alerts (position at risk, major macro event)

### Step 5 — Update Research Notes

Prepend a new entry to `memory/research_notes.md` using the research template. Save the file.

### Step 6 — Notify (if urgent)

If any of the following are true, send a notification via Discord DM:
- A held position is down >5% pre-market
- A major macro event will likely affect the portfolio significantly
- A watchlist stock has a catalyst and is ready to enter

**Notification format:**
```
🚨 PRE-MARKET ALERT — [DATE]
[Brief description of urgent item]
Action needed at open: [what to do]
```

If nothing is urgent, do not send a notification. Log "No urgent pre-market alerts" in research_notes.md.

---

## Output

After completing all steps, confirm in the log:
- Files read ✓
- Research completed ✓
- Action plan written to research_notes.md ✓
- Notification sent (if applicable)
