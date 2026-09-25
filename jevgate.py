#!/usr/bin/env python3
"""
jevgate.py — Jev schema v2: a skip-gate on FIRST calls. Paper only.

FIRST (YES if spot > target and mid >= 0.52; NO if spot < target and mid <= 0.48;
else COINFLIP) stays exactly as the app makes it. Nothing here changes, flips or
adds a call. Jev only decides which FIRST calls to TAKE and which to SKIP.

    python3 jevgate.py score --fake
    python3 jevgate.py score --calls calls.csv --ticks ticks.csv --outcomes outcomes.csv \
        --answers jevgate.jsonl [--cache-only] [gate flags] [label flags] [--report REPORT.md]

Flow per FIRST call:
  1. gate_state(call, ticks_before): a compact, position-free state built ONLY
     from ticks at or before the lock time.
  2. One Jev request with six atomic "choice" questions (QUESTIONS below),
     sent through an injectable transport wrapped in jevloop.JevCache, so each
     answer is paid for once and replayed free. Same state -> same cache key.
  3. GatePolicy (thresholds are CLI flags; no defaults tuned on data) takes or
     skips the call.
  4. `score` labels each judgment from ticks/outcomes AFTER the lock, scores each
     of the six on its own, and then scores the gate: gated vs all FIRST calls.

Output goes to stdout, and to --report (REPORT.md is gitignored). Never commit it.
No order code, no order endpoints. --fake uses a scripted stub and never touches
the network.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import os
import random
import sys
import tempfile
from collections import defaultdict
from contextlib import redirect_stdout
from dataclasses import dataclass, field

from fairvalue import effective_minutes, fair_prob_above, order_fee, sigma_per_minute
from jevloop import JevCache

WINDOW_S = 900.0                     # 15-minute markets
FIRST_MAX_SECS = 60.0                # FIRST only if seen <= 60 s after open (APK 0.2.4 rule; frozen)
HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURES = os.path.join(HERE, "tests", "fixtures")
OPTIONAL_TICK_COLS = ("coinbase_spot", "kraken_spot", "taker_flow_imbalance",
                      "book_imbalance", "event_clock_min")
REQUIRED_TICK_COLS = ("series", "ticker", "ts", "secs_left", "yes_bid", "yes_ask",
                      "spot", "target", "sigma")
REQUIRED_CALL_COLS = ("ts", "ticker", "call_type", "side", "ask_at_call")

# Six atomic questions. Order and wording are part of the cache key: change them
# and every answer is re-asked (that is a new schema version, on purpose).
QUESTIONS = {
    "leader_holds": {"type": "choice", "options": ["yes", "no", "unclear"],
                     "question": "Will the called side still lead at settlement?"},
    "jump_risk_60s": {"type": "choice", "options": ["low", "high"],
                      "question": "Will the Kalshi mid move by 30 points or more within the next 60 seconds?"},
    "regime": {"type": "choice", "options": ["trend", "chop", "spike"],
               "question": "Over the next 5 minutes, is this market trending, chopping, or spiking?"},
    "toxic_flow": {"type": "choice", "options": ["yes", "no"],
                   "question": "Is recent flow running against the called side?"},
    "venue_disagreement": {"type": "choice", "options": ["yes", "no"],
                           "question": "Do the spot venues disagree enough to matter?"},
    "priced_in": {"type": "choice", "options": ["yes", "no"],
                  "question": "Is the ask already at or above the called side's fair chance?"},
}
SCHEMA = "jevgate-v2"


# --- inputs ----------------------------------------------------------------------

@dataclass
class GTick:
    series: str
    ticker: str
    ts: float
    secs_left: float
    yes_bid: float
    yes_ask: float
    spot: float
    target: float
    sigma: float
    coinbase_spot: float | None = None
    kraken_spot: float | None = None
    taker_flow_imbalance: float | None = None
    book_imbalance: float | None = None
    event_clock_min: float | None = None

    @property
    def mid(self) -> float:
        return (self.yes_bid + self.yes_ask) / 2


@dataclass
class Call:
    ts: float                      # lock time, unix seconds
    ticker: str
    side: str                      # "up" (YES) or "down" (NO)
    ask: float                     # price paid for the called side
    secs_left: float | None = None
    result: str | None = None      # from calls.csv, used only if outcomes.csv lacks the ticker


def close_key(ticker: str) -> str:
    parts = ticker.split("-")
    return parts[1] if len(parts) > 1 else ticker


def _opt(v: str | None) -> float | None:
    v = (v or "").strip()
    return float(v) if v else None


def _norm_ts(v: float) -> float:
    return v / 1000.0 if v > 1e11 else v          # the phone export writes milliseconds


def load_ticks(path: str) -> list[GTick]:
    with open(path, newline="") as fh:
        rd = csv.DictReader(fh)
        missing = [c for c in REQUIRED_TICK_COLS if c not in (rd.fieldnames or [])]
        if missing:
            raise SystemExit(f"{path}: missing required columns {missing}. Stop and ask; nothing is invented.")
        out = []
        for i, r in enumerate(rd, start=2):
            try:
                req = [float(r[c]) for c in REQUIRED_TICK_COLS[2:]]
            except (TypeError, ValueError):
                raise SystemExit(f"{path} line {i}: a required field is blank or not a number")
            out.append(GTick(r["series"], r["ticker"], _norm_ts(req[0]), *req[1:],
                             **{c: _opt(r.get(c)) for c in OPTIONAL_TICK_COLS}))
    return out


def load_calls(path: str, max_secs_since_open: float = FIRST_MAX_SECS) -> tuple[list[Call], dict]:
    """first_lock rows only; timing-verified <= max_secs_since_open when secs_left is known."""
    counts = defaultdict(int)
    calls = []
    with open(path, newline="") as fh:
        rd = csv.DictReader(fh)
        missing = [c for c in REQUIRED_CALL_COLS if c not in (rd.fieldnames or [])]
        if missing:
            raise SystemExit(f"{path}: missing required columns {missing}. Stop and ask; nothing is invented.")
        for r in rd:
            if r["call_type"].strip() != "first_lock":
                counts["not first_lock (ignored)"] += 1
                continue
            side = {"yes": "up", "up": "up", "no": "down", "down": "down"}.get(r["side"].strip().lower())
            ts, ask, sl = _opt(r["ts"]), _opt(r["ask_at_call"]), _opt(r.get("secs_left"))
            if side is None or ts is None or ask is None:
                counts["first_lock missing side/ts/ask (excluded)"] += 1
                continue
            if sl is not None and WINDOW_S - sl > max_secs_since_open:
                counts[f"first_lock later than {max_secs_since_open:g}s after open (excluded)"] += 1
                continue
            if sl is None:
                counts["first_lock timing unknown in calls.csv (kept)"] += 1
            res = (r.get("result") or "").strip().lower() or None
            calls.append(Call(_norm_ts(ts), r["ticker"].strip(), side, ask, sl, res))
    return calls, dict(counts)


def load_outcomes(path: str) -> dict[str, str]:
    with open(path, newline="") as fh:
        return {r["ticker"]: r["result"].strip().lower() for r in csv.DictReader(fh)}


# --- state -------------------------------------------------------------------------

def _wanders(t: GTick) -> float | None:
    mins = max(t.secs_left, 0.0) / 60.0
    wander = t.spot * sigma_per_minute(t.sigma) * math.sqrt(effective_minutes(mins))
    return None if wander <= 0 else (t.spot - t.target) / wander


def _r(x: float | None, nd: int) -> float | None:
    return None if x is None else round(x, nd)


def gate_state(call: Call, ticks_before: list[GTick], max_tick_age: float = 30.0) -> dict | None:
    """
    The state Jev sees, from information available at the FIRST lock only.
    Any tick with ts > call.ts is dropped here, whatever the caller passes.
    `ticks_before` holds the call's own market, plus (optionally) other series
    closing at the same time; KXETH15M feeds eth_gap_in_wanders.

    up_mid_change: the Kalshi up-mid change from the previous tick to the lock tick.
    Optional fields are None when the tape doesn't carry them. Never 0, never guessed.
    Returns None when there is no tick of this market within max_tick_age of the lock.
    """
    past = [t for t in ticks_before if t.ts <= call.ts]
    own = sorted((t for t in past if t.ticker == call.ticker), key=lambda t: t.ts)
    if not own or call.ts - own[-1].ts > max_tick_age:
        return None
    t = own[-1]
    prev = own[-2] if len(own) > 1 else None
    close = t.ts + t.secs_left
    secs_since_open = (WINDOW_S - call.secs_left) if call.secs_left is not None \
        else call.ts - (close - WINDOW_S)
    mins_left = max(close - call.ts, 0.0) / 60.0
    fair = fair_prob_above(t.spot, t.target, max(t.secs_left, 0.0) / 60.0, t.sigma)

    cbk = None
    if t.coinbase_spot is not None and t.kraken_spot is not None:
        cbk = t.coinbase_spot - t.kraken_spot
    eth = sorted((x for x in past if x.series.upper().startswith("KXETH15M")
                  and close_key(x.ticker) == close_key(call.ticker)
                  and call.ts - x.ts <= max_tick_age), key=lambda x: x.ts)
    return {
        "side": call.side,
        "ask_paid": _r(call.ask, 4),
        "secs_since_open": _r(secs_since_open, 1),
        "minutes_left": _r(mins_left, 2),
        "gap_dollars": _r(t.spot - t.target, 2),
        "gap_in_wanders": _r(_wanders(t), 3),
        "fair_up": _r(fair, 4),
        "spread": _r(t.yes_ask - t.yes_bid, 4),
        "up_mid_change": None if prev is None else _r(t.mid - prev.mid, 4),
        "coinbase_minus_kraken": _r(cbk, 2),
        "taker_flow_imbalance": _r(t.taker_flow_imbalance, 3),
        "book_imbalance": _r(t.book_imbalance, 3),
        "eth_gap_in_wanders": _r(_wanders(eth[-1]), 3) if eth else None,
        "event_clock_min": _r(t.event_clock_min, 1),
    }


def gate_request(state: dict) -> dict:
    """Same shape as jevloop.JevDecider.request: {"state", "questions"}. Deterministic."""
    return {"schema": SCHEMA, "state": state, "questions": QUESTIONS}


# --- transports ----------------------------------------------------------------------

def _post_openrouter(body: dict) -> dict:
    import urllib.request
    req = urllib.request.Request(
        "https://openrouter.ai/api/alpha/decisions", data=json.dumps(body).encode(),
        headers={"Authorization": "Bearer " + os.environ["OPENROUTER_API_KEY"],
                 "Content-Type": "application/json", "X-Title": "kalshi-paper-hud"},
        method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def jev_multi(request: dict, post=_post_openrouter) -> dict:
    """
    Multi-question adapter (jevloop.call_jev serializes only `direction`, and it
    stays untouched because paperlive.py runs on it). Same route, model pin and
    key handling as call_jev: typesafe/jev-1.13, key from OPENROUTER_API_KEY,
    never logged. `post` is injectable; tests pass a fake and never hit the network.
    Returns {"answers": {name: {"choice", "confidence", "probabilities"}}, "model"}.
    """
    qs = request["questions"]
    body = {"model": "typesafe/jev-1.13", "state": request["state"],
            "questions": {n: {"type": "choice", "instructions": q["question"],
                              "criteria": {o: o for o in q["options"]}} for n, q in qs.items()}}
    for attempt in range(3):
        try:
            p = post(body)
            return {"answers": {n: {"choice": p["answers"][n]["choice"],
                                    "confidence": float(p["answers"][n].get("confidence", 0.0)),
                                    "probabilities": p["answers"][n].get("probabilities")}
                                for n in qs},
                    "model": p.get("model")}
        except Exception:
            if attempt == 2:
                raise


def fake_jev(request: dict) -> dict:
    """
    FAKE scripted answers for tests and `--fake`. A fixed function of the state,
    so runs are reproducible. It is not Jev and its scores mean nothing.
    """
    s = request["state"]
    up = s["side"] == "up"
    fair = s["fair_up"] if up else 1 - s["fair_up"]
    lead = "yes" if fair >= 0.6 else ("no" if fair <= 0.45 else "unclear")
    gw = s["gap_in_wanders"]
    jump = "high" if gw is None or abs(gw) < 0.2 else "low"
    mc = s["up_mid_change"] or 0.0
    regime = "trend" if abs(mc) >= 0.02 else ("spike" if s["spread"] >= 0.05 else "chop")
    toxic = "yes" if (mc if up else -mc) < 0 else "no"
    cbk = s["coinbase_minus_kraken"]
    venue = "yes" if cbk is not None and abs(cbk) > 10 else "no"
    priced = "yes" if s["ask_paid"] >= fair else "no"
    choices = dict(leader_holds=lead, jump_risk_60s=jump, regime=regime, toxic_flow=toxic,
                   venue_disagreement=venue, priced_in=priced)
    conf = round(0.55 + 0.4 * min(abs(fair - 0.5) * 2, 1.0), 3)
    return {"answers": {n: {"choice": c, "confidence": conf, "probabilities": None}
                        for n, c in choices.items()}, "model": "FAKE-stub"}


def parse_answers(ans: dict) -> dict[str, dict]:
    """
    -> {question: {"choice": str, "p": {option: prob}}}. Unknown or missing choices raise.
    Probabilities come from the answer's `probabilities` (dict by option, or a list
    in option order), renormalized. If absent: the chosen option gets
    max(confidence, 1/k) and the rest share the remainder equally.
    """
    out = {}
    answers = ans.get("answers") or {}
    for name, q in QUESTIONS.items():
        opts = q["options"]
        a = answers.get(name)
        if a is None:
            raise ValueError(f"Jev answer is missing question {name!r}")
        choice = a.get("choice")
        if choice not in opts:
            raise ValueError(f"Jev returned an unknown choice for {name}: {choice!r}")
        pr = a.get("probabilities")
        if isinstance(pr, list) and len(pr) == len(opts):
            pr = dict(zip(opts, pr))
        if isinstance(pr, dict) and sum(float(pr.get(o) or 0) for o in opts) > 0:
            tot = sum(float(pr.get(o) or 0) for o in opts)
            p = {o: float(pr.get(o) or 0) / tot for o in opts}
        else:
            c = max(float(a.get("confidence") or 0.0), 1.0 / len(opts))
            p = {o: (c if o == choice else (1 - c) / (len(opts) - 1)) for o in opts}
        out[name] = {"choice": choice, "p": p}
    return out


# --- policy ------------------------------------------------------------------------

@dataclass
class GatePolicy:
    """
    Take a FIRST call only if every enabled requirement holds. The structural
    defaults are the task's example rule (leader_holds=yes, jump_risk=low,
    priced_in=no). Every probability threshold defaults to neutral (0 or 1),
    so nothing here was tuned on data. The gate can only skip; it never flips a side.
    """
    need_leader: bool = True
    leader_min_p: float = 0.0
    need_jump_low: bool = True
    max_jump_p: float = 1.0
    need_not_priced_in: bool = True
    max_priced_in_p: float = 1.0
    need_no_toxic: bool = False
    max_toxic_p: float = 1.0
    need_no_venue: bool = False
    max_venue_p: float = 1.0
    regimes: tuple = ("trend", "chop", "spike")

    def take(self, a: dict) -> tuple[bool, str]:
        checks = [
            (self.need_leader, a["leader_holds"]["choice"] == "yes", "leader_holds != yes"),
            (True, a["leader_holds"]["p"]["yes"] >= self.leader_min_p, "P(leader yes) < min"),
            (self.need_jump_low, a["jump_risk_60s"]["choice"] == "low", "jump_risk high"),
            (True, a["jump_risk_60s"]["p"]["high"] <= self.max_jump_p, "P(jump high) > max"),
            (self.need_not_priced_in, a["priced_in"]["choice"] == "no", "priced_in yes"),
            (True, a["priced_in"]["p"]["yes"] <= self.max_priced_in_p, "P(priced_in) > max"),
            (self.need_no_toxic, a["toxic_flow"]["choice"] == "no", "toxic_flow yes"),
            (True, a["toxic_flow"]["p"]["yes"] <= self.max_toxic_p, "P(toxic) > max"),
            (self.need_no_venue, a["venue_disagreement"]["choice"] == "no", "venue_disagreement yes"),
            (True, a["venue_disagreement"]["p"]["yes"] <= self.max_venue_p, "P(venue) > max"),
            (True, a["regime"]["choice"] in self.regimes, "regime not allowed"),
        ]
        for enabled, ok, why in checks:
            if enabled and not ok:
                return False, why
        return True, "take"


# --- labels (ground truth, computed AFTER the lock) ---------------------------------------

@dataclass
class LabelCfg:
    jump_points: float = 0.30        # task definition: a >= 30-point move in the Kalshi mid
    horizon_s: float = 60.0          # jump / toxic / venue horizon
    regime_s: float = 300.0          # regime window (next 5 min)
    regime_trend_eff: float = 0.6    # |net| / range at or above this = trend
    regime_spike_range: float = 0.30 # range at or above this (and not trend) = spike
    venue_threshold: float = 10.0    # dollars; round number fixed before any data was seen
    coverage_slack_s: float = 10.0   # tape must reach horizon - slack, or the label is skipped


def _covered(path: list[GTick], lock_ts: float, horizon: float, slack: float) -> bool:
    return bool(path) and path[-1].ts >= lock_ts + horizon - slack


def label_jump(mid0: float, path: list[GTick], lock_ts: float, cfg: LabelCfg) -> bool | None:
    """max |up-mid - mid at lock| over (lock, lock + horizon] >= jump_points."""
    w = [t for t in path if lock_ts < t.ts <= lock_ts + cfg.horizon_s]
    if not _covered(w, lock_ts, cfg.horizon_s, cfg.coverage_slack_s):
        return None
    return max(abs(t.mid - mid0) for t in w) >= cfg.jump_points - 1e-9


def label_toxic(side: str, mid0: float, path: list[GTick], lock_ts: float, cfg: LabelCfg) -> bool | None:
    """The called side's mid is lower at lock + horizon than at the lock."""
    w = [t for t in path if lock_ts < t.ts <= lock_ts + cfg.horizon_s]
    if not _covered(w, lock_ts, cfg.horizon_s, cfg.coverage_slack_s):
        return None
    change = w[-1].mid - mid0
    return (change if side == "up" else -change) < 0


