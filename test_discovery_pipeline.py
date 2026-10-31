from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch
import pandas as pd
import discovery_state as state
from yahoo_discovery import leaders, history_scores, allowed
from paper_pipeline import ReadOnly, with_auto

NOW = datetime(2026,10,30,22,tzinfo=timezone.utc)
MANUAL = NS(EXCLUDED={"AMD","AMZN"}, EXCHANGES={"NASDAQ"}, with_watchlist=lambda f:f)


def row(symbol="TEST", age=0, expires=2):
    return dict(symbol=symbol, observed_at=(NOW-timedelta(days=age)).isoformat(), expires_at=(NOW+timedelta(days=expires)).isoformat(), reasons=[], strength=1)


class DiscoveryTests(unittest.TestCase):
    def test_expiry_future_and_symbol_validation(self):
        rows = [row(),row("OLD",expires=-1),row("FUTURE",age=-1),row("BAD/USD")]
        self.assertEqual([r["symbol"] for r in state.fresh(dict(version=1,entries=rows),NOW)],["TEST"])

    def test_historical_windows(self):
        dates = list(pd.bdate_range("2026-10-22",periods=6).strftime("%Y-%m-%d"))
        f = pd.DataFrame({"Close":[10,11,12,13,14,15],"Volume":[1000000]*6},index=pd.to_datetime(dates))
        scores = history_scores(f,dates,dates[-2])
        self.assertAlmostEqual(scores["five_session"],50)
        self.assertAlmostEqual(scores["previous_session"],100*(14/13-1))
        with self.assertRaises(ValueError): history_scores(f.iloc[:-1],dates,dates[-2])

    def test_movers_are_not_only_gainers(self):
        r = leaders(dict(UP=10,DOWN=-30,FLAT=0))
        self.assertEqual(r["gainers"],["UP"])
        self.assertEqual(r["movers"][0],"DOWN")

    def test_asset_identity_exclusions_and_duplicates(self):
        def asset(s,**kw): return dict(symbol=s,tradable=True,status="active",exchange="NASDAQ",**{"class":"us_equity"},**kw)
        assets=[asset("TEST"),asset("AMD"),asset("DUP"),asset("DUP"),asset("BTC/USD")]
        with patch.dict(sys.modules,{"paper_watchlist":MANUAL}): self.assertEqual(allowed(assets),{"TEST"})

    def test_readonly_blocks_writes_before_transport(self):
        class API:
            def call(self,*args,**kw): raise AssertionError("Transport invoked")
        for method in ("POST","DELETE","PATCH"):
            with self.assertRaises(RuntimeError): ReadOnly(API()).call(method,"/v2/orders")

    def test_auto_merges_without_replacing_manual(self):
        class API:
            def call(self,method,path,**kw):
                assert method == "GET" and path == "/v2/assets"
                return [dict(symbol="AUTO",tradable=True,status="active",exchange="NASDAQ",**{"class":"us_equity"})]
        with patch("paper_pipeline.read",return_value=[row("AUTO"),row("UNKNOWN")]), patch.dict(sys.modules,{"paper_watchlist":MANUAL}), patch("builtins.print"):
            self.assertEqual(with_auto(lambda a:["MANUAL","NORMAL"])(API()),["AUTO","MANUAL","NORMAL"])

    def test_bad_state_keeps_normal(self):
        with patch("paper_pipeline.read",side_effect=ValueError()),patch("builtins.print"):
            self.assertEqual(with_auto(lambda a:["NORMAL"])(None),["NORMAL"])

    def test_state_branch_roundtrip_and_race_rejection(self):
        def local(*args,cwd):
            r=subprocess.run(["git",*args],cwd=cwd,capture_output=True,text=True)
            self.assertEqual(r.returncode,0,r.stderr); return r.stdout.strip()
        with tempfile.TemporaryDirectory() as folder:
            old=os.getcwd()
            remote=Path(folder)/"remote.git"; repo=Path(folder)/"repo"
            remote.mkdir();repo.mkdir()
            local("init","--bare",cwd=remote);local("init",cwd=repo)
            local("config","user.name","Test",cwd=repo);local("config","user.email","test@example.invalid",cwd=repo)
            (repo/"README").write_text("code remains unchanged")
            local("add","README",cwd=repo);local("commit","-m","baseline",cwd=repo)
            head=local("rev-parse","HEAD",cwd=repo)
            local("remote","add","origin",str(remote),cwd=repo)
            try:
                os.chdir(repo)
                with patch.object(state,"DIR",Path("state")):
                    state.load(); payload=dict(version=1,entries=[row()])
                    (state.DIR/"watchlist_auto.json").write_text(json.dumps(payload));(state.DIR/"watchlist_auto.txt").write_text("TEST\n")
                    state.save()
                    self.assertEqual(local("rev-parse","HEAD",cwd=repo),head)
                    (state.DIR/"watchlist_auto.json").write_text(json.dumps(dict(payload, stale_writer=True)))
                    with self.assertRaises(RuntimeError): state.save()
                    state.load();self.assertEqual(json.loads((state.DIR/"watchlist_auto.json").read_text()),payload)
            finally: os.chdir(old)


if __name__ == "__main__": unittest.main()
