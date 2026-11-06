from datetime import datetime, timezone
from decimal import Decimal as D, ROUND_CEILING, ROUND_FLOOR
import json
from pathlib import Path
from types import SimpleNamespace as NS
import sys
from zoneinfo import ZoneInfo
import pytest
import paper_entry_fix as fix

NOW = datetime(2026,11,5,15,tzinfo=timezone.utc)
NY = ZoneInfo("America/New_York")


def size(r,q,now,equity,cash,risk,cid):
    bid,ask,ref=D(str(q["bp"])),D(str(q["ap"])),D(str(r["reference"]))
    stop=D(str(r["stop"])).quantize(D(".01"),rounding=ROUND_FLOOR)
    if not 0 <= (now-datetime.fromisoformat(q["t"])).total_seconds() <= 10 or not 0<stop<bid<=ask: return None
    if (ask-bid)/ask>D(".005") or abs(ask/ref-1)>D(".02"): return None
    entry=(ask*D("1.0015")).quantize(D(".01"),rounding=ROUND_CEILING)
    target=next((D(str(h)) for h in r["highs"] if D(str(h))>entry),None)
    unit,cost=entry*D("1.0015")-stop*D(".9985"),entry*D("1.0015")
    if unit/entry>D(".06") or target is None or (target-entry)/(entry-stop)<D("1.5"): return None
    qty=min(risk/unit,cash/cost,equity*D(".25")/cost).to_integral_value(rounding=ROUND_FLOOR)
    if qty<1: return None
    body=dict(symbol=r["symbol"],qty=str(qty),limit_price=str(entry),stop_loss=dict(stop_price=str(stop)),take_profit=dict(limit_price=str(target)),client_order_id=cid)
    return body,qty*cost,qty*unit


def setup(rows, monkeypatch):
    monkeypatch.setattr(fix.time,"sleep",lambda _:None)
    monkeypatch.delenv("ALPACA_PAPER_KILL_SWITCH",raising=False)
    monkeypatch.setenv("ALPACA_PAPER_STRATEGY","compression_family")
    class API:
        enabled=True
        def __init__(self): self.orders=[];self.count=0;self.existing=set();self.clock=NOW;self.quote_overrides={};self.ambiguous=False
        def call(self,method,path,params=None,**kwargs):
            assert method=="GET"
            if path=="/v2/clock": return dict(timestamp=self.clock.isoformat(),is_open=True)
            if path=="/v2/calendar": return [dict(date="2026-11-05",open="09:30",close="16:00")]
            r=next(r for r in rows if r["symbol"]==params["symbols"])
            q=dict(bp=r["reference"]-.01,ap=r["reference"],t=self.clock.isoformat());q.update(self.quote_overrides.get(r["symbol"],{}))
            return dict(quotes={r["symbol"]:q})
        def lookup(self,cid): return {"id":"existing"} if cid in self.existing else None
        def submit(self,body):
            self.count+=1
            if self.ambiguous: raise TimeoutError("do not print sensitive broker text")
            self.orders.append(body);return dict(id="order"+str(self.count),status="accepted")
    api=API()
    def book(orders,opened,positions):
        cash=sum((D(o["qty"])*D(o["limit_price"])*D("1.0015") for o in orders),D(0))
        risk=sum((D(o["qty"])*(D(o["limit_price"])*D("1.0015")-D(o["stop_loss"]["stop_price"])*D(".9985")) for o in orders),D(0))
        return risk,cash,{o["symbol"] for o in orders}
    def window(c,calendar,entry=False):
        hour=datetime.fromisoformat(c["timestamp"]).astimezone(NY)
        return hour.hour<15 or hour.hour==15 and (not entry or hour.minute<30)
    base=NS(account=lambda *a:(D(2000),D(2000)),snapshot=lambda a:(a.orders,[],[]),book=book,
        manage=lambda *a:False,window=window,scan=lambda *a:rows,num=lambda v:D(str(v)),
        stamp=datetime.fromisoformat,NY=NY,PREFIX="cgt2k-",size=size,
        target=lambda hs,e:next((D(str(h)) for h in hs if D(str(h))>e),None))
    return base,api


def candidate(i,p=50,stop=49,target=54):
    return dict(symbol="TEST"+str(i),reference=p,stop=stop,highs=[target],history=dict(score=1),quality=1)


@pytest.fixture(autouse=True)
def isolated(tmp_path,monkeypatch): monkeypatch.chdir(tmp_path)


def test_fifth_ranked_candidate_is_actually_considered(monkeypatch):
    rows=[candidate(i,*prices) for i,prices in enumerate([(400,390,425),(450,440,475),(480,470,505),(600,590,625),(50,49,54)],1)]
    base,api=setup(rows,monkeypatch)
    report=fix.with_replacements(base,None)(api,"tick","expected")
    assert report["status"]=="PAPER_ENTRIES_SUBMITTED"
    assert [o["symbol"] for o in api.orders]==["TEST5"]
    assert D(report["submitted"][0]["planned_risk"])<=15
    assert len([r for r in report["entry_attempts"] if r["decision"]=="skip"])==4