def label_venue(path: list[GTick], lock_ts: float, cfg: LabelCfg) -> bool | None:
    """max |coinbase - kraken| over (lock, lock + horizon] > venue_threshold. None without Kraken data."""
    w = [t for t in path if lock_ts < t.ts <= lock_ts + cfg.horizon_s
         and t.coinbase_spot is not None and t.kraken_spot is not None]
    if not w:
        return None
    return max(abs(t.coinbase_spot - t.kraken_spot) for t in w) > cfg.venue_threshold


def label_regime(mid0: float, path: list[GTick], lock_ts: float, cfg: LabelCfg) -> str | None:
    """
    Regime of the Kalshi up-mid over the next regime_s seconds (lock mid included):
        range = max(mid) - min(mid);  net = |last mid - mid at lock|
        trend  if range > 0 and net / range >= regime_trend_eff   (the path mostly went one way)
        spike  if not trend and range >= regime_spike_range       (a big move that came back)
        chop   otherwise                                           (small, directionless)
    """
    w = [t for t in path if lock_ts < t.ts <= lock_ts + cfg.regime_s]
    if not _covered(w, lock_ts, cfg.regime_s, cfg.coverage_slack_s):
        return None
    mids = [mid0] + [t.mid for t in w]
    rng = max(mids) - min(mids)
    net = abs(mids[-1] - mid0)
    if rng > 0 and net / rng >= cfg.regime_trend_eff:
        return "trend"
    if rng >= cfg.regime_spike_range - 1e-9:
        return "spike"
    return "chop"


