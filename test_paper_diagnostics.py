import unittest
from paper_diagnostics import account_checks, safe_error, diagnose


def account():
    return dict(id="expected", status="ACTIVE", currency="USD", trading_blocked=False,
        transfers_blocked=False, account_blocked=False, equity="2000", cash="2000",
        buying_power="4000", non_marginable_buying_power="2000", short_market_value="0")


class DiagnosticsTests(unittest.TestCase):
    def test_good_account(self):
        checks, amounts = account_checks(account(), "expected")
        self.assertTrue(all(checks.values()))
        self.assertEqual(amounts["available_cash_usd"], "2000")

    def test_mismatch_is_specific(self):
        checks, _ = account_checks(account(), "wrong")
        self.assertFalse(checks["expected_account_id_matches"])
        self.assertTrue(checks["account_active"])

    def test_missing_boolean_fails_closed(self):
        a = account(); del a["transfers_blocked"]
        self.assertFalse(account_checks(a, "expected")[0]["transfers_unblocked"])

    def test_invalid_financial_fields(self):
        for value in ("NaN", "Infinity", "bad"):
            a = account(); a["equity"] = value
            self.assertFalse(account_checks(a, "expected")[0]["valid_equity"])

    def test_http_code_without_body(self):
        self.assertEqual(safe_error(RuntimeError("Broker HTTP 401; response withheld")), "BROKER_HTTP_401")
        self.assertNotIn("secret", safe_error(RuntimeError("secret-value")))

    def test_failed_account_never_advances_or_writes(self):
        class Fake:
            def call(self, method, path):
                assert method == "GET" and path == "/v2/account"
                return account()
        report = diagnose(Fake(), "wrong")
        self.assertEqual(report["error_code"], "ACCOUNT_CHECK_FAILED")


if __name__ == "__main__": unittest.main()
