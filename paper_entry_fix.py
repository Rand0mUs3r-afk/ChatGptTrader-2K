"""Try replacement ranked entries without changing broker, quote or risk gates."""
from datetime import timedelta
from decimal import Decimal as D, ROUND_CEILING, ROUND_FLOOR
from functools import wraps
import html
import json
import os
from pathlib import Path
import time


def emit(attempts):
    out = Path("reports/paper"); out.mkdir(parents=True, exist_ok=True)
    temp = out/"entry_attempts.tmp"
    temp.write_text(json.dumps(attempts, indent=2, allow_nan=False)+"\n")
    temp.replace(out/"entry_attempts.json")
    def cell(v): return html.escape(str(v)[:160]).replace("|", "&#124;").replace("\n", " ")
    text = "# Entry evaluation audit\n\nAll existing price, risk and session gates remain mandatory. Submission is not a fill.\n\n"
    text += "| Rank | Ticker | Decision | Reason |\n|---|---|---|---|\n"
    for r in attempts[:50]:
        text += "| "+" | ".join(cell(r.get(k,"—")) for k in ("rank","symbol","decision","reason"))+" |\n"
    if not attempts: text += "\nNo entry evaluations recorded; consult the main run status.\n"
    if len(attempts)>50: text += "\nFirst 50 shown; full results are in entry_attempts.json.\n"
    (out/"entry_attempts.md").write_text(text)


def scan_summary(trace):
    a = trace.get("scan_audit")
    if not a: return
    out = Path("reports/paper"); out.mkdir(parents=True, exist_ok=True)
    text = "# Scanner rejection audit\n\nStatus: "+html.escape(str(a.get("status","UNKNOWN")))+".\n\n| Reason | Symbols |\n|---|---:|\n"
    for reason,count in sorted((a.get("reasons") or {}).items()):
        text += "| "+html.escape(str(reason)).replace("|","&#124;")+" | "+str(int(count))+" |\n"
    text += "\nFull per-symbol details are in scan_audit.json and scan_symbol_diagnostics.csv.\n"
    (out/"scan_audit.md").write_text(text)


def size_reason(base, row, quote, now, equity, cash, risk, cid):
    sized = base.size(row, quote, now, equity, cash, risk, cid)
    if sized: return sized, None
    bid,ask,ref = base.num(quote["bp"]),base.num(quote["ap"]),base.num(row["reference"])
    stop = base.num(row["stop"]).quantize(D(".01"), rounding=ROUND_FLOOR)
    age = (now-base.stamp(quote["t"])).total_seconds()
    if not 0 <= age <= 10: return None, "STALE_OR_FUTURE_QUOTE"
    if not 0 < bid <= ask or ask < 1 or ref <= 0: return None, "INVALID_QUOTE_OR_REFERENCE"
    if not 0 < stop < bid: return None, "STOP_NOT_BELOW_BID"
    if (ask-bid)/ask > D(".005"): return None, "SPREAD_TOO_WIDE"
    if abs(ask/ref-1) > D(".02"): return None, "PRICE_DRIFT_ABOVE_2_PERCENT"
    entry = (ask*D("1.0015")).quantize(D(".01"), rounding=ROUND_CEILING)
    unit,cost = entry*D("1.0015")-stop*D(".9985"),entry*D("1.0015")
    target = base.target(row["highs"], entry)
    if unit/entry > D(".06"): return None, "POSITION_RISK_ABOVE_6_PERCENT"
    if target is None: return None, "NO_PRIOR_HIGH_TARGET"
    if (target-entry)/(entry-stop) < D("1.5"): return None, "REWARD_RISK_BELOW_1_5"
    if equity*D(".25") < cost: return None, "POSITION_CAP_CANNOT_FIT_ONE_SHARE"
    if cash < cost: return None, "CASH_CANNOT_FIT_ONE_SHARE"
    if risk < unit: return None, "WEIGHTED_RISK_CANNOT_FIT_ONE_SHARE"
    return None, "SIZE_REJECTED"


