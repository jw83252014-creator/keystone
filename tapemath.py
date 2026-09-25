"""
tapemath.py — three calculators for the APK dashboard. Stdlib only.

  1. touch_odds      "Does this side have the power to get back to 55?"
  2. streak_report   "Is a 4-loss streak telling me anything?"  (your data decides)
  3. whale_shift     "Someone just bought $10M of BTC. What does Up go to?"

Nothing here places an order.
"""

from __future__ import annotations

import math

from fairvalue import effective_minutes, norm_cdf, sigma_per_minute


# --- 1. Overturn / touch odds ---------------------------------------------------

def touch_odds(price_now: float, level: float) -> float:
    """
    Chance this side's price touches `level` at least once before the window
    closes, IF the market is pricing fairly.

    A fair binary price is a martingale that ends at 0 or 1, so by optional
    stopping:
        level above now:  price_now / level
        level below now:  (1 - price_now) / (1 - level)

    26c -> 55c comes out 47%. That is why the dip-and-recover shape shows up
    so often: a fair market produces it about half the time on its own.
    A scalping signal only has edge if it beats this number on the ledger.
    """
    if not (0.0 < price_now < 1.0 and 0.0 < level < 1.0):
        raise ValueError("prices must be strictly between 0 and 1")
    if level >= price_now:
        return price_now / level
    return (1.0 - price_now) / (1.0 - level)


# --- 2. Streak truth-test -------------------------------------------------------

def win_rate_after(wl: str, k: int, after: str = "L") -> tuple[float | None, int]:
    """
    Win rate on the call that comes right after k results in a row of `after`.
    Returns (rate, sample_size). This IS the code for the streak intuition:
    if a 4-loss streak makes the next win more likely, this number rises
    above the overall win rate. If it doesn't, the streak carries nothing.
    """
    wl = wl.upper()
    hits = n = 0
    for i in range(k, len(wl)):
        if wl[i - k:i] == after * k:
            n += 1
            hits += wl[i] == "W"
    return (hits / n if n else None), n


def longest_run(wl: str, ch: str = "L") -> int:
    best = cur = 0
    for c in wl.upper():
        cur = cur + 1 if c == ch else 0
        best = max(best, cur)
    return best


def p_loss_run_at_least(n: int, win_rate: float, k: int) -> float:
    """Exact chance that n independent calls contain a losing run of k or more."""
    q = 1.0 - win_rate
    state = [1.0] + [0.0] * (k - 1)
    hit = 0.0
    for _ in range(n):
        new = [0.0] * k
        for c, m in enumerate(state):
            if m:
                new[0] += m * win_rate
                if c + 1 >= k:
                    hit += m * q
                else:
                    new[c + 1] += m * q
        state = new
    return hit


def runs_test_z(wl: str) -> float | None:
    """
    Wald-Wolfowitz runs test. |z| < 2: the W/L order looks like independent
    calls. z < -2: results clump into streaks (regimes) — which would make a
    losing streak a reason to size DOWN, not up.
    """
    wl = wl.upper()
    w, l = wl.count("W"), wl.count("L")
    n = w + l
    if w == 0 or l == 0 or n < 20:
        return None
    runs = 1 + sum(1 for i in range(1, len(wl)) if wl[i] != wl[i - 1])
    mu = 2 * w * l / n + 1
    var = (mu - 1) * (mu - 2) / (n - 1)
    return (runs - mu) / math.sqrt(var)


