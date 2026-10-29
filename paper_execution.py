"""Alpaca US PAPER coordinator. No live endpoint or override. Dedicated account only."""
import argparse
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D, ROUND_FLOOR, ROUND_CEILING
import hashlib
import json
import math
import os
from pathlib import Path
import re
import time
from zoneinfo import ZoneInfo

PREFIX = "cgt2k-"
NY = ZoneInfo("America/New_York")
TERMINAL = {"filled", "canceled", "expired", "rejected", "replaced"}
ACTIVE_STOP = {"new", "accepted", "pending_new", "accepted_for_bidding", "partially_filled"}


def num(v):
    n = D(str(v))
    if not n.is_finite(): raise ValueError("Nonfinite amount")
    return n


def stamp(v):
    t = datetime.fromisoformat(v.replace("Z", "+00:00"))
    if t.tzinfo is None: raise ValueError("Naive time")
    return t.astimezone(timezone.utc)


def session(day, key):
    return datetime.fromisoformat(day["date"]+"T"+day[key]).replace(tzinfo=NY)


def window(clock, calendar, entry=False):
    now = stamp(clock["timestamp"])
    day = next((d for d in calendar if d["date"] == str(now.astimezone(NY).date())), None)
    return bool(clock["is_open"] and day and session(day,"open") <= now < session(day,"close")-(timedelta(minutes=30) if entry else timedelta(0)))


class API:
    def __init__(self, enabled=False):
        import requests
        self.http = requests.Session()
        self.enabled, self.verified = enabled, False
        key, secret = os.getenv("ALPACA_PAPER_KEY_ID"), os.getenv("ALPACA_PAPER_SECRET_KEY")
        if not key or not secret: raise RuntimeError("Missing paper secrets")
        self.headers = {"APCA-API-KEY-ID":key, "APCA-API-SECRET-KEY":secret}

    def call(self, method, path, params=None, body=None, data=False, missing=False):
        if not path.startswith("/v2/") or ".." in path or "?" in path: raise ValueError("Invalid path")
        write = method == "POST" and path == "/v2/orders" or method == "DELETE" and re.fullmatch(r"/v2/orders/[A-Za-z0-9-]+",path)
        if method != "GET" and (not write or data or not self.enabled or not self.verified): raise RuntimeError("Write gate closed")
        base = "https://data.alpaca.markets" if data else "https://paper-api.alpaca.markets"
        r = self.http.request(method,base+path,params=params,json=body,headers=self.headers,timeout=25,allow_redirects=False)
        if missing and r.status_code == 404: return None
        if not 200 <= r.status_code < 300: raise RuntimeError("Broker HTTP "+str(r.status_code)+"; response withheld")
        return None if r.status_code == 204 else r.json()

    def lookup(self,cid):
        return self.call("GET","/v2/orders:by_client_order_id",params={"client_order_id":cid,"nested":"true"},missing=True)

    def submit(self,body):
        prior = self.lookup(body["client_order_id"])
        # Never retry an ambiguous POST. Reconcile this client ID on the next run.
        return prior if prior else self.call("POST","/v2/orders",body=body)


def account(api,expected):
    a = api.call("GET","/v2/account")
    if not expected or a.get("id") != expected or a.get("status") != "ACTIVE" or a.get("currency") != "USD": raise RuntimeError("Account mismatch")
    if any(a.get(k) is not False for k in ("trading_blocked","transfers_blocked","account_blocked")) or a.get("trade_suspended_by_user") is True: raise RuntimeError("Account restricted")
    equity, cash = num(a["equity"]), min(num(a["cash"]),num(a["buying_power"]),num(a["non_marginable_buying_power"]))
    if equity <= 0 or cash < 0 or num(a.get("short_market_value",0)) != 0: raise RuntimeError("Invalid capital")
    api.verified = True
    return equity,cash


