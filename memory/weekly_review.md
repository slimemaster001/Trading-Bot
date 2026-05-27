# Weekly Reviews

> A cumulative record of every Friday end-of-week review.  
> Newest entry first. Used by the agent to track progress over time and spot patterns.

---

## Review Template

```
---
### Week of [DATE] — Weekly Review

**Portfolio Performance:**
- Total portfolio value: $X
- Week return: +X% / -X%
- S&P 500 this week: +X% / -X%
- Outperformance: +X% / -X%
- YTD vs S&P 500: +X% / -X%

**Positions Reviewed:**
| Ticker | Thesis Still Valid? | Action Taken | Notes |
|--------|---------------------|--------------|-------|
| | | | |

**Trades This Week:**
- Opened: [list]
- Closed: [list with results]

**What Went Well:**
- [observation]

**What Went Wrong / Could Be Improved:**
- [observation]

**Agent Grade This Week:** [A/B/C/D/F]
- Reasoning: [why]

**Strategy Adjustments:**
- [any changes to strategy or guardrails]

**Watchlist for Next Week:**
- [TICKER]: [brief thesis]
---
```

---

---
### Week of 2026-05-11 — Weekly Review (First Review)

> **Note:** This is the first weekly review. Paper trading began April 24, 2026. The review covers the full period Apr 24 – May 15 for context, with this-week detail for May 11–15.

---

**Portfolio Performance:**
- Total portfolio value: $100,000 (100% cash — no positions taken)
- This week return (May 11–15): +0.00%
- S&P 500 this week: ~+0.14% (7,398 → 7,408.50)
- SPY this week: ~+0.11% ($739.80 → $740.65 est.)
- Outperformance this week: **-0.11%** (essentially flat — cash outperformed Friday's -1.0% pullback, offset prior days' rally)
- **Cumulative (since Apr 25):** Portfolio 0.00% vs SPY +3.83% → **cumulative underperformance: -3.83%**

---

**Positions Reviewed:**

| Ticker | Thesis Still Valid? | Action Taken | Notes |
|--------|---------------------|--------------|-------|
| AMAT | ✅ Yes — strengthened by Q2 beat ($7.91B rev, $2.86 EPS, Q3 guide $8.95B) + tariff truce | Held cash; FOMO rule blocked Friday entry at $469 | Closed $436.56 — sell-the-news confirmed. Re-entry setup forming for May 18–22 at $420–$445 |
| NVDA | ✅ Yes — H200 China exports cleared, Mag-7 AI capex boom, earnings May 20 (90% beat probability) | Held cash; FOMO + earnings guardrail | $235→$227 week. Guardrail activates May 18. Evaluate post-earnings May 21+ |
| MU | ✅ Yes — AI HBM demand intact | FOMO exclusion still applies | $800+ range; parabolic run since May 2. Not an entry. |

---

**Trades This Week:**
- Opened: None
- Closed: None (no open positions since paper trading began April 24)

**Total trades since inception (Apr 24):** 0 entries, 0 exits.

---

**What Went Well:**

1. **AMAT FOMO rule at open (May 15):** AMAT opened at $469 (+5% gap post-earnings). FOMO threshold was $460.16 (3% rule). Agent correctly blocked entry. AMAT closed at $436.56 — a $32.44/share avoidance of immediate loss on a 5% position = ~$350 paper loss avoided.

2. **AMAT sell-the-news pattern correctly predicted:** The agent lesson file documented "AMAT average next-day post-earnings move = -3.63% even on beats." This is exactly what occurred. The gap-and-fade pattern played out perfectly. This validates the FOMO rule and supports the May 18–22 re-entry thesis.

3. **All guardrails correctly applied throughout the week:** NVDA earnings guardrail (May 18), AMAT FOMO block, MU exclusion. Zero violations.

4. **Cash protected portfolio on Friday May 15:** PPI +6.0% surprise + Warsh hawkish Day 1 + Iran escalation = S&P -1.24% on Friday. Cash position gave +1.0% outperformance on the day.

5. **Macro research was timely and accurate:** Correctly flagged PPI release, Warsh Day 1, Iran naval blockade extension as risks. Research notes are comprehensive and actionable.

---

**What Went Wrong / Could Be Improved:**

1. **Zero capital deployment for 3+ consecutive weeks.** Portfolio has been 100% cash since paper trading began April 24 (21+ calendar days). Cumulative underperformance vs SPY: -3.83%. Every individual decision was defensible, but the cumulative result is poor. The strategy framework requires entries, not just avoidance.

2. **NVDA planned entry ($195–$203) was never executed.** The May 3 pre-market research documented a valid NVDA entry plan with 3/5 signals met (RSI 53, above 50/200-day, Mag-7 AI capex catalyst). No routine ran May 4–13. By May 14, NVDA had run to $224–$228 (+14–17%) — the window was permanently closed. This represents an approximately $600–$800 missed gain on a 5% position.

