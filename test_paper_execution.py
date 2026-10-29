from datetime import timedelta
from decimal import Decimal as D
import pytest
import paper_execution as p

NOW = p.stamp("2026-10-28T14:30:00Z")
DAY = [dict(date="2026-10-28",open="09:30",close="16:00")]
ROW = dict(symbol="TEST",reference=100,stop=98,highs=[105],quality=.8,history=dict(score=.5,lower=.3,samples=30))
Q = dict(bp=99.99,ap=100,t=NOW.isoformat())


def test_cash_risk_and_gtc_bracket():
    body,cash,risk = p.size(ROW,Q,NOW,D(2000),D(2000),D(15),"id")
    assert body["qty"] == "4" and cash <= 500 and risk <= 15
    assert body["time_in_force"] == "gtc" and body["order_class"] == "bracket"
    assert body["take_profit"]["limit_price"] == "105.00"


@pytest.mark.parametrize("q",[dict(Q,bp=0),dict(Q,ap=110),dict(Q,bp=101),dict(Q,t=(NOW-timedelta(seconds=11)).isoformat())])
def test_invalid_quote(q):
    assert p.size(ROW,q,NOW,D(2000),D(2000),D(15),"id") is None


def test_hours_and_cutoff():
    assert p.window(dict(timestamp=NOW.isoformat(),is_open=True),DAY)
    assert not p.window(dict(timestamp=NOW.isoformat(),is_open=True),[])
    assert not p.window(dict(timestamp="2026-10-28T19:30:00Z",is_open=True),DAY,entry=True)


def test_account_identity_and_no_leverage():
    class Fake:
        def call(self,*args,**kw):
            return dict(id="expected",status="ACTIVE",currency="USD",equity="2000",cash="2000",buying_power="4000",non_marginable_buying_power="2000",trading_blocked=False,transfers_blocked=False,account_blocked=False)
    f = Fake()
    assert p.account(f,"expected") == (D(2000),D(2000))
    with pytest.raises(RuntimeError): p.account(f,"wrong")


def test_pending_and_partial_fill_risk():
    r = dict(id="r",symbol="TEST",client_order_id="cgt2k-test",side="buy",status="partially_filled",order_class="bracket",qty="2",filled_qty="1",limit_price="100",legs=[dict(id="s",side="sell",type="stop",status="new",qty="2",filled_qty="0",stop_price="98")])
    position = dict(symbol="TEST",side="long",qty="1",avg_entry_price="100")
    risk,cash,occupied = p.book([r],[],[position])
    assert risk == D("4.594") and cash == D("100.1500") and occupied == {"TEST"}
    r["legs"][0]["status"] = "held"
    with pytest.raises(RuntimeError): p.book([r],[],[position])


def test_ambiguous_submit_not_retried():
    api = object.__new__(p.API); api.lookup = lambda cid: None; calls = []
    def fail(*args,**kw): calls.append(args); raise TimeoutError()
    api.call = fail
    with pytest.raises(TimeoutError): api.submit({"client_order_id":"same"})
    assert len(calls) == 1


def test_history_and_confirmed_high():
    import pandas as pd
    assert p.highs(pd.DataFrame({"High":[1,2,5,2,1,3,6]})) == [5]
    assert p.history_stats([1]*19,[True]*19)["score"] == 0
    assert p.history_stats([1]*20,[True]*20)["score"] == .5


def test_signal_parity_inside_breakout():
    import pandas as pd
    f = pd.DataFrame(dict(Open=[8]*5,High=[11,12,11,10,11],Low=[6,5,6,7,7],Close=[9,9,9,9,10.5],Volume=[100]*5))
    assert p.features(f,"compression_family")[-1]["stop"] == pytest.approx(5.6)