def labels_for(call: Call, won: bool | None, ticks_before: list[GTick],
               ticks_after: list[GTick], cfg: LabelCfg) -> dict:
    own_before = [t for t in ticks_before if t.ticker == call.ticker and t.ts <= call.ts]
    mid0 = max(own_before, key=lambda t: t.ts).mid
    after = sorted((t for t in ticks_after if t.ticker == call.ticker and t.ts > call.ts),
                   key=lambda t: t.ts)
    return {
        "leader_holds": won,
        "jump_risk_60s": label_jump(mid0, after, call.ts, cfg),
        "regime": label_regime(mid0, after, call.ts, cfg),
        "toxic_flow": label_toxic(call.side, mid0, after, call.ts, cfg),
        "venue_disagreement": label_venue(after, call.ts, cfg),
        "priced_in": None if won is None else (not won),     # PROXY: the called side lost
    }


# Which option's probability forecasts each binary label.
EVENT_OPTION = {"jump_risk_60s": "high", "toxic_flow": "yes",
                "venue_disagreement": "yes", "priced_in": "yes"}


def forecast(name: str, a: dict) -> float:
    """P(event). leader_holds: P(yes) + P(unclear)/2 (an 'unclear' counts as a coin flip)."""
    p = a[name]["p"]
    if name == "leader_holds":
        return p["yes"] + 0.5 * p["unclear"]
    return p[EVENT_OPTION[name]]


