import unittest
from unittest.mock import patch
from paper_universe_fallback import parse_names, verified, with_fallback, FallbackError

HTML = '<table id="constituents"><tr><th>Symbol</th><th>Security</th></tr><tr><td>TEST</td><td>Test Company</td></tr></table>'


class UniverseFallbackTests(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(parse_names(HTML, minimum=1), {"TEST": "Test Company"})

    def test_bad_source_rejected(self):
        for html in ("blocked", HTML, HTML.replace("TEST", "BAD/SYMBOL")):
            with self.assertRaises(FallbackError): parse_names(html)
        with self.assertRaises(FallbackError):
            parse_names(HTML.replace('</table>', '<tr><td>TEST</td><td>Again</td></tr></table>'), minimum=1)

    def test_broker_identity_and_exclusions_preserved(self):
        assets = [dict(symbol=s, name=n, tradable=True, status="active", **{"class":"us_equity"}) for s,n in [("TEST","Test Company"),("AMD","AMD"),("AMZN","Amazon"),("WRONG","Other")]]
        names = dict(TEST="Test Company", AMD="AMD", AMZN="Amazon", WRONG="Wrong Company")
        self.assertEqual(verified(names, assets, lambda x:x.lower().split(), minimum=1), ["TEST"])
        with self.assertRaises(FallbackError): verified(names, assets, lambda x:x.lower().split())

    def test_primary_success_does_not_fetch_fallback(self):
        with patch("paper_universe_fallback.save"), patch("requests.get") as get:
            self.assertEqual(with_fallback(lambda api:["TEST"])(None), ["TEST"])
            get.assert_not_called()

    def test_broker_or_identity_failure_does_not_trigger_fallback(self):
        def fail(api): raise RuntimeError("Insufficient verified assets")
        with patch("requests.get") as get:
            with self.assertRaises(RuntimeError): with_fallback(fail)(None)
            get.assert_not_called()

    def test_primary_http_failure_uses_public_source_and_keeps_broker_get_only(self):
        import sys
        from types import SimpleNamespace
        def fail(api): raise RuntimeError("Universe unavailable")
        class API:
            def call(self, method, path, **kwargs):
                assert method == "GET" and path == "/v2/assets"
                return []
        fake = SimpleNamespace(normalize=lambda x:x)
        with patch.dict(sys.modules, {"paper_execution":fake}), patch("requests.get", return_value=SimpleNamespace(status_code=200,text=HTML)), patch("paper_universe_fallback.parse_names",return_value={"TEST":"Test"}), patch("paper_universe_fallback.verified",return_value=["TEST"]) as verify, patch("paper_universe_fallback.save") as save:
            self.assertEqual(with_fallback(fail)(API()), ["TEST"])
            verify.assert_called_once()
            self.assertEqual(save.call_args.args[0]["status"], "VERIFIED")


if __name__ == "__main__": unittest.main()
