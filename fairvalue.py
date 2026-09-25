"""
fairvalue.py — random-walk fair value for Kalshi 15-minute crypto binaries.

This is the method Tiger described, written down so it can be tested:
    driftless random walk + realized vol scaled by sqrt(time remaining)
    -> probability distribution around the target
    -> compare to the market's price
    -> trade only when the gap clears the fee

Stdlib only. No network. Nothing here places an order.

TWO THINGS THIS MODEL GETS RIGHT THAT A NAIVE VERSION DOES NOT
-------------------------------------------------------------
1. Settlement is a 60-second AVERAGE of the CF Benchmarks index, not a point
   read at expiry. The average of a random walk over its final minute has
   LOWER variance than its endpoint. Ignoring this overstates the chance of
   crossing the target. Corrected via effective time below.

2. The fee is charged on the way in AND on the way out. A scalp pays it twice.
   Any "edge" smaller than the round-trip fee is a loss wearing a nice hat.

THE THING TO BE HONEST ABOUT
----------------------------
Output is almost entirely determined by the sigma you feed it. Get vol wrong
by 20% and fair value moves 10+ points, which is larger than any edge you are
hunting. Run sigma_sensitivity() before believing any single number.
That is why Tiger waits and sanity-checks volume instead of firing early.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

MINUTES_PER_YEAR = 525_600.0

# Kalshi fee constants. KXBTC15M: taker M=1, maker M=0 (resting orders free).
# UNVERIFIED against the fee-schedule PDF — cent vs centicent rounding is still
# an open item. Pin a verification date here once confirmed.
FEE_COEFFICIENT = 0.07
TAKER_MULTIPLIER = 1.0
MAKER_MULTIPLIER = 0.0
FEE_ROUNDING_INCREMENT = 0.01  # dollars; 0.0001 if centicent turns out correct

SETTLEMENT_AVG_SECONDS = 60.0


def norm_cdf(x: float) -> float:
    """Standard normal CDF via erf. No scipy needed."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def effective_minutes(minutes_left: float,
                      avg_seconds: float = SETTLEMENT_AVG_SECONDS) -> float:
    """
    Time to use in the sqrt(t) scaling, corrected for settlement averaging.

    Settlement = mean of the index over the final `avg_seconds`. For a Brownian
    path the variance of that mean is (avg/3) rather than avg, so:

        t_eff = (t - avg) + avg/3  =  t - (2/3)*avg

    At 8 minutes left this is a small correction. Inside the final minute it
    dominates, and it is exactly the zone where a point-read model is most
    wrong and most tempting.
    """
    avg_min = avg_seconds / 60.0
    return max(minutes_left - (2.0 / 3.0) * avg_min, 1e-9)


def sigma_per_minute(sigma_annual: float) -> float:
    """Convert annualized vol (0.50 = 50%) to per-minute return vol."""
    return sigma_annual / math.sqrt(MINUTES_PER_YEAR)


def fair_prob_above(spot: float,
                    target: float,
                    minutes_left: float,
                    sigma_annual: float) -> float:
    """
    P(settlement value > target) under a driftless random walk.

    Drift is deliberately omitted. Over 15 minutes any drift estimate is far
    smaller than its own standard error — including it adds noise, not signal.
    """
    t_eff = effective_minutes(minutes_left)
    price_sigma = spot * sigma_per_minute(sigma_annual) * math.sqrt(t_eff)
    if price_sigma <= 0:
        return 1.0 if spot > target else 0.0
    return norm_cdf((spot - target) / price_sigma)


def fair_prob_with_drift(spot: float,
                         target: float,
                         minutes_left: float,
                         sigma_annual: float,
                         drifts: tuple[tuple[float, float], ...] = ()) -> float:
    """
    Fair value when signals predict a short-horizon move.

    Each signal enters as (expected_move_dollars, horizon_minutes): how far it
    predicts price will travel, and over how long. Only the part of a signal's
    horizon that fits inside the remaining clock can affect settlement.

    This replaces hand-tuned "signals matter more early / late" weights.
    Phi does the time-weighting itself:
      - near the target, a small predicted move shifts probability a lot,
        and MORE as the clock runs down (less noise left to wash it out)
      - far from the target, the same move barely matters, least of all late

    Feed it only drifts from signals validated on the ledger.
    An unvalidated drift is an opinion with units.
    """
    shift = 0.0
    for move, horizon in drifts:
        if horizon > 0:
            shift += move * min(minutes_left, horizon) / horizon
    t_eff = effective_minutes(minutes_left)
    price_sigma = spot * sigma_per_minute(sigma_annual) * math.sqrt(t_eff)
    if price_sigma <= 0:
        return 1.0 if spot + shift >= target else 0.0
    return norm_cdf((spot + shift - target) / price_sigma)


