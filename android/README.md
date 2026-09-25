# Moto native APK 0.2.4

Direct Kalshi market/settlement and Coinbase spot requests. Phone-local FIRST CALL and LATE locks persist and grade against official outcomes. Quotes, observed chart, phone tick indicators, replay, paper exit calculator, and a 45-entry finance/plain-language glossary are included.

Optional ThinkPad requests provide timestamped desktop history and research. Those features remain separate from local phone calls; read ../DATA-AND-INDEPENDENCE.md. Phone mix uses approximately two-second samples, not desktop minute candles.

Collection runs while the Activity is visible; background monitoring is not implemented. Replay rotates at 8 MB per file. No orders, account keys or calibrated entry edge. Coinbase is not the settlement index.

Build: `bash build.sh` with JDK 21, Android platform 36 and build-tools 36.0.0. The development signing key stays outside the source tree. Preserve it for updates. Version 0.2.3 classified a directional opening observation as FIRST only if observed within 60 seconds of market open; later first observations show LATE-FIRST and their age. Version 0.2.4 adds three timing-specific scoreboard rows with win rate, saved paper ask, and win-minus-ask. The YES/NO rule stays unchanged.

Validation: native Java checks, signed local APK build, and installed-hash comparison.
