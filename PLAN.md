# PLAN.md — Keystone execution plan

Owners: **TC** terminal Claude (ThinkPad) · **KS** Keystone (GPT-6 Sol) · **CC** Claude chat · **J** Jeff

Tick a box only when its "done when" is true. One step at a time. Log every step in `STATUS.md`.

## Phase 0 — Channel and safety (TC)

- [x] 0.1 Repo holds code and plans only. Done when: `.gitignore` excludes `*.csv`, `*.jsonl`, `*.db`, `wl.txt`, `REPORT.md`, `.env`.
- [ ] 0.2 Install `.claude/settings.json` and the order tripwire from the handoff. Done when: a Bash command containing `portfolio/orders` is blocked.
- [x] 0.3 Confirm no Kalshi trading key exists on this machine. Done when: logged in `STATUS.md`.

## Phase 1 — Export the app's two weeks (TC, J)

- [x] 1.1 J says where the app stores its logs; TC pulls them with `adb pull`.
- [x] 1.2 Write `ticks.csv`, `calls.csv`, `outcomes.csv`, `wl.txt` in the handoff formats.
- [ ] 1.3 Run the three sanity checks from the handoff. Done when: all pass.
- [ ] 1.4 Put the four files in the Keystone Drive folder, or J attaches them in the chat.

## Phase 2 — Baselines (TC runs, CC reviews)

- [x] 2.1 `python3 jevloop.py replay --csv ticks.csv --outcomes outcomes.csv --decider both`, then again with `--memory`.
- [x] 2.2 `streak_report` on `wl.txt`.
- [x] 2.3 Per-bucket table from `calls.csv` (first lock, coin-flip start, late lock): count, win rate, average ask, win rate minus average ask, 95% range.
- [ ] 2.4 Everything printed goes in `REPORT.md` → Drive. Done when: CC has reviewed it. No Phase 3 before that.

## Phase 3 — Jev as the trading decider (TC, CC)

- [x] 3.1 Wire `call_jev()` in `jevloop.py`. Pin `jev-1.13.0`. Key in an environment variable.
- [x] 3.2 Week 1 only: `--decider jev --cache jev.jsonl`, then sweep `--agree` (matched to one minute of time), `--min-conf`, `--target-cents`, `--stop-cents`. Count every setting tried.
- [ ] 3.3 Freeze one setting. Run it once on week 2. Done when: `REPORT.md` shows week 1, week 2 and the settings count, all versus the random null after fees.

## Phase 4 — Jev as the token-saving front door (KS, TC)

- [ ] 4.1 Wire `call_jev()` in `jevroute.py`.
- [ ] 4.2 In Agent Bridge, before each task: `pick_tool` chooses the agent or tool, `prune` chooses which files and messages ride along.
- [ ] 4.3 Log one week: tokens in versus tokens sent, and every dropped chunk with its reason.
- [ ] 4.4 Audit with `drop_misses`. Done when: at least 30% of tokens saved with no more than 5% of drops later needed. Otherwise lower `keep_p` and repeat.

## Phase 5 — Live recorder on every coin (TC)

- [ ] 5.1 Extend `tiger.py` to all seven 15-minute series.
- [ ] 5.2 Record Coinbase and Kraken spot; store their median as the index proxy.
- [ ] 5.3 Log large spot prints (top 1% by size) with timestamps.
- [ ] 5.4 Keep Kalshi server time and local time on every row. Done when: 24 hours recorded with no gaps.

## Phase 6 — The app's screen (J, whoever builds the app)

- [ ] 6.1 The disagreement row from the handoff, per market, every tick.
- [ ] 6.2 Fair value as a live line through the round, with the first call marked.
- [ ] 6.3 Each signal on its own and combined, like the Signal Lab.
- [ ] 6.4 A tap-to-call button that logs J's call with the price at that second.

## Phase 7 — Research tests on recorder data (CC designs, TC runs)

Each test: write the card before looking, explore on some days, check once on held-out days, count every variant tried.

- [ ] H1 Magnet: the Kalshi mid moves toward spot-implied fair value
- [ ] H2 A side at 26¢ gets back to 55¢ more often than the 47% a fair market gives
- [ ] H3 Market shapes persist for days beyond what volatility explains
- [ ] H4 Big spot prints lead Kalshi at +1, +5, +30 and +60 seconds
- [ ] H5 Win and loss streaks carry information (runs test)
- [ ] H6 BTC leads the alt books
- [ ] H7 Minute-one direction is underpriced (one-minute candles)
- [ ] H8 CF Benchmarks leads Kalshi by 1–2 seconds

## Phase 8 — The gate (J, Null-Axiom)

- [ ] 8.1 A strategy beats the random null after fees on held-out days.
- [ ] 8.2 Null-Axiom reviews `REPORT.md` by date and signs off.
- [ ] 8.3 Only then: design a small executor with hard caps — per trade, per day, kill switch. Not before.

## Jeff's open items

- [x] Where the app stores its logs on the phone (answered: STATUS.md)
- [x] At what second "first observation" is taken (answered + fixed: STATUS.md)
- [ ] Confirm the maker fee on KXBTC15M in Kalshi's fee schedule
- [ ] Send CC the repo link (code and plans only)
