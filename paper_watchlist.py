"""Merge manual tickers into the existing Alpaca-backed paper scan."""
from functools import wraps
import json
import os
from pathlib import Path
import re

WATCHLIST = Path(__file__).with_name("watchlist.txt")
EXCLUDED = {"AMD", "AMZN"}
EXCHANGES = {"NYSE", "NASDAQ", "AMEX", "ARCA", "BATS", "NYSEARCA"}


def parse_watchlist(text):
    symbols, rejected = set(), []
    for line_no, raw in enumerate(text.splitlines(), 1):
        symbol = raw.split("#", 1)[0].strip().upper()
        if not symbol: continue
        if not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,9}", symbol):
            rejected.append(dict(line=line_no, reason="INVALID_SYMBOL_FORMAT"))
            continue
        symbols.add(symbol)
    if len(symbols) > 100: raise ValueError("Watchlist exceeds 100 unique tickers")
    return sorted(symbols), rejected


def merge(api, normal, path=WATCHLIST):
    path = Path(path)
    if path.stat().st_size > 65536: raise ValueError("Watchlist exceeds 64 KiB")
    symbols, rejected = parse_watchlist(path.read_text(encoding="utf-8-sig"))
    merged, accepted = set(normal), []
    if symbols:
        # Exact Alpaca symbols only: do not guess Yahoo share-class conversions.
        rows = api.call("GET", "/v2/assets", params={"status": "active", "asset_class": "us_equity"})
        if not isinstance(rows, list): raise ValueError("Invalid Alpaca asset response")
        assets, duplicates = {}, set()
        for row in rows:
            symbol = row.get("symbol")
            if symbol in assets: duplicates.add(symbol)
            assets[symbol] = row
        for symbol in symbols:
            asset = assets.get(symbol, {})
            if symbol in EXCLUDED: reason = "EXISTING_POLICY_EXCLUSION"
            elif symbol in duplicates: reason = "AMBIGUOUS_BROKER_ASSET"
            elif not asset: reason = "NOT_FOUND_IN_ACTIVE_ALPACA_ASSETS"
            elif asset.get("symbol") != symbol or asset.get("class") != "us_equity": reason = "NOT_US_EQUITY"
            elif asset.get("status") != "active" or asset.get("tradable") is not True: reason = "NOT_ACTIVE_AND_TRADABLE"
            elif asset.get("exchange") not in EXCHANGES: reason = "EXCHANGE_NOT_ALLOWED"
            else: reason = None
            if reason:
                rejected.append(dict(symbol=symbol, reason=reason))
                continue
            accepted.append(dict(symbol=symbol, broker_name=asset.get("name", ""), already_in_normal_universe=symbol in merged))
            merged.add(symbol)
    report = dict(source="watchlist.txt", requested=symbols, accepted=accepted,
        rejected=rejected, total_scan_symbols=len(merged), market_data_source="Alpaca",
        note="Watchlist inclusion is not entry approval. Existing strategy/history/quote/cash/risk/session gates apply.")
    out = Path("reports/paper"); out.mkdir(parents=True, exist_ok=True)
    (out/"manual_watchlist.json").write_text(json.dumps(report, indent=2)+"\n")
    print(json.dumps({"manual_watchlist": report}))
    if os.getenv("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
            f.write("# Manual watchlist\n\n```json\n"+json.dumps(report, indent=2)+"\n```\n")
    return sorted(merged)


def with_watchlist(primary, path=WATCHLIST):
    @wraps(primary)
    def universe(api):
        return merge(api, primary(api), path)
    return universe


def main():
    import paper_execution as base
    from paper_universe_fallback import with_fallback
    import paper_stage_report as reporting
    original = base.universe
    base.universe = with_watchlist(with_fallback(original))
    try:
        return reporting.main()
    finally:
        base.universe = original


if __name__ == "__main__":
    raise SystemExit(main())
