# Alpaca US paper execution

This adds a separate runner; the old offline CLI/configuration remains disabled.

## Browser activation
Use a separate empty USD 2,000 Alpaca paper account. Configure secrets
ALPACA_PAPER_KEY_ID and ALPACA_PAPER_SECRET_KEY, and repository variable
ALPACA_EXPECTED_PAPER_ACCOUNT_ID (account API id, not account number).
Actions > Alpaca 2K paper execution every 20 minutes > Run workflow > check.
Require READ_ONLY_VERIFIED, then run scan. Both are read-only.
After verification set repository variable ALPACA_PAPER_ENABLED=true to enable
scheduled tick runs. Manual tick also permits paper orders when enabled.
ALPACA_PAPER_KILL_SWITCH=true blocks new entries but enabled management continues.
ALPACA_PAPER_ENABLED=false disables all writes, including exit management.
Neither switch liquidates holdings. Inspect Alpaca orders/positions after failure.

## Rules and limits
Fixed USD 15 combined buffered planned stop risk, including pending entries;
6% per-entry planned risk, four symbols, 25% equity maximum per new position,
whole shares and available-cash sizing. Equity growth can compound; risk stays USD 15.
Gaps, slippage, outages and partial fills can exceed planned losses.

Signals port original run_recent_blend.py compression/blend arithmetic from
f2b5542f892cf1c6f3724ed68d343b4699354541. Completed daily bars, not scalping.
S&P500 plus SPY/QQQ, original AMD/AMZN exclusions; exact normalized broker-name
verification quarantines ambiguous assets. Missing universe data stops execution.
Confirmed prior swing-high targets use two bars on each side; minimum 1.5 reward/risk,
20 historical outcomes and positive shrunk mean. Ranking is in-sample and biased
by the current universe, not a future probability or proof of optimality.
Default IEX data differs from entitled SIP coverage. No qualifying setup means no order.

GTC limit brackets, <=10-second quotes, <=0.5% spread, <=2% reference drift,
15-bps-per-side planning buffer. No entries in final 30 session minutes.
Stale entries >=20 minutes old and partial parents are canceled. Held stop legs
are not active protection. Unprotected owned positions and seven-session time exits
use cancel-confirm-requery before market sells. A cancellation failure halts;
terminal exits with residual holdings require manual review. Bracket stops may
not activate until full parent fill; partial fills between checks may be unprotected.

Broker order IDs/fills are the durable journal; reports are audit snapshots only.
Restart reconciliation checks ownership, foreign orders and pending risk/cash.
Stable per-symbol/date client IDs prevent same-day repeat attempts. Ambiguous POSTs
are not retried. Local reservations are conservatively double-counted within a run.
History reaching 500 root/open-order rows stops execution rather than truncate.
Do not run a second copy or mix manual trades/deposits/other strategies in this account.

Cron targets 20-minute intervals during UTC hours 13-21 weekdays. Broker calendar
checks regular US sessions, holidays and early closes. GitHub may delay/drop runs.
Tests/setup add latency. This is not continuous monitoring or guaranteed close-time
execution. Continuous alerts and paginated archival history remain operational limits.
Tests use mocks and synthetic data; real credentials, entitlements, broker behavior
and all existing tests still need GitHub workflow verification. Paper-only software;
not a validated live system or a guarantee of profit.
