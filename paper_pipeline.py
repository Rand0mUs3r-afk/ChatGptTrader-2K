"""Merge automatic discoveries into normal/manual scans; post-close mode never writes."""
import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
from discovery_state import read


class ReadOnly:
    enabled = False
    verified = False
    def __init__(self, api): self.api = api
    def call(self, method, path, *args, **kwargs):
        if method != "GET": raise RuntimeError("Research forbids writes")
        return self.api.call(method, path, *args, **kwargs)


def with_auto(primary):
    def universe(api):
        normal = primary(api)
        report = dict(source="discovery-state", accepted=[], rejected=[])
        try:
            rows = read(datetime.now(timezone.utc))
            if rows:
                from yahoo_discovery import allowed
                assets = api.call("GET", "/v2/assets", params={"status":"active", "asset_class":"us_equity"})
                valid = allowed(assets)
                for row in rows:
                    report["accepted" if row["symbol"] in valid else "rejected"].append(row["symbol"])
        except Exception as exc:
            report.update(status="UNAVAILABLE_NORMAL_UNIVERSE_RETAINED", error_type=type(exc).__name__)
        print(json.dumps({"auto_watchlist":report}))
        return sorted(set(normal) | set(report["accepted"]))
    return universe


def execute(mode):
    import paper_execution as base
    import paper_stage_report as stage
    from paper_universe_fallback import with_fallback
    from paper_watchlist import with_watchlist
    original = base.universe
    base.universe = with_auto(with_watchlist(with_fallback(original)))
    report = dict(status="FAILED_CLOSED", mode=mode, paper_only=True)
    try:
        expected = os.getenv("ALPACA_EXPECTED_PAPER_ACCOUNT_ID", "")
        if mode != "post-close":
            enabled = mode == "tick" and os.getenv("ALPACA_PAPER_ENABLED", "false").lower() == "true"
            return stage.execute(mode, enabled, expected)
        api = ReadOnly(base.API(enabled=False))
        base.account(api, expected)
        clock = api.call("GET", "/v2/clock"); now = base.stamp(clock["timestamp"])
        today = str(now.astimezone(base.NY).date())
        calendar = api.call("GET", "/v2/calendar", params={"start":str((now-timedelta(days=900)).date()), "end":str(now.date()+timedelta(days=20))})
        current = next((d for d in calendar if d["date"] == today), None)
        if current and now < base.session(current,"close")+timedelta(minutes=20):
            return dict(report, status="WAITING_FOR_COMPLETED_SESSION", read_only=True, orders_enabled=False)
        closed = [d for d in calendar if base.session(d,"close")+timedelta(minutes=20) <= now]
        if not closed: raise ValueError("No completed session")
        following = next((d for d in calendar if d["date"] > closed[-1]["date"]), None)
        if not following: raise ValueError("Next session missing")
        strategy = os.getenv("ALPACA_PAPER_STRATEGY", "compression_family")
        if strategy not in {"compression_family", "balanced_blend"}: raise ValueError("Unknown strategy")
        rows = base.scan(api, calendar, now, strategy)
        previews = []
        for i,r in enumerate(rows,1):
            tp = base.target(r["highs"],base.num(r["reference"]))
            previews.append(dict(rank=i, symbol=r["symbol"], reference_close=r["reference"], stop=r["stop"],
                reference_target=str(tp) if tp else None, history=r["history"], quality=r["quality"]))
        report.update(status="POST_CLOSE_SCAN_COMPLETED", read_only=True, orders_enabled=False,
            signal_session=closed[-1]["date"], next_trading_session=following["date"], candidates=previews,
            note="Close-based research only, not queued orders. Next-session quotes, cash, capacity and USD 15 planned-risk checks remain mandatory.")
        return report
    except Exception as exc:
        report.update(error_type=type(exc).__name__, error_code=stage.error_code(exc))
        return report
    finally:
        base.universe = original


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("check","scan","tick","post-close"), default="check")
    report = execute(parser.parse_args().mode)
    text = json.dumps(report, indent=2, allow_nan=False)
    out = Path("reports/paper"); out.mkdir(parents=True, exist_ok=True)
    (out/"status.json").write_text(text+"\n")
    print(text)
    if os.getenv("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f: f.write("# Paper pipeline\n\n```json\n"+text+"\n```\n")
    raise SystemExit(1 if report["status"] == "FAILED_CLOSED" else 0)
