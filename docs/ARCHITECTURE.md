# Architecture and provenance

## Baseline

Source: Rand0mUs3r-afk/ChatGptTrader, commit c823b1209ee62d692d659dcededa0efcb9069ed7. Relevant inspected components include portfolio_paper.py, portfolio_previous_high.py, portfolio_runtime.py and src/swingbot. The existing repository uses source-rewriting policy wrappers; this new foundation uses explicit typed models and configuration instead. Only the historical_stats calculation has been ported so far. Full scanner/ranking/execution parity is a separate milestone, not a claim of this commit.

## Flow

StrategyScanner -> validated Candidate records -> ranked allocator -> independent budget validation -> disabled plan/report. ReconciledBookProvider will eventually supply isolated strategy cash, marked positions and outstanding buy reservations. No adapter implementation or executable broker route exists yet.

Equity = strategy cash + marked holdings. Available cash = strategy cash minus pending buy notional. Planned loss = sum of nonnegative entry-to-stop dollar risk on positions and pending buys. Profitable holdings do not offset other positions' loss budgets. Growing equity does not make unrealized profits spendable cash.

Allocation first rejects ineligible or duplicate/occupied instruments, sorts by historical score, historical lower bound and setup quality, then splits remaining risk among the highest-ranked capacity-fitting candidates using positive score/quality weights. Whole shares are bounded by cash, risk allocation and a fraction of current equity. Unused capital or risk is allowed. Stops and targets are not altered to force acceptance. A missing or malformed book never means an empty broker account. Offline snapshots are not broker-authoritative.

## Persistence

IntentLedger is a transactional dry-run journal only, with an explicit USD 2,000 initial-capital reservation limit. It independently checks submitted plan arithmetic, position rules, duplicate symbols, cash and aggregate risk under BEGIN IMMEDIATE. It refuses initialization over an existing file. Full compounding broker bookkeeping, order ownership, partial-fill updates, cancellations, release semantics and restart recovery must be implemented before any execution path is added. State files and credentials are ignored by git.

## Markets and deployment

Instrument identity includes symbol, exchange MIC and currency. Only XNAS/XNYS/ARCX and USD are accepted initially. TSX/CAD needs verified identifiers, an execution provider, calendars, tick/lot rules and FX snapshots before enabling. The core is scheduler-independent; CI tests software but never schedules trading. A future VM deployment must have a single writer, backups and failure monitoring.

## Validation

Before this commit was proposed, 22 synthetic unit tests and two offline CLI smoke checks passed in the assistant sandbox. No brokerage account was accessed or changed. GitHub CI validation is pending until the commit exists. Tests demonstrate software invariants, not profitability or guaranteed maximum losses.
