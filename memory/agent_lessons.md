# Agent Lessons Learned

> This is the agent's self-improvement log.  
> Every significant mistake, insight, or behavioral correction is recorded here.  
> The agent reads this file at the start of EVERY routine to avoid repeating past errors.  
> Entries are permanent — never delete, only add.

---

## Behavioral Rules (Hard Rules)

These are non-negotiable rules that have been set from the start:

1. **Never skip the stop loss.** A 7% stop is a 7% stop. No exceptions for "it'll come back."
2. **Read all memory files before acting.** Never make a trade decision without context from trade_log, research_notes, and this file.
3. **Document before trading.** Write the thesis in trade_log.md before placing the order.
4. **Cash is a position.** When in doubt, do nothing.
5. **No FOMO entries.** If a stock has already run 20%+, it is not a new entry — it is a missed opportunity.
6. **Paper trading mode is active.** Do not attempt live order placement until mode is switched to LIVE in config/guardrails.md.

---

## Lessons Log

**2026-04-27 — Environment Variables Not Available in Scheduled Sandbox**
- What happened: Both Alpaca API (ALPACA_API_KEY / ALPACA_SECRET_KEY) and Discord bot (DISCORD_BOT_TOKEN / DISCORD_USER_ID) environment variables are absent when routines run in the Cowork/Claude scheduled task sandbox. yfinance and external finance APIs are also blocked by network proxy rules.
- What I should do differently: Always expect API calls (Alpaca, Discord) and live price fetches (yfinance, Yahoo Finance) to fail in scheduled mode. Fall back to: (1) web search for price estimates, (2) document the gap clearly in log files, (3) note Discord DM as "attempted but failed — env vars not configured." Do NOT treat this as a fatal error — complete all file updates regardless.
- Applied to: All routines (pre-market, open, midday, close, weekly). Resolution: Aaron must configure env vars in the Railway/Cowork deployment environment for live Discord/Alpaca integration.

**2026-04-27 — Memory File Staleness — Always Re-Read Before Acting**
- What happened: The initial read of trade_log.md showed an open AMAT position that had actually been cancelled by the midday routine. The stale cached version caused initial confusion.
- What I should do differently: Always re-read key memory files immediately before writing EOD updates — don't rely on the first read from the start of the routine if multiple routines may have run during the session.
- Applied to: Market close routine specifically; also relevant to weekly review.

**2026-05-14 — Missed NVDA Entry (May 4 – May 14 Gap)**
- What happened: The May 3 pre-market research identified a strong NVDA entry opportunity ($195–203, RSI ~53, 3/5 signals met) for Monday May 4 open. No routine appears to have run May 4–13. By May 14, NVDA had moved to $224–228 (+14–17% from planned entry) with RSI back at/above 70 — the entry window had closed.
- What I should do differently: When the scheduled market open routine runs after a multi-day gap (no recent log entries), immediately flag the gap and assess whether planned entries are still valid or have become FOMO chases. A planned entry at $X is only valid if the current price is still within ±5% of that range. If the stock has run >10% from the planned entry level, treat it as a missed opportunity and reset — don't rationalize entry at the new elevated price.
- Applied to: All market open routines. Specifically: always check the date of the last market open log entry vs. today. If gap > 2 days, do a catch-up sweep before evaluating entries.

**2026-05-14 — AMAT Earnings Guardrail Correctly Applied**
- What happened: AMAT earnings were confirmed for tonight (May 14 AH, 4:30 PM ET). The 2-day pre-earnings guardrail activated May 12. Market open routine correctly identified and enforced the block without needing to debate it.
- What I should do differently: Nothing — this was correct behavior. Reinforce: earnings guardrail is absolute. Even if a stock looks compelling, the 2-day window is non-negotiable.
- Applied to: All routines. Earnings guardrail check should be done BEFORE signal evaluation for every candidate.

**2026-05-15 — Watchlist Too Narrow — Simultaneous Blackouts Prevent Deployment**
- What happened: For 3+ weeks (Apr 24 – May 15), the agent held 100% cash. The entire watchlist (AMAT, NVDA, MU) was simultaneously blocked: AMAT by earnings guardrail then FOMO gap, NVDA by elevated RSI then FOMO then earnings guardrail, MU by parabolic FOMO run. With only 3 stocks on the watchlist — all in the same sector (semiconductors) — there was no fallback candidate available during any of these blackout periods. The cumulative opportunity cost is ~5.3% vs. SPY.
- What I should do differently: Always maintain a minimum of 5 active watchlist candidates across at least 2 different sectors. When the semiconductor watchlist is fully blocked, there must be at least 1-2 setups in other sectors (e.g., healthcare, financials, consumer discretionary, energy, broad market ETF) that meet 3/5 signals. Before declaring "no valid entry," verify that the watchlist has cross-sector depth. If it doesn't, add new candidates.
- Applied to: Pre-market routine and weekly review. Add a watchlist audit step: count active (non-blocked) candidates. If fewer than 2, expand the watchlist before the next open.