# --- scoring math ------------------------------------------------------------------------

EPS = 1e-6


def brier(ps: list[float], ys: list[int]) -> float:
    return sum((p - y) ** 2 for p, y in zip(ps, ys)) / len(ps)


def log_loss(ps: list[float], ys: list[int]) -> float:
    tot = 0.0
    for p, y in zip(ps, ys):
        p = min(max(p, EPS), 1 - EPS)
        tot -= math.log(p) if y else math.log(1 - p)
    return tot / len(ps)


def lift(ps: list[float], ys: list[int], q: float = 0.2) -> float | None:
    """
    Event rate in the top probability bucket minus the bottom bucket. Buckets are by
    probability VALUE: top = p >= the (1-q) quantile, bottom = p <= the q quantile,
    so a constant forecast puts everything in both and its lift is exactly 0.
    """
    if not ps:
        return None
    s = sorted(ps)
    lo = s[min(int(q * len(s)), len(s) - 1)]
    hi = s[max(math.ceil((1 - q) * len(s)) - 1, 0)]
    top = [y for p, y in zip(ps, ys) if p >= hi]
    bot = [y for p, y in zip(ps, ys) if p <= lo]
    return sum(top) / len(top) - sum(bot) / len(bot)


def calibration(ps: list[float], ys: list[int], bins: int = 5) -> list[tuple]:
    """(lo, hi, n, mean forecast, event rate) for equal-width probability bins."""
    rows = []
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        idx = [i for i, p in enumerate(ps) if lo <= p < hi or (b == bins - 1 and p == 1.0)]
        n = len(idx)
        rows.append((lo, hi, n, sum(ps[i] for i in idx) / n if n else None,
                     sum(ys[i] for i in idx) / n if n else None))
    return rows


