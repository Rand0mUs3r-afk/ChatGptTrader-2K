from copy import deepcopy
import pytest
import paper_execution as base
from paper_group_cancel import GroupCancelAPI, with_group_cancels

CLOCK=dict(timestamp="2026-11-05T20:40:00Z",is_open=True)
CALENDAR=[dict(date=d,open="09:30",close="16:00") for d in ["2026-10-28","2026-10-29","2026-10-30","2026-11-02","2026-11-03","2026-11-04","2026-11-05"]]


def bracket(root="root1", symbol="TEST"):
    return dict(id=root,client_order_id=base.PREFIX+root,symbol=symbol,side="buy",order_class="bracket",status="filled",qty="2",filled_qty="2",filled_at="2026-10-28T15:00:00Z",submitted_at="2026-10-28T15:00:00Z",legs=[
        dict(id=root+"-stop",symbol=symbol,side="sell",type="stop",status="new",qty="2",filled_qty="0",stop_price="98"),
        dict(id=root+"-target",symbol=symbol,side="sell",type="limit",status="new",qty="2",filled_qty="0",limit_price="105")])


class Broker:
    enabled=True
    def __init__(self):
        self.roots=[bracket()];self.opened=list(self.roots[0]["legs"])
        self.position=dict(symbol="TEST",side="long",qty="2",avg_entry_price="100")
        self.deletes=[];self.submitted=[];self.complete=True;self.fail=False
    def call(self,method,path,params=None,**kwargs):
        if method=="GET" and path=="/v2/orders": return self.opened if params["status"]=="open" else self.roots
        if method=="GET" and path=="/v2/positions": return [self.position]
        if method=="GET" and path=="/v2/positions/TEST": return self.position
        if method=="DELETE":
            oid=path.rsplit("/",1)[-1];self.deletes.append(oid)
            if self.fail: raise RuntimeError("Broker HTTP 403; response withheld")
            group=next((r for r in self.roots if oid in {r["id"]}|{x["id"] for x in r["legs"]}),None)
            if not group or not any(o["id"]==oid for o in self.opened): raise RuntimeError("Broker HTTP 422; response withheld")
            if self.complete:
                ids={group["id"]}|{l["id"] for l in group["legs"]}
                self.opened=[o for o in self.opened if o["id"] not in ids]
                for l in group["legs"]: l["status"]="canceled"
            return None
        raise AssertionError((method,path))
    def lookup(self,cid): return None
    def submit(self,body): self.submitted.append(body);return dict(id="exit1",status="accepted")


def test_original_manager_reproduces_group_cancel_failure():
    api=Broker()
    with pytest.raises(RuntimeError,match="422"): base.manage(api,CLOCK,CALENDAR)
    assert not api.opened and not api.submitted and api.position["qty"]=="2"


def test_group_aware_manager_cancels_once_and_exits_fresh_qty():
    api=Broker()
    assert with_group_cancels(base.manage,base.PREFIX)(api,CLOCK,CALENDAR)
    assert api.deletes==["root1-stop"]
    assert len(api.submitted)==1 and api.submitted[0]["side"]=="sell" and api.submitted[0]["qty"]=="2"


def test_unconfirmed_group_cancel_never_sends_competing_exit(monkeypatch):
    monkeypatch.setattr(base.time,"sleep",lambda _:None)
    api=Broker();api.complete=False
    with pytest.raises(RuntimeError,match="Cancellation not confirmed"):
        with_group_cancels(base.manage,base.PREFIX)(api,CLOCK,CALENDAR)
    assert api.deletes==["root1-stop"] and not api.submitted


def test_real_cancel_failure_is_not_ignored():
    api=Broker();api.fail=True
    with pytest.raises(RuntimeError,match="403"):
        with_group_cancels(base.manage,base.PREFIX)(api,CLOCK,CALENDAR)
    assert not api.submitted


def test_readonly_remains_readonly():
    api=Broker();api.enabled=False
    assert not with_group_cancels(base.manage,base.PREFIX)(api,CLOCK,CALENDAR)
    assert not api.deletes and not api.submitted


def test_separate_groups_are_each_canceled_once():
    api=Broker();api.roots.append(bracket("root2","OTHER"));api.opened.extend(api.roots[1]["legs"])
    proxy=GroupCancelAPI(api,base.PREFIX)
    proxy.call("GET","/v2/orders",params={"status":"all"})
    for oid in ("root1-stop","root1-target","root2-stop","root2-target"):
        proxy.call("DELETE","/v2/orders/"+oid)
    assert api.deletes==["root1-stop","root2-stop"]


def test_pipeline_installs_group_cancels_and_restores_originals(monkeypatch):
    import sys
    from types import SimpleNamespace as NS
    import paper_pipeline as pipeline
    api=Broker(); original_manage=base.manage
    facade=NS(PREFIX=base.PREFIX,manage=original_manage,universe=lambda a:[])
    def original_run(client,mode,expected):
        assert mode=="tick"
        return dict(status="BOOK_MANAGED_RECHECK_NEXT_RUN",managed=facade.manage(client,CLOCK,CALENDAR))
    facade.run=original_run
    stage=NS(execute=lambda mode,enabled,expected:facade.run(api,mode,expected),error_code=lambda e:type(e).__name__)
    modules={"paper_execution":facade,"paper_stage_report":stage,
        "paper_universe_fallback":NS(with_fallback=lambda f:f),"paper_watchlist":NS(with_watchlist=lambda f:f),
        "paper_entry_fix":NS(with_replacements=lambda b,f:f,scan_summary=lambda t:None),
        "paper_scan_audit":NS(with_scan_audit=lambda *a:None)}
    for name,module in modules.items(): monkeypatch.setitem(sys.modules,name,module)
    monkeypatch.setenv("ALPACA_PAPER_ENABLED","true")
    report=pipeline.execute("tick")
    assert report["status"]=="BOOK_MANAGED_RECHECK_NEXT_RUN" and report["managed"]
    assert len(api.submitted)==1 and api.deletes==["root1-stop"]
    assert facade.run is original_run and facade.manage is original_manage