def fair_prob_in_settlement_window(known_prints: list[float],
                                   spot_now: float,
                                   target: float,
                                   sigma_annual: float,
                                   window_prints: int = 60) -> float:
    """
    Fair P(Up) INSIDE the final 60 seconds, where part of the settlement
    average is already fixed. Use this, not fair_prob_above, once fewer than
    60 s remain — the t - 40 s shortcut assumes all 60 prints are still ahead,
    and inside the last 40 s it collapses to "wherever spot is right now."

    Settlement = mean of 60 one-second index prints. With k prints recorded,
    only the remaining m = 60 - k are uncertain, each one spot_now plus a
    random walk:
        mean     = (sum(known) + m * spot_now) / 60
        variance = s^2 * m(m+1)(2m+1) / 6 / 60^2      s = per-second $ vol

    Example: 30 s in, the known half averages $20 over target, spot sits
    exactly AT target. Spot-only says 50%. The locked-in half says ~97%.

    known_prints must come from a BRTI proxy (median of the index's exchange
    mids). Coinbase alone adds tracking error in exactly the minute that
    decides settlement.
    """
    k = len(known_prints)
    m = window_prints - k
    if m < 0:
        raise ValueError("more known prints than the window holds")
    mean = (sum(known_prints) + m * spot_now) / window_prints
    s = spot_now * sigma_annual / math.sqrt(MINUTES_PER_YEAR * 60.0)
    var = s * s * m * (m + 1) * (2 * m + 1) / 6.0 / window_prints ** 2
    if var <= 0:
        return 1.0 if mean >= target else 0.0
    return norm_cdf((mean - target) / math.sqrt(var))


def realized_vol_annual(prices: list[float], seconds_per_bar: float) -> float:
    """
    Annualized realized vol from a price series. Feed it the most recent bars
    from the SAME window you are trading — vol regime shifts fast.
    """
    if len(prices) < 3:
        raise ValueError("need at least 3 prices to estimate vol")
    rets = [math.log(prices[i] / prices[i - 1]) for i in range(1, len(prices))]
    n = len(rets)
    mean = sum(rets) / n
    var = sum((r - mean) ** 2 for r in rets) / (n - 1)
    bars_per_year = MINUTES_PER_YEAR * 60.0 / seconds_per_bar
    return math.sqrt(var * bars_per_year)


def order_fee(price: float, contracts: int, taker: bool = True) -> float:
    """
    Kalshi fee, rounded once PER ORDER (not per contract).
    Maker multiplier is 0 on KXBTC15M -> resting orders cost nothing.
    """
    multiplier = TAKER_MULTIPLIER if taker else MAKER_MULTIPLIER
    if multiplier == 0.0:
        return 0.0
    raw = multiplier * FEE_COEFFICIENT * contracts * price * (1.0 - price)
    steps = math.ceil(raw / FEE_ROUNDING_INCREMENT)
    return steps * FEE_ROUNDING_INCREMENT


@dataclass
class Assessment:
    fair_prob: float
    market_price: float
    gross_edge: float        # fair - price, in probability points
    breakeven_prob: float    # price + fee, what you must beat
    net_edge: float          # fair - breakeven
    kelly_fraction: float
    verdict: str