def snapshot(api):
    roots = api.call("GET","/v2/orders",params={"status":"all","nested":"true","limit":500,"direction":"desc"})
    opened = api.call("GET","/v2/orders",params={"status":"open","nested":"false","limit":500})
    positions = api.call("GET","/v2/positions")
    if len(roots) >= 500 or len(opened) >= 500: raise RuntimeError("Incomplete history; review required")
    roots = [r for r in roots if r.get("client_order_id","").startswith(PREFIX)]
    known = {x["id"] for r in roots for x in [r]+(r.get("legs") or [])}
    if any(o["id"] not in known for o in opened): raise RuntimeError("Foreign open order")
    return roots,opened,positions


def exit_id(r):
    return PREFIX+"x-"+hashlib.sha256(r["id"].encode()).hexdigest()[:28]


def owner(p,roots):
    qty = num(p["qty"])
    if p.get("side") != "long" or qty <= 0: raise RuntimeError("Unexpected position")
    owners = []
    for r in roots:
        if r.get("side") != "buy" or r["symbol"] != p["symbol"]: continue
        exited = sum((num(l.get("filled_qty") or 0) for l in r.get("legs") or []),D(0))
        exited += sum((num(x.get("filled_qty") or 0) for x in roots if x.get("client_order_id") == exit_id(r) and x.get("side") == "sell"),D(0))
        if num(r.get("filled_qty") or 0)-exited == qty: owners.append(r)
    if len(owners) != 1: raise RuntimeError("Ownership mismatch")
    return owners[0]


def stop_for(r,qty):
    return min((num(l["stop_price"]) for l in r.get("legs") or [] if l.get("side") == "sell" and l.get("type") == "stop" and l.get("status") in ACTIVE_STOP and num(l.get("qty") or 0)-num(l.get("filled_qty") or 0) >= qty),default=None)


def book(roots,opened,positions):
    risk,cash,occupied,pending_symbols = D(0),D(0),set(),set()
    for p in positions:
        r = owner(p,roots); qty,entry = num(p["qty"]),num(p["avg_entry_price"])
        stop = stop_for(r,qty)
        if stop is None or stop <= 0: raise RuntimeError("Unprotected position")
        risk += qty*max(D(0),entry*D("1.0015")-stop*D(".9985")); occupied.add(p["symbol"])
    for r in roots:
        if r.get("side") != "buy" or r.get("status") in TERMINAL: continue
        left = num(r["qty"])-num(r.get("filled_qty") or 0)
        if left <= 0: continue
        if r["symbol"] in pending_symbols: raise RuntimeError("Duplicate pending symbol")
        pending_symbols.add(r["symbol"])
        stops = [num(l["stop_price"]) for l in r.get("legs") or [] if l.get("stop_price")]
        if r.get("order_class") != "bracket" or not stops: raise RuntimeError("Missing bracket stop")
        entry,stop = num(r["limit_price"]),min(stops)
        if not 0 < stop < entry: raise RuntimeError("Invalid pending stop")
        cash += left*entry*D("1.0015"); risk += left*(entry*D("1.0015")-stop*D(".9985")); occupied.add(r["symbol"])
    occupied |= {o["symbol"] for o in opened}
    if risk > 15 or len(occupied) > 4: raise RuntimeError("Risk/capacity exceeded")
    return risk,cash,occupied


def manage(api,clock,calendar):
    roots,opened,positions = snapshot(api)
    owned = [(p,owner(p,roots)) for p in positions]
    if not api.enabled or not window(clock,calendar): return False
    now,acted = stamp(clock["timestamp"]),False
    for r in roots:
        if r.get("side") == "buy" and r.get("status") not in TERMINAL and (now-stamp(r.get("submitted_at") or r["created_at"]) >= timedelta(minutes=20) or num(r.get("filled_qty") or 0) > 0):
            api.call("DELETE","/v2/orders/"+r["id"]); acted = True
    if acted:
        roots,opened,positions = snapshot(api); owned = [(p,owner(p,roots)) for p in positions]
    for p,r in owned:
        qty = num(p["qty"])
        date = str(stamp(r.get("filled_at") or r["submitted_at"]).astimezone(NY).date())
        days = [d for d in calendar if date <= d["date"] <= str(now.astimezone(NY).date())]
        timed = len(days) > 7 or len(days) == 7 and now >= session(days[-1],"close")-timedelta(minutes=30)
        existing = api.lookup(exit_id(r))
        if existing:
            if existing.get("status") in TERMINAL: raise RuntimeError("Terminal exit with residual position")
            acted = True; continue
        if stop_for(r,qty) is not None and not timed: continue
        ids = {r["id"]} | {l["id"] for l in r.get("legs") or []}
        for o in opened:
            if o["id"] in ids: api.call("DELETE","/v2/orders/"+o["id"])
        for attempt in range(5):
            remaining = api.call("GET","/v2/orders",params={"status":"open","nested":"false","limit":500})
            if len(remaining) >= 500: raise RuntimeError("Incomplete cancellation snapshot")
            if not any(o["symbol"] == p["symbol"] for o in remaining): break
            time.sleep(1)
        else: raise RuntimeError("Cancellation not confirmed")
        fresh = api.call("GET","/v2/positions/"+p["symbol"],missing=True)
        if fresh:
            if fresh.get("side") != "long" or not 0 < num(fresh["qty"]) <= qty: raise RuntimeError("Position changed during exit")
            api.submit(dict(symbol=p["symbol"],qty=fresh["qty"],side="sell",type="market",time_in_force="day",client_order_id=exit_id(r)))
        acted = True
    return acted


