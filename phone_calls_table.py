#!/usr/bin/env python3
"""Phone (Moto APK) lock scoreboard from phone-audit.json, per the Keystone handoff Task 2 table.

Per call type and per "how late was the first lock", report: count, win rate, average price paid
for the called side, win rate minus price (edge before fees), and a 95% range. Also writes
calls.csv and outcomes.csv in the handoff's column format. Paper research only.
"""
import csv, json, math, re, sys, collections
from datetime import datetime, timezone, timedelta
from pathlib import Path

src = Path(sys.argv[1])
out = Path(sys.argv[2]) if len(sys.argv) > 2 else src.parent
d = json.loads(src.read_text())
locks, meta, res = d["locks"], d["lock_metadata"], d["official_results"]
EDT = timezone(timedelta(hours=-4))


def close_ts(ticker):
    m = re.match(r"KXBTC15M-(\d\d)([A-Z]{3})(\d\d)(\d\d)(\d\d)", ticker)
    yy, mon, dd, hh, mi = m.groups()
    mo = ["JAN","FEB","MAR","APR","MAY","JUN","JUL","AUG","SEP","OCT","NOV","DEC"].index(mon) + 1
    return datetime(2000 + int(yy), mo, int(dd), int(hh), int(mi), tzinfo=EDT).timestamp()


def first_seen_secs(ticker):
    note = meta.get(ticker, "")
    m = re.search(r"(\d+)m (\d+)s after open", note if isinstance(note, str) else "")
    if m:
        return int(m.group(1)) * 60 + int(m.group(2))
    at = meta.get(ticker + ".at")
    return (at / 1000 - (close_ts(ticker) - 900)) if isinstance(at, (int, float)) else None


calls = []
for t, v in locks.items():
    if t not in res:
        continue
    open_call, late_call, open_mid, late_mid = (v.split("|") + ["", "", "", ""])[:4]
    result = res[t].upper()
    close = close_ts(t)
    if open_call in ("YES", "NO"):
        ask = meta.get(t + ".ask")
        if not isinstance(ask, (int, float)):  # fall back to the called side's mid at lock
            ask = float(open_mid) if open_call == "YES" else 1 - float(open_mid)
        seen = first_seen_secs(t)
        calls.append(dict(ts=meta.get(t + ".at", ""), ticker=t, secs_left=900 - seen if seen is not None else "",
                          call_type="first_lock", side=open_call, ask_at_call=ask, result=result, seen=seen))
    elif open_call == "COINFLIP":
        calls.append(dict(ts=meta.get(t + ".at", ""), ticker=t, secs_left="", call_type="coin_flip_start",
                          side="", ask_at_call="", result=result, seen=first_seen_secs(t)))
        if late_call in ("YES", "NO"):
            ask = meta.get(t + ".late_ask")
            if not isinstance(ask, (int, float)) and late_mid:
                ask = float(late_mid) if late_call == "YES" else 1 - float(late_mid)
            la = meta.get(t + ".late_at")
            calls.append(dict(ts=la or "", ticker=t, secs_left=(close - la / 1000) if la else "",
                              call_type="late_lock", side=late_call, ask_at_call=ask, result=result, seen=None))


def row(label, cs):
    cs = [c for c in cs if c["side"] and isinstance(c["ask_at_call"], (int, float))]
    n = len(cs)
    if not n:
        return f"| {label} | 0 | | | | |"
    w = sum(c["side"] == c["result"] for c in cs) / n
    a = sum(c["ask_at_call"] for c in cs) / n
    half = 1.96 * math.sqrt(w * (1 - w) / n)
    return f"| {label} | {n} | {w*100:.1f}% | {a*100:.1f}c | {(w-a)*100:+.1f} pts | {max(0,w-half)*100:.0f}-{min(1,w+half)*100:.0f}% |"


print("# Phone lock scoreboard\n")
print(f"Source: {src.name}, exported {d.get('exported_at')}. {len(locks)} locks, {len(res)} official results.\n")
print("| Group | n | Win rate | Avg price paid | Win minus price | 95% range of win rate |")
print("|---|---|---|---|---|---|")
first = [c for c in calls if c["call_type"] == "first_lock"]
print(row("First lock (all)", first))
for lo, hi, lab in ((0, 60, "first seen in minute 1"), (60, 180, "first seen 1-3 min in"),
                    (180, 480, "first seen 3-8 min in"), (480, 901, "first seen 8+ min in")):
    print(row(f"  ↳ {lab}", [c for c in first if c["seen"] is not None and lo <= c["seen"] < hi]))
print(row("Late lock (after coin flip)", [c for c in calls if c["call_type"] == "late_lock"]))
cf = [c for c in calls if c["call_type"] == "coin_flip_start"]
print(f"| Coin-flip starts (no side) | {len(cf)} | | | | |")

# streak string for tapemath.streak_report (first locks, oldest first)
first_sorted = sorted((c for c in first if isinstance(c["ts"], (int, float))), key=lambda c: c["ts"])
wl = "".join("W" if c["side"] == c["result"] else "L" for c in first_sorted)
(out / "wl.txt").write_text(wl)
cols = ["ts", "ticker", "secs_left", "call_type", "side", "ask_at_call", "coinbase_spot", "kraken_spot", "target", "result"]
with open(out / "calls.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore"); w.writeheader(); w.writerows(calls)
with open(out / "outcomes.csv", "w", newline="") as f:
    w = csv.writer(f); w.writerow(["ticker", "result"])
    for t, r in sorted(res.items()):
        w.writerow([t, r.lower()])
print(f"\nWrote calls.csv ({len(calls)} rows), outcomes.csv ({len(res)} rows), wl.txt ({len(wl)} first-lock results).")
