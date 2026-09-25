#!/usr/bin/env python3
"""
Writes the tiny FAKE fixture for `python3 jevgate.py score --fake`.

Everything here is fabricated from a seeded random walk: dates in the year 2000,
made-up tickers and prices. It is not market data and shows nothing about Kalshi,
Jev or any strategy. Re-run to regenerate: python3 tests/fixtures/make_fixture.py
"""
import csv
import os
import random

HERE = os.path.dirname(os.path.abspath(__file__))
OPEN0 = 946684800            # 2000-01-01 00:00 UTC, obviously not real
WINDOWS = 14
TAPE_S = 420                 # ticks from open to open + 7 min: covers every label horizon
rng = random.Random(20000101)


def ticker(series, i):
    close = OPEN0 + (i + 1) * 900
    hh, mm = divmod((close - OPEN0) // 60, 60)
    return f"{series}-00JAN01{hh:02d}{mm:02d}-00"


ticks, calls, outcomes = [], [], []
for i in range(WINDOWS):
    open_ts = OPEN0 + i * 900
    target = 50000.0 + rng.uniform(-200, 200)
    spot = target + rng.uniform(-60, 60)
    mid = min(max(0.5 + (spot - target) / 300, 0.08), 0.92)
    drift = rng.choice([-1, 0, 0, 1]) * 0.004
    jump_at = open_ts + rng.choice([60, 75, 90]) if i % 4 == 1 else None
    kraken = i % 3 != 0                        # a third of the windows have no Kraken column
    eth_target = 3000.0 + rng.uniform(-20, 20)
    eth_spot = eth_target + rng.uniform(-6, 6)
    lock_off = 20 + (i * 7) % 35
    for k in range(0, TAPE_S // 5 + 1):
        ts = open_ts + 5 * k
        step = rng.gauss(0, 4)
        spot += step
        eth_spot += rng.gauss(0, 0.4)
        mid = min(max(mid + drift + step / 400 + rng.gauss(0, 0.004), 0.03), 0.97)
        if jump_at and ts == jump_at:
            mid = min(max(mid + (0.34 if mid < 0.5 else -0.34), 0.03), 0.97)
        half = 0.01 + (0.02 if i % 5 == 2 else 0.0)
        bid, ask = round(max(mid - half, 0.01), 2), round(min(mid + half, 0.99), 2)
        cb = spot + rng.uniform(-3, 3)
        kr = (spot + rng.uniform(-3, 3) + (14 if i % 6 == 4 else 0)) if kraken else None
        row = dict(series="KXBTC15M", ticker=ticker("KXBTC15M", i), ts=f"{ts:.3f}",
                   secs_left=f"{900 - 5 * k:.1f}", yes_bid=bid, yes_ask=ask,
                   spot=round(spot, 2), target=round(target, 2), sigma="0.4500",
                   coinbase_spot=round(cb, 2), kraken_spot="" if kr is None else round(kr, 2),
                   taker_flow_imbalance=round(rng.uniform(-1, 1), 3) if i % 2 else "")
        ticks.append(row)
        if i % 2 == 0:
            ticks.append(dict(series="KXETH15M", ticker=ticker("KXETH15M", i), ts=f"{ts + 1:.3f}",
                              secs_left=f"{899 - 5 * k:.1f}", yes_bid=0.49, yes_ask=0.51,
                              spot=round(eth_spot, 2), target=round(eth_target, 2), sigma="0.5500",
                              coinbase_spot="", kraken_spot="", taker_flow_imbalance=""))
        if 5 * k == (lock_off // 5) * 5:
            lock_ts, lock_bid, lock_ask, lock_spot = ts + 2, bid, ask, spot
    # FIRST rule, applied as the app does (frozen): YES / NO / COINFLIP
    m = (lock_bid + lock_ask) / 2
    first = "YES" if lock_spot > target and m >= 0.52 else ("NO" if lock_spot < target and m <= 0.48 else "COINFLIP")
    final = spot + sum(rng.gauss(0, 4) for _ in range((900 - TAPE_S) // 5))
    result = "yes" if final > target else "no"
    outcomes.append(dict(ticker=ticker("KXBTC15M", i), result=result))
    tk = ticker("KXBTC15M", i)
    ts_out = lock_ts * 1000 if i == 3 else lock_ts   # one row in phone milliseconds
    if first == "COINFLIP":
        calls.append(dict(ts=ts_out, ticker=tk, secs_left="", call_type="coin_flip_start", side="",
                          ask_at_call="", result=result.upper()))
        continue
    ask = lock_ask if first == "YES" else round(1 - lock_bid, 2)
    calls.append(dict(ts=ts_out, ticker=tk, secs_left="" if i == 5 else 900 - (lock_ts - open_ts),
                      call_type="first_lock", side=first, ask_at_call=ask, result=result.upper()))
    if i == 7:   # the same window re-listed as a late lock: must be ignored
        calls.append(dict(ts=lock_ts + 200, ticker=tk, secs_left=900 - (lock_ts + 200 - open_ts),
                          call_type="late_lock", side=first, ask_at_call=ask, result=result.upper()))
# one FIRST row seen too late (90 s): the timing check must drop it
calls.append(dict(ts=OPEN0 + 90, ticker=ticker("KXBTC15M", 0), secs_left=810, call_type="first_lock",
                  side="YES", ask_at_call=0.6, result=outcomes[0]["result"].upper()))

cols = ["series", "ticker", "ts", "secs_left", "yes_bid", "yes_ask", "spot", "target", "sigma",
        "coinbase_spot", "kraken_spot", "taker_flow_imbalance"]
with open(os.path.join(HERE, "ticks.csv"), "w", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=cols)
    w.writeheader()
    w.writerows(ticks)
with open(os.path.join(HERE, "calls.csv"), "w", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=["ts", "ticker", "secs_left", "call_type", "side", "ask_at_call",
                                       "coinbase_spot", "kraken_spot", "target", "result"])
    w.writeheader()
    w.writerows(calls)
with open(os.path.join(HERE, "outcomes.csv"), "w", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=["ticker", "result"])
    w.writeheader()
    w.writerows(outcomes)
print(f"fixture: {len(ticks)} ticks, {len(calls)} call rows, {len(outcomes)} outcomes (all fake)")