def features(frame,mode):
    o,h,l,c = (frame[x] for x in ("Open","High","Low","Close")); span = h-l; bull = c > o
    high3,low3 = h.shift().rolling(3).max(),l.shift().rolling(3).min()
    mean,std = c.shift().rolling(3).mean(),c.shift().rolling(3).std(ddof=0)
    checks = {
        "breakout3":bull & (c > high3), "momentum3":bull & (c > c.shift()) & (c.shift() > c.shift(2)),
        "reversal2":bull & (c.shift() < o.shift()) & (c > h.shift()),
        "dip_rebound3":bull & (std > 0) & (c.shift() < mean-std) & (c > c.shift()),
        "inside_breakout":bull & (h.shift() < h.shift(2)) & (l.shift() > l.shift(2)) & (c > h.shift()),
        "failed_reclaim":bull & (span > 0) & (l < low3) & (c > low3) & (c-l >= .75*span),
        "compression_breakout":bull & (span.shift() < span.shift(2)) & (span.shift() < span.shift(3)) & (c > h.shift()),
        "fast_breakout":bull & (c > h.shift()), "fast_momentum":bull & (c > c.shift()),
        "fast_reversal":bull & (c.shift() < o.shift()) & (c > h.shift()),
        "bullish_engulfing":bull & (c.shift() < o.shift()) & (o <= c.shift()) & (c >= o.shift())}
    families = [{"breakout3","momentum3","fast_breakout","fast_momentum"},{"reversal2","dip_rebound3","failed_reclaim","fast_reversal","bullish_engulfing"},{"inside_breakout","compression_breakout"}]
    base = l.rolling(3).min()-.1*span.rolling(3).mean(); result = []
    for i in range(len(frame)):
        active = {k for k,v in checks.items() if i >= 3 and bool(v.iloc[i])}
        stops = {k:float(base.iloc[i]) for k in active}
        if "failed_reclaim" in stops: stops["failed_reclaim"] = float(l.iloc[i]-.1*span.iloc[i])
        if "compression_breakout" in stops: stops["compression_breakout"] = float(l.iloc[i-1]-.1*span.iloc[i-1])
        active = {k for k in active if math.isfinite(stops[k]) and 0 < stops[k] < c.iloc[i]}
        groups = [g for g in families if g & active]
        votes = active & families[2] if mode == "compression_family" else active & set.union(*families) if len(groups) >= 2 else set()
        result.append(dict(stop=min((stops[k] for k in votes),default=None),quality=float((c.iloc[i]-l.iloc[i])/span.iloc[i]) if span.iloc[i] > 0 else 0))
    return result


def highs(frame):
    h = list(frame.High)
    return [h[i] for i in range(len(h)-3,1,-1) if h[i] > max(h[i-2:i]) and h[i] >= max(h[i+1:i+3])]


def target(prior,entry):
    return next((num(h).quantize(D(".01"),rounding=ROUND_FLOOR) for h in prior if num(h).quantize(D(".01"),rounding=ROUND_FLOOR) > entry),None)


