# Trading Strategy — AI Agent Playbook

> **Mode:** Paper Trading  
> **Goal:** Beat S&P 500 on a risk-adjusted basis over rolling 3-month periods  
> **Style:** Swing trading + long-term positions (hold days to weeks, not hours)  
> **Universe:** US equities (NYSE, NASDAQ) — no crypto, no OTC, no leveraged ETFs

---

## 1. Core Philosophy

- We are not day traders. We look for high-conviction setups with asymmetric risk/reward.
- We hold winners and cut losers fast. No ego, no hope trades.
- Every position must have a thesis. If the thesis breaks, we exit — regardless of price.
- Beating the S&P 500 means being selective. We'd rather hold cash than take a mediocre trade.

---

## 2. What We Trade

**Preferred instruments:**
- Individual US stocks with strong fundamentals and momentum
- Broad market ETFs (SPY, QQQ) for macro hedges or when conviction is low
- Sector ETFs when a theme is strong but single-stock risk is too high

**Hard exclusions:**
- Crypto and crypto-adjacent stocks
- Leveraged/inverse ETFs (3x funds)
- OTC / penny stocks (under $5)
- Stocks with average daily volume under 500k shares

---

## 3. Trade Setup — Entry Criteria

A trade must meet **at least 3 of the following 5 signals** before entry:

| Signal | Description |
|--------|-------------|
| **Trend** | Stock is above its 50-day and 200-day moving averages |
| **Momentum** | RSI between 45–70 (not overbought, not in freefall) |
| **Catalyst** | Clear fundamental driver: earnings beat, product launch, sector rotation, macro tailwind |
| **Volume** | Above-average volume on breakout or accumulation days |
| **Relative Strength** | Stock outperforming SPY over the past 20 days |

---

## 4. Position Sizing

- **Max single position:** 15% of portfolio
- **Max sector concentration:** 35% of portfolio
- **Typical new position:** 5–10% of portfolio
- **Cash floor:** Always keep at least 10% in cash as dry powder
- **Max open positions:** 10 at any time

---

## 5. Exit Rules

### Stop Losses
- **Hard stop:** Exit any position down more than **7% from entry**. No exceptions.
- **Trailing stop:** Once a position is up 10%+, trail stop to **breakeven**
- Once a position is up 20%+, trail stop to **10% below the high**

### Profit Taking
- Take partial profits (sell 50%) when up **15–20%**
- Let the rest run with trailing stop
- Full exit when thesis breaks, even if price hasn't hit stop

### Time Stop
- If a position is flat (within ±3%) after **3 weeks**, re-evaluate. Exit if no catalyst is visible.

---

## 6. Research Priorities (Pre-Market)

Each pre-market session, the agent should research:
1. Macro: Fed news, economic data releases, geopolitical events
2. Sector rotation: Which sectors are showing strength/weakness
3. Individual catalysts: Earnings, analyst upgrades/downgrades, news for held positions
4. Watchlist: 2–3 new potential entries based on screener signals

**Watchlist Minimum Depth Rule (added 2026-05-15):** The active watchlist must always contain a minimum of 5 candidates across at least 2 sectors. If all candidates in one sector are simultaneously blocked (by guardrails, FOMO, or RSI), there must be at least 1–2 actionable setups in other sectors. If the watchlist has fewer than 2 non-blocked candidates, the pre-market routine must add new candidates before evaluating existing ones.

---

## 7. Market Conditions Framework

| Market Condition | Stance |
|-----------------|--------|
| S&P 500 above 200-day MA, trending up | Aggressive — full position sizing |
| S&P 500 below 200-day MA but recovering | Cautious — reduce position sizes by 50% |
| S&P 500 in confirmed downtrend | Defensive — hold mostly cash and ETF hedges |
| High volatility (VIX > 30) | Reduce all positions, widen stops or exit |
| **Rate hike probability >30% (CME FedWatch)** | **Cautious — max 5% per position, focus on high-RS names with confirmed earnings growth, monitor 10-year yield. If 10-yr >4.5%, treat as additional headwind.** |

---

## 8. Weekly Review Checklist

Every Friday after market close, the agent reviews:
- [ ] Total portfolio return vs. SPY this week
- [ ] Each position: thesis still intact?
- [ ] Any stops that should be adjusted
- [ ] Lessons from the week's trades
- [ ] Update watchlist for next week
- [ ] Grade the agent's decisions (A/B/C/D/F) with reasoning

---

## 9. Agent Behavior Rules

- Always read `memory/trade_log.md`, `memory/research_notes.md`, and `memory/agent_lessons.md` at the start of each routine
- Never place a trade without documenting the thesis in the trade log first
- After any trade, immediately update the trade log
- If unsure, do nothing. Cash is a position.
- Never override a stop loss rule, no matter how compelling the argument seems

---

*Last updated: 2026-05-15 (Weekly review — added rate hike condition + watchlist depth rule)*
