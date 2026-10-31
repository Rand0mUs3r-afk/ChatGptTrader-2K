"""Yahoo is discovery only. Alpaca remains the entry-data and broker authority."""
from datetime import datetime, timedelta, timezone
import json
import math
import os
import re
from zoneinfo import ZoneInfo
from discovery_state import DIR, read

NY = ZoneInfo("America/New_York")
TOP = 10


def allowed(assets):
    from paper_watchlist import EXCLUDED, EXCHANGES
    counts = {}
    for a in assets: counts[a.get("symbol")] = counts.get(a.get("symbol"),0)+1
    return {a["symbol"] for a in assets if isinstance(a.get("symbol"),str)
        and re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}",a["symbol"]) and counts[a["symbol"]] == 1
        and a["symbol"] not in EXCLUDED and a.get("tradable") is True
        and a.get("status") == "active" and a.get("class") == "us_equity" and a.get("exchange") in EXCHANGES}


def leaders(values):
    return dict(gainers=sorted((s for s in values if values[s] > 0), key=lambda s:(-values[s], s))[:TOP],
        movers=sorted(values, key=lambda s:(-abs(values[s]), s))[:TOP])


def history_scores(frame, dates, previous):
    f = frame[["Close", "Volume"]].dropna().copy()
    index = f.index.tz_convert(NY) if f.index.tz is not None else f.index
    f.index = index.strftime("%Y-%m-%d")
    if f.index.has_duplicates: raise ValueError("Duplicate history dates")
    f = f.loc[f.index.isin(dates)].sort_index()
    if list(f.index[-6:]) != dates[-6:]: raise ValueError("Missing completed-session bars")
    close, volume = f.Close.astype(float), f.Volume.astype(float)
    if any(not math.isfinite(v) or v <= 0 for v in close) or any(not math.isfinite(v) or v < 0 for v in volume): raise ValueError("Invalid history")
    if close.iloc[-1] < 1 or (close.iloc[-5:]*volume.iloc[-5:]).median() < 2000000: return None
    pos = list(f.index).index(previous)
    if pos < 1: raise ValueError("Missing previous-session reference")
    return dict(latest_completed=100*(close.iloc[-1]/close.iloc[-2]-1),
        previous_session=100*(close.iloc[pos]/close.iloc[pos-1]-1),
        five_session=100*(close.iloc[-1]/close.iloc[-6]-1))


