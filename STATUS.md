# STATUS.md — terminal Claude's log

## Questions for Jeff
- Install the order tripwire (Phase 0.2): Claude Code's auto-mode check won't let terminal Claude write `.claude/settings.json` itself.
  Jeff creates it from HANDOFF-CLAUDE-CODE.md, with the hook command pointing at `tools/block_orders.py`.
- Maker fee on KXBTC15M: still unconfirmed.

## Log
- 2026-09-24 · 1.1 · Logs found: the app writes `phone-audit.json` + `observations*.jsonl` to `/sdcard/Android/data/com.agentbridge.kalshihud/files/`. Pulled with adb.
- 2026-09-24 · 1.2 · Wrote calls.csv / outcomes.csv / wl.txt from phone-audit.json (`phone_calls_table.py`).
  ticks.csv built from the desktop tape instead (`build_ticks.py`), because the phone rotates its observation file and keeps only ~4 days.
  Desktop ticks carry Coinbase only (no Kraken column yet).
- 2026-09-24 · answer · "First observation" was NOT a fixed second: the app locked whenever it first saw the window, sometimes minutes late when asleep.
  Fixed in APK 0.2.4 (Sol/Keystone): FIRST only if seen ≤60 s after open; otherwise LATE-FIRST, scored separately.
- 2026-09-24 · 2.1-2.3 · Baselines, streak report and per-bucket table run. Numbers in REPORT.md (Drive), not here.
- 2026-09-24 · 3.1 · `call_jev()` wired (OpenRouter `typesafe/jev-1.13`, key from env). `jev_prefetch.py` fills the cache in parallel with the exact replay requests.
- 2026-09-24 · 3.2 · Week-1 sweep done: agree {1,4,12} × min_conf {0.5,0.6,0.7} × exits {hold, ±10c} = 18 settings.
- 2026-09-24 · 3.3 · Frozen before week 2: agree=4, min_conf=0.6, hold. The week-2 cache is partly filled (rate-limited at 16 threads); resume at 6 threads.
- 2026-09-25 · 0.1 · `.gitignore` in place. 0.3 · No Kalshi trading key on this machine: the only secret is OPENROUTER_API_KEY in ~/.config/kalshi-hud/secrets.env, and ~/.local/share/kalshi-hud-signing/development.p12 is the APK signing key.