def test_quote_rejection_is_logged_and_next_candidate_tried(monkeypatch):
    base,api=setup([candidate(1),candidate(2)],monkeypatch)
    api.quote_overrides["TEST1"]=dict(bp=52.99,ap=53)
    report=fix.with_replacements(base,None)(api,"tick","expected")
    assert report["entry_attempts"][0]["reason"]=="PRICE_DRIFT_ABOVE_2_PERCENT"
    assert api.orders[0]["symbol"]=="TEST2"
    assert "PRICE_DRIFT_ABOVE_2_PERCENT" in Path("reports/paper/entry_attempts.md").read_text()


def test_total_risk_and_capacity_stay_bounded(monkeypatch):
    base,api=setup([candidate(i) for i in range(20)],monkeypatch)
    fix.with_replacements(base,None)(api,"tick","expected")
    risk,cash,occupied=base.book(api.orders,[],[])
    assert risk<=15 and cash<=2000 and len(occupied)<=4


def test_kill_switch_still_blocks_entries(monkeypatch):
    base,api=setup([candidate(1)],monkeypatch);monkeypatch.setenv("ALPACA_PAPER_KILL_SWITCH","true")
    assert fix.with_replacements(base,None)(api,"tick","expected")["status"]=="NEW_ENTRIES_DISABLED"
    assert not api.orders


def test_readonly_modes_delegate_without_entering_tick_loop():
    calls=[]
    run=fix.with_replacements(None,lambda *a:calls.append(a) or "read-only")
    assert run(None,"scan","expected")=="read-only" and calls[0][1]=="scan"


def test_late_session_gate_is_unchanged(monkeypatch):
    base,api=setup([candidate(1)],monkeypatch);api.clock=datetime(2026,11,5,20,45,tzinfo=timezone.utc)
    assert fix.with_replacements(base,None)(api,"tick","expected")["status"]=="LATE_SESSION_NO_ENTRIES"
    assert not api.orders


def test_ambiguous_submission_stops_and_is_not_retried(monkeypatch):
    base,api=setup([candidate(1),candidate(2)],monkeypatch);api.ambiguous=True
    with pytest.raises(TimeoutError): fix.with_replacements(base,None)(api,"tick","expected")
    assert api.count==1
    text=Path("reports/paper/entry_attempts.json").read_text()
    assert "SUBMISSION_OUTCOME_UNKNOWN" in text and "sensitive broker" not in text


def test_disabled_api_still_blocks_entries(monkeypatch):
    base,api=setup([candidate(1)],monkeypatch);api.enabled=False
    assert fix.with_replacements(base,None)(api,"tick","expected")["status"]=="NEW_ENTRIES_DISABLED"
    assert not api.orders


def test_postclose_audit_is_installed_and_functions_restored(monkeypatch):
    import paper_pipeline as pipeline
    original=lambda api:[]; original_run=lambda *a:None; seen=[]
    class API:
        def __init__(self,enabled): assert enabled is False
        def call(self,method,path,**kw):
            assert method=="GET"
            return dict(timestamp="2026-11-05T22:00:00+00:00") if path=="/v2/clock" else [dict(date="2026-11-05",close="16:00"),dict(date="2026-11-06",close="16:00")]
    def audited(base,scan,trace):
        def wrapped(*args):
            seen.append(True);trace["scan_audit"]=dict(status="SCAN_COMPLETED",reasons={"NO_LATEST_DAILY_SIGNAL":1});return []
        return wrapped
    base=NS(API=API,universe=original,run=original_run,account=lambda *a:None,stamp=datetime.fromisoformat,NY=NY,
        scan=lambda *a:[],session=lambda d,k:datetime.fromisoformat(d["date"]+"T"+d[k]).replace(tzinfo=NY))
    modules=dict(paper_execution=base,paper_stage_report=NS(error_code=lambda e:type(e).__name__),
        paper_universe_fallback=NS(with_fallback=lambda f:f),paper_watchlist=NS(with_watchlist=lambda f:f),paper_scan_audit=NS(with_scan_audit=audited))
    for name,module in modules.items(): monkeypatch.setitem(sys.modules,name,module)
    monkeypatch.setenv("ALPACA_PAPER_STRATEGY","compression_family")
    result=pipeline.execute("post-close")
    assert result["status"]=="POST_CLOSE_SCAN_COMPLETED" and seen
    assert result["diagnostics"]["scan_audit"]["status"]=="SCAN_COMPLETED"
    assert base.universe is original and base.run is original_run
    assert "NO_LATEST_DAILY_SIGNAL" in Path("reports/paper/scan_audit.md").read_text()


def test_invalid_quote_failure_is_logged_without_weakening_checks(monkeypatch):
    base,api=setup([candidate(1)],monkeypatch)
    api.quote_overrides["TEST1"]=dict(t="invalid timestamp")
    with pytest.raises(ValueError): fix.with_replacements(base,None)(api,"tick","expected")
    assert api.count==0 and "SIZE_VALIDATION_FAILED" in Path("reports/paper/entry_attempts.json").read_text()