def score_binary(ps: list[float], ys: list[int]) -> dict:
    n = len(ps)
    if n == 0:
        return {"n": 0}
    base = sum(ys) / n
    bp = [base] * n
    return {"n": n, "base_rate": base,
            "brier": brier(ps, ys), "brier_base": brier(bp, ys),
            "log_loss": log_loss(ps, ys), "log_loss_base": log_loss(bp, ys),
            "lift": lift(ps, ys), "calibration": calibration(ps, ys)}


def score_multiclass(pdicts: list[dict], labels: list[str], classes: list[str]) -> dict:
    n = len(labels)
    if n == 0:
        return {"n": 0}
    freq = {c: sum(l == c for l in labels) / n for c in classes}

    def br(pds):
        return sum(sum((pd[c] - (l == c)) ** 2 for c in classes) for pd, l in zip(pds, labels)) / n

    def ll(pds):
        return -sum(math.log(min(max(pd[l], EPS), 1 - EPS)) for pd, l in zip(pds, labels)) / n

    return {"n": n, "freq": freq, "brier": br(pdicts), "brier_base": br([freq] * n),
            "log_loss": ll(pdicts), "log_loss_base": ll([freq] * n)}


def bootstrap_ci(vals: list[float], rounds: int = 2000, seed: int = 11) -> tuple | None:
    if len(vals) < 10:
        return None
    rng = random.Random(seed)
    means = sorted(sum(rng.choice(vals) for _ in vals) / len(vals) for _ in range(rounds))
    return means[int(0.025 * rounds)], means[int(0.975 * rounds)]


