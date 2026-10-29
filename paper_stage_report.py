"""Stage reporting for the unchanged paper runner. No credentials/raw responses logged."""
import argparse
from functools import wraps
import json
import os
from pathlib import Path

ERRORS = {
    "Universe unavailable": "UNIVERSE_SOURCE_UNAVAILABLE",
    "Universe schema changed": "UNIVERSE_SCHEMA_CHANGED",
    "Invalid universe symbol": "UNIVERSE_SYMBOL_INVALID",
    "Incomplete universe": "UNIVERSE_INCOMPLETE",
    "Insufficient verified assets": "INSUFFICIENT_VERIFIED_ASSETS",
    "Insufficient calendar": "INSUFFICIENT_CALENDAR",
    "Unsupported feed": "UNSUPPORTED_DATA_FEED",
    "Repeated pagination token": "REPEATED_DATA_TOKEN",
    "Invalid OHLCV": "INVALID_OHLCV",
    "Unknown strategy": "UNKNOWN_STRATEGY",
    "First run requires empty USD 2000 paper account": "INITIAL_ACCOUNT_REQUIREMENTS_FAILED",
    "Entry not accepted": "ENTRY_NOT_ACCEPTED",
    "Terminal exit with residual position": "RESIDUAL_POSITION_AFTER_EXIT",
    "Incomplete cancellation snapshot": "INCOMPLETE_CANCELLATION_SNAPSHOT",
    "Cancellation not confirmed": "CANCELLATION_UNCONFIRMED",
    "Position changed during exit": "POSITION_CHANGED_DURING_EXIT",
    "Write gate closed": "WRITE_GATE_CLOSED",
}


def error_code(exc):
    if type(exc) is RuntimeError and str(exc) in ERRORS:
        return ERRORS[str(exc)]
    from paper_diagnostics import safe_error
    return safe_error(exc)


def instrument(fn, name, trace):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        trace["calls"][name] = trace["calls"].get(name, 0)+1
        try:
            return fn(*args, **kwargs)
        except Exception:
            # The innermost failing stage wins, not its outer callers.
            trace.setdefault("failed_stage", name)
            raise
    return wrapped


def execute(mode, enabled, expected):
    import paper_execution as base
    trace = {"calls": {}, "mode": mode, "writes_enabled": enabled}
    names = ("account", "snapshot", "manage", "universe", "scan", "size")
    originals = {name: getattr(base, name) for name in names}
    class TracedAPI(base.API):
        def call(self, method, path, *args, **kwargs):
            # Record a route category, never order IDs, query parameters or bodies.
            route = path if path in {"/v2/account", "/v2/orders", "/v2/orders:by_client_order_id", "/v2/positions", "/v2/clock", "/v2/calendar", "/v2/assets", "/v2/stocks/bars", "/v2/stocks/quotes/latest"} else "/v2/resource/[redacted]"
            trace["last_request"] = dict(method=method, route=route, data=kwargs.get("data", False))
            try:
                return super().call(method, path, *args, **kwargs)
            except Exception:
                trace["failed_request"] = dict(trace["last_request"])
                raise
    report = {"status": "FAILED_CLOSED", "paper_only": True, "mode": mode}
    try:
        for name, fn in originals.items():
            setattr(base, name, instrument(fn, name, trace))
        report = base.run(TracedAPI(enabled), mode, expected)
    except Exception as exc:
        report.update(error_type=type(exc).__name__, error_code=error_code(exc))
        trace.setdefault("failed_stage", "INITIALIZATION")
    finally:
        for name, fn in originals.items():
            setattr(base, name, fn)
    report["diagnostics"] = trace
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("check", "scan", "tick"), default="check")
    args = parser.parse_args()
    enabled = args.mode == "tick" and os.getenv("ALPACA_PAPER_ENABLED", "false").lower() == "true"
    report = execute(args.mode, enabled, os.getenv("ALPACA_EXPECTED_PAPER_ACCOUNT_ID", ""))
    text = json.dumps(report, indent=2, allow_nan=False)
    out = Path("reports/paper"); out.mkdir(parents=True, exist_ok=True)
    (out/"status.json").write_text(text+"\n")
    print(text)
    if os.getenv("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
            f.write("# Alpaca PAPER stage report\n\n```json\n"+text+"\n```\n")
    return 1 if report.get("status") == "FAILED_CLOSED" else 0


if __name__ == "__main__":
    raise SystemExit(main())
