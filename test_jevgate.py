"""Tests for jevgate.py. No network: every transport here is a fake."""
import json
import os

import pytest

import jevgate as g

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tests", "fixtures")
OPEN = 1_000_000.0


def tick(ts, mid=0.5, spot=50010.0, target=50000.0, ticker="KXBTC15M-00JAN010015-00",
         series="KXBTC15M", half=0.01, **opt):
    return g.GTick(series, ticker, ts, OPEN + 900 - ts, mid - half, mid + half, spot, target, 0.45, **opt)


def tape(mids, start=OPEN, step=5.0, **kw):
    return [tick(start + i * step, m, **kw) for i, m in enumerate(mids)]


def call(ts=OPEN + 30, side="up", ask=0.55, ticker="KXBTC15M-00JAN010015-00"):
    return g.Call(ts, ticker, side, ask, 900 - (ts - OPEN))


def answer(**choices):
    base = dict(leader_holds="yes", jump_risk_60s="low", regime="trend", toxic_flow="no",
                venue_disagreement="no", priced_in="no")
    base.update(choices)
    return {"answers": {n: {"choice": c, "confidence": 0.8, "probabilities": None}
                        for n, c in base.items()}}


# --- gate takes / skips exactly the scripted calls --------------------------------------

def test_fake_transport_gate_takes_and_skips_expected(tmp_path):
    scripted = {0.51: answer(), 0.52: answer(leader_holds="unclear"), 0.53: answer(jump_risk_60s="high"),
                0.54: answer(priced_in="yes"), 0.55: answer(toxic_flow="yes", regime="spike")}
    seen = []

    def transport(req):
        seen.append(req)
        return scripted[req["state"]["ask_paid"]]

    ticks, calls, outcomes = [], [], {}
    for i, ask in enumerate(sorted(scripted)):
        tk = f"KXBTC15M-00JAN01{i:02d}15-00"
        ticks += [tick(OPEN + 5 * k, 0.55, ticker=tk) for k in range(0, 80)]
        calls.append(call(OPEN + 32, "up", ask, tk))
        outcomes[tk] = "yes"
    jev = g.CountingCache(str(tmp_path / "c.jsonl"), transport=transport)
    recs, _ = g.run(calls, ticks, outcomes, jev, g.GatePolicy(), g.LabelCfg())
    taken = {r.call.ask for r in recs if r.take}
    assert taken == {0.51, 0.55}               # toxic/regime are not required by default
    assert len(seen) == 5

    strict = g.GatePolicy(need_no_toxic=True, regimes=("trend", "chop"))
    recs, _ = g.run(calls, ticks, outcomes, jev, strict, g.LabelCfg())
    assert {r.call.ask for r in recs if r.take} == {0.51}
    assert len(seen) == 5                      # answers replayed from the cache

    # the gate never flips a side: every taken call keeps its FIRST side
    assert all(r.call.side == "up" for r in recs)


def test_probability_thresholds():
    a = g.parse_answers({"answers": {**answer()["answers"],
                                     "leader_holds": {"choice": "yes", "probabilities": {"yes": 0.6, "no": 0.3, "unclear": 0.1}}}})
    assert g.GatePolicy(leader_min_p=0.55).take(a)[0]
    assert not g.GatePolicy(leader_min_p=0.65).take(a)[0]
    assert not g.GatePolicy(max_jump_p=0.1).take(a)[0]   # low chosen at 0.8 -> P(high)=0.2


def test_unknown_or_missing_choice_raises():
    with pytest.raises(ValueError):
        g.parse_answers(answer(regime="sideways"))
    bad = answer()
    del bad["answers"]["priced_in"]
    with pytest.raises(ValueError):
        g.parse_answers(bad)


# --- state builder ------------------------------------------------------------------------

def test_state_never_uses_ticks_after_lock():
    c = call(OPEN + 32)
    past = tape([0.50, 0.52, 0.54, 0.55, 0.56, 0.57, 0.58])       # ts OPEN .. OPEN+30
    future = [tick(OPEN + 35 + 5 * k, 0.99, spot=99999.0, coinbase_spot=1.0, kraken_spot=500.0,
                   book_imbalance=0.9, event_clock_min=1.0) for k in range(20)]
    eth_future = [tick(OPEN + 33, 0.5, spot=4000, target=3000, series="KXETH15M",
                       ticker="KXETH15M-00JAN010015-00")]
    a = g.gate_state(c, past)
    b = g.gate_state(c, past + future + eth_future)
    assert a == b
    assert a["up_mid_change"] == pytest.approx(0.01)
    assert a["eth_gap_in_wanders"] is None