def discover(api, yf):
    import paper_execution as base
    from paper_universe_fallback import with_fallback
    from paper_watchlist import parse_watchlist, WATCHLIST
    base.account(api, os.getenv("ALPACA_EXPECTED_PAPER_ACCOUNT_ID", ""))
    clock = api.call("GET", "/v2/clock"); now = base.stamp(clock["timestamp"])
    today = str(now.astimezone(NY).date())
    calendar = api.call("GET", "/v2/calendar", params={"start":str((now-timedelta(days=60)).date()), "end":str(now.date()+timedelta(days=20))})
    closed = [d for d in calendar if base.session(d,"close")+timedelta(minutes=20) <= now]
    dates = [d["date"] for d in closed]
    previous = max(d for d in dates if d < today)
    future = [d for d in calendar if d["date"] > today]
    if len(dates) < 7 or len(future) < 5: raise ValueError("Insufficient session calendar")
    expiry = base.session(future[4], "close").astimezone(timezone.utc).isoformat()
    eligible = allowed(api.call("GET", "/v2/assets", params={"status":"active", "asset_class":"us_equity"}))
    old = {r["symbol"]:r for r in read(now) if r["symbol"] in eligible}
    found, errors, pool = {}, [], set(old)
    def add(symbol, category, value, session_date):
        if symbol not in eligible or not math.isfinite(float(value)): return
        r = found.setdefault(symbol, dict(symbol=symbol, observed_at=now.isoformat(), expires_at=expiry, reasons=[], strength=0))
        r["reasons"].append(dict(category=category, change_pct=float(value), session=session_date))
        r["strength"] = max(r["strength"], abs(float(value)))
    snapshot_date = today if clock["is_open"] else dates[-1]
    for query in ("day_gainers", "day_losers", "most_actives", "small_cap_gainers"):
        try:
            response = yf.screen(query, count=50)
            quotes = response["quotes"]
            if not isinstance(quotes, list): raise ValueError("Invalid screener response")
            accepted = 0
            for q in quotes:
                s = q.get("symbol")
                if s not in eligible: continue
                pool.add(s)
                try:
                    price, volume, change = float(q["regularMarketPrice"]), float(q["regularMarketVolume"]), float(q["regularMarketChangePercent"])
                    ts = datetime.fromtimestamp(float(q["regularMarketTime"]), timezone.utc)
                    if not all(math.isfinite(v) for v in (price, volume, change)): continue
                    if price < 1 or volume < 0 or price*volume < 2000000 or str(ts.astimezone(NY).date()) != snapshot_date: continue
                    if clock["is_open"] and not 0 <= (now-ts).total_seconds() <= 5400: continue
                except (KeyError, TypeError, ValueError, OverflowError): continue
                category = "current_"+query if query != "small_cap_gainers" else "current_smallcap_activity"
                add(s, category, change, snapshot_date); accepted += 1
                if accepted >= TOP: break
        except Exception as exc: errors.append(dict(source=query, error_type=type(exc).__name__))
    try: pool.update(with_fallback(base.universe)(api))
    except Exception as exc: errors.append(dict(source="normal_universe", error_type=type(exc).__name__))
    manual, invalid = parse_watchlist(WATCHLIST.read_text(encoding="utf-8-sig")); pool.update(manual)
    pool = sorted(pool & eligible)
    if len(pool) > 1500: raise ValueError("Discovery pool exceeds 1500 symbols")
    measures = {k:{} for k in ("latest_completed", "previous_session", "five_session")}
    covered = 0
    for offset in range(0, len(pool), 100):
        batch = pool[offset:offset+100]
        try:
            data = yf.download(batch, period="1mo", interval="1d", auto_adjust=True, group_by="ticker", threads=4, progress=False, timeout=15)
            for s in batch:
                try:
                    f = data[s] if getattr(data.columns, "nlevels", 1) > 1 else data if len(batch) == 1 else None
                    if f is None: continue
                    scores = history_scores(f, dates, previous)
                    if scores is None: continue
                    covered += 1
                    for key,value in scores.items(): measures[key][s] = value
                except (KeyError, TypeError, ValueError, IndexError): continue
        except Exception as exc: errors.append(dict(source="historical_batch", error_type=type(exc).__name__))
    for category, values in measures.items():
        for kind, symbols in leaders(values).items():
            for s in symbols: add(s, category+"_"+kind, values[s], previous if category == "previous_session" else dates[-1])
    old.update(found)
    entries = sorted(old.values(), key=lambda r:(r["observed_at"], float(r.get("strength",0))), reverse=True)[:150]
    report = dict(version=1, updated_at=now.isoformat(), entries=entries, discovery_only=True,
        status="DEGRADED" if errors or not covered else "COMPLETED", errors=errors,
        coverage=dict(pool_symbols=len(pool), historical_symbols_covered=covered, previous_session=previous,
            latest_completed_session=dates[-1], ranking_scope="Current screeners plus normal/manual/recently discovered eligible universe, not all US stocks",
            historical_basis="Yahoo adjusted-close changes; five_session means five completed trading-session returns"))
    DIR.mkdir(exist_ok=True)
    (DIR/"watchlist_auto.json").write_text(json.dumps(report, indent=2, allow_nan=False)+"\n")
    (DIR/"watchlist_auto.txt").write_text("\n".join(sorted(r["symbol"] for r in entries))+"\n")
    print(json.dumps({k:v for k,v in report.items() if k != "entries"}, indent=2))
    if os.getenv("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
            f.write("# Yahoo discovery (not entry approval)\n\n```json\n"+json.dumps(report, indent=2)+"\n```\n")


if __name__ == "__main__":
    try:
        import yfinance as yf
        from paper_execution import API
        from paper_pipeline import ReadOnly
        discover(ReadOnly(API(enabled=False)), yf)
    except Exception as exc:
        raise SystemExit("Discovery failed: "+type(exc).__name__+"; details withheld") from None