def normalize(name):
    ignored = {"inc","incorporated","corp","corporation","company","co","plc","ltd","limited","common","stock","ordinary","share","shares","class","a","b","de"}
    return sorted(w for w in re.findall(r"[a-z0-9]+",name.lower().replace(".","")) if w not in ignored)


def universe(api):
    import requests
    from bs4 import BeautifulSoup
    r = requests.get("https://www.slickcharts.com/sp500",headers={"User-Agent":"Mozilla/5.0"},timeout=25,allow_redirects=False)
    if r.status_code != 200: raise RuntimeError("Universe unavailable")
    names = {}
    for table in BeautifulSoup(r.text,"html.parser").find_all("table"):
        headers = [x.get_text(" ",strip=True) for x in table.find_all("th")]
        if "Company" not in headers or "Symbol" not in headers: continue
        for row in table.find_all("tr"):
            cells = row.find_all("td")
            if not cells: continue
            if len(cells) != len(headers): raise RuntimeError("Universe schema changed")
            symbol = cells[headers.index("Symbol")].get_text(strip=True)
            if not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,9}",symbol) or symbol in names: raise RuntimeError("Invalid universe symbol")
            names[symbol] = cells[headers.index("Company")].get_text(" ",strip=True)
        break
    if not 450 <= len(names) <= 550: raise RuntimeError("Incomplete universe")
    assets = {a["symbol"]:a for a in api.call("GET","/v2/assets",params={"status":"active","asset_class":"us_equity"})}
    for s in ("SPY","QQQ"): names[s] = assets.get(s,{}).get("name","")
    selected = []
    for s,name in sorted(names.items()):
        a = assets.get(s,{})
        if s not in {"AMD","AMZN"} and normalize(name) and normalize(name) == normalize(a.get("name","")) and a.get("tradable") is True and a.get("class") == "us_equity" and a.get("status") == "active": selected.append(s)
    if len(selected) < 400: raise RuntimeError("Insufficient verified assets")
    return selected


def history_stats(returns,hits):
    n = len(returns)
    if n < 20: return dict(samples=n,score=0,lower=0)
    p,z = sum(hits)/n,1.96
    lower = (p+z*z/(2*n)-z*math.sqrt(p*(1-p)/n+z*z/(4*n*n)))/(1+z*z/n)
    return dict(samples=n,score=sum(returns)/(n+20),lower=lower)


def scan(api,calendar,now,mode):
    import pandas as pd
    import numpy as np
    closed = [d for d in calendar if session(d,"close")+timedelta(minutes=20) <= now]
    if len(closed) < 100: raise RuntimeError("Insufficient calendar")
    symbols,bars,feed = universe(api),{},os.getenv("ALPACA_DATA_FEED","iex")
    if feed not in {"iex","sip"}: raise RuntimeError("Unsupported feed")
    for offset in range(0,len(symbols),100):
        params = dict(symbols=",".join(symbols[offset:offset+100]),timeframe="1Day",start=closed[0]["date"]+"T00:00:00Z",end=session(closed[-1],"close").isoformat(),adjustment="split",feed=feed,limit=10000); seen = set()
        while True:
            page = api.call("GET","/v2/stocks/bars",params=params,data=True)
            for s,rows in page.get("bars",{}).items(): bars.setdefault(s,[]).extend(rows)
            token = page.get("next_page_token")
            if not token: break
            if token in seen: raise RuntimeError("Repeated pagination token")
            seen.add(token); params["page_token"] = token
    ranked = []
    for s in symbols:
        if not bars.get(s): continue
        f = pd.DataFrame(bars[s]).rename(columns=dict(o="Open",h="High",l="Low",c="Close",v="Volume"))
        f.index = pd.to_datetime(f.t,utc=True).dt.tz_convert("America/New_York").dt.tz_localize(None).dt.normalize(); f = f.sort_index()
        dates = list(f.index.strftime("%Y-%m-%d"))
        if len(dates) < 100 or dates != [d["date"] for d in closed if d["date"] >= dates[0]]: continue
        f = f[["Open","High","Low","Close","Volume"]].astype(float)
        if not np.isfinite(f.to_numpy()).all() or (f.iloc[:,:4] <= 0).any().any() or (f.Volume < 0).any() or (f.Low > f[["Open","Close"]].min(axis=1)).any() or (f.High < f[["Open","Close"]].max(axis=1)).any(): raise RuntimeError("Invalid OHLCV")
        signals = features(f,mode)
        if signals[-1]["stop"] is None: continue
        returns,hits,i = [],[],20; values = f[["Open","High","Low","Close"]].to_numpy()
        while i+7 < len(f):
            stop = signals[i]["stop"]; entry = values[i+1][0]*1.0015
            tp = target(highs(f.iloc[:i+1]),num(entry)) if stop is not None and 0 < stop < entry else None
            if tp is None or (float(tp)-entry)/(entry-stop) < 1.5 or (entry-stop)/entry > .06: i += 1; continue
            hit,price,end = False,values[i+7][3],i+7
            for j in range(i+1,i+8):
                o,h,l,c = values[j]
                if o <= stop: price,end = o,j; break
                if o >= float(tp): price,hit,end = float(tp),True,j; break
                if l <= stop: price,end = stop,j; break
                if h >= float(tp): price,hit,end = float(tp),True,j; break
            returns.append((price*.9985-entry)/(entry-stop*.9985)); hits.append(hit); i = end+1
        h = history_stats(returns,hits)
        if h["samples"] >= 20 and h["score"] > 0: ranked.append(dict(symbol=s,reference=float(f.Close.iloc[-1]),stop=signals[-1]["stop"],quality=signals[-1]["quality"],highs=highs(f),history=h))
    return sorted(ranked,key=lambda r:(-r["history"]["score"],-r["history"]["lower"],-r["quality"],r["symbol"]))