def with_replacements(base, original):
    @wraps(original)
    def run(api, mode, expected):
        if mode != "tick": return original(api, mode, expected)
        attempts = []
        emit(attempts)
        def record(rank, symbol, decision, reason, **extra):
            attempts.append(dict(rank=rank,symbol=symbol,decision=decision,reason=reason,**extra))
            emit(attempts)
        equity,cash = base.account(api,expected); roots,opened,positions = base.snapshot(api)
        if not roots and (positions or opened or abs(equity-2000)>D(".01") or abs(cash-2000)>D(".01")):
            raise RuntimeError("First run requires empty USD 2000 paper account")
        report = dict(paper_only=True,mode=mode,equity=str(equity),submitted=[],entry_attempts=attempts)
        clock = api.call("GET","/v2/clock"); now = base.stamp(clock["timestamp"])
        calendar = api.call("GET","/v2/calendar",params={"start":str((now-timedelta(days=900)).date()),"end":str(now.astimezone(base.NY).date())})
        if base.manage(api,clock,calendar): report["status"]="BOOK_MANAGED_RECHECK_NEXT_RUN"; return report
        risk,pending,occupied = base.book(*base.snapshot(api)); report["planned_risk"]=str(risk)
        if not base.window(clock,calendar): report["status"]="MARKET_CLOSED"; return report
        if not api.enabled or os.getenv("ALPACA_PAPER_KILL_SWITCH","false").lower()=="true": report["status"]="NEW_ENTRIES_DISABLED"; return report
        if not base.window(clock,calendar,entry=True): report["status"]="LATE_SESSION_NO_ENTRIES"; return report
        strategy=os.getenv("ALPACA_PAPER_STRATEGY","compression_family")
        if strategy not in {"compression_family","balanced_blend"}: raise RuntimeError("Unknown strategy")
        if len(occupied)>=4: report["status"]="POSITION_CAPACITY"; return report
        ranked=base.scan(api,calendar,now,strategy)
        report["candidates"]=[{k:v for k,v in r.items() if k!="highs"} for r in ranked]
        candidates=[(i,r) for i,r in enumerate(ranked,1) if r["symbol"] not in occupied]
        weights=[max(D(".01"),base.num(r["history"]["score"]))*max(D(".1"),base.num(r["quality"])) for _,r in candidates]
        reserved_cash,reserved_risk,reserved=D(0),D(0),set()
        for index,(rank,row) in enumerate(candidates):
            equity,cash=base.account(api,expected);risk,pending,occupied=base.book(*base.snapshot(api))
            slots=max(0,4-len(occupied|reserved))
            if not slots: break
            symbol=row["symbol"]
            if symbol in occupied|reserved:
                record(rank,symbol,"skip","ALREADY_OCCUPIED");continue
            room=max(D(0),D(15)-risk-reserved_risk)
            available=max(D(0),cash-pending-reserved_cash)
            if room<=0 or available<=0:
                record(rank,symbol,"skip","CASH_OR_RISK_BUDGET_EXHAUSTED");break
            clock=api.call("GET","/v2/clock")
            if not base.window(clock,calendar,entry=True):
                record(rank,symbol,"skip","ENTRY_WINDOW_CLOSED");break
            cid=base.PREFIX+base.stamp(clock["timestamp"]).astimezone(base.NY).strftime("%Y%m%d")+"-"+symbol.replace(".","_")
            if api.lookup(cid):
                record(rank,symbol,"skip","CLIENT_ORDER_ID_ALREADY_EXISTS");continue
            quote=api.call("GET","/v2/stocks/quotes/latest",params={"symbols":symbol,"feed":os.getenv("ALPACA_DATA_FEED","iex")},data=True).get("quotes",{}).get(symbol)
            clock=api.call("GET","/v2/clock")
            if not base.window(clock,calendar,entry=True):
                record(rank,symbol,"skip","ENTRY_WINDOW_CLOSED");break
            if not quote:
                record(rank,symbol,"skip","NO_QUOTE_RETURNED");continue
            # Rolling window preserves weighted allocation across remaining available slots.
            # Rejected candidates do not permanently consume a shortlist slot.
            allowance=room*weights[index]/sum(weights[index:index+slots])
            try:
                sized,reason=size_reason(base,row,quote,base.stamp(clock["timestamp"]),equity,available,allowance,cid)
            except Exception as exc:
                record(rank,symbol,"failed","SIZE_VALIDATION_FAILED",error_type=type(exc).__name__)
                raise
            if not sized:
                record(rank,symbol,"skip",reason);continue
            body,cost,planned=sized
            reserved_cash+=cost;reserved_risk+=planned;reserved.add(symbol)
            record(rank,symbol,"submission_requested","PAPER_BRACKET_REQUEST",client_order_id=cid,planned_risk=str(planned))
            try:
                order=api.submit(body)
            except Exception:
                record(rank,symbol,"unknown","SUBMISSION_OUTCOME_UNKNOWN",client_order_id=cid)
                raise
            if not order.get("id") or order.get("status") in {"rejected","canceled","expired"}:
                record(rank,symbol,"not_accepted","ENTRY_NOT_ACCEPTED",client_order_id=cid)
                raise RuntimeError("Entry not accepted")
            report["submitted"].append(dict(order_id=order["id"],client_order_id=cid,body=body,planned_risk=str(planned)))
            record(rank,symbol,"submitted","PAPER_ORDER_RECORDED_NOT_FILL_CONFIRMED",order_id=order["id"],planned_risk=str(planned))
            time.sleep(2)
            if base.manage(api,api.call("GET","/v2/clock"),calendar): report["post_submit_management"]=True;break
        report["status"]="PAPER_ENTRIES_SUBMITTED" if report["submitted"] else "NO_QUALIFYING_EXECUTABLE_SETUP"
        return report
    return run