def test_missing_optional_fields_are_none_not_zero():
    s = g.gate_state(call(), tape([0.5, 0.52, 0.55, 0.56, 0.57, 0.58, 0.6]))
    for k in ("coinbase_minus_kraken", "taker_flow_imbalance", "book_imbalance",
              "eth_gap_in_wanders", "event_clock_min"):
        assert k in s and s[k] is None
    first_only = g.gate_state(call(OPEN + 2), tape([0.5]))
    assert first_only["up_mid_change"] is None


def test_optional_fields_come_through_when_present():
    t = tape([0.5, 0.55], coinbase_spot=50012.0, kraken_spot=50004.5, taker_flow_imbalance=0.25,
             book_imbalance=-0.1, event_clock_min=12.0)
    eth = [tick(OPEN + 6, 0.5, spot=3010, target=3000, series="KXETH15M", ticker="KXETH15M-00JAN010015-00")]
    s = g.gate_state(call(OPEN + 8), t + eth)
    assert s["coinbase_minus_kraken"] == 7.5
    assert s["taker_flow_imbalance"] == 0.25 and s["book_imbalance"] == -0.1 and s["event_clock_min"] == 12.0
    assert s["eth_gap_in_wanders"] is not None and s["eth_gap_in_wanders"] > 0


def test_loaders_blank_optional_is_none_and_missing_required_stops(tmp_path):
    p = tmp_path / "t.csv"
    p.write_text("series,ticker,ts,secs_left,yes_bid,yes_ask,spot,target,sigma,kraken_spot\n"
                 "KXBTC15M,X-A-1,1,899,0.4,0.5,10,9,0.4,\n")
    t = g.load_ticks(str(p))[0]
    assert t.kraken_spot is None and t.coinbase_spot is None
    p.write_text("series,ticker,ts,secs_left,yes_bid,yes_ask,spot,target\nKXBTC15M,X,1,899,0.4,0.5,10,9\n")
    with pytest.raises(SystemExit):
        g.load_ticks(str(p))


def test_state_is_compact_and_position_free():
    s = g.gate_state(call(), tape([0.5, 0.52, 0.55, 0.56, 0.57, 0.58, 0.6]))
    req = json.dumps(g.gate_request(s))
    assert len(req) < 2000                                   # far under 1k tokens
    for bad in ("holding", "pnl", "streak", "won", "result", "chat"):
        assert bad not in json.dumps(s)


# --- cache ----------------------------------------------------------------------------

def test_identical_states_same_key_and_second_run_is_free(tmp_path):
    t = tape([0.5, 0.52, 0.55, 0.56, 0.57, 0.58, 0.6])
    r1 = g.gate_request(g.gate_state(call(), t))
    r2 = g.gate_request(g.gate_state(call(), list(reversed(t))))
    assert g.CountingCache.key(r1) == g.CountingCache.key(r2)

    n = {"calls": 0}

    def transport(req):
        n["calls"] += 1
        return g.fake_jev(req)

    path = str(tmp_path / "cache.jsonl")
    ticks, _ = g.load_ticks(os.path.join(FIX, "ticks.csv")), None
    calls, _ = g.load_calls(os.path.join(FIX, "calls.csv"))
    outs = g.load_outcomes(os.path.join(FIX, "outcomes.csv"))
    first = g.CountingCache(path, transport=transport)
    g.run(calls, ticks, outs, first, g.GatePolicy(), g.LabelCfg())
    assert first.calls > 0
    second = g.CountingCache(path, transport=transport)
    before = n["calls"]
    g.run(calls, ticks, outs, second, g.GatePolicy(), g.LabelCfg(), cache_only=True)
    assert second.calls == 0 and n["calls"] == before


def test_multi_adapter_serializes_all_six_with_fake_post():
    sent = {}

    def fake_post(body):
        sent.update(body)
        return {"model": "fake", "answers": {n: {"choice": q["options"][0], "confidence": 0.7}
                                             for n, q in g.QUESTIONS.items()}}

    s = g.gate_state(call(), tape([0.5, 0.52, 0.55, 0.56, 0.57, 0.58, 0.6]))
    out = g.jev_multi(g.gate_request(s), post=fake_post)
    assert set(sent["questions"]) == set(g.QUESTIONS)
    assert sent["model"] == "typesafe/jev-1.13"
    assert set(out["answers"]) == set(g.QUESTIONS)
    g.parse_answers(out)


# --- labels ---------------------------------------------------------------------------

CFG = g.LabelCfg()