def streak_report(wl: str, max_k: int = 5) -> str:
    wl = "".join(c for c in wl.upper() if c in "WL")
    n = len(wl)
    if n == 0:
        return "no calls"
    p = wl.count("W") / n
    lines = [f"{n} calls, overall win rate {p:.1%}"]
    for k in range(1, max_k + 1):
        r, m = win_rate_after(wl, k, "L")
        lines.append(f"  after {k} loss(es) in a row: "
                     + (f"{r:.1%} win  (n={m})" if m else "no cases yet"))
    lr = longest_run(wl, "L")
    lines.append(f"longest losing run: {lr}  — chance of a run that long or longer "
                 f"in {n} independent calls: {p_loss_run_at_least(n, p, max(lr,1)):.0%}")
    z = runs_test_z(wl)
    lines.append("runs test: " + ("need 20+ calls" if z is None else
                 f"z = {z:+.2f}  ({'looks independent' if abs(z) < 2 else 'streaky' if z < 0 else 'alternating'})"))
    return "\n".join(lines)


# --- 3. Whale impact on the Kalshi probability ---------------------------------

def inv_norm(p: float) -> float:
    lo, hi = -10.0, 10.0
    for _ in range(100):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if norm_cdf(mid) < p else (lo, mid)
    return (lo + hi) / 2


def impact_dollars(notional_usd: float, daily_volume_usd: float,
                   sigma_daily: float, spot: float, y: float = 1.0) -> float:
    """
    Square-root law of market impact, confirmed on Bitcoin across more than a
    million large orders: peak move ~ Y * sigma_daily * sqrt(Q / V) * spot.
    Y is of order 1. V is daily spot volume where the order executes and
    where arbitrage spreads it. Both need calibrating from your own recorder.
    """
    return y * sigma_daily * math.sqrt(notional_usd / daily_volume_usd) * spot


def whale_shift(prob_up_now: float, minutes_left: float, notional_usd: float,
                daily_volume_usd: float, sigma_annual: float, spot: float,
                y: float = 1.0, keep: float = 1.0) -> float:
    """
    Fair P(Up) right after a market buy (+notional) or sell (-notional).

    keep = share of the impact still there at settlement. Research on Bitcoin
    finds impact from uninformed flow decays almost completely, so keep < 1
    for most whales. keep = 1 gives the peak — the most the book can jump.
    """
    price_sigma = spot * sigma_per_minute(sigma_annual) * math.sqrt(effective_minutes(minutes_left))
    offset = inv_norm(prob_up_now) * price_sigma          # where spot sits vs target now
    sigma_daily = sigma_annual / math.sqrt(365.0)
    move = math.copysign(impact_dollars(abs(notional_usd), daily_volume_usd,
                                        sigma_daily, spot, y), notional_usd) * keep
    return norm_cdf((offset + move) / price_sigma)


if __name__ == "__main__":
    print("A. The streak arithmetic at a 70% hit rate")
    q = 0.30
    print(f"   5 losses in a row, from scratch : {q**5:.2%}")
    print(f"   4 losses in a row, from scratch : {q**4:.2%}")
    print(f"   5th loss GIVEN 4 already happened: {q**5 / q**4:.0%}\n")

    print("B. Touch odds in a fair market")
    for now, lvl in ((0.26, 0.55), (0.31, 0.55), (0.48, 0.55), (0.75, 0.55)):
        print(f"   at {now*100:.0f}c, touches {lvl*100:.0f}c before close: {touch_odds(now, lvl):.0%}")

    print("\nC. Whale buy when Up = 38%  (spot $64,000, sigma 29%/yr, ASSUMED $5B/day spot volume)")
    for size in (1e6, 5e6, 10e6, 25e6, 50e6):
        row = [whale_shift(0.38, m, size, 5e9, 0.29, 64000) for m in (12, 8, 2)]
        imp = impact_dollars(size, 5e9, 0.29 / math.sqrt(365), 64000)
        print(f"   ${size/1e6:>4.0f}M buy  (~${imp:,.0f} move):  "
              f"12 min {row[0]:.0%}   8 min {row[1]:.0%}   2 min {row[2]:.0%}")
    print("\n   $10M buy at 8 min, volume assumption swapped:")
    for v in (2e9, 5e9, 10e9):
        print(f"     V = ${v/1e9:.0f}B/day -> Up {whale_shift(0.38, 8, 10e6, v, 0.29, 64000):.0%}")
