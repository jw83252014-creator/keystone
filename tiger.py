#!/usr/bin/env python3
"""
tiger.py — records KXBTC15M microstructure and serves the volatility panel.

    python3 tiger.py            # record + serve on http://localhost:8787
    python3 tiger.py --once     # single poll, print, exit (use this first)

Stdlib only. Read-only. No credentials, no auth headers, no order endpoints.

WHAT THIS IS FOR
----------------
Two jobs, one loop:

 1. RECORD. Every poll writes book + index + timestamps to SQLite. This data
    cannot be backfilled — Kalshi does not sell it and nobody else is storing
    it at your parameters. Every hour this is not running is an hour gone.

 2. DISPLAY. Implied volatility (from the book) against realized volatility
    (measured). Not a probability — a probability hides the assumption that
    produces it. See fairvalue.implied_sigma for the argument.

IT ALSO ANSWERS THE LATENCY QUESTION
------------------------------------
Every row stores both the Kalshi server timestamp and the local clock at
receipt. Tiger's claim is that CF Benchmarks leads Kalshi's display by 1-2s.
That claim becomes measurable the moment this has run for an hour.

SAFETY
------
ALLOWED_PATHS below is the complete set of endpoints this file will call.
There is no order-submission code here to disable, because none was written.
Adding one is a gate decision, not a code change.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from fairvalue import (fair_prob_above, implied_sigma, order_fee,
                       realized_vol_annual)

# --- configuration -----------------------------------------------------------

# P0-2 from the 2026-07-27 review. Verify against Kalshi's quick-start before
# trusting; if this 404s, that IS the finding — log it, do not probe hosts.
KALSHI_BASE = "https://external-api.kalshi.com/trade-api/v2"
SERIES = "KXBTC15M"

# Public, unauthenticated. Used ONLY to estimate realized volatility.
# This is NOT the settlement index. Levels differ from BRTI; vol does not,
# materially. Never store this in a column called `price` or `btc_price`.
COINBASE_CANDLES = ("https://api.exchange.coinbase.com"
                    "/products/BTC-USD/candles?granularity=60")

ALLOWED_PATHS = ("/markets", "/markets/{ticker}/orderbook", COINBASE_CANDLES)

DB_PATH = Path("tiger.db")
POLL_SECONDS = 5
PORT = 8787
USER_AGENT = "keystone-tiger/0.1 (research; read-only)"

# --- storage -----------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS tick (
  id              INTEGER PRIMARY KEY,
  local_ts        REAL NOT NULL,   -- our clock at receipt
  server_ts       TEXT,            -- Kalshi's Date header, for lag measurement
  ticker          TEXT NOT NULL,
  floor_strike    REAL,
  expiration_time TEXT,
  seconds_left    REAL,
  yes_bid         REAL,            -- dollars
  yes_ask         REAL,
  no_bid          REAL,
  no_ask          REAL,
  volume          REAL,            -- volume_fp upstream (fractional)
  coinbase_spot   REAL,            -- proxy ONLY. never the settlement value.
  brti_official   REAL,            -- permanently NULL until licensed
  realized_sigma  REAL,
  implied_sigma   REAL,
  UNIQUE(ticker, local_ts)
);
CREATE INDEX IF NOT EXISTS tick_ticker_ts ON tick(ticker, local_ts);
"""


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    return conn


# --- fetching ----------------------------------------------------------------

