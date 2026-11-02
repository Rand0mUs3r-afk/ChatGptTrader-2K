# Scan rejection diagnostics

This change adds observations to normal stage-reported scan/tick runs. It does not loosen eligibility, switch to intraday bars, change rankings/sizing/stops/targets, add broker calls, or force orders. Post-close research is not instrumented by this wrapper.

Download reports/paper/scan_audit.json and scan_symbol_diagnostics.csv from the existing paper workflow artifact. status.json also contains diagnostics.scan_audit with the final observed combined-universe count, daily-bar page count, strategy/feed, last completed session, ranked-candidate count and reason totals.

Reasons: NO_DAILY_BARS, INSUFFICIENT_DAILY_BARS, CALENDAR_COVERAGE_MISMATCH, NO_LATEST_DAILY_SIGNAL, INSUFFICIENT_HISTORICAL_SAMPLES, NONPOSITIVE_HISTORY_SCORE, PASSED_SCANNER. UNCLASSIFIED and its timestamp/observation variants mean observation was insufficient, not a proven rejection cause. SCAN_ABORTED means the underlying scanner failed and partial observations are not a completed scan.

The wrapper calls the original scanner once and observes its universe, GET bar responses, feature results and history statistics. Helpers are restored in finally. No credentials, raw broker responses, quotes or order identifiers are written by the audit. Reporting failures are marked AUDIT_UNAVAILABLE and do not replace the original scanner result; original scanner errors still propagate. Local tests use synthetic inputs, not live Alpaca data. Do not interpret zero candidates as proof that no opportunities existed market-wide.
