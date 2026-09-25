#!/usr/bin/env python3
"""Build Keystone ticks.csv + outcomes.csv from the desktop tape, for jevloop.py replay.

ticks.csv: series,ticker,ts,secs_left,yes_bid,yes_ask,spot,target,sigma  (5s cadence per ticker)
sigma = fairvalue.realized_vol_annual over the last 30 one-minute Coinbase closes.
outcomes.csv = desktop ledger settlements, plus phone official results when given.
Paper research only.
"""
import csv, json, sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, sys.argv[3] if len(sys.argv) > 3 else ".")
from fairvalue import realized_vol_annual  # noqa: E402

paper = Path(sys.argv[1])
out = Path(sys.argv[2])
phone_audit = Path(sys.argv[4]) if len(sys.argv) > 4 else None

res = {}
for line in open(paper / "ledger.jsonl"):
    d = json.loads(line)
    if d.get("ticker", "").startswith("KXBTC15M") and d.get("actual_result") in ("yes", "no"):
        res[d["ticker"]] = d["actual_result"]
if phone_audit:
    for t, r in json.loads(phone_audit.read_text())["official_results"].items():
        res.setdefault(t, r.lower())

closes, cur_min, last_emit, rows = [], None, {}, 0
with open(out / "ticks.csv", "w", newline="") as fh:
    w = csv.writer(fh)
    w.writerow(["series", "ticker", "ts", "secs_left", "yes_bid", "yes_ask", "spot", "target", "sigma"])
    for line in open(paper / "observations.jsonl"):
        if '"KXBTC15M' not in line[:120]:
            continue
        d = json.loads(line)
        spot, ts = d.get("spot"), d.get("ts")
        if spot is None or ts is None:
            continue
        t = datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
        m = int(t // 60)
        if cur_min is None or m != cur_min:
            if cur_min is not None:
                closes.append(last_spot)
                closes = closes[-31:]
            cur_min = m
        last_spot = spot
        tk = d.get("ticker")
        b, a, k, s = d.get("yes_bid"), d.get("yes_ask"), d.get("strike"), d.get("seconds_to_close")
        if tk not in res or None in (b, a, k, s) or not (0 < b <= a < 1) or len(closes) < 10:
            continue
        if t - last_emit.get(tk, 0) < 5:
            continue
        sig = realized_vol_annual(closes, 60)
        if not sig:
            continue
        last_emit[tk] = t
        w.writerow(["KXBTC15M", tk, f"{t:.3f}", f"{s:.1f}", b, a, spot, k, f"{sig:.4f}"])
        rows += 1

with open(out / "outcomes.csv", "w", newline="") as fh:
    w = csv.writer(fh)
    w.writerow(["ticker", "result"])
    for t, r in sorted(res.items()):
        w.writerow([t, r])
print(f"ticks.csv rows={rows} tickers={len(last_emit)}; outcomes.csv rows={len(res)}")
