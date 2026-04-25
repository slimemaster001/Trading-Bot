# Routine: Midday Check
**Schedule:** Monday–Friday, 12:00 PM US Central  
**Notify:** Log updates only (no notification unless emergency)

---

## Agent Instructions

You are an autonomous AI trading agent running the midday routine. Your job is a quick portfolio health check — cut losses that have deepened, tighten stops on winners, and log any meaningful developments. Keep this routine focused and efficient.

### Step 1 — Read Memory Files

Read the following files before acting:
- `memory/trade_log.md` (active positions only)
- `memory/agent_lessons.md`
- `config/guardrails.md`

### Step 2 — Position Health Check

For each open position in `memory/trade_log.md`:

1. Get the current price via Alpaca API
2. Calculate intraday P&L and total P&L from entry
3. Apply the following midday rules:

| Condition | Action |
|-----------|--------|
| Down >7% from entry | **EXIT immediately** — hard stop triggered |
| Down >5% from entry | Log warning — monitor closely for rest of day |
| Up >20% from entry | **Tighten trailing stop to 10% below today's high** |
| Up >10%, stop still at entry | **Confirm stop is at breakeven** |
| Flat — thesis unchanged | No action needed |
| News event changed thesis | **Consider exiting** — log reasoning |

### Step 3 — No New Entries at Midday

Do **not** open new positions at midday. This routine is for portfolio management only. New entries are reserved for the pre-market/market-open routine or the next day's pre-market.

*Exception: If a pre-market watchlist stock triggers all 5 signals and is breaking out on high volume — log it for the weekly review and consider it for next morning's pre-market routine.*

### Step 4 — Update Trade Log

Append midday notes to relevant position entries in `memory/trade_log.md`:
- Any stop adjustments
- Any exits taken
- Any notable price action

Use format: `[MIDDAY DATE] — [brief note]`

### Step 5 — Quick Research Scan (5 minutes max)

Using Tavily + yfinance, do a fast scan for:
- Any breaking news for held positions
- Any Fed announcements or macro developments since morning
- If anything material found, prepend a brief note to `memory/research_notes.md`

### Step 6 — Notify (emergency only)

Only send a Discord DM notification if:
- A stop was triggered and a position was exited
- A major news event is affecting the portfolio

```
⚠️ MIDDAY ALERT — [DATE]
[Brief description of action taken or event]
```

---

## Output

After completing all steps, confirm in the log:
- Files read ✓
- Positions checked ✓
- Trade log updated ✓
- Research scan done ✓
- Notification sent (if applicable)
