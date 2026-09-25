# Keystone handoff — for Claude Code on the ThinkPad

From Claude (claude.ai chat) · 2026-09-24 · for Jeff's terminal agent

## What this project is

Paper-only research on Kalshi's 15-minute crypto markets. Nothing here places an order, and nothing should. Your job: get two weeks of the Moto G app's data into three CSVs, run the replay tools on them, and send the output back.

## Ground rules

1. No live orders. There is no order code in these files — keep it that way.
2. No Kalshi trading key on this machine. That is the real lock: without a signed key, Kalshi cannot accept an order. The hook below is only a tripwire.
3. No password vaults or tokens in files, commands or logs. Jev's API key lives in an environment variable.
4. Don't run with `--dangerously-skip-permissions`. Use the allowlist below.

## Files (all in this folder)

| File | What it does |
|---|---|
| `fairvalue.py` | Fair chance of Up from spot, target, time left and volatility; fees; the final-minute lock-in |
| `tapemath.py` | Touch odds, streak report, whale-impact calculator |
| `jevloop.py` | Paper game loop and replay backtest: rules, random null, Jev, filters, memory, answer cache |
| `jevroute.py` | Jev as the token-saving front door: picks the agent or tool, prunes context, logs every drop |
| `tiger.py` + `panel.html` | Live recorder and local panel, reading Kalshi's public API |
| `replay.py` | Calibration check: model versus market price |

## Task 1 — export the app's two weeks

Ask Jeff where the app keeps its logs on the phone, then `adb pull` them.

Write three files. Prices in dollars (0.58, not 58). Times in unix seconds.

**`ticks.csv`** — `series,ticker,ts,secs_left,yes_bid,yes_ask,spot,target,sigma,coinbase_spot,kraken_spot`

- `spot`: median of Coinbase and Kraken (with two venues, their average)
- `target`: the window's `floor_strike`
- `sigma`: yearly realized volatility from the last 30 one-minute spot closes — `fairvalue.realized_vol_annual(closes, 60)`

**`calls.csv`** — `ts,ticker,secs_left,call_type,side,ask_at_call,coinbase_spot,kraken_spot,target,result`

- `call_type`: `first_lock`, `coin_flip_start` or `late_lock`

**`outcomes.csv`** — `ticker,result`, from Kalshi's public settled-markets endpoint (read-only, no key):

`GET https://external-api.kalshi.com/trade-api/v2/markets?series_ticker=KXBTC15M&status=settled&limit=100`

Page with `cursor` and repeat for each 15-minute coin series. Use `close_time`, never `expiration_time` (it sits a week later). Quotes arrive as `*_dollars` strings.

Checks before anything runs:

- each window's `floor_strike` equals the previous window's `expiration_value`
- every price is between 0 and 1
- no duplicate `(ticker, ts)` rows

## Task 2 — run these and send back all output

```
python3 jevloop.py replay --csv ticks.csv --outcomes outcomes.csv --decider both
python3 jevloop.py replay --csv ticks.csv --outcomes outcomes.csv --decider both --memory
python3 -c "from tapemath import streak_report; print(streak_report(open('wl.txt').read()))"
```

`wl.txt` is the W/L string, oldest first. Also build one table from `calls.csv`, per `call_type`: count, win rate, average `ask_at_call`, win rate minus average ask, and a 95% range.

Send back the three CSVs (attached in the chat, or a Drive link) plus everything printed.

## Task 3 — only after Task 2: wire Jev

1. Implement `call_jev()` in `jevloop.py` (TypeSafe SDK or the OpenRouter route the app already uses). Pin `jev-1.13.0`. Return `{"direction": "up" | "down" | "unclear", "confidence": float}`.
2. Split the data by day: week 1 to tune, week 2 to test.
3. Week 1 only: `--decider jev --cache jev.jsonl`, then sweep `--agree`, `--min-conf`, `--target-cents`, `--stop-cents`. Cached answers make the sweep free.
4. Match the agreement window in time: at a 5-second cadence, `--agree 12` is one minute.
5. Pick one setting. Run it once on week 2. Report both weeks and how many settings were tried.

## HUD row for the app (for whoever builds the screen)

Per market, refreshed every tick:

| Coinbase − target | Kraken − target | Coinbase − Kraken | Fair (index proxy) | Kalshi Up | Gap |
|---|---|---|---|---|---|
| +$20 · 0.38 wanders | +$14 · 0.27 | $6 | 64% | 58¢ | +6 pts |

Draw fair value as a line through the round with the first call marked, so a strong yes sliding back to a coin flip is visible the moment it happens.

## Permissions — `.claude/settings.json`

```json
{
  "permissions": {
    "allow": ["Bash(adb devices)", "Bash(adb pull:*)", "Bash(python3 jevloop.py:*)",
              "Bash(python3 tapemath.py:*)", "Bash(git add:*)", "Bash(git commit:*)",
              "Edit(./STATUS.md)", "Edit(./PLAN.md)"],
    "deny":  ["Read(./.env)", "Bash(rm -rf:*)"]
  },
  "hooks": {
    "PreToolUse": [
      {"matcher": "Bash",
       "hooks": [{"type": "command", "command": "python3 ~/.claude/block_orders.py"}]}
    ]
  }
}
```

Check the syntax against `/permissions` and the current hooks docs for your version.

## Tripwire — `~/.claude/block_orders.py`

```python
#!/usr/bin/env python3
"""Blocks shell commands that look like order placement. Exit 2 = blocked."""
import json, sys
cmd = json.load(sys.stdin).get("tool_input", {}).get("command", "")
for bad in ("portfolio/orders", "place_order", "create_order"):
    if bad in cmd:
        print(f"Blocked: '{bad}'. Keystone is paper-only.", file=sys.stderr)
        sys.exit(2)
```

## Two questions for Jeff

- Where does the app store its logs on the phone?
- At what second into a window is "first observation" taken?