def gate_stats(rows: list[dict]) -> dict:
    """rows: {"ask", "won"}. Net per contract = won - ask - taker fee (fairvalue.order_fee)."""
    n = len(rows)
    if n == 0:
        return {"n": 0}
    nets = [(1.0 if r["won"] else 0.0) - r["ask"] - order_fee(r["ask"], 1, taker=True) for r in rows]
    return {"n": n, "win_rate": sum(r["won"] for r in rows) / n,
            "avg_ask": sum(r["ask"] for r in rows) / n,
            "net": sum(nets) / n, "ci": bootstrap_ci(nets)}


# --- run -----------------------------------------------------------------------------------

@dataclass
class Record:
    call: Call
    state: dict
    answers: dict
    take: bool
    reason: str
    won: bool | None
    labels: dict = field(default_factory=dict)


def run(calls: list[Call], ticks: list[GTick], outcomes: dict[str, str], jev, policy: GatePolicy,
        cfg: LabelCfg, max_tick_age: float = 30.0, cache_only: bool = False) -> tuple[list[Record], dict]:
    by_close = defaultdict(list)
    for t in ticks:
        by_close[close_key(t.ticker)].append(t)
    counts = defaultdict(int)
    recs = []
    for c in sorted(calls, key=lambda c: (c.ts, c.ticker)):
        near = by_close.get(close_key(c.ticker), [])
        before = [t for t in near if t.ts <= c.ts]          # the state never sees past this line
        after = [t for t in near if t.ticker == c.ticker and t.ts > c.ts]
        state = gate_state(c, before, max_tick_age)
        if state is None:
            counts["no tick at the lock (excluded)"] += 1
            continue
        if state["secs_since_open"] is not None and state["secs_since_open"] > FIRST_MAX_SECS:
            counts[f"lock later than {FIRST_MAX_SECS:g}s after open by tape (excluded)"] += 1
            continue
        req = gate_request(state)
        if cache_only and not jev.has(req):
            counts["no cached answer (excluded, --cache-only)"] += 1
            continue
        answers = parse_answers(jev(req))
        take, why = policy.take(answers)
        res = outcomes.get(c.ticker) or c.result
        won = None if res not in ("yes", "no") else ((res == "yes") == (c.side == "up"))
        if won is None:
            counts["no settlement result (kept for labels that don't need it)"] += 1
        recs.append(Record(c, state, answers, take, why, won, labels_for(c, won, before, after, cfg)))
    return recs, dict(counts)


class CountingCache(JevCache):
    """jevloop.JevCache plus a lookup that never calls the transport (for --cache-only)."""

    @staticmethod
    def key(request: dict) -> str:
        import hashlib
        return hashlib.sha1(json.dumps(request, sort_keys=True, default=str).encode()).hexdigest()

    def has(self, request: dict) -> bool:
        return self.key(request) in self.store


# --- report ------------------------------------------------------------------------------

def _f(x, fmt="{:.4f}"):
    return "n/a" if x is None else fmt.format(x)


def print_binary(name: str, s: dict, note: str = "") -> None:
    print(f"\n### {name}{note}")
    if s["n"] == 0:
        print("n = 0: nothing scored.")
        return
    print(f"n {s['n']}   base rate {s['base_rate']:.3f}")
    print(f"Brier {s['brier']:.4f} vs base {s['brier_base']:.4f}   "
          f"log loss {s['log_loss']:.4f} vs base {s['log_loss_base']:.4f}   "
          f"lift top-vs-bottom bucket {_f(s['lift'], '{:+.3f}')}")
    print("| p bin | n | mean p | event rate |\n|---|---|---|---|")
    for lo, hi, n, mp, er in s["calibration"]:
        print(f"| {lo:.1f}-{hi:.1f} | {n} | {_f(mp, '{:.3f}')} | {_f(er, '{:.3f}')} |")


