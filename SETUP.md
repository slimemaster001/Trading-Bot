# Setup Guide — AI Trading Bot

Complete step-by-step guide from zero to a running paper trading bot with Discord notifications and slash commands.

---

## What You're Building

```
Claude Code routines (cloud, scheduled)
  → reads memory files, does research, places paper trades
  → pushes updates to GitHub
  → sends you a Discord DM when something happens

Discord Bot (Railway, always-on, free)
  → /positions   — live P&L on all open trades
  → /report      — today's trading summary
  → /status      — quick portfolio health check
  → /watchlist   — current watchlist
```

---

## Step 1 — Get Your API Keys

### Alpaca (Paper Trading — free)
1. Sign up at [app.alpaca.markets](https://app.alpaca.markets)
2. Switch to **Paper Trading** in the top-left toggle
3. Go to **API Keys → Generate New Key**
4. Copy your **API Key** and **Secret Key** — secret is shown only once

### Tavily (Free Web Search — 1,000 searches/month free)
1. Sign up at [tavily.com](https://tavily.com)
2. Go to **Dashboard → API Keys → Create Key**
3. No credit card needed for the free tier

### Discord Bot
1. Go to [discord.com/developers/applications](https://discord.com/developers/applications)
2. Click **New Application** → name it "Trading Bot" → Create
3. Go to **Bot** (left sidebar) → click **Reset Token** → copy the token
4. Scroll down and enable:
   - ✅ **Server Members Intent**
   - ✅ **Message Content Intent**
5. Go to **OAuth2 → URL Generator**:
   - Scopes: ✅ `bot`, ✅ `applications.commands`
   - Bot Permissions: ✅ `Send Messages`, ✅ `Read Messages/View Channels`
6. Copy the generated URL → paste it in your browser → add the bot to your server

**Get your IDs** (enable Developer Mode first: Discord → Settings → Advanced → Developer Mode):
- **Server ID (GUILD_ID):** Right-click your server name → Copy Server ID
- **Your User ID (USER_ID):** Right-click your own name anywhere → Copy User ID

---

## Step 2 — Push Project to GitHub

1. Create a new **private** repo at [github.com/new](https://github.com/new) — name it `trading-bot`
2. Push this entire project folder to it:
```bash
cd "Trading BOT"
git init
git add .
git commit -m "Initial trading bot scaffold"
git remote add origin https://github.com/YOUR_USERNAME/trading-bot.git
git push -u origin main
```
3. Confirm `.env` is **not** in the repo (`.gitignore` handles this)

---

## Step 3 — Deploy Discord Bot to Railway (free)

1. Sign up at [railway.app](https://railway.app) with your GitHub account
2. Click **New Project → Deploy from GitHub repo**
3. Select your `trading-bot` repo
4. Railway auto-detects Python + the `railway.toml` config — click **Deploy**
5. Go to your project → **Variables** tab → add these 3 environment variables:

| Variable | Value |
|----------|-------|
| `DISCORD_BOT_TOKEN` | Your bot token from Step 1 |
| `DISCORD_GUILD_ID` | Your server ID from Step 1 |
| `DISCORD_USER_ID` | Your Discord user ID from Step 1 |

6. Railway will redeploy automatically — check the **Logs** tab for:
   ```
   ✅ Trading bot online as Trading Bot#XXXX | Guild: XXXXXXXXX
   ```
7. Go to Discord → type `/` in your server → you should see `/positions`, `/report`, `/status`, `/watchlist`

---

## Step 4 — Set Environment Variables in Claude Code

In your Claude Code project settings, add all of these:

| Variable | Value |
|----------|-------|
| `ALPACA_API_KEY` | Your Alpaca paper API key |
| `ALPACA_SECRET_KEY` | Your Alpaca secret key |
| `TAVILY_API_KEY` | Your Tavily API key |
| `DISCORD_BOT_TOKEN` | Your Discord bot token |
| `DISCORD_USER_ID` | Your Discord user ID |

*(Claude routines use these to call APIs and send you DMs)*

---

## Step 5 — Verify Config

Open `config/guardrails.md` and confirm:
- `TRADING_MODE = PAPER` ✅
- Position limits match your paper account size
- Loss limits look reasonable

---

## Step 6 — Schedule the Routines in Claude Code

Create 5 scheduled routines, each using the corresponding file in `routines/` as the system prompt:

| Routine | File | Time (US Central) | Days |
|---------|------|-------------------|------|
| Pre-market | `routines/pre_market.md` | 6:00 AM | Mon–Fri |
| Market Open | `routines/market_open.md` | 8:30 AM | Mon–Fri |
| Midday | `routines/midday.md` | 12:00 PM | Mon–Fri |
| Market Close | `routines/market_close.md` | 3:00 PM | Mon–Fri |
| Weekly Review | `routines/weekly_review.md` | 4:00 PM | Fri only |

---

## Step 7 — First Test Run

Before enabling the schedule:
1. **Run pre-market manually** ("Run Now" in Claude Code)
   - Check `memory/research_notes.md` was updated ✅
   - Check your Discord DMs — you should only get one if something is urgent
2. **Try a slash command** in Discord: `/status`
   - Should show mode = PAPER, 0 open positions ✅
3. **Run market open manually**
   - Confirm no real trades placed, guardrails logged ✅
4. **Run market close manually**
   - Check your Discord DMs for the daily log ✅

If all 4 checks pass, enable the schedule and let it run.

---

## Step 8 — First Week

- Check Discord each morning for pre-market alerts
- Use `/positions` and `/report` during the day to monitor
- Read `memory/weekly_review.md` every Friday evening
- After 4 weeks, decide if you're ready for live trading

---

## Switching to Live Trading (future)

**Do not do this for at least 4 weeks of paper trading.**

1. Change `TRADING_MODE = LIVE` in `config/guardrails.md`
2. Update Alpaca keys in Claude Code env vars to your **live** account keys
3. Update Railway env var `ALPACA_API_KEY` + `ALPACA_SECRET_KEY` as well
4. Start with half the normal position sizes for the first 2 weeks
5. Monitor closely

---

## Project File Map

```
Trading BOT/
├── SETUP.md                      ← you are here
├── .env.example                  ← copy to .env and fill in keys (never commit .env)
├── .gitignore
├── requirements.txt              ← Python dependencies
├── Procfile                      ← Railway process definition
├── railway.toml                  ← Railway deployment config
│
├── bot/
│   ├── bot.py                    ← Discord bot (slash commands)
│   ├── notify.py                 ← sends DMs from Claude routines
│   └── research.py               ← yfinance + Tavily research helpers
│
├── strategy/
│   └── trading_strategy.md       ← trading rules and philosophy
│
├── memory/
│   ├── trade_log.md              ← all trades, open and closed
│   ├── research_notes.md         ← rolling market research + EOD summaries
│   ├── weekly_review.md          ← Friday weekly review archive
│   └── agent_lessons.md          ← lessons and behavioral rules
│
├── routines/
│   ├── pre_market.md             ← 6:00 AM routine prompt
│   ├── market_open.md            ← 8:30 AM routine prompt
│   ├── midday.md                 ← 12:00 PM routine prompt
│   ├── market_close.md           ← 3:00 PM routine prompt
│   └── weekly_review.md          ← Friday 4:00 PM routine prompt
│
└── config/
    └── guardrails.md             ← limits, loss rules, API config
```

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| Bot not showing slash commands | Wait 1–2 min after Railway deploy, then type `/` in Discord |
| Railway deploy fails | Check Logs tab — usually a missing env variable |
| Alpaca auth error | Double-check you're using PAPER keys with the paper endpoint |
| Tavily returns no results | Verify API key is set correctly; check remaining free quota at tavily.com |
| DMs not arriving | Make sure `DISCORD_USER_ID` is your user ID, not the bot's ID |
| Agent making no trades | Often correct — check `memory/research_notes.md` for its reasoning |

---

*Last updated: 2026-04-25*
