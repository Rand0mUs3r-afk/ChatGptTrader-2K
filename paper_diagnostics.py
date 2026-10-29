"""Read-only diagnostics. Never print credentials, account IDs or broker responses."""
from decimal import Decimal
import json
import os
from pathlib import Path
import re


def account_checks(a, expected):
    checks = {
        "expected_account_id_present": bool(expected),
        "expected_account_id_matches": bool(expected and a.get("id") == expected),
        "account_active": a.get("status") == "ACTIVE",
        "account_usd": a.get("currency") == "USD",
        "trading_unblocked": a.get("trading_blocked") is False,
        "transfers_unblocked": a.get("transfers_blocked") is False,
        "account_unblocked": a.get("account_blocked") is False,
        "user_not_suspended": a.get("trade_suspended_by_user") is not True,
    }
    values = {}
    for key in ("equity", "cash", "buying_power", "non_marginable_buying_power", "short_market_value"):
        try:
            value = Decimal(str(a.get(key, 0) if key == "short_market_value" else a[key]))
            if not value.is_finite(): raise ValueError()
            values[key] = value
        except (KeyError, ValueError, ArithmeticError):
            checks["valid_"+key] = False
    if len(values) == 5:
        available = min(values[k] for k in ("cash", "buying_power", "non_marginable_buying_power"))
        checks.update(positive_equity=values["equity"] > 0,
            nonnegative_available_cash=available >= 0, no_short_exposure=values["short_market_value"] == 0)
        amounts = {"equity_usd": str(values["equity"]), "available_cash_usd": str(available)}
    else:
        amounts = {}
    return checks, amounts


def safe_error(exc):
    known = {
        "Missing paper secrets": "MISSING_PAPER_SECRETS",
        "Account mismatch": "ACCOUNT_MISMATCH",
        "Account restricted": "ACCOUNT_RESTRICTED",
        "Invalid capital": "INVALID_CAPITAL",
        "Incomplete history; review required": "INCOMPLETE_ORDER_HISTORY",
        "Foreign open order": "FOREIGN_OPEN_ORDER",
        "Unexpected position": "UNEXPECTED_POSITION",
        "Ownership mismatch": "OWNERSHIP_MISMATCH",
        "Unprotected position": "UNPROTECTED_POSITION",
        "Duplicate pending symbol": "DUPLICATE_PENDING_SYMBOL",
        "Missing bracket stop": "MISSING_BRACKET_STOP",
        "Invalid pending stop": "INVALID_PENDING_STOP",
        "Risk/capacity exceeded": "RISK_OR_CAPACITY_EXCEEDED",
    }
    if type(exc) is RuntimeError:
        text = str(exc)
        if text in known: return known[text]
        match = re.fullmatch(r"Broker HTTP ([0-9]{3}); response withheld", text)
        if match: return "BROKER_HTTP_"+match[1]
    return "UNCLASSIFIED_"+type(exc).__name__


def diagnose(api, expected):
    report = {"paper_only": True, "read_only": True, "stage": "ACCOUNT_REQUEST"}
    try:
        a = api.call("GET", "/v2/account")
        checks, amounts = account_checks(a, expected)
        report.update(stage="ACCOUNT_VALIDATION", checks=checks, amounts=amounts)
        failed = [key for key, passed in checks.items() if not passed]
        if failed:
            report.update(status="FAILED", error_code="ACCOUNT_CHECK_FAILED", failed_checks=failed)
            return report
        report["stage"] = "ORDER_POSITION_RECONCILIATION"
        import paper_execution as runner
        roots, opened, positions = runner.snapshot(api)
        report["counts"] = dict(bot_root_orders=len(roots), open_orders=len(opened), positions=len(positions))
        if not roots:
            if positions or opened:
                report.update(status="FAILED", error_code="INITIAL_ACCOUNT_NOT_EMPTY")
                return report
            if any(abs(Decimal(amounts[k])-2000) > Decimal(".01") for k in ("equity_usd", "available_cash_usd")):
                report.update(status="FAILED", error_code="INITIAL_CAPITAL_NOT_2000")
                return report
        risk, pending, occupied = runner.book(roots, opened, positions)
        report.update(status="READ_ONLY_VERIFIED", planned_risk_usd=str(risk), pending_cash_usd=str(pending))
    except Exception as exc:
        report.update(status="FAILED", error_code=safe_error(exc))
    return report


def main():
    names = ("ALPACA_PAPER_KEY_ID", "ALPACA_PAPER_SECRET_KEY", "ALPACA_EXPECTED_PAPER_ACCOUNT_ID")
    present = {name: bool(os.getenv(name)) for name in names}
    report = dict(paper_only=True, read_only=True, configuration_present=present)
    try:
        if not all(present.values()):
            report.update(status="FAILED", stage="CONFIGURATION", error_code="MISSING_CONFIGURATION")
        else:
            import paper_execution as runner
            # enabled=False is permanent in this diagnostic, regardless of repository variables.
            report.update(diagnose(runner.API(enabled=False), os.environ[names[2]]))
    except Exception as exc:
        report.update(status="FAILED", stage="INITIALIZATION", error_code=safe_error(exc))
    text = json.dumps(report, indent=2, allow_nan=False)
    out = Path("reports/paper-diagnostics"); out.mkdir(parents=True, exist_ok=True)
    (out/"status.json").write_text(text+"\n")
    print(text)
    if os.getenv("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
            f.write("# Read-only Alpaca diagnostics\n\n```json\n"+text+"\n```\n")
    return 0 if report.get("status") == "READ_ONLY_VERIFIED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
