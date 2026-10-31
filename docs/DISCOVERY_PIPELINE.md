# Three-workflow pipeline

Run Yahoo movers discovery manually first, inspect coverage/errors and publication.
It creates/updates watchlist_auto.json and watchlist_auto.txt on discovery-state only.
Only discovery requests contents:write. State publishing never changes main and
rejects conflicting updates. GH_TOKEN is passed only to Git state transport.
Manual watchlist.txt is untouched. The existing backup branch remains untouched.

Next run the Alpaca workflow in scan mode during regular hours; automatic/manual/normal
universes merge with fresh Alpaca verification. Entry bars/quotes remain from Alpaca.
Tick enablement, kill switch, long-only strategy, history rules, late cutoff, capacity,
cash and fixed USD 15 combined planned risk remain unchanged. A mover is not a buy.

Run Alpaca post-close next-session setups after close+20 minutes for completed daily
Alpaca-bar research including all lists. This mode forbids broker writes and never
queues orders. It labels the calendar-selected next trading session. A too-early
run waits; weekend/holiday runs use the latest completed session. Next-session entries
are independently rescanned with executable quotes and current portfolio constraints.

Discovery: 0/30 minutes during UTC 13-22 weekdays; entry retains 20-minute schedule;
post-close probes 20:35/21:35 UTC. GitHub schedules are best effort. The next scan
after successful state publication can use the snapshot. Automatic rows expire at
the fifth future trading-session close unless rediscovered, capped at 150 symbols.
Manual rows do not expire. Missing/corrupt state is optional to normal trading scans.

Yahoo screens day_gainers/day_losers/most_actives/small_cap_gainers (last is activity
ranked) promote at most 10 eligible symbols each. Historical adjusted-close ranks
cover latest completed session, previous session and five completed session returns.
The pool is current screens plus normal/manual/recent discoveries, NOT all US stocks.
Missing history is skipped; $1 price/$2m daily or median five-session dollar volume
are research defaults, not optimized rules. Original symbol exclusions still apply.
Yahoo failures retain fresh prior rows and report DEGRADED/coverage. Yahoo is discovery
only; no news or new intraday entry strategy is included. No profitability guarantee.

State branch creation needs permitted Actions write access. Verify all three workflows
after deployment; tests use synthetic data/local Git, not real Yahoo or broker fills.
Pre-upgrade manual-watchlist code is e33b6ae1d9d8727d7c43318d0b8bac421e09e37a.
Code rollback does not restore broker positions, secrets or repository variables.
