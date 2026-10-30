import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from paper_watchlist import parse_watchlist, merge, with_watchlist


def asset(symbol, **changes):
    row = dict(symbol=symbol, name="Test Company", status="active", tradable=True,
        exchange="NASDAQ", **{"class": "us_equity"})
    row.update(changes)
    return row


class FakeAPI:
    def __init__(self, rows): self.rows, self.calls = rows, []
    def call(self, method, path, **kwargs):
        assert method == "GET" and path == "/v2/assets"
        self.calls.append((method, path))
        return self.rows


class WatchlistTests(unittest.TestCase):
    def test_comments_case_duplicates_and_bad_lines(self):
        symbols, rejected = parse_watchlist("# sample\naapl\nAAPL # duplicate\nBRK.B\nBTC/USD\n")
        self.assertEqual(symbols, ["AAPL", "BRK.B"])
        self.assertEqual(rejected, [dict(line=5, reason="INVALID_SYMBOL_FORMAT")])

    def test_unique_limit(self):
        with self.assertRaises(ValueError): parse_watchlist("\n".join("T"+str(i) for i in range(101)))

    def check_merge(self, text, rows, normal):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/"watchlist.txt"; path.write_text(text)
            api = FakeAPI(rows)
            with patch("paper_watchlist.print"), patch("paper_watchlist.os.getenv", return_value=None), patch("paper_watchlist.Path") as mock_path:
                mock_path.side_effect = lambda value: Path(folder)/"reports" if value == "reports/paper" else Path(value)
                result = merge(api, normal, path)
            report = json.loads((Path(folder)/"reports/manual_watchlist.json").read_text())
            self.assertEqual(report["market_data_source"], "Alpaca")
            self.assertEqual(report["total_scan_symbols"], len(result))
            return result, api

    def test_merges_and_deduplicates(self):
        result, api = self.check_merge("AAPL\nTEST\n", [asset("AAPL"),asset("TEST")], ["AAPL","SPY"])
        self.assertEqual(result, ["AAPL","SPY","TEST"])
        self.assertEqual(len(api.calls), 1)

    def test_invalid_untradable_otc_crypto_and_exclusions_skipped(self):
        rows = [asset("AMD"),asset("AMZN"),asset("HALT",tradable=False),asset("OTC",exchange="OTC"),asset("CRYPTO",**{"class":"crypto"})]
        result, _ = self.check_merge("AMD\nAMZN\nHALT\nOTC\nCRYPTO\nUNKNOWN\n", rows, ["SPY"])
        self.assertEqual(result, ["SPY"])

    def test_empty_watchlist_keeps_normal_and_makes_no_request(self):
        result, api = self.check_merge("# nothing selected\n", [], ["SPY"])
        self.assertEqual(result,["SPY"]); self.assertFalse(api.calls)

    def test_duplicate_asset_rejected(self):
        result, _ = self.check_merge("TEST", [asset("TEST"),asset("TEST")], ["SPY"])
        self.assertEqual(result,["SPY"])

    def test_normal_universe_failure_is_not_bypassed(self):
        def fail(api): raise RuntimeError("normal source failed")
        with patch("paper_watchlist.merge") as merged:
            with self.assertRaises(RuntimeError): with_watchlist(fail)(None)
            merged.assert_not_called()


if __name__ == "__main__": unittest.main()