def print_gate(label: str, g: dict) -> str:
    if g["n"] == 0:
        return f"| {label} | 0 | | | | |"
    ci = "n/a (n<10)" if g["ci"] is None else f"{g['ci'][0]*100:+.1f} to {g['ci'][1]*100:+.1f} pts"
    return (f"| {label} | {g['n']} | {g['win_rate']*100:.1f}% | {g['avg_ask']*100:.1f}c | "
            f"{g['net']*100:+.1f} pts | {ci} |")


def report(recs: list[Record], counts: dict, policy: GatePolicy, cfg: LabelCfg,
           fake: bool, jev_calls: int) -> None:
    print("# Jev gate on FIRST calls (schema v2)")
    if fake:
        print("\n**FAKE answers (scripted stub, tiny fixture). Mechanics only. These numbers mean nothing.**")
    print(f"\nGate settings: {policy}")
    print(f"Label settings: {cfg}")
    print(f"Calls with a state and an answer: {len(recs)}. New transport calls this run: {jev_calls}.")
    for k, v in sorted(counts.items()):
        print(f"  - {k}: {v}")

    print("\n## Each judgment on its own (all calls with an answer, gate ignored)")
    print("Base rate = the event rate in this same sample (in-sample, so it is a generous baseline).")
    for name in QUESTIONS:
        if name == "regime":
            continue
        pairs = [(forecast(name, r.answers), int(r.labels[name])) for r in recs
                 if r.labels.get(name) is not None]
        note = {"leader_holds": " (event: the called side wins; p = P(yes) + P(unclear)/2)",
                "jump_risk_60s": f" (event: max |mid change| in {cfg.horizon_s:g}s >= {cfg.jump_points:g})",
                "toxic_flow": f" (event: the called side's mid is lower after {cfg.horizon_s:g}s)",
                "venue_disagreement": f" (event: |coinbase - kraken| > ${cfg.venue_threshold:g} within "
                                      f"{cfg.horizon_s:g}s; calls without Kraken data are skipped)",
                "priced_in": " (PROXY label: the called side lost. Not a direct test of 'ask >= fair'.)"}[name]
        print_binary(name, score_binary([p for p, _ in pairs], [y for _, y in pairs]), note)

    classes = QUESTIONS["regime"]["options"]
    rr = [r for r in recs if r.labels.get("regime") is not None]
    m = score_multiclass([r.answers["regime"]["p"] for r in rr], [r.labels["regime"] for r in rr], classes)
    print(f"\n### regime (label from the next {cfg.regime_s:g}s of the Kalshi mid: trend if |net|/range >= "
          f"{cfg.regime_trend_eff:g}, else spike if range >= {cfg.regime_spike_range:g}, else chop)")
    if m["n"]:
        print(f"n {m['n']}   class rates " + ", ".join(f"{c} {m['freq'][c]:.3f}" for c in classes))
        print(f"multiclass Brier {m['brier']:.4f} vs base {m['brier_base']:.4f}   "
              f"log loss {m['log_loss']:.4f} vs base {m['log_loss_base']:.4f}")
    for c in classes:
        print_binary(f"regime = {c} (one vs rest)",
                     score_binary([r.answers["regime"]["p"][c] for r in rr],
                                  [int(r.labels["regime"] == c) for r in rr]))

    print("\n## Gate result (FIRST rule unchanged; the gate only skips)")
    settled = [r for r in recs if r.won is not None]
    rows = lambda rs: [{"ask": r.call.ask, "won": r.won} for r in rs]
    print("Net = win rate - avg ask - taker fee (fairvalue.order_fee, 1 contract). 95% range: bootstrap of the mean net.")
    print("| Group | n | Win rate | Avg ask | Win - ask - fee | 95% range |\n|---|---|---|---|---|---|")
    print(print_gate("All FIRST calls", gate_stats(rows(settled))))
    print(print_gate("Gated (taken)", gate_stats(rows([r for r in settled if r.take]))))
    print(print_gate("Skipped", gate_stats(rows([r for r in settled if not r.take]))))
    why = defaultdict(int)
    for r in recs:
        if not r.take:
            why[r.reason] += 1
    if why:
        print("Skip reasons (first failed check): " + ", ".join(f"{k} {v}" for k, v in sorted(why.items())))