def test_jump_label_hand_built():
    lock = OPEN + 30
    no_jump = tape([0.52, 0.55, 0.49, 0.60, 0.58, 0.54, 0.57, 0.50, 0.51, 0.53, 0.56, 0.55, 0.54], start=lock + 5)
    jump = tape([0.52, 0.55, 0.83, 0.60, 0.58, 0.54, 0.57, 0.50, 0.51, 0.53, 0.56, 0.55, 0.54], start=lock + 5)
    assert g.label_jump(0.53, no_jump, lock, CFG) is False
    assert g.label_jump(0.53, jump, lock, CFG) is True           # +0.30 exactly counts
    down = tape([0.52, 0.20] + [0.5] * 11, start=lock + 5)
    assert g.label_jump(0.53, down, lock, CFG) is True
    in_window = tape([0.52] * 12, start=lock + 5)                  # up to lock + 60
    late = [tick(lock + 61, 0.99)]                                  # a jump just after 60 s
    assert g.label_jump(0.53, in_window + late, lock, CFG) is False
    assert g.label_jump(0.53, tape([0.52, 0.55], start=lock + 5), lock, CFG) is None   # tape too short


def test_toxic_label():
    lock = OPEN + 30
    falling = tape([0.52 - 0.005 * k for k in range(13)], start=lock + 5)
    assert g.label_toxic("up", 0.53, falling, lock, CFG) is True
    assert g.label_toxic("down", 0.53, falling, lock, CFG) is False


def test_venue_label():
    lock = OPEN + 30
    wide = tape([0.5] * 12, start=lock + 5, coinbase_spot=100.0, kraken_spot=85.0)
    tight = tape([0.5] * 12, start=lock + 5, coinbase_spot=100.0, kraken_spot=95.0)
    assert g.label_venue(wide, lock, CFG) is True
    assert g.label_venue(tight, lock, CFG) is False
    assert g.label_venue(tape([0.5] * 12, start=lock + 5), lock, CFG) is None   # no Kraken: skipped


def test_regime_label():
    lock = OPEN + 30
    n = 60
    trend = tape([0.5 + 0.004 * k for k in range(1, n + 1)], start=lock + 5)
    spike = tape([0.5] * 10 + [0.85] + [0.5] * (n - 11), start=lock + 5)
    chop = tape([0.5 + (0.01 if k % 2 else -0.01) for k in range(n)], start=lock + 5)
    assert g.label_regime(0.5, trend, lock, CFG) == "trend"
    assert g.label_regime(0.5, spike, lock, CFG) == "spike"
    assert g.label_regime(0.5, chop, lock, CFG) == "chop"


# --- scoring math -----------------------------------------------------------------------

def test_perfect_forecaster_brier_zero():
    ys = [1, 0, 1, 1, 0, 0, 1, 0]
    s = g.score_binary([float(y) for y in ys], ys)
    assert s["brier"] == 0.0
    assert s["log_loss"] < 1e-5
    assert s["lift"] == 1.0


def test_base_rate_forecaster_lift_zero():
    ys = [1, 0, 1, 1, 0, 0, 1, 0, 1, 1]
    base = sum(ys) / len(ys)
    s = g.score_binary([base] * len(ys), ys)
    assert s["lift"] == pytest.approx(0.0)
    assert s["brier"] == pytest.approx(s["brier_base"])
    assert s["log_loss"] == pytest.approx(s["log_loss_base"])


def test_calibration_bins_and_multiclass():
    rows = g.calibration([0.05, 0.15, 0.5, 0.95, 1.0], [0, 0, 1, 1, 1])
    assert [r[2] for r in rows] == [2, 0, 1, 0, 2]
    labels = ["trend", "chop", "spike", "trend"]
    perfect = [{c: float(c == l) for c in ("trend", "chop", "spike")} for l in labels]
    m = g.score_multiclass(perfect, labels, ["trend", "chop", "spike"])
    assert m["brier"] == 0.0 and m["brier_base"] > 0


def test_gate_stats_fee():
    st = g.gate_stats([{"ask": 0.5, "won": True}, {"ask": 0.5, "won": False}])
    assert st["win_rate"] == 0.5 and st["avg_ask"] == 0.5
    assert st["net"] == pytest.approx(-g.order_fee(0.5, 1))


# --- end to end on the fake fixture ----------------------------------------------------

def test_cli_fake_end_to_end(capsys):
    assert g.main(["score", "--fake"]) == 0
    out = capsys.readouterr().out
    assert "FAKE answers" in out
    for name in g.QUESTIONS:
        assert name in out
    assert "PROXY" in out and "Gated (taken)" in out and "All FIRST calls" in out


def test_calls_loader_filters_first_lock_and_timing():
    calls, counts = g.load_calls(os.path.join(FIX, "calls.csv"))
    assert all(c.ts < 1e11 for c in calls)                       # ms converted to seconds
    assert all(c.secs_left is None or 900 - c.secs_left <= 60 for c in calls)
    assert counts.get("not first_lock (ignored)", 0) >= 1
    assert any("later than 60" in k for k in counts)