def size(row,quote,now,equity,cash,risk,cid):
    bid,ask,ref = num(quote["bp"]),num(quote["ap"]),num(row["reference"])
    stop = num(row["stop"]).quantize(D(".01"),rounding=ROUND_FLOOR)
    age = (now-stamp(quote["t"])).total_seconds()
    if not 0 <= age <= 10 or not 0 < stop < bid <= ask or ask < 1 or ref <= 0: return None
    if (ask-bid)/ask > D(".005") or abs(ask/ref-1) > D(".02"): return None
    entry = (ask*D("1.0015")).quantize(D(".01"),rounding=ROUND_CEILING); tp = target(row["highs"],entry)
    unit,cost = entry*D("1.0015")-stop*D(".9985"),entry*D("1.0015")
    if unit/entry > D(".06") or tp is None or (tp-entry)/(entry-stop) < D("1.5"): return None
    qty = min(max(D(0),risk)/unit,max(D(0),cash)/cost,equity*D(".25")/cost).to_integral_value(rounding=ROUND_FLOOR)
    if qty < 1: return None
    body = dict(symbol=row["symbol"],qty=str(qty),side="buy",type="limit",time_in_force="gtc",limit_price=str(entry),order_class="bracket",take_profit={"limit_price":str(tp)},stop_loss={"stop_price":str(stop)},extended_hours=False,client_order_id=cid)
    return body,qty*cost,qty*unit


