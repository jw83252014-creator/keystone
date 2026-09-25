# Ways Jev could make money: ranked (terminal Claude, 2026-09-25)

Jev is cheap (~$0.04 per million input tokens, output free) and fast (70-500 ms). It only picks from menus.
So the money is where many small decisions have a price on them. The ideas below are ranked by how soon they could pay and how honestly we can test them.

## 1. Kalshi 15-min: Jev as a skip-gate on FIRST (in progress)
- FIRST at minute 1 wins about 57% at a 59c price, so there's no edge on average. Jev's job is to pick WHICH FIRST calls to take, not to call direction.
- Schema v2 (after buberlo/jev-trader's design): one call, six atomic judgments. regime, leader_holds_to_settle, jump_risk_60s, toxic_flow, venue_disagreement, late_minute_lock.
  Code combines them. Score each judgment on its own before combining.
- Pass bar: the FIRST+gate subset beats its average price paid after fees on held-out days.

## 2. Token savings on our own agents (jevroute.py): saves money now
- Jev picks the agent/tool and prunes context before Claude / Sol / Grok calls.
- Target: ≥30% fewer tokens with ≤5% of dropped chunks later needed (PLAN.md Phase 4).

## 3. Publish the first open-source Jev prediction-market toolkit
- A Sep 20 survey of Jev finance repos found none for prediction markets, arbitrage or Polymarket/Kalshi.
- The Keystone repo (code only, no results; TypeSafe terms) could be that. Stars, TypeSafe hackathon prizes (announced, not yet dated), and sponsorships are the realistic payoff.

## 4. Sell cheap classification as a service
- Real reported costs: tax pages ~$0.001/page at 100% accuracy on one corpus; 500 emails for 3.5c; banking intents 92.4% for $0.44.
- Offer local businesses inbox triage, invoice/receipt sorting, and lead scoring. Needs customers, not an edge.

## 5. Games: build them, don't farm them
- Jev plays Mario, Tetris, Doom and StarCraft well enough for demos, streams and game-AI content. That can earn through content and hackathons.
- Bots on real-money skill games and paid game economies break those platforms' rules and get accounts banned, so they're not on the list.

## Not recommended
- Handing Jev direction calls on its own. Our pilot scored worse than the Kalshi mid (Brier 0.140 vs 0.055), and week 1 of the replay was only break-even at best.

Sources: gist.github.com/drillan/6916b16e8ea31a8ec36c8f59d6483150 (Jev finance projects, surveyed 2026-09-20); flowtivity.ai/blog/jev-use-cases; mindstudio.ai/blog/jev-use-cases-automation.
