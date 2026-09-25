# Task: Jev schema v2, a skip-gate on FIRST calls (for a Claude Code cloud session)

Read CLAUDE.md, PLAN.md and JEV-EXPLAINER.md first. Paper only. **Make no Jev or network calls in this task.** Everything is tested with fake answers.
Open a PR with the code and tests; put no data or result numbers in the repo.

## Why
FIRST (YES if spot > target and mid ≥ 0.52; NO if spot < target and mid ≤ 0.48; else COINFLIP) is priced about fairly on average.
Jev's job here is not to call direction. It decides **which FIRST calls to take and which to skip**.
FIRST's rule itself stays unchanged.

## Build `jevgate.py`
1. **State builder** `gate_state(call, ticks_before)`. Uses only information available at the moment of the FIRST lock:
   side, ask paid, secs_since_open, minutes_left, gap_dollars, gap_in_wanders, fair_up (via fairvalue), spread, up_mid_change,
   and these optional fields (None when missing, never invented): coinbase_minus_kraken, taker_flow_imbalance, book_imbalance, eth_gap_in_wanders, event_clock_min.
   Keep it compact (well under 1k tokens). No chat text, no win/loss streaks, nothing that argues for an answer.
2. **One request with six atomic questions** (Jev "choice" questions; same request/answer shape as `JevDecider.request` / `call_jev` in jevloop.py):
   - `leader_holds`: {yes, no, unclear}. Will the called side still lead at settlement?
   - `jump_risk_60s`: {low, high}. A move of ≥30 points in the Kalshi mid within 60 s?
   - `regime`: {trend, chop, spike}
   - `toxic_flow`: {yes, no}. Is recent flow running against the called side?
   - `venue_disagreement`: {yes, no}. Do the spot venues disagree enough to matter?
   - `priced_in`: {yes, no}. Is the ask already at or above the called side's fair chance?
3. **Transport is injectable** (default `jevloop.call_jev`), wrapped in `jevloop.JevCache` so that answers are paid once and replayed free.
   Request keys must be deterministic (the same state gives the same hash).
4. **Gate policy in code, configurable:** e.g. take only if leader_holds=yes with p ≥ X, jump_risk=low, priced_in=no.
   Every threshold is a CLI flag. There are no defaults tuned on data.

## Score each judgment on its own (`jevgate.py score`)
The ground-truth label for each question is computed in code from ticks/outcomes after the lock:
- leader_holds → the settlement result equals the called side
- jump_risk_60s → max |mid change| over the next 60 s ≥ 0.30
- toxic_flow → the called side's mid falls over the next 60 s
- venue_disagreement → |coinbase − kraken| above a flag threshold (skipped when there's no Kraken data)
- priced_in → the called side lost (a proxy; say so in the output)
- regime → a code label from the next 5 min's path (range vs net move); define it in a docstring

Per judgment, report: n, Brier and log loss vs the base-rate forecast, lift of the top vs bottom probability bucket, and a 5-bucket calibration table.
Then report the **gate result:** gated vs all FIRST calls, as count, win rate, avg ask, and win rate minus ask after taker fees (fairvalue.order_fee), with a 95% range.
Output goes to stdout / REPORT.md only (REPORT.md is gitignored).

## Inputs (formats in HANDOFF-CLAUDE-CODE.md)
- `calls.csv` (call_type=first_lock rows only, timing-verified ≤60 s after open when known)
- `ticks.csv`, `outcomes.csv`
- `--answers cache.jsonl`, or `--fake` for the test stub

## Tests (`test_jevgate.py`, no network)
- A fake transport returns scripted answers. Assert the gate takes and skips exactly the expected calls.
- The state builder never uses a tick after the lock time (a leak test).
- Missing optional fields come through as None, not 0.
- Identical states produce identical cache keys; a second run makes 0 transport calls.
- The label functions work on hand-built tick sequences, including a ≥30-point jump and no jump.
- The scoring math: a perfect fake forecaster gets Brier 0; a base-rate forecaster gets lift ≈ 0.

## Done when
`python3 -m pytest -q` passes, `python3 jevgate.py score --fake` runs end to end on a tiny fixture in `tests/fixtures/`, and the PR description lists every flag.
Terminal Claude will run it on the real data and real Jev answers.
