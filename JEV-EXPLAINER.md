# Jev in Keystone: what it sees, what it answers, how we test it

Plain-language guide. Paper only. Code does the math; Jev picks from a menu; code executes on paper.

## The loop (same shape as the Mario and voice agents)
1. **Code builds the state** from the tape every 5 s per market: numbers only, all computed in Python.
2. **Jev answers typed questions** in one pass (OpenRouter `typesafe/jev-1.13`).
   It returns a choice, a probability for each option, and a confidence.
3. **Code applies policy:** the confidence cutoff, N agreeing answers in a row, take-profit/stop, fees.
   Jev never sees our position, so one cached answer is valid under every setting. Sweeps are free.

## What Jev gets today (`jevloop.facts`)
| Field | Meaning |
|---|---|
| minutes_left | time to close |
| fair_up | fair chance BTC settles above target, from spot, target, time and volatility (hidden in the final minute) |
| up_ask / up_bid / down_ask / down_bid / spread | Kalshi's book top |
| up_mid_change | how the Kalshi mid moved since the last decision |
| gap_dollars | spot minus target |
| gap_in_wanders | that gap divided by how far BTC typically wanders in the time left: the gap that matters |
| edge_buy_up / edge_buy_down | fair value minus (ask + fee) |
| similar_past (with --memory) | how often Up won, and what it cost, the last N times the market looked like this |

The question: "Which way does this market move over the next minute?", with options up / down / unclear.

## What Claude would add (next schema version, v2)
Kept compact: Jev loses accuracy as the state fills with irrelevant material.
- **Second question in the same call:** "Will the side leading now still lead at settlement?" This matches how Jeff actually bets.
- **Third question:** `jump_risk_60s` in {low, high}. The math baseline its label has to beat: spot within $15 of target, or 2-5 min left.
- **coinbase_minus_kraken** (venue disagreement), since settlement is a multi-venue index.
- **first_call** and **first_tier** (FIRST / LATE-FIRST / COINFLIP, STRONG / LEAN / EDGE), plus **secs_since_open**.
- **taker_flow_imbalance** and **book_imbalance.** Weak alone, but Jev may find interactions.
- **locked_avg_gap** in the final minute: how much of the settlement average is already fixed.
- **eth_gap_in_wanders, sol_gap_in_wanders:** the other 15-minute coins at the same moment.
- **event_clock:** minutes to the next scheduled release (8:30 ET data, 2 pm Fed, 08:00 UTC Deribit expiry).
Never put chat-room text, streaks of our own wins/losses, or anything that argues for an answer into the state.

## Backtest plan
- **Data:** desktop tape (ticks.csv, 5 s cadence) and phone calls (calls.csv). Both stay out of git.
- **Split by day:** week 1 (Sep 8-15) is for tuning, week 2 (Sep 16+) is for one test only.
- **Week-1 sweep:** agree × min_conf × exits (settings count logged in STATUS.md; results in the private REPORT, not here).
- **Locked before looking at week 2:** agree=4, min_conf=0.6, hold to settlement.
- **Live paper (paperlive.py):** the same frozen setting on live markets, next to a rules decider on the same days.
- **Pass bar:** positive net on week 2 with the 95% range above zero, and beats both rules and random on the same markets.

## Research loop (proposed, `research_loop.py`)
A Python loop that churns replays with memory, so nobody re-tests the same idea by accident.
1. Keep a **ledger** (sqlite) of every strategy tried: parameters, hash, train result, and test result if run.
2. **Propose** the next variant from a menu (schema field on/off, cutoff, agree N, exits).
   Jev or an LLM can pick from the menu; code checks it hasn't been tried.
3. **Replay on the train window only**, using cached Jev answers (free) and new calls only for new schema versions.
4. **Promote** at most one variant per round to a single held-out test. Log how many were tried (the multiple-testing count).
5. **Walk forward:** each new week becomes the next test window, and old test weeks join training.
6. **Nightly:** run on the ThinkPad under cron; write a one-page summary to agent-bridge/outbox.

## App / APK plan (what Jeff sees)
- **Done (APK 0.2.4):** three scored rows: FIRST (≤60 s), LATE-FIRST, late after COINFLIP. Each shows win %, price paid, and win minus price.
- **Next:** a signal board. Every signal is a number with its distance from neutral:
  - Coinbase-target, Kraken-target, Coinbase-Kraken, each in $ and in wanders
  - fair value vs Kalshi Up, with the gap in points
  - Tiger p, flow, book, jump-risk clock
  - Jev's latest answers with confidence and the agree count
- **Views:** switch between the bar view and one combined chart. The combined chart draws a fair-value line across the round with the FIRST call marked, so a strong YES sliding back to a coin flip shows immediately (the ghost-chart style from ChatGPT's sketch).