def run(api,mode,expected):
    equity,cash = account(api,expected); roots,opened,positions = snapshot(api)
    if not roots and (positions or opened or abs(equity-2000) > D(".01") or abs(cash-2000) > D(".01")): raise RuntimeError("First run requires empty USD 2000 paper account")
    report = dict(paper_only=True,mode=mode,equity=str(equity),submitted=[])
    if mode == "check": book(roots,opened,positions); report["status"] = "READ_ONLY_VERIFIED"; return report
    clock = api.call("GET","/v2/clock"); now = stamp(clock["timestamp"])
    calendar = api.call("GET","/v2/calendar",params={"start":str((now-timedelta(days=900)).date()),"end":str(now.astimezone(NY).date())})
    if mode == "tick" and manage(api,clock,calendar): report["status"] = "BOOK_MANAGED_RECHECK_NEXT_RUN"; return report
    risk,pending,occupied = book(*snapshot(api)); report["planned_risk"] = str(risk)
    if not window(clock,calendar): report["status"] = "MARKET_CLOSED"; return report
    if mode == "tick" and (not api.enabled or os.getenv("ALPACA_PAPER_KILL_SWITCH","false").lower() == "true"): report["status"] = "NEW_ENTRIES_DISABLED"; return report
    if mode == "tick" and not window(clock,calendar,entry=True): report["status"] = "LATE_SESSION_NO_ENTRIES"; return report
    strategy = os.getenv("ALPACA_PAPER_STRATEGY","compression_family")
    if strategy not in {"compression_family","balanced_blend"}: raise RuntimeError("Unknown strategy")
    if len(occupied) >= 4: report["status"] = "POSITION_CAPACITY"; return report
    ranked = scan(api,calendar,now,strategy); report["candidates"] = [{k:v for k,v in r.items() if k != "highs"} for r in ranked]
    if mode == "scan": report["status"] = "READ_ONLY_SCAN"; return report
    selected = [r for r in ranked if r["symbol"] not in occupied][:4-len(occupied)]
    weights = [max(D(".01"),num(r["history"]["score"]))*max(D(".1"),num(r["quality"])) for r in selected]
    reserved_cash,reserved_risk,reserved = D(0),D(0),set()
    for index,row in enumerate(selected):
        equity,cash = account(api,expected); risk,pending,occupied = book(*snapshot(api))
        if len(occupied | reserved) >= 4 or row["symbol"] in occupied: continue
        clock = api.call("GET","/v2/clock")
        if not window(clock,calendar,entry=True): break
        cid = PREFIX+stamp(clock["timestamp"]).astimezone(NY).strftime("%Y%m%d")+"-"+row["symbol"].replace(".","_")
        if api.lookup(cid): continue
        q = api.call("GET","/v2/stocks/quotes/latest",params={"symbols":row["symbol"],"feed":os.getenv("ALPACA_DATA_FEED","iex")},data=True).get("quotes",{}).get(row["symbol"])
        clock = api.call("GET","/v2/clock")
        if not window(clock,calendar,entry=True): break
        if not q: continue
        allowance = max(D(0),D(15)-risk-reserved_risk)*weights[index]/sum(weights[index:])
        sized = size(row,q,stamp(clock["timestamp"]),equity,max(D(0),cash-pending-reserved_cash),allowance,cid)
        if not sized: continue
        body,cost,planned = sized; reserved_cash += cost; reserved_risk += planned; reserved.add(row["symbol"])
        order = api.submit(body)
        if not order.get("id") or order.get("status") in {"rejected","canceled","expired"}: raise RuntimeError("Entry not accepted")
        report["submitted"].append(dict(order_id=order["id"],client_order_id=cid,body=body,planned_risk=str(planned)))
        time.sleep(2)
        if manage(api,api.call("GET","/v2/clock"),calendar): report["post_submit_management"] = True; break
    report["status"] = "PAPER_ENTRIES_SUBMITTED" if report["submitted"] else "NO_QUALIFYING_EXECUTABLE_SETUP"
    return report


def main():
    p = argparse.ArgumentParser(); p.add_argument("--mode",choices=("check","scan","tick"),default="check"); args = p.parse_args()
    # Enabling management is separate from the new-entry kill switch.
    enabled = args.mode == "tick" and os.getenv("ALPACA_PAPER_ENABLED","false").lower() == "true"
    out = Path("reports/paper"); out.mkdir(parents=True,exist_ok=True); report = dict(status="FAILED_CLOSED",paper_only=True)
    try: report = run(API(enabled),args.mode,os.getenv("ALPACA_EXPECTED_PAPER_ACCOUNT_ID",""))
    except Exception as exc:
        report["error_type"] = type(exc).__name__
        raise SystemExit("Paper runner stopped. Inspect Alpaca orders and positions; failure does not mean flat.") from None
    finally:
        text = json.dumps(report,indent=2,allow_nan=False); (out/"status.json").write_text(text+"\n"); print(text)
        if os.getenv("GITHUB_STEP_SUMMARY"):
            with open(os.environ["GITHUB_STEP_SUMMARY"],"a") as f: f.write("# Alpaca PAPER\n\n```json\n"+text+"\n```\n")


if __name__ == "__main__": main()
