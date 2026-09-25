#!/usr/bin/env python3
"""
jevloop.py — Kalshi 15-minute markets as a game. Jev picks a button; code plays it on paper.

Same pattern as the Mario and voice agents: code turns the market into
decision-shaped facts, offers only the legal moves, the decider picks one,
code executes it ON PAPER, and the loop repeats every few seconds.

    python3 jevloop.py replay --csv ticks.csv --outcomes outcomes.csv --decider both
    python3 jevloop.py replay --db tiger.db --outcomes outcomes.csv --decider rules

  --decider rules   fair-value rules: enter on edge after fee, cash out when the
                    bid pays more than the model says the position is worth
  --decider random  THE NULL: trades at the same rate, picks sides at random
  --decider both    runs rules, then random at the rules' own trading rate
  --decider jev     needs call_jev() wired to TypeSafe; everything else is ready
  --memory          give every decision the similar-situations tape (see Recall)
  --agree N         act only after N identical answers in a row (trade-jev used 4)
  --min-conf P      ignore Jev answers below this confidence (trade-jev used 0.7)
  --target-cents C  cash out when the position is up C cents   (code, not model)
  --stop-cents C    cut when the position is down C cents      (code, not model)
  --min-hold S      no model-initiated exit for S seconds after entry
  --cache FILE      store every Jev answer once; later sweeps cost nothing

Tick CSV columns:  series,ticker,ts,secs_left,yes_bid,yes_ask,spot,target,sigma
Outcomes CSV:      ticker,result            (result = yes / no, from Kalshi)

No order-submission code exists here. "Execute" writes a paper fill. Live
trading stays behind the Null-Axiom gate.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
import sqlite3
import statistics
from collections import defaultdict
from dataclasses import dataclass

from fairvalue import effective_minutes, fair_prob_above, order_fee, sigma_per_minute

ENTRY_EDGE = 0.03      # probability points after fee required to enter
EXIT_EDGE = 0.01       # cash out when the bid beats the model's value by this much
LATENCY_S = 1.0        # decision -> fill delay; fills use the first tick at or after it
EVERY_S = 5.0          # decision cadence per market
MIN_CONF = 0.70        # trade-jev's registered cutoff; below it Jev's answer is ignored
MIN_WINDOWS = 200      # below this, numbers print but no verdict


@dataclass
class Tick:
    series: str
    ticker: str
    ts: float
    secs_left: float
    yes_bid: float
    yes_ask: float
    spot: float
    target: float
    sigma: float


@dataclass
class Position:
    side: str            # "up" or "down"
    entry_price: float
    entry_fee: float
    entry_ts: float


def fee1(p: float) -> float:
    return order_fee(p, 1, taker=True)


def close_key(ticker: str) -> str:
    """KXBTC15M-26AUG171115-15 -> 26AUG171115. Coins closing together share a key."""
    parts = ticker.split("-")
    return parts[1] if len(parts) > 1 else ticker


# --- loading -----------------------------------------------------------------

def load_csv(path: str) -> list[Tick]:
    with open(path, newline="") as fh:
        return [Tick(r["series"], r["ticker"], float(r["ts"]), float(r["secs_left"]),
                     float(r["yes_bid"]), float(r["yes_ask"]), float(r["spot"]),
                     float(r["target"]), float(r["sigma"])) for r in csv.DictReader(fh)]


def load_tiger_db(path: str) -> list[Tick]:
    conn = sqlite3.connect(path)
    rows = conn.execute(
        "SELECT ticker, local_ts, seconds_left, yes_bid, yes_ask, coinbase_spot, "
        "floor_strike, realized_sigma FROM tick WHERE yes_bid IS NOT NULL AND "
        "yes_ask IS NOT NULL AND coinbase_spot IS NOT NULL AND floor_strike IS NOT NULL "
        "AND realized_sigma IS NOT NULL ORDER BY ticker, local_ts").fetchall()
    return [Tick(r[0].split("-")[0], r[0], r[1], r[2] or 0.0, r[3], r[4], r[5], r[6], r[7])
            for r in rows]


def load_outcomes(path: str | None) -> dict[str, str]:
    if not path:
        return {}
    with open(path, newline="") as fh:
        return {r["ticker"]: r["result"].strip().lower() for r in csv.DictReader(fh)}


# --- the game state ------------------------------------------------------------

def facts(t: Tick, pos: Position | None, prev: Tick | None) -> dict:
    """
    Decision-shaped facts, every number computed here, never by the model.
    Inside the final minute there is no fair value: without the locked-in
    settlement prints the model would be wrong exactly when it matters.
    """
    mins = max(t.secs_left, 0.0) / 60.0
    fair = fair_prob_above(t.spot, t.target, mins, t.sigma) if mins >= 1.0 else None
    up_ask, up_bid = t.yes_ask, t.yes_bid
    dn_ask, dn_bid = 1.0 - up_bid, 1.0 - up_ask
    f = {"market": t.ticker, "series": t.series, "minutes_left": round(mins, 2), "fair_up": fair,
         "up_ask": up_ask, "up_bid": up_bid, "down_ask": dn_ask, "down_bid": dn_bid,
         "spread": round(up_ask - up_bid, 4)}
    if prev is not None:   # what happened since the last decision — Mario's "recent-control result"
        f["up_mid_change"] = round((up_ask + up_bid - prev.yes_ask - prev.yes_bid) / 2, 4)
    if fair is not None:
        wander = t.spot * sigma_per_minute(t.sigma) * math.sqrt(effective_minutes(mins))
        f["gap_dollars"] = round(t.spot - t.target, 2)
        f["gap_in_wanders"] = round((t.spot - t.target) / wander, 3)   # the gap that matters
        f["edge_buy_up"] = fair - (up_ask + fee1(up_ask))
        f["edge_buy_down"] = (1 - fair) - (dn_ask + fee1(dn_ask))
    if pos is not None:
        bid = up_bid if pos.side == "up" else dn_bid
        f["holding"] = pos.side
        f["exit_now_pnl"] = bid - fee1(bid) - pos.entry_price - pos.entry_fee
        if fair is not None:
            p_win = fair if pos.side == "up" else 1 - fair
            f["hold_minus_exit"] = p_win - (bid - fee1(bid))   # < 0: cashing out beats holding
    return f


def legal_moves(pos: Position | None, f: dict) -> list[str]:
    """First item is always the do-nothing move."""
    if pos is None:
        return ["wait"] + ([] if f.get("fair_up") is None else ["buy_up", "buy_down"])
    return ["hold", f"sell_{pos.side}"]


# --- deciders ------------------------------------------------------------------

class RulesDecider:
    name = "rules"

    def decide(self, f: dict, moves: list[str]) -> str:
        if "buy_up" in moves and f["edge_buy_up"] >= max(ENTRY_EDGE, f["edge_buy_down"]):
            return "buy_up"
        if "buy_down" in moves and f["edge_buy_down"] >= ENTRY_EDGE:
            return "buy_down"
        sell = next((m for m in moves if m.startswith("sell")), None)
        if sell and f.get("hold_minus_exit") is not None and f["hold_minus_exit"] < -EXIT_EDGE:
            return sell
        return moves[0]


class RandomDecider:
    """
    The null to beat. Acts at a fixed rate and picks sides at random. In a fairly
    priced market its expected P&L before fees is zero, so whatever it loses is
    the fee bill for trading that often. Any real decider must beat it.
    """
    name = "random"

    def __init__(self, act_rate: float, seed: int = 7):
        self.p, self.rng = act_rate, random.Random(seed)

    def decide(self, f: dict, moves: list[str]) -> str:
        active = moves[1:]
        if active and self.rng.random() < self.p:
            return self.rng.choice(active)
        return moves[0]


def call_jev(request: dict) -> dict:
    """
    TypeSafe Jev via OpenRouter's decisions route (same route the APK HUD uses).
    Pinned to typesafe/jev-1.13; the dated model id Jev reports is logged in the answer.
    Returns {"direction": "up" | "down" | "unclear", "confidence": float, ...}.
    Key comes from OPENROUTER_API_KEY in the environment. Never logged.
    """
    import os
    import urllib.request
    q = request["questions"]["direction"]
    body = {"model": "typesafe/jev-1.13", "state": request["state"],
            "questions": {"direction": {"type": "choice", "instructions": q["question"],
                                        "criteria": {o: o for o in q["options"]}}}}
    req = urllib.request.Request(
        "https://openrouter.ai/api/alpha/decisions", data=json.dumps(body).encode(),
        headers={"Authorization": "Bearer " + os.environ["OPENROUTER_API_KEY"],
                 "Content-Type": "application/json", "X-Title": "kalshi-paper-hud"},
        method="POST")
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                p = json.load(resp)
            a = p["answers"]["direction"]
            return {"direction": a["choice"], "confidence": float(a.get("confidence", 0.0)),
                    "probabilities": a.get("probabilities"), "model": p.get("model")}
        except Exception:
            if attempt == 2:
                raise


POSITION_FIELDS = ("holding", "exit_now_pnl", "hold_minus_exit")


class JevCache:
    """
    Store every Jev answer once, replay forever. The question Jev sees carries
    market facts only — never our position — so one answer stays valid under
    any cutoff, agreement, stop or target setting. One paid run; every settings
    sweep after that is free. (trade-jev's "run once, replay many," made exact
    by keeping the position out of the question.)
    """
    def __init__(self, path: str, transport=call_jev):
        self.path, self.transport, self.calls, self.store = path, transport, 0, {}
        try:
            with open(path) as fh:
                for line in fh:
                    rec = json.loads(line)
                    self.store[rec["key"]] = rec["answer"]
        except FileNotFoundError:
            pass

    def __call__(self, request: dict) -> dict:
        key = hashlib.sha1(json.dumps(request, sort_keys=True, default=str).encode()).hexdigest()
        if key not in self.store:
            self.calls += 1
            self.store[key] = self.transport(request)
            with open(self.path, "a") as fh:
                fh.write(json.dumps({"key": key, "answer": self.store[key]}) + "\n")
        return self.store[key]


class JevDecider:
    """
    Jev answers one position-free question — which way next — and code turns
    that into a legal move. Below the confidence cutoff the answer is ignored.
    """
    name = "jev"

    def __init__(self, transport=call_jev, min_conf: float = MIN_CONF):
        self.transport, self.min_conf = transport, min_conf

    @staticmethod
    def request(f: dict) -> dict:
        state = {k: v for k, v in f.items() if k not in POSITION_FIELDS}
        return {"state": state, "questions": {"direction": {
            "type": "choice", "options": ["up", "down", "unclear"],
            "question": "Which way does this market move over the next minute?"}}}

    def decide(self, f: dict, moves: list[str]) -> str:
        ans = self.transport(self.request(f))
        d, conf = ans.get("direction"), ans.get("confidence", 0.0)
        if d not in ("up", "down", "unclear"):
            raise ValueError(f"Jev returned an unknown direction: {d!r}")
        if d == "unclear" or conf < self.min_conf:
            return moves[0]
        if f"buy_{d}" in moves:
            return f"buy_{d}"
        held = f.get("holding")
        if held and d != held:
            return f"sell_{held}"               # strong call against what we hold: cash out
        return moves[0]


class Agreement:
    """
    Act only after the inner decider proposes the SAME move N times in a row on
    the same market. trade-jev found Jev flips between answers from one snapshot
    to the next; four agreeing answers (one minute) is the filter that tamed it.
    """
    def __init__(self, inner, n: int):
        self.inner, self.n = inner, max(1, n)
        self.name = f"{inner.name}+agree{self.n}"
        self.last: dict[str, tuple[str, int]] = {}

    def decide(self, f: dict, moves: list[str]) -> str:
        move = self.inner.decide(f, moves)
        prev, streak = self.last.get(f.get("market"), (None, 0))
        streak = streak + 1 if move == prev else 1
        self.last[f.get("market")] = (move, streak)
        return move if (move == moves[0] or streak >= self.n) else moves[0]


def exit_rule(pos: Position | None, t: Tick, target: float, stop: float) -> str | None:
    """Stops and targets live in code. The model never gets a vote on these."""
    if pos is None or not (target or stop):
        return None
    bid = t.yes_bid if pos.side == "up" else 1.0 - t.yes_ask
    gain = bid - pos.entry_price
    if (target and gain >= target) or (stop and gain <= -stop):
        return f"sell_{pos.side}"
    return None


class Recall:
    """
    Memory done the useful way — the 50 First Dates tape. Not "your last five
    results" (outcomes don't carry over between windows) but "what happened the
    last N times the market looked like this": same stretch of the clock, same
    gap in wander units. For each: how often Up won, and what Up cost.
    Won minus cost = how the market has mispriced this exact situation.

    Only markets that have already closed are ever added. No peeking.
    """
    TIME_EDGES = (2, 4, 7, 10, 99)    # minutes-left buckets
    GAP_STEP = 0.25                   # wander units per bucket

    def __init__(self, min_n: int = 30):
        self.min_n = min_n
        self.cells = defaultdict(lambda: [0, 0, 0.0])     # count, up wins, summed up ask

    def key(self, minutes_left: float, gap: float) -> tuple[int, int]:
        tb = next(i for i, e in enumerate(self.TIME_EDGES) if minutes_left < e)
        return tb, max(-12, min(12, math.floor(gap / self.GAP_STEP)))

    def add(self, minutes_left: float, gap: float, up_ask: float, up_won: bool) -> None:
        c = self.cells[self.key(minutes_left, gap)]
        c[0] += 1
        c[1] += int(up_won)
        c[2] += up_ask

    def lookup(self, minutes_left: float, gap: float) -> dict:
        n, w, s = self.cells.get(self.key(minutes_left, gap), (0, 0, 0.0))
        if n < self.min_n:
            return {"n": n, "note": "not enough history yet"}
        return {"n": n, "up_won": round(w / n, 3), "avg_up_ask": round(s / n, 3),
                "past_edge_up": round(w / n - s / n, 3)}


# --- replay --------------------------------------------------------------------

def replay(ticks: list[Tick], decider, outcomes: dict[str, str],
           every_s: float = EVERY_S, latency_s: float = LATENCY_S,
           recall: Recall | None = None, target: float = 0.0, stop: float = 0.0,
           min_hold: float = 0.0):
    by_ticker = defaultdict(list)
    for t in ticks:
        by_ticker[t.ticker].append(t)
    groups = defaultdict(list)                        # markets closing in the same minute
    for ticker, ts in by_ticker.items():
        ts.sort(key=lambda x: x.ts)
        groups[round((ts[0].ts + ts[0].secs_left) / 60.0)].append(ticker)
    trades, windows, decisions, actions = [], {}, 0, 0

    for g in sorted(groups):
        seen = []                                     # (ticker, minutes_left, gap, up_ask)
        for ticker in groups[g]:
            ts = by_ticker[ticker]
            pos, prev, last, net = None, None, -1e18, 0.0
            for i, t in enumerate(ts):
                if t.ts - last < every_s:
                    prev = t
                    continue
                last = t.ts
                f = facts(t, pos, prev)
                if recall is not None and "gap_in_wanders" in f:
                    f["similar_past"] = recall.lookup(f["minutes_left"], f["gap_in_wanders"])
                    seen.append((ticker, f["minutes_left"], f["gap_in_wanders"], t.yes_ask))
                moves = legal_moves(pos, f)
                forced = exit_rule(pos, t, target, stop)
                move = forced or decider.decide(f, moves)
                if (not forced and pos is not None and move != moves[0]
                        and t.ts - pos.entry_ts < min_hold):
                    move = moves[0]                   # too soon to bail on the model's say-so
                decisions += 1
                prev = t
                if move == moves[0]:
                    continue
                fill = next((x for x in ts[i:] if x.ts >= t.ts + latency_s), None)
                if fill is None:
                    continue                          # no later quote: the order never fills
                actions += 1
                if move.startswith("buy"):
                    side = move[4:]
                    price = fill.yes_ask if side == "up" else 1.0 - fill.yes_bid
                    pos = Position(side, price, fee1(price), fill.ts)
                else:
                    price = fill.yes_bid if pos.side == "up" else 1.0 - fill.yes_ask
                    pnl = price - fee1(price) - pos.entry_price - pos.entry_fee
                    trades.append(dict(ticker=ticker, kind="cash_out", side=pos.side,
                                       fees=pos.entry_fee + fee1(price), pnl=pnl))
                    net += pnl
                    pos = None
            settled = True
            if pos is not None:
                res = outcomes.get(ticker)
                if res not in ("yes", "no"):
                    settled = False                   # never guess an outcome
                else:
                    payout = 1.0 if (res == "yes") == (pos.side == "up") else 0.0
                    pnl = payout - pos.entry_price - pos.entry_fee
                    trades.append(dict(ticker=ticker, kind="held", side=pos.side,
                                       fees=pos.entry_fee, pnl=pnl))
                    net += pnl
            windows[ticker] = (net, settled)
        if recall is not None:                        # only now has every market in the group closed
            for ticker, m, gap, ask in seen:
                res = outcomes.get(ticker)
                if res in ("yes", "no"):
                    recall.add(m, gap, ask, res == "yes")
    rate = actions / decisions if decisions else 0.0
    return trades, windows, rate


def summarize(name: str, trades: list[dict], windows: dict, rounds: int = 2000) -> dict:
    ok = {k: v[0] for k, v in windows.items() if v[1]}
    clusters = defaultdict(float)                     # coins closing together = one sample
    for k, v in ok.items():
        clusters[close_key(k)] += v
    fees = sum(t["fees"] for t in trades)
    net = sum(t["pnl"] for t in trades)
    out = dict(name=name, windows=len(ok), unsettled=len(windows) - len(ok),
               trades=len(trades), cash_outs=sum(t["kind"] == "cash_out" for t in trades),
               win_rate=(sum(t["pnl"] > 0 for t in trades) / len(trades)) if trades else None,
               gross=net + fees, fees=fees, net=net, ci=None)
    vals = list(clusters.values())
    if len(vals) >= 10:
        rng = random.Random(11)
        means = sorted(statistics.fmean(rng.choice(vals) for _ in vals) for _ in range(rounds))
        out["ci"] = (means[int(.025 * rounds)], means[int(.975 * rounds)])
        out["per_close"] = statistics.fmean(vals)
    return out


def show(s: dict) -> None:
    wr = "n/a" if s["win_rate"] is None else f"{s['win_rate']:.0%}"
    print(f"[{s['name']}] windows {s['windows']} (unsettled {s['unsettled']})  trades {s['trades']} "
          f"(cash-outs {s['cash_outs']})  win rate {wr}")
    print(f"    gross ${s['gross']:+.2f}   fees ${s['fees']:.2f}   net ${s['net']:+.2f}  per 1 contract")
    if s["ci"]:
        print(f"    net per close time ${s['per_close']:+.3f}, 95% range "
              f"${s['ci'][0]:+.3f} to ${s['ci'][1]:+.3f}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("replay")
    src = r.add_mutually_exclusive_group(required=True)
    src.add_argument("--csv")
    src.add_argument("--db")
    r.add_argument("--outcomes")
    r.add_argument("--decider", choices=["rules", "random", "both", "jev"], default="both")
    r.add_argument("--act-rate", type=float, default=0.05)
    r.add_argument("--every", type=float, default=EVERY_S)
    r.add_argument("--memory", action="store_true")
    r.add_argument("--min-n", type=int, default=30)
    r.add_argument("--agree", type=int, default=1)
    r.add_argument("--min-conf", type=float, default=MIN_CONF)
    r.add_argument("--target-cents", type=float, default=0.0)
    r.add_argument("--stop-cents", type=float, default=0.0)
    r.add_argument("--min-hold", type=float, default=0.0)
    r.add_argument("--cache")
    a = ap.parse_args()

    ticks = load_csv(a.csv) if a.csv else load_tiger_db(a.db)
    outcomes = load_outcomes(a.outcomes)
    if not ticks:
        raise SystemExit("no ticks loaded — nothing to replay, and nothing will be invented")

    mem = lambda: Recall(a.min_n) if a.memory else None
    knobs = dict(target=a.target_cents / 100, stop=a.stop_cents / 100, min_hold=a.min_hold)
    wrap = lambda d: Agreement(d, a.agree) if a.agree > 1 else d
    runs = []
    if a.decider in ("rules", "both"):
        tr, w, rate = replay(ticks, wrap(RulesDecider()), outcomes, a.every, recall=mem(), **knobs)
        runs.append(summarize("rules", tr, w))
        a.act_rate = rate
    if a.decider in ("random", "both"):
        tr, w, _ = replay(ticks, RandomDecider(a.act_rate), outcomes, a.every, recall=mem(), **knobs)
        runs.append(summarize(f"random @ {a.act_rate:.1%} of decisions", tr, w))
    if a.decider == "jev":
        transport = JevCache(a.cache) if a.cache else call_jev
        tr, w, _ = replay(ticks, wrap(JevDecider(transport, a.min_conf)), outcomes, a.every,
                          recall=mem(), **knobs)
        runs.append(summarize("jev", tr, w))
    for s in runs:
        show(s)
    if any(s["windows"] < MIN_WINDOWS for s in runs):
        print(f"\nFewer than {MIN_WINDOWS} settled windows — read these as mechanics, not a verdict.")


if __name__ == "__main__":
    main()
