from datetime import datetime,timedelta,timezone
from types import SimpleNamespace as NS
import json
import pytest
import paper_scan_audit as audit


def fixture():
    calendar=[{'date':str((datetime(2025,1,1)+timedelta(days=i)).date()),'close':'21:00'} for i in range(200)]
    symbols=['NOBARS','SHORT','GAP','NOSIGNAL','FEW','NEG','PASS']
    bars={s:[{'t':d['date']+'T05:00:00Z','symbol':s} for d in calendar] for s in symbols}
    bars['NOBARS']=[];bars['SHORT']=bars['SHORT'][-99:];bars['GAP']=bars['GAP'][::2]
    base=NS(NY=timezone.utc,stamp=lambda s:datetime.fromisoformat(s.replace('Z','+00:00')),session=lambda d,k:datetime.fromisoformat(d['date']+'T'+d[k]).replace(tzinfo=timezone.utc))
    base.universe=lambda api:list(symbols)
    base.features=lambda rows,mode:[{'stop':None if rows[0]['symbol']=='NOSIGNAL' else 1}]
    base.history_stats=lambda names,hits:{'samples':19 if names[0]=='FEW' else 20,'score':0 if names[0]=='NEG' else 1}
    def scan(api,calendar,now,mode):
        expected=[d['date'] for d in calendar]
        names=base.universe(api);data=api.call('GET','/v2/stocks/bars',data=True)['bars'];ranked=[]
        for s in names:
            rows=data.get(s,[])
            if not rows: continue
            dates=[r['t'][:10] for r in rows]
            if len(dates)<100 or dates!=[d for d in expected if d>=dates[0]]: continue
            signals=base.features(rows,mode)
            if signals[-1]['stop'] is None: continue
            h=base.history_stats([s],[])
            if h['samples']>=20 and h['score']>0: ranked.append({'symbol':s})
        return ranked
    calls=[]
    class API:
        def __init__(self,enabled=False): self.enabled=enabled
        def call(self,method,path,**kwargs):
            calls.append((method,path));assert method=='GET';return {'bars':bars}
    return base,scan,API(),calendar,datetime(2026,1,1,tzinfo=timezone.utc),calls


def test_unchanged_candidates_requests_and_gate_counts(tmp_path,monkeypatch):
    monkeypatch.chdir(tmp_path);base,scan,api,calendar,now,calls=fixture()
    expected=scan(api,calendar,now,'compression_family');calls.clear()
    originals=(base.universe,base.features,base.history_stats)
    trace={};actual=audit.with_scan_audit(base,scan,trace)(api,calendar,now,'compression_family')
    assert actual==expected==[{'symbol':'PASS'}]
    assert calls==[('GET','/v2/stocks/bars')]
    assert (base.universe,base.features,base.history_stats)==originals and 'call' not in api.__dict__
    reasons=trace['scan_audit']['reasons']
    for reason in ('NO_DAILY_BARS','INSUFFICIENT_DAILY_BARS','CALENDAR_COVERAGE_MISMATCH','NO_LATEST_DAILY_SIGNAL','INSUFFICIENT_HISTORICAL_SAMPLES','NONPOSITIVE_HISTORY_SCORE','PASSED_SCANNER'):
        assert reasons[reason]==1
    report=json.loads((tmp_path/'reports/paper/scan_audit.json').read_text())
    assert report['summary']['unique_scan_symbols']==7
    assert (tmp_path/'reports/paper/scan_symbol_diagnostics.csv').exists()


def test_reporting_failure_does_not_block_original_result(tmp_path,monkeypatch):
    monkeypatch.chdir(tmp_path);base,scan,api,calendar,now,calls=fixture()
    def fail(*args): raise PermissionError('sensitive details must not be logged')
    monkeypatch.setattr(audit,'write_reports',fail);trace={}
    assert audit.with_scan_audit(base,scan,trace)(api,calendar,now,'compression_family')==[{'symbol':'PASS'}]
    assert trace['scan_audit']['status']=='AUDIT_UNAVAILABLE'
    assert 'sensitive' not in json.dumps(trace)


def test_original_scanner_exception_is_not_suppressed(tmp_path,monkeypatch):
    monkeypatch.chdir(tmp_path);base,scan,api,calendar,now,calls=fixture()
    original=base.universe
    def fail(*args): raise RuntimeError('Original scanner failure')
    trace={}
    with pytest.raises(RuntimeError,match='Original scanner failure'):
        audit.with_scan_audit(base,fail,trace)(api,calendar,now,'compression_family')
    assert base.universe is original and 'call' not in api.__dict__
    assert trace['scan_audit']['status']=='SCAN_ABORTED'


def test_stage_report_integration_is_read_only(tmp_path,monkeypatch):
    import sys
    import paper_stage_report as stage
    monkeypatch.chdir(tmp_path);base,scan,api,calendar,now,calls=fixture()
    base.API=type(api);base.scan=scan
    for name in ('account','snapshot','manage','size'): setattr(base,name,lambda *a:None)
    def run(client,mode,expected):
        assert client.enabled is False
        rows=base.scan(client,calendar,now,'compression_family')
        return {'status':'READ_ONLY_SCAN','candidates':rows}
    base.run=run
    monkeypatch.setitem(sys.modules,'paper_execution',base)
    report=stage.execute('scan',False,'expected')
    assert report['candidates']==[{'symbol':'PASS'}]
    assert report['diagnostics']['writes_enabled'] is False
    assert report['diagnostics']['scan_audit']['ranked_candidates']==1
    assert report['diagnostics']['calls']=={'scan':1,'universe':1}
    assert base.scan is scan and calls==[('GET','/v2/stocks/bars')]
