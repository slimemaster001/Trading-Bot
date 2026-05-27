# Guardrails & Configuration

> This file is read by the bot at startup and by Claude during periodic reviews.
> Changes here take immediate effect on the next run.
> Do not modify this file while a trade session is active.

---

## 🆕 Day Trading Mode (v2.0)

The bot now runs as an **intraday day-trading system**.  
Every position is opened after 9:45 AM ET and closed by 3:30 PM ET the same day.  
No overnight holds.

### Day Trading Limits

| Rule | Value | Notes |
|------|-------|-------|
| Position size per trade | 5% of equity | ~$5,000 at $100k |
| Max simultaneous positions | 3 | = 15% deployed max |
| Stop loss (day trade) | 2% below entry | Tighter than swing stops |
| Take profit (day trade) | 4% above entry | 2:1 risk/reward minimum |
| Entry window | 9:45 AM – 2:45 PM ET | No entries in first 15 min or last 75 min |
| EOD force-close | 3:30 PM ET | All positions closed regardless of P&L |
| Minimum position score | 6.0 / 10 | Don't trade anything with final_score < 6 |
| Scanner universe | ~200 stocks | See config/universe.json |
| Ollama model | auto-detected | Set OLLAMA_MODEL env var to override |

### Pattern Day Trader (PDT) Warning

⚠️ If trading a **live account under $25,000**, Alpaca will enforce PDT rules:  
max 3 day-trades per rolling 5 business days.  
In **PAPER** mode this does not apply.  
Switch to LIVE only after $25k equity OR limit to 3 round-trips/week.

---

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

## Starting Capital

```
STARTING_CAPITAL = $100,000   (Alpaca paper account default)
```

Use the current portfolio equity (from Alpaca `/v2/account → equity`) for all percentage calculations — not the starting capital. Starting capital is recorded here for benchmarking only.

---

## Position Limits

| Rule | Value | Dollar equivalent at $100k |
|------|-------|---------------------------|
| Max single position size | 15% of portfolio | ~$15,000 |
| Typical new position size | 5–10% of portfolio | $5,000–$10,000 |
| First trade / new strategy | 5% of portfolio | ~$5,000 |
| Max sector concentration | 35% of portfolio | ~$35,000 |
| Max open positions | 10 | — |
| Minimum cash reserve | 10% of portfolio | ~$10,000 |

**How to calculate share count:**
1. Decide position size % (e.g. 7%)
2. Multiply by current portfolio equity (e.g. $100,000 × 7% = $7,000)
3. Divide by entry price (e.g. $7,000 ÷ $180.00 = 38 shares)
4. Round down to whole shares — never round up

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
