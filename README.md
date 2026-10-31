# ChatGptTrader-2K

Private, paper-only architecture milestone based on Rand0mUs3r-afk/ChatGptTrader at commit c823b1209ee62d692d659dcededa0efcb9069ed7. This is not yet a full strategy/execution clone or an operating broker bot. No market-data connection, brokerage connection, credentials or trading schedule is included.

## Agreed capital and risk rules

- USD 2,000 starting strategy equity, not a permanent equity ceiling.
- Planning allocations can grow with strategy equity and shrink after losses. Spendable cash remains separate from unrealized equity.
- USD 15 fixed combined planned stop-loss risk across existing positions, pending buys and new plans. This does not grow automatically with equity.
- Maximum 6% planned stop-loss risk relative to each position's entry value. Stops are preserved; quantities are reduced or the setup is skipped.
- Initial configurable allocator defaults: four occupied positions, at most 25% of current strategy equity per new position, whole-share sizing, 1.5 minimum reward/risk, at least 20 historical observations and a positive shrunk historical score. These defaults are starting policies, not optimized performance claims.
- A planned stop-loss ceiling cannot guarantee realized losses during gaps or execution failures.

## Run locally

```bash
python -m pip install -e '.[test]'
python -m trader.cli check
python -m trader.cli plan --book examples/book.json --candidates examples/candidates.json
python -m pytest -q
```

The examples are synthetic fixtures, not real securities recommendations, quotes or performance. The default example produces an offline USD 250 plan with USD 15 planned risk. The CLI never submits orders. Changing execution_enabled to true is explicitly rejected in this milestone.

## Included

- Decimal-based equity, cash, exposure and pending-order risk models.
- Ranked, weighted allocation with per-position and portfolio gates.
- Original historical-score shrinkage/Wilson arithmetic preserved as a helper; historical scores are not win probabilities.
- Plan/skip explanations and an offline JSON CLI.
- SQLite intent-journal prototype with exclusive initialization, transactional reservations, independent financial validation and idempotent intent IDs.
- Future scanner and reconciled-book adapter interfaces.
- Automated tests and CI.

The SQLite prototype is intentionally limited to initially empty-account dry-run reservations. It cannot consume real fills, reconcile a running account or release reservations. Compounding is supported by the allocator's supplied Book snapshot, not by a live ledger adapter yet. Do not use this prototype as an execution ledger.

## Next phases

1. Port and parity-test the pinned baseline's setup detection, indicator inputs, historical ranking pipeline and prior-high targets behind StrategyScanner. This source integration is not implemented yet.
2. Add read-only paper broker reconciliation, fresh quotes/calendars, strategy-only cash accounting, partial-fill handling and atomic reservation-to-fill transitions.
3. Add independently tested, explicitly enabled paper execution with a separate account identity and order prefix. No existing bot state or secrets should be copied.
4. Add TSX/CAD data and broker adapters, exchange calendars and explicit FX valuation. XTSE/CAD instruments are rejected until then.

See docs/ARCHITECTURE.md for boundaries and outstanding work. The original ChatGptTrader, crypto bot and Shorts bot are unchanged.

FROM PERPLEXITY OCT 30

## Dated clarification: original milestone versus newer workflow

The original text above is preserved as the earlier architecture-milestone description. It is not a complete inventory of the current repository: a newer Alpaca paper-execution workflow is now present. Its existence does not establish successful broker operation, strategy parity, or resolution of every earlier prototype limitation. This review did not execute the runner or its tests.

## Automation configuration inspected

- Workflow: .github/workflows/alpaca-paper-20m.yml (Alpaca 2K paper execution every 20 minutes).
- Configured cron: 10,30,50 13-21 * * 1-5, meaning minutes 10, 30 and 50 during UTC hours 13 through 21, Monday through Friday.
- Manual modes: check, scan and tick; the manual default is check. Scheduled runs select tick.
- The workflow runs regression tests before invoking python paper_watchlist.py --mode with the selected mode. Tick is described as permitting PAPER orders when enabled, not real-money orders.
- It reads ALPACA_PAPER_ENABLED (default false), ALPACA_PAPER_KILL_SWITCH (default false), the expected paper-account ID, paper credentials, strategy selection and data-feed selection. Actual variable/secret values were not inspected.
- CURRENT ENABLED/DISABLED STATUS: NOT VERIFIED. A configured cron is not evidence that GitHub Actions is enabled, execution switches permit orders, credentials work, or scheduled runs succeed. The connected review tools did not expose actual workflow activation or repository Actions settings.

## Visibility and account separation

The repository was listed PUBLIC in the GitHub inventory reviewed on October 30, despite the older Private wording above. Verify current visibility in repository settings; README wording is not an access-control setting. Never commit credentials or sensitive account records.

This project is related to ChatGptTrader but must not be assumed to share its account, state or risk budget. Verify dedicated paper-account identities before enabling competing bots. Historical scores are not calibrated win probabilities.

## Manual stop

To prevent new triggers, open Actions > Alpaca 2K paper execution every 20 minutes > workflow menu > Disable workflow. Cancel queued/running runs separately. Stopping the workflow does not close existing broker-paper positions or cancel outstanding orders, and may stop workflow-managed position checks. Inspect the paper account separately.

This append changes documentation only. It does not modify code, schedules, enable flags, kill switches, credentials or broker accounts. This is a dated review, not a continuously refreshed operational dashboard.
