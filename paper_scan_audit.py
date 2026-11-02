"""Observe the existing scanner without changing rankings, gates or broker requests."""
from collections import Counter
import csv
from datetime import timedelta
from functools import wraps
import inspect
import json
import os
from pathlib import Path
import re


def write_reports(summary,rows):
    folder=Path('reports/paper');folder.mkdir(parents=True,exist_ok=True)
    (folder/'scan_audit.json').write_text(json.dumps({'summary':summary,'symbols':rows},indent=2,allow_nan=False)+'\n')
    fields=['symbol','reason','bars','expected_rows','first_date','last_date','latest_signal','samples','score']
    with (folder/'scan_symbol_diagnostics.csv').open('w',newline='') as out:
        writer=csv.DictWriter(out,fieldnames=fields);writer.writeheader();writer.writerows(rows)


def with_scan_audit(base,scan,trace):
    @wraps(scan)
    def wrapped(api,calendar,now,mode):
        state={'symbols':None,'bars':{},'features':{},'history':{},'pages':0,'errors':[]}
        completed=False;ranked=[]
        try: code=getattr(inspect.unwrap(scan),'__code__',None)
        except Exception: code=None
        def observe(fn,*args):
            try: fn(*args)
            except Exception as exc:
                if len(state['errors'])<10: state['errors'].append(type(exc).__name__)
        def symbol():
            frame=inspect.currentframe().f_back
            try:
                while frame is not None:
                    if frame.f_code is code:
                        value=frame.f_locals.get('s')
                        return value if isinstance(value,str) and re.fullmatch(r'[A-Z][A-Z0-9.-]{0,19}',value) else None
                    frame=frame.f_back
            finally: del frame
        originals={name:getattr(base,name,None) for name in ('universe','features','history_stats')}
        call=api.call;had_call='call' in getattr(api,'__dict__',{});prior_call=getattr(api,'__dict__',{}).get('call')
        def universe(*args,**kwargs):
            result=originals['universe'](*args,**kwargs)
            if isinstance(result,(list,tuple,set)): observe(lambda:state.update(symbols=sorted(set(result))))
            return result
        def features(*args,**kwargs):
            result=originals['features'](*args,**kwargs);ticker=symbol()
            if ticker: observe(lambda:state['features'].update({ticker:result[-1]['stop'] is not None}))
            return result
        def history(*args,**kwargs):
            result=originals['history_stats'](*args,**kwargs);ticker=symbol()
            if ticker: observe(lambda:state['history'].update({ticker:{'samples':int(result['samples']),'score':float(result['score'])}}))
            return result
        def collect(page):
            state['pages']+=1
            for ticker,rows in page.get('bars',{}).items():
                bucket=state['bars'].setdefault(ticker,{'count':0,'dates':[],'invalid_dates':False})
                bucket['count']+=len(rows)
                for row in rows:
                    try: bucket['dates'].append(str(base.stamp(row['t']).astimezone(base.NY).date()))
                    except Exception: bucket['invalid_dates']=True
        def tracked_call(method,path,*args,**kwargs):
            result=call(method,path,*args,**kwargs)
            if method=='GET' and path=='/v2/stocks/bars' and kwargs.get('data',False): observe(collect,result)
            return result
        try:
            for name,fn in (('universe',universe),('features',features),('history_stats',history)):
                if callable(originals[name]): setattr(base,name,fn)
            api.call=tracked_call
            ranked=scan(api,calendar,now,mode);completed=True
            return ranked
        finally:
            for name,fn in originals.items():
                if fn is not None: setattr(base,name,fn)
            if had_call: api.call=prior_call
            elif 'call' in getattr(api,'__dict__',{}): del api.call
            try:
                closed=[d['date'] for d in calendar if base.session(d,'close')+timedelta(minutes=20)<=now]
                passed={row['symbol'] for row in ranked}
                symbols=state['symbols'] if state['symbols'] is not None else sorted(set(state['bars'])|set(state['features'])|passed)
                rows=[]
                for ticker in symbols:
                    data=state['bars'].get(ticker,{'count':0,'dates':[],'invalid_dates':False})
                    dates=sorted(data['dates']);expected=[d for d in closed if dates and d>=dates[0]]
                    signal=state['features'].get(ticker);stats=state['history'].get(ticker,{})
                    if not completed: reason='SCAN_ABORTED'
                    elif ticker in passed: reason='PASSED_SCANNER'
                    elif state['errors']: reason='UNCLASSIFIED_OBSERVATION_ERROR'
                    elif state['pages']==0 and signal is None: reason='UNCLASSIFIED_NO_BAR_OBSERVATION'
                    elif data['count']==0: reason='NO_DAILY_BARS'
                    elif data['count']<100: reason='INSUFFICIENT_DAILY_BARS'
                    elif signal is None and data['invalid_dates']: reason='UNCLASSIFIED_TIMESTAMP'
                    elif signal is None and dates!=expected: reason='CALENDAR_COVERAGE_MISMATCH'
                    elif signal is False: reason='NO_LATEST_DAILY_SIGNAL'
                    elif stats.get('samples',20)<20: reason='INSUFFICIENT_HISTORICAL_SAMPLES'
                    elif stats and stats['score']<=0: reason='NONPOSITIVE_HISTORY_SCORE'
                    else: reason='UNCLASSIFIED'
                    rows.append(dict(symbol=ticker,reason=reason,bars=data['count'],expected_rows=len(expected),first_date=dates[0] if dates else None,last_date=dates[-1] if dates else None,latest_signal=signal,samples=stats.get('samples'),score=stats.get('score')))
                feed=os.getenv('ALPACA_DATA_FEED','iex')
                summary=dict(status=('AUDIT_PARTIAL' if state['errors'] else 'SCAN_COMPLETED') if completed else 'SCAN_ABORTED',universe_observed=state['symbols'] is not None,unique_scan_symbols=len(symbols),bar_pages=state['pages'],strategy=mode if mode in ('compression_family','balanced_blend') else 'UNKNOWN',feed=feed if feed in ('iex','sip') else 'UNKNOWN',last_completed_session=closed[-1] if closed else None,ranked_candidates=len(passed),reasons=dict(Counter(row['reason'] for row in rows)),observation_errors=state['errors'],entry_rules_changed=False)
                write_reports(summary,rows)
                trace['scan_audit']=summary
            except Exception as exc:
                trace['scan_audit']={'status':'AUDIT_UNAVAILABLE','error_type':type(exc).__name__,'entry_rules_changed':False}
    return wrapped
