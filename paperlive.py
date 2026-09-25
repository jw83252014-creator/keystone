#!/usr/bin/env python3
"""
paperlive.py — Jev trading Kalshi's 15-minute markets live, ON PAPER.

    python3 paperlive.py                      # BTC, frozen week-1 setting, runs until Ctrl-C
    python3 paperlive.py --series all         # every coin with a Coinbase price feed
    python3 paperlive.py --decider rules      # free: the fair-value rules, no Jev credits

Every 5 seconds it reads Kalshi's public quotes and Coinbase spot, builds the
same decision-shaped facts the backtest uses, and asks the decider. Any move is
filled on paper at the NEXT quote — buy at the ask, sell at the bid, taker fee
included — so it never gets a fill it couldn't have had. When a window settles,
open paper positions settle against Kalshi's own result.

It also writes every tick and outcome in the backtest's CSV format, so each day
of paper trading becomes tomorrow's replay data — including the random-trader
null, run afterwards with jevloop.py.

Defaults are the setting frozen before week 2: agree 4, confidence 0.6, hold.

PAPER ONLY. Read-only public endpoints, no credentials, no order code.
"""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import time
from datetime import datetime
from pathlib import Path

from fairvalue import realized_vol_annual
from jevloop import (Agreement, JevCache, JevDecider, Position, RulesDecider, Tick,
                     exit_rule, facts, fee1, legal_moves)
from tiger import KALSHI_BASE, get_json, price

COINBASE = "https://api.exchange.coinbase.com"
SPOT = {"KXBTC15M": "BTC-USD", "KXETH15M": "ETH-USD", "KXSOL15M": "SOL-USD",
        "KXXRP15M": "XRP-USD", "KXDOGE15M": "DOGE-USD"}     # BNB, HYPE: no Coinbase feed yet
COST_PER_JEV_CALL = 0.0000165                               # measured in the week-1 run

SCHEMA = """
CREATE TABLE IF NOT EXISTS decision (ts REAL, ticker TEXT, secs_left REAL, move TEXT, facts TEXT);
CREATE TABLE IF NOT EXISTS fill (ts REAL, ticker TEXT, action TEXT, side TEXT, price REAL, fee REAL, pnl REAL);
CREATE TABLE IF NOT EXISTS settle (ts REAL, ticker TEXT, result TEXT, side TEXT, pnl REAL);
"""


# --- the one place that touches the network ----------------------------------

class LiveSource:
    def __init__(self):
        self._vol = {}

    def market(self, series: str) -> dict | None:
        payload, _ = get_json(f"{KALSHI_BASE}/markets?series_ticker={series}&status=open&limit=20")
        markets = sorted(payload.get("markets") or [], key=lambda m: m.get("close_time") or "")
        return markets[0] if markets else None

    def spot(self, product: str) -> float:
        payload, _ = get_json(f"{COINBASE}/products/{product}/ticker")
        return float(payload["price"])

    def sigma(self, product: str) -> float:
        stamp, value = self._vol.get(product, (0.0, None))
        if value is None or time.time() - stamp > 60:        # candles once a minute is plenty
            rows, _ = get_json(f"{COINBASE}/products/{product}/candles?granularity=60")
            closes = [float(c[4]) for c in sorted(rows[:31], key=lambda c: c[0])]
            value = realized_vol_annual(closes, 60.0)
            self._vol[product] = (time.time(), value)
        return value

    def result(self, ticker: str) -> str | None:
        payload, _ = get_json(f"{KALSHI_BASE}/markets/{ticker}")
        r = (payload.get("market") or {}).get("result")
        return r if r in ("yes", "no") else None


def snapshot(src, series: str, now: float) -> Tick | None:
    m = src.market(series)
    if not m or not m.get("close_time") or m.get("floor_strike") is None:
        return None
    end = datetime.fromisoformat(m["close_time"].replace("Z", "+00:00")).timestamp()
    bid, ask = price(m, "yes_bid"), price(m, "yes_ask")
    if end <= now or bid is None or ask is None or not (0 < bid < ask < 1):
        return None
    product = SPOT[series]
    return Tick(series, m["ticker"], now, end - now, bid, ask,
                src.spot(product), float(m["floor_strike"]), src.sigma(product))


# --- the paper book --------------------------------------------------------------