# --- CLI ---------------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("score", help="score the six judgments and the gate")
    src = s.add_mutually_exclusive_group(required=True)
    src.add_argument("--answers", help="JSONL answer cache (jevloop.JevCache format); misses call Jev")
    src.add_argument("--fake", action="store_true", help="scripted stub answers, no network; "
                     "inputs default to tests/fixtures/")
    s.add_argument("--calls", help="calls.csv (first_lock rows are used)")
    s.add_argument("--ticks", help="ticks.csv")
    s.add_argument("--outcomes", help="outcomes.csv")
    s.add_argument("--cache-only", action="store_true",
                   help="with --answers: never call Jev; calls without a cached answer are excluded")
    s.add_argument("--report", help="also write the output to this file (e.g. REPORT.md, gitignored)")
    s.add_argument("--max-tick-age", type=float, default=30.0,
                   help="seconds; the lock tick must be this fresh or the call is excluded")
    B = argparse.BooleanOptionalAction
    d = GatePolicy()
    s.add_argument("--need-leader", action=B, default=d.need_leader, help="take only if leader_holds=yes")
    s.add_argument("--leader-min-p", type=float, default=d.leader_min_p, help="and P(leader_holds=yes) >= this")
    s.add_argument("--need-jump-low", action=B, default=d.need_jump_low, help="take only if jump_risk_60s=low")
    s.add_argument("--max-jump-p", type=float, default=d.max_jump_p, help="and P(jump_risk_60s=high) <= this")
    s.add_argument("--need-not-priced-in", action=B, default=d.need_not_priced_in, help="take only if priced_in=no")
    s.add_argument("--max-priced-in-p", type=float, default=d.max_priced_in_p, help="and P(priced_in=yes) <= this")
    s.add_argument("--need-no-toxic", action=B, default=d.need_no_toxic, help="take only if toxic_flow=no")
    s.add_argument("--max-toxic-p", type=float, default=d.max_toxic_p, help="and P(toxic_flow=yes) <= this")
    s.add_argument("--need-no-venue", action=B, default=d.need_no_venue, help="take only if venue_disagreement=no")
    s.add_argument("--max-venue-p", type=float, default=d.max_venue_p, help="and P(venue_disagreement=yes) <= this")
    s.add_argument("--regimes", default=",".join(d.regimes), help="comma list of regimes the gate allows")
    c = LabelCfg()
    s.add_argument("--jump-points", type=float, default=c.jump_points, help="jump label: mid move threshold")
    s.add_argument("--horizon", type=float, default=c.horizon_s, help="seconds for jump/toxic/venue labels")
    s.add_argument("--regime-secs", type=float, default=c.regime_s, help="seconds for the regime label")
    s.add_argument("--regime-trend-eff", type=float, default=c.regime_trend_eff, help="|net|/range for trend")
    s.add_argument("--regime-spike-range", type=float, default=c.regime_spike_range, help="range for spike")
    s.add_argument("--venue-threshold", type=float, default=c.venue_threshold,
                   help="dollars of |coinbase - kraken| for the venue label")
    s.add_argument("--coverage-slack", type=float, default=c.coverage_slack_s,
                   help="seconds the tape may end short of a label horizon")
    return ap


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    if a.cache_only and a.fake:
        raise SystemExit("--cache-only goes with --answers")
    paths = {k: getattr(a, k) or (os.path.join(FIXTURES, f"{k}.csv") if a.fake else None)
             for k in ("calls", "ticks", "outcomes")}
    if None in paths.values():
        raise SystemExit("--calls, --ticks and --outcomes are required without --fake")
    regimes = tuple(x.strip() for x in a.regimes.split(",") if x.strip())
    bad = set(regimes) - set(QUESTIONS["regime"]["options"])
    if bad:
        raise SystemExit(f"unknown regimes: {sorted(bad)}")
    policy = GatePolicy(a.need_leader, a.leader_min_p, a.need_jump_low, a.max_jump_p,
                        a.need_not_priced_in, a.max_priced_in_p, a.need_no_toxic, a.max_toxic_p,
                        a.need_no_venue, a.max_venue_p, regimes)
    cfg = LabelCfg(a.jump_points, a.horizon, a.regime_secs, a.regime_trend_eff,
                   a.regime_spike_range, a.venue_threshold, a.coverage_slack)

    calls, ccounts = load_calls(paths["calls"])
    ticks = load_ticks(paths["ticks"])
    outcomes = load_outcomes(paths["outcomes"])
    if not calls or not ticks:
        raise SystemExit("no FIRST calls or no ticks loaded; nothing will be invented")

    tmp = None
    if a.fake:                        # never mix fake answers into a real cache file
        tmp = tempfile.TemporaryDirectory()
        jev = CountingCache(os.path.join(tmp.name, "fake.jsonl"), transport=fake_jev)
    else:
        jev = CountingCache(a.answers, transport=jev_multi)
    try:
        recs, rcounts = run(calls, ticks, outcomes, jev, policy, cfg, a.max_tick_age, a.cache_only)
        buf = io.StringIO()
        with redirect_stdout(buf):
            report(recs, {**ccounts, **rcounts}, policy, cfg, a.fake, jev.calls)
        sys.stdout.write(buf.getvalue())
        if a.report:
            with open(a.report, "a") as fh:
                fh.write(buf.getvalue() + "\n")
    finally:
        if tmp is not None:
            tmp.cleanup()
    return 0


if __name__ == "__main__":
    sys.exit(main())
