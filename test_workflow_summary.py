import unittest
import tempfile
from pathlib import Path
from workflow_summary import discovery, paper, number, cell, table, load_auto


class SummaryTests(unittest.TestCase):
    def test_discovery_findings_and_refresh_counts(self):
        report=dict(status="COMPLETED",updated_at="now",entries=[dict(symbol="TEST",observed_at="now",expires_at="later",reasons=[dict(category="five_session_gainers",change_pct=12.5,session="day")])],coverage=dict(pool_symbols=100,historical_symbols_covered=90))
        text=discovery(report,"success","success")
        self.assertIn("TEST",text); self.assertIn("12.50%",text)
        self.assertIn("discovered/refreshed this run: 1",text)

    def test_previous_state_is_not_called_new(self):
        text=discovery(dict(updated_at="old",entries=[dict(symbol="OLD",observed_at="old")]),"failure","skipped")
        self.assertIn("not new findings",text);self.assertIn("Publication is not confirmed",text)
        self.assertIn("discovered/refreshed this run: 0",text)

    def test_candidate_and_order_tables(self):
        r=dict(status="PAPER_ENTRIES_SUBMITTED",mode="tick",candidates=[dict(symbol="TEST",reference=100,stop=98,history=dict(samples=30,score=.5))],submitted=[dict(order_id="order1",planned_risk="4",body=dict(symbol="TEST",qty="2",limit_price="100",stop_loss=dict(stop_price="98"),take_profit=dict(limit_price="105")))])
        text=paper(r,"success")
        for value in ("Ranked setup candidates","Submitted paper orders","order1","0.500"): self.assertIn(value,text)
        self.assertIn("not a fill confirmation",text)

    def test_early_return_does_not_claim_scan_passed(self):
        text=paper(dict(status="LATE_SESSION_NO_ENTRIES"),"success")
        self.assertIn("before a new setup scan",text)
        self.assertIn("not recorded",text)

    def test_failure_does_not_claim_no_orders(self):
        self.assertIn("Order outcome may be unknown",paper(dict(status="FAILED_CLOSED"),"failure"))

    def test_readonly_postclose_is_labeled(self):
        text=paper(dict(status="POST_CLOSE_SCAN_COMPLETED",mode="post-close",signal_session="Friday",next_trading_session="Monday",candidates=[]),"success")
        self.assertIn("Monday",text);self.assertIn("Candidates returned: 0",text)

    def test_safe_fields_and_escaping(self):
        text=paper(dict(status="OK",ALPACA_PAPER_SECRET_KEY="NEVER_PRINT",account_id="PRIVATE_ACCOUNT"),"success")
        self.assertNotIn("NEVER_PRINT",text);self.assertNotIn("PRIVATE_ACCOUNT",text)
        self.assertEqual(cell("<script>|`\n"),"&lt;script&gt;&#124;&#96; ")
        self.assertEqual(number("NaN"),"—");self.assertEqual(number(0),"0.00")

    def test_large_table_is_explicitly_truncated(self):
        self.assertIn("first 50 of 51",table("Rows",["Ticker"],[["TEST"]]*51))

    def test_reads_only_auto_watchlist_line_from_log(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"runner.log"
            path.write_text('other output\n{"manual_watchlist":{}}\n{"auto_watchlist":{"accepted":["TEST"],"rejected":[]}}\n')
            self.assertEqual(load_auto(path)["accepted"],["TEST"])
            path.write_text("unrelated output")
            self.assertEqual(load_auto(path),{})


if __name__ == "__main__": unittest.main()