def assess(spot: float,
           target: float,
           minutes_left: float,
           sigma_annual: float,
           market_price: float,
           contracts: int = 1,
           taker: bool = True,
           kelly_scale: float = 0.5) -> Assessment:
    """
    One trade, fully costed. market_price is the ASK you would actually pay,
    expressed in dollars (0.53 for 53 cents), for the side you are buying.
    """
    fair = fair_prob_above(spot, target, minutes_left, sigma_annual)
    fee = order_fee(market_price, contracts, taker) / max(contracts, 1)
    breakeven = market_price + fee
    net = fair - breakeven

    # Kelly on a binary: you risk `breakeven` to win 1.00.
    b = (1.0 - breakeven) / breakeven if breakeven > 0 else 0.0
    kelly = ((b * fair) - (1.0 - fair)) / b if b > 0 else 0.0
    kelly = max(0.0, kelly) * kelly_scale

    if net <= 0:
        verdict = "NO_TRADE — edge does not clear the fee"
    elif net < 0.02:
        verdict = "NO_TRADE — edge inside vol-estimate error"
    else:
        verdict = "candidate — log it, do not assume it"

    return Assessment(fair, market_price, fair - market_price,
                      breakeven, net, kelly, verdict)


def implied_sigma(spot: float,
                  target: float,
                  minutes_left: float,
                  market_price: float,
                  lo: float = 0.01,
                  hi: float = 5.0,
                  tol: float = 1e-6) -> float | None:
    """
    Invert the model: what annualized volatility does the market's own price
    imply? Bisection — fair_prob is monotonic in sigma on each side of the
    target, so this is well behaved.

    THIS IS THE NUMBER TO PUT ON A DASHBOARD, not a fair probability.

    A fair probability hides its assumption: 77.8% looks authoritative and is
    entirely an artifact of the sigma you chose. Implied sigma exposes the
    assumption instead, and it is directly comparable to the realized vol you
    just measured. "The market is pricing 33% vol while the last hour realized
    52%" is a falsifiable statement about the world. "Fair value is 77.8%" is
    a statement about your own input.

    Returns None when the price is unreachable at any sigma (e.g. a market
    priced below 50% while spot is above target — no volatility explains it,
    which is itself a finding worth logging).
    """
    if not (0.0 < market_price < 1.0):
        return None
    above = spot > target
    if (above and market_price < 0.5) or (not above and market_price > 0.5):
        return None  # no sigma reproduces this; flag it, don't fudge it

    for _ in range(200):
        mid = (lo + hi) / 2.0
        p = fair_prob_above(spot, target, minutes_left, mid)
        if abs(p - market_price) < tol:
            return mid
        # Higher sigma pulls probability toward 0.5 from either side.
        if (p > market_price) == above:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def sigma_sensitivity(spot: float,
                      target: float,
                      minutes_left: float,
                      market_price: float,
                      sigmas: tuple[float, ...] = (0.30, 0.40, 0.50, 0.60, 0.70)
                      ) -> list[tuple[float, float, float]]:
    """
    Run this BEFORE trusting any fair value. Returns (sigma, fair, edge).
    If the sign of the edge flips across this range, you do not have a trade —
    you have an opinion about volatility.
    """
    out = []
    for s in sigmas:
        fair = fair_prob_above(spot, target, minutes_left, s)
        out.append((s, fair, fair - market_price))
    return out


if __name__ == "__main__":
    # Live window observed 2026-08-17 on KXBTC15M (kxbtc15m-26aug171115).
    SPOT, TARGET, LEFT, ASK = 63_903.56, 63_810.87, 8.2, 0.88

    print(f"spot {SPOT:,.2f}  target {TARGET:,.2f}  "
          f"{SPOT - TARGET:+,.2f} away  {LEFT} min left  market ask {ASK:.2f}\n")
    print(f"{'sigma':>7} {'fair':>8} {'edge':>8}")
    for s, fair, edge in sigma_sensitivity(SPOT, TARGET, LEFT, ASK):
        print(f"{s:>7.0%} {fair:>8.1%} {edge:>+8.1%}")

    print()
    a = assess(SPOT, TARGET, LEFT, sigma_annual=0.50,
               market_price=ASK, contracts=1, taker=True)
    print(f"fair {a.fair_prob:.1%}   breakeven {a.breakeven_prob:.1%}   "
          f"net edge {a.net_edge:+.1%}")
    print(f"half-Kelly size: {a.kelly_fraction:.1%} of bankroll")
    print(a.verdict)