**2026-05-15 — Cumulative Opportunity Cost Must Be Tracked Weekly**
- What happened: Three consecutive weeks of 100% cash while SPY returned approximately +5.3% (Apr 25 → May 15). Each individual daily decision was defensible, but the cumulative underperformance grew unchecked. The agent was "locally correct" (each guardrail/FOMO rule applied appropriately) but "globally suboptimal" (never deployed capital at all during a strong bull run).
- What I should do differently: Every weekly review must calculate and front-load the cumulative underperformance figure. If cash has underperformed SPY by more than 3% over 3+ weeks while market conditions are Aggressive (S&P above 200-day MA, VIX < 30), treat this as a problem requiring action — specifically, expanding the watchlist and lowering the bar for first-entry position size to 3-5% of portfolio for new candidates. Capital deployment is part of the job; persistent inaction is itself a mistake.
- Applied to: Weekly review routine. Also applies to pre-market when stance is Aggressive but portfolio is 100% cash with no near-term entry visible.

**2026-05-15 — Planned Entry Must Be Executed or Explicitly Voided Within 2 Sessions**
- What happened: The May 3 pre-market routine documented a clear NVDA entry plan at $195-203 (RSI 53, 3/5 signals met, stop $186, target $230-240). The market open routine on May 14 ran without any May 4-13 log entries — meaning the entry was never executed OR voided. By May 14, NVDA had run to $224-228 (+14-17%) and the window was closed. The plan simply evaporated between sessions.
- What I should do differently: Any documented entry plan must be marked as EXECUTED, CANCELLED, or ELAPSED within 2 trading sessions. If a routine runs and sees a planned entry from 2+ sessions ago, it must immediately check: (a) is the price still within ±5% of the planned entry? If yes, re-evaluate; if no, mark MISSED and reset. Planned entries cannot remain open indefinitely — they expire.
- Applied to: Market open routine specifically. Check for any open planned entries from prior sessions at the top of every routine.

**2026-05-23 — An Open Entry Window Is Worthless Without a Routine to Pull the Trigger**
- What happened: AMAT has been inside the confirmed entry zone ($427–$447) for the full trading week of May 16–22 (all five sessions). The pre-market addendum from May 23 explicitly confirmed entry criteria: price ≤$447.98, RSI estimated 50–58, 3/5 signals met, no FOMO gap. Despite all conditions being met, no position was entered because no pre-market or market-open routine ran during the week. Cumulative opportunity cost vs. SPY reached -4.4%.
- What I should do differently: When a confirmed entry plan exists in research_notes.md with explicit action conditions (price range, RSI range, position size, stop level), and those conditions are met at the start of a trading day, the market-open routine must treat this as a standing limit order instruction — execute on the first available routine run within those parameters. A perfectly prepared entry plan that never fires is not discipline; it is inaction. The weekly review should also explicitly flag any planned entries that were open all week but not executed, treat that as a grade-D outcome, and escalate urgency for the following week.
- Applied to: Market open routine and weekly review. If a confirmed entry plan is more than 3 sessions old and conditions are still met, grade the inaction as a D and reduce friction for the next execution window.

**2026-05-23 — NVDA Post-Earnings RSI Confirmed Within Entry Range**
- What happened: NVDA earnings beat May 20 (revenue $81.62B, +85% YoY) triggered a sell-the-news drop to $215.33. RSI as of May 19 confirmed at 61.66–61.85 (source: GuruFocus) — squarely within the 45–70 entry threshold. The agent correctly predicted the sell-the-news pattern (documented from prior AMAT lesson). With guardrail cleared post-earnings and RSI confirmed, NVDA is now a valid entry candidate. Price $215.33 is above 200-day MA ($209.98) but below 50-day MA ($224.67) — a constructive setup.
- What I should do differently: When post-earnings RSI confirmation is obtained during research, do not wait for the next "entry window" — update the watchlist and flag the entry as actionable immediately. The pre-market routine on Tuesday May 27 must verify RSI and price, then execute if criteria hold.
- Applied to: Pre-market routine May 27. NVDA is now a validated entry alongside AMAT — both need to be acted on Tuesday (after Memorial Day).

**2026-05-23 — Healthcare Sector Rotation Is Real and Actionable This Week**
- What happened: XLV and healthcare stocks (MRK +5.64% Friday on lung cancer drug catalyst) were the top-performing sector for the week of May 19–22 (+3.3% vs SPY +0.4%). The research identified a genuine sector rotation away from rate-sensitive growth tech toward defensive healthcare. This is exactly the cross-sector diversification the strategy requires. However, MRK's RSI is at the top edge of the range (66–78 across sources) after the sharp Friday move — the entry must wait for RSI to cool to ≤70.
- What I should do differently: When a sector rotation is confirmed by price action and institutional flows (healthcare being the top performer), screen it immediately for individual entries. Don't wait until cumulative underperformance forces action. MRK should be screened Tuesday May 27 pre-market with the same rigor as semiconductor names. XLV is the safer entry if MRK RSI remains elevated.
- Applied to: Pre-market routine and watchlist building. Cross-sector entries should be pursued proactively when rotation is confirmed, not reactively after missing gains.

### Template for new lessons:
```
**[DATE] — [Lesson Title]**
- What happened: [brief description]
- What I should do differently: [specific behavioral change]
- Applied to: [which routine or decision type]
```

---

*Last updated: 2026-05-23 (Weekly Review)*