def get_json(url: str) -> tuple[dict | list, str | None]:
    """Returns (payload, server_date_header). Raises on anything unexpected."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                               "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode()), r.headers.get("Date")


def active_market() -> tuple[dict, str | None]:
    """The open KXBTC15M window closing soonest."""
    payload, date_hdr = get_json(
        f"{KALSHI_BASE}/markets?series_ticker={SERIES}&status=open&limit=20")
    markets = payload.get("markets") or []
    if not markets:
        raise RuntimeError(f"no open {SERIES} markets in response; "
                           f"keys={sorted(payload)}")
    markets.sort(key=lambda m: m.get("close_time") or m.get("expiration_time") or "")
    return markets[0], date_hdr


def realized_sigma_now(bars: int = 45) -> tuple[float, float]:
    """(annualized realized vol, latest close) from 1-minute candles."""
    payload, _ = get_json(COINBASE_CANDLES)
    if not isinstance(payload, list) or len(payload) < 5:
        raise RuntimeError("unexpected candle payload")
    rows = sorted(payload[:bars], key=lambda c: c[0])   # oldest -> newest
    closes = [float(c[4]) for c in rows]
    return realized_vol_annual(closes, seconds_per_bar=60.0), closes[-1]


def price(market: dict, side: str) -> float | None:
    """
    Read one quote off a market record, in dollars.

    The live API (verified 2026-08-17 against settled KXBTC15M records) quotes
    '<side>_dollars' as decimal strings, e.g. "0.8800". The integer-cent fields
    this file originally read no longer appear — reading them returned None
    silently, so the recorder would have logged empty prices with no error.
    That is the P0-1 failure from the 2026-07-27 review, repeated.

    Legacy integer cents are accepted only as a fallback. If NEITHER key
    exists, raise: an empty book and an unparseable record must never look
    the same.
    """
    key = f"{side}_dollars"
    if key in market:
        v = market[key]
        return None if v in (None, "") else float(v)
    if side in market:
        v = market[side]
        return None if v is None else float(v) / 100.0
    raise KeyError(f"market record has neither {key!r} nor {side!r}; "
                   f"schema drifted? keys={sorted(market)}")


def volume_of(market: dict) -> float | None:
    """Live API: 'volume_fp' fixed-point string. Legacy: integer 'volume'."""
    for k in ("volume_fp", "volume"):
        if k in market and market[k] not in (None, ""):
            return float(market[k])
    return None


# --- one poll ----------------------------------------------------------------

def poll_once(conn: sqlite3.Connection) -> dict:
    local_ts = time.time()
    market, server_ts = active_market()
    sigma_r, spot = realized_sigma_now()

    ticker = market["ticker"]
    strike = float(market.get("floor_strike") or 0.0)
    expiry = market.get("close_time") or market.get("expiration_time")
    secs_left = None
    if expiry:
        try:
            end = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
            secs_left = (end - datetime.now(timezone.utc)).total_seconds()
        except ValueError:
            pass

    yes_ask = price(market, "yes_ask")
    mins_left = (secs_left or 0.0) / 60.0

    sigma_i = None
    if yes_ask and strike and mins_left > 0.05:
        sigma_i = implied_sigma(spot, strike, mins_left, yes_ask)

    row = dict(local_ts=local_ts, server_ts=server_ts, ticker=ticker,
               floor_strike=strike, expiration_time=expiry,
               seconds_left=secs_left,
               yes_bid=price(market, "yes_bid"), yes_ask=yes_ask,
               no_bid=price(market, "no_bid"), no_ask=price(market, "no_ask"),
               volume=volume_of(market), coinbase_spot=spot,
               brti_official=None, realized_sigma=sigma_r, implied_sigma=sigma_i)

    conn.execute(
        "INSERT OR IGNORE INTO tick (local_ts,server_ts,ticker,floor_strike,"
        "expiration_time,seconds_left,yes_bid,yes_ask,no_bid,no_ask,volume,"
        "coinbase_spot,brti_official,realized_sigma,implied_sigma) VALUES "
        "(:local_ts,:server_ts,:ticker,:floor_strike,:expiration_time,"
        ":seconds_left,:yes_bid,:yes_ask,:no_bid,:no_ask,:volume,"
        ":coinbase_spot,:brti_official,:realized_sigma,:implied_sigma)", row)
    conn.commit()
    return row


def assess_row(row: dict) -> dict:
    """Everything the panel needs, derived — never stored twice."""
    out = dict(row)
    spot, strike = row["coinbase_spot"], row["floor_strike"]
    mins = (row["seconds_left"] or 0.0) / 60.0
    ask, sigma_r = row["yes_ask"], row["realized_sigma"]

    out["fair"] = None
    out["net_edge"] = None
    out["verdict"] = "waiting for a live window"

    if ask and strike and mins > 0.05 and sigma_r:
        fair = fair_prob_above(spot, strike, mins, sigma_r)
        breakeven = ask + order_fee(ask, 1, taker=True)
        edge = fair - breakeven
        out["fair"] = fair
        out["breakeven"] = breakeven
        out["net_edge"] = edge
        if abs(edge) < 0.02:
            out["verdict"] = "NO_TRADE — inside vol-estimate error"
        elif edge > 0:
            out["verdict"] = f"Up looks cheap by {edge:+.1%} — log it"
        else:
            out["verdict"] = f"Up looks rich by {edge:+.1%} — Down is the side"
    return out


# --- server ------------------------------------------------------------------

PANEL_HTML = (Path(__file__).parent / "panel.html")


class Handler(BaseHTTPRequestHandler):
    conn: sqlite3.Connection = None  # set in serve()

    def log_message(self, *a):        # quiet
        pass

    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/api/state"):
            cur = self.conn.execute(
                "SELECT * FROM tick ORDER BY local_ts DESC LIMIT 1")
            r = cur.fetchone()
            cols = [d[0] for d in cur.description]
            payload = assess_row(dict(zip(cols, r))) if r else {
                "verdict": "no data yet — is the recorder running?"}
            self._send(200, json.dumps(payload, default=str).encode(),
                       "application/json")
        elif self.path in ("/", "/index.html"):
            if not PANEL_HTML.exists():
                self._send(500, b"panel.html missing next to tiger.py",
                           "text/plain")
                return
            self._send(200, PANEL_HTML.read_bytes(), "text/html; charset=utf-8")
        else:
            self._send(404, b"not found", "text/plain")


def serve(conn: sqlite3.Connection):
    Handler.conn = conn
    srv = HTTPServer(("127.0.0.1", PORT), Handler)
    srv.timeout = 0.5
    print(f"panel:  http://localhost:{PORT}")
    print(f"db:     {DB_PATH.resolve()}")
    print("ctrl-c to stop\n")
    last = 0.0
    while True:
        srv.handle_request()
        if time.time() - last >= POLL_SECONDS:
            last = time.time()
            try:
                row = poll_once(conn)
                a = assess_row(row)
                left = (row["seconds_left"] or 0) / 60.0
                print(f"{row['ticker']}  {left:5.1f}m  ask {row['yes_ask']}  "
                      f"impl {_pct(row['implied_sigma'])}  "
                      f"real {_pct(row['realized_sigma'])}  {a['verdict']}")
            except (urllib.error.URLError, urllib.error.HTTPError) as e:
                print(f"[fetch] {e}")
            except Exception as e:                      # noqa: BLE001
                print(f"[error] {type(e).__name__}: {e}")


def _pct(v):
    return "  n/a" if v is None else f"{v:5.1%}"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--once", action="store_true",
                    help="single poll, print, exit")
    args = ap.parse_args()
    conn = db()
    if args.once:
        row = poll_once(conn)
        print(json.dumps(assess_row(row), indent=2, default=str))
        return
    serve(conn)


if __name__ == "__main__":
    main()