class Paper:
    def __init__(self, decider, db_path: str, out_dir: str = ".",
                 target: float = 0.0, stop: float = 0.0, min_hold: float = 0.0):
        self.decider, self.target, self.stop, self.min_hold = decider, target, stop, min_hold
        self.db = sqlite3.connect(db_path)
        self.db.executescript(SCHEMA)
        self.state: dict[str, dict] = {}        # ticker -> pos, prev, pending, close_ts
        self.out = Path(out_dir)
        self.net = self.fees = 0.0
        self.trades = 0

    def _csv(self, name: str, header: list[str], row: list) -> None:
        path = self.out / name
        new = not path.exists()
        with open(path, "a", newline="") as fh:
            w = csv.writer(fh)
            if new:
                w.writerow(header)
            w.writerow(row)

    def on_tick(self, t: Tick) -> None:
        self._csv("ticks.csv", ["series", "ticker", "ts", "secs_left", "yes_bid", "yes_ask",
                                "spot", "target", "sigma"],
                  [t.series, t.ticker, t.ts, t.secs_left, t.yes_bid, t.yes_ask,
                   t.spot, t.target, t.sigma])
        st = self.state.setdefault(t.ticker, {"pos": None, "prev": None, "pending": None,
                                              "close_ts": t.ts + t.secs_left})
        if st["pending"]:                      # fill last decision at THIS later quote
            self._fill(t, st, st.pop("pending"))
            st["pending"] = None
        f = facts(t, st["pos"], st["prev"])
        moves = legal_moves(st["pos"], f)
        forced = exit_rule(st["pos"], t, self.target, self.stop)
        move = forced or self.decider.decide(f, moves)
        if (not forced and st["pos"] is not None and move != moves[0]
                and t.ts - st["pos"].entry_ts < self.min_hold):
            move = moves[0]
        self.db.execute("INSERT INTO decision VALUES (?,?,?,?,?)",
                        (t.ts, t.ticker, t.secs_left, move, json.dumps(f, default=str)))
        if move != moves[0]:
            st["pending"] = move
        st["prev"] = t

    def _fill(self, t: Tick, st: dict, move: str) -> None:
        if move.startswith("buy") and st["pos"] is None:
            side = move[4:]
            px = t.yes_ask if side == "up" else 1.0 - t.yes_bid
            st["pos"] = Position(side, px, fee1(px), t.ts)
            self.db.execute("INSERT INTO fill VALUES (?,?,?,?,?,?,?)",
                            (t.ts, t.ticker, "buy", side, px, fee1(px), None))
            self.fees += fee1(px)
        elif move.startswith("sell") and st["pos"] is not None:
            pos = st["pos"]
            px = t.yes_bid if pos.side == "up" else 1.0 - t.yes_ask
            pnl = px - fee1(px) - pos.entry_price - pos.entry_fee
            self.db.execute("INSERT INTO fill VALUES (?,?,?,?,?,?,?)",
                            (t.ts, t.ticker, "sell", pos.side, px, fee1(px), pnl))
            self.fees += fee1(px)
            self.net += pnl
            self.trades += 1
            st["pos"] = None
        self.db.commit()

    def settle_due(self, src, now: float, grace: float = 60.0) -> None:
        for ticker in [k for k, s in self.state.items() if now > s["close_ts"] + grace]:
            res = src.result(ticker)
            if res is None:
                continue                         # not published yet; try again next pass
            pos = self.state.pop(ticker)["pos"]
            self._csv("outcomes.csv", ["ticker", "result"], [ticker, res])
            if pos is not None:
                payout = 1.0 if (res == "yes") == (pos.side == "up") else 0.0
                pnl = payout - pos.entry_price - pos.entry_fee
                self.db.execute("INSERT INTO settle VALUES (?,?,?,?,?)",
                                (now, ticker, res, pos.side, pnl))
                self.net += pnl
                self.trades += 1
            self.db.commit()

    def summary(self, jev_calls: int = 0) -> str:
        holding = [f"{k.split('-')[0]} {s['pos'].side}" for k, s in self.state.items() if s["pos"]]
        return (f"PAPER  net ${self.net:+.2f}  fees ${self.fees:.2f}  closed trades {self.trades}  "
                f"open [{', '.join(holding) or '-'}]  jev calls {jev_calls} (~${jev_calls * COST_PER_JEV_CALL:.2f})")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--series", default="KXBTC15M", help="KXBTC15M, a comma list, or 'all'")
    ap.add_argument("--decider", choices=["jev", "rules"], default="jev")
    ap.add_argument("--agree", type=int, default=4)
    ap.add_argument("--min-conf", type=float, default=0.6)
    ap.add_argument("--target-cents", type=float, default=0.0)
    ap.add_argument("--stop-cents", type=float, default=0.0)
    ap.add_argument("--min-hold", type=float, default=0.0)
    ap.add_argument("--every", type=float, default=5.0)
    ap.add_argument("--cache", default="jev_live.jsonl")
    ap.add_argument("--db", default="paper.db")
    a = ap.parse_args()

    series = list(SPOT) if a.series == "all" else [s.strip() for s in a.series.split(",")]
    series = [s for s in series if s in SPOT]
    cache = JevCache(a.cache) if a.decider == "jev" else None
    base = JevDecider(cache, a.min_conf) if cache else RulesDecider()
    decider = Agreement(base, a.agree) if a.agree > 1 else base
    book = Paper(decider, a.db, target=a.target_cents / 100, stop=a.stop_cents / 100,
                 min_hold=a.min_hold)
    src = LiveSource()
    print(f"PAPER TRADING ONLY — {', '.join(series)} — {decider.name} — every {a.every:.0f}s. Ctrl-C to stop.")
    last_summary = 0.0
    while True:
        loop_start = time.time()
        for s in series:
            try:
                t = snapshot(src, s, time.time())
                if t:
                    book.on_tick(t)
            except Exception as e:                                   # noqa: BLE001
                print(f"[{s}] {type(e).__name__}: {e}")
        try:
            book.settle_due(src, time.time())
        except Exception as e:                                       # noqa: BLE001
            print(f"[settle] {type(e).__name__}: {e}")
        if time.time() - last_summary >= 60:
            print(time.strftime("%H:%M:%S"), book.summary(cache.calls if cache else 0))
            last_summary = time.time()
        time.sleep(max(0.0, a.every - (time.time() - loop_start)))


if __name__ == "__main__":
    main()
