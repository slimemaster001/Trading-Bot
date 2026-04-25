# Guardrails & Configuration

> This file is read by the agent at the start of every routine.  
> Changes here take immediate effect on the next routine run.  
> Do not modify this file while a routine is running.

---

## 🔴 Trading Mode

```
TRADING_MODE = PAPER
```

**Options:** `PAPER` | `LIVE`

- `PAPER` → Use Alpaca paper trading endpoint: `https://paper-api.alpaca.markets`
- `LIVE` → Use Alpaca live endpoint: `https://api.alpaca.markets`

⚠️ **Do not switch to LIVE until you have manually reviewed at least 4 weeks of paper trading results and are satisfied with agent behavior.**

---

## Position Limits

| Rule | Value |
|------|-------|
| Max single position size | 15% of portfolio |
| Typical new position size | 5–10% of portfolio |
| Max sector concentration | 35% of portfolio |
| Max open positions | 10 |
| Minimum cash reserve | 10% of portfolio |

**If any limit would be violated by a new trade → abort the trade and log the reason.**

---

## Loss Limits

| Rule | Value |
|------|-------|
| Hard stop per position | 7% below entry price |
| Daily portfolio loss halt | -3% in a single day |
| Weekly portfolio loss halt | -5% in a single week |

**Daily loss halt:** If the total portfolio is down >3% in a single day, do not place any new trades for the remainder of that day. Log the halt and resume normal operation next morning.

**Weekly loss halt:** If the total portfolio is down >5% in a week, reduce all position sizes by 50% the following week and enter cautious mode until recovered.

---

## Asset Restrictions

| Restriction | Details |
|-------------|---------|
| No crypto | Do not trade any cryptocurrency or crypto ETFs |
| No leveraged ETFs | No 2x or 3x leveraged funds |
| No inverse ETFs | No short-only ETFs |
| No OTC / penny stocks | Minimum price $5.00 |
| No low-liquidity stocks | Minimum average daily volume 500,000 shares |
| No single-stock options | Equities only at this stage |

---

## Order Rules

| Rule | Value |
|------|-------|
| Order type | Limit orders only (no market orders) |
| Slippage tolerance | ±0.2% of last price |
| Open timing | No entries in first 15 minutes after market open |
| Close timing | No new entries in last 30 minutes before market close |
| Earnings timing | Do not open new positions within 2 days before earnings |

---

## API Configuration

```
# Alpaca
ALPACA_BASE_URL = https://paper-api.alpaca.markets   # switches to live URL when mode = LIVE
ALPACA_API_KEY  = [set as environment variable: ALPACA_API_KEY]
ALPACA_SECRET   = [set as environment variable: ALPACA_SECRET_KEY]

# Tavily (free web search — 1,000/month free)
TAVILY_API_KEY = [set as environment variable: TAVILY_API_KEY]

# Discord Bot
DISCORD_BOT_TOKEN = [set as environment variable: DISCORD_BOT_TOKEN]
DISCORD_GUILD_ID  = [set as environment variable: DISCORD_GUILD_ID]
DISCORD_USER_ID   = [set as environment variable: DISCORD_USER_ID]
```

**Never hardcode API keys. Always use environment variables.**

---

## Research Tools

| Tool | Purpose | Cost |
|------|---------|------|
| yfinance | Stock prices, fundamentals, Yahoo Finance news | Free — no API key |
| Tavily | Macro news, web search for broader context | Free (1,000/month) |

Use `bot/research.py` helpers in all routines. Call `generate_premarket_brief()` in pre-market, `get_portfolio_snapshot()` in market open/midday/close.

---

## Notification Rules

| Routine | Notify via Discord DM? |
|---------|----------------------|
| Pre-market | Only if urgent (position at risk, VIX spike, major macro event) |
| Market open | Only if trades placed or exits triggered |
| Midday | Only if stop triggered or emergency |
| Market close | Always — daily log DM |
| Weekly review | Always — full report DM |

Notifications are sent via `bot/notify.py` → `send_dm()`.
Slash commands (`/positions`, `/report`, `/status`, `/watchlist`) are available any time via Discord.

---

## Emergency Override

If the agent determines that market conditions are exceptional (e.g., circuit breakers triggered, market halted, extreme VIX spike >50), the agent should:
1. Place no new trades
2. Do not adjust existing stops (let them protect naturally)
3. Send an immediate Discord DM alert via `bot/notify.py`
4. Log the situation in research_notes.md
5. Wait for the next scheduled routine before reassessing

---

*Last updated: 2026-04-24*