3. **Watchlist is too narrow (3 stocks, 1 sector).** All three watchlist stocks (AMAT, NVDA, MU) were simultaneously blocked for 3+ weeks: AMAT by earnings guardrail then FOMO gap, NVDA by RSI/FOMO/guardrail, MU by parabolic run. A wider, cross-sector watchlist would have provided alternative entry opportunities during these blackout periods.

4. **Cumulative underperformance not explicitly tracked.** This review is the first time the -3.83% cumulative gap vs SPY has been formally calculated. Without this number visible in every review, the growing opportunity cost was invisible. This must be tracked weekly going forward.

---

**Agent Grade This Week: C+**
*(Viewed cumulatively — this week alone was essentially even with SPY, grade would be B)*

| Dimension | Grade | Reasoning |
|-----------|-------|-----------|
| Research quality | B+ | Thorough, accurate, well-documented. Correctly predicted AMAT sell-the-news; flagged all macro risks in real time. Deduction: watchlist breadth insufficient. |
| Trade discipline (followed rules?) | A | 100% rule adherence. FOMO, guardrails, stops all applied correctly throughout. |
| Stop loss adherence | N/A | No positions held. Cannot be evaluated. |
| Sizing decisions | N/A | No positions taken. Cannot be evaluated. |
| Overall judgment | D+ | Three weeks, zero deployment, -3.83% cumulative vs benchmark. The rules were followed but the overall outcome is poor. Selecting only 3 correlated stocks that all become simultaneously untradeable for weeks is itself a judgment failure. |

**Overall weekly grade: C+** — Discipline is excellent; strategic outcome is poor. An honest grade must reflect that the portfolio has meaningfully underperformed SPY while holding cash during a bull market.

---

**Strategy Adjustments:**

1. **NEW RULE — Minimum Watchlist Depth:** Always maintain a minimum of 5 active watchlist candidates across at least 2 different sectors. When all candidates in a sector are blocked (guardrail/FOMO/RSI), at least 1–2 non-semiconductor setups must be available. This prevents total deployment blackouts. → *Added to strategy Section 6 / agent_lessons.md.*

2. **NEW RULE — Planned Entry Expiry:** Any documented entry plan must be explicitly marked EXECUTED, CANCELLED, or ELAPSED within 2 trading sessions. Plans cannot remain open indefinitely. → *Added to agent_lessons.md.*

3. **NEW METRIC — Weekly Cumulative Tracking:** Every weekly review must report cumulative portfolio return vs. SPY from inception date. If cumulative underperformance exceeds 5%, the agent must actively expand the watchlist and lower initial entry size to 3–5% to begin deployment. → *Added to weekly review template.*

4. **WARSH ERA MARKET CONDITION:** Add to strategy framework: "Rising rate hike probability (CME FedWatch >30% for 2026) → reduce position sizes to 5% max, focus on high-RS names with earnings visibility, avoid purely rate-sensitive tech. Monitor 10-year yield: above 4.5% = additional caution flag." → *Strategy update below.*

---

**Watchlist for Next Week (May 18–22):**

| Ticker | Brief Thesis | Entry Zone | Key Condition |
|--------|-------------|------------|---------------|
| 🔥 AMAT | Post-earnings gap fill complete; sell-the-news confirmed; thesis intact; no earnings until Aug 2026 | $420–$445 (Monday open) | RSI 45–65 + S&P shows stability + no >3% gap up |
| ⏳ NVDA | AI accelerator leader; ~90% beat probability May 20; H200 China cleared | Post-earnings only (May 21+ AM) | Must evaluate Thursday May 21 morning; no entry until then |
| 🆕 AMD | #2 AI chip play; Q1 2026 $5.8B data center (+57%); ATH was $456 on May 8 | ~$415–$435 (if RSI cools) | Screen Monday May 18: verify RSI < 70, 3/5 signals, not at ATH |
| 🔍 META | AI monetization; massive capex; not a hardware play (less rate-sensitive) | TBD — research Monday | Full 5-signal screen required first |
| 🩺 XLV | Defensive healthcare ETF; inelastic demand; rate hike hedge | TBD — contingency | Activate if stance shifts to Defensive (yield >5%, VIX >30) |

**Strategy Stance: 🟡 CAUTIOUS**
- 10-year yield at 4.55% (12-month high); rate hike odds 45%; PPI +6.0% (1-year high); oil $120; Warsh hawkish.
- Upgrade conditions: AMAT stabilizes to $430–$445 + RSI 45-65 → initiate 5% position. NVDA earnings beat (May 20) without stock collapse. S&P holds above 7,300 support.

---

*Last updated: 2026-05-15 (Weekly Review)*

---

*(No previous reviews — this is the first)*

---

*Last updated: 2026-04-24*
