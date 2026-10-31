"""Human-readable GitHub findings. Render only allowlisted report fields."""
import argparse
from decimal import Decimal, InvalidOperation
import html
import json
import os
from pathlib import Path

LIMIT = 50


def cell(value):
    if value is None: return "—"
    return html.escape(str(value).replace("\n", " ").replace("\r", " ")[:240]).replace("|", "&#124;").replace("`", "&#96;")


def number(value, places=2):
    if isinstance(value, bool) or value is None: return "—"
    try:
        n = Decimal(str(value))
        return format(n, "."+str(places)+"f") if n.is_finite() and abs(n) < Decimal("1e15") else "—"
    except (InvalidOperation, ValueError, TypeError): return "—"


def load(path):
    try:
        p = Path(path)
        if p.stat().st_size > 2000000: return {}
        value = json.loads(p.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError): return {}


def load_auto(path):
    try:
        p = Path(path)
        if p.stat().st_size > 2000000: return {}
        for line in p.read_text().splitlines():
            if line.startswith('{"auto_watchlist":'):
                value = json.loads(line).get("auto_watchlist")
                if isinstance(value, dict): return value
    except (OSError, ValueError): pass
    return {}


def table(title, headers, rows):
    rows = list(rows)
    text = "\n## "+title+"\n\n"
    if not rows: return text+"None recorded in this report.\n"
    text += "| "+" | ".join(map(cell, headers))+" |\n| "+" | ".join("---" for _ in headers)+" |\n"
    text += "".join("| "+" | ".join(cell(v) for v in row)+" |\n" for row in rows[:LIMIT])
    if len(rows) > LIMIT: text += "\nShowing first "+str(LIMIT)+" of "+str(len(rows))+" rows; see the full JSON artifact.\n"
    return text


def discovery(report, outcome, publish):
    entries = report.get("entries", []) if isinstance(report.get("entries"), list) else []
    current = outcome == "success" and bool(report.get("updated_at"))
    text = "# Yahoo discovery findings\n\n"
    text += "- Discovery step: "+cell(outcome)+".\n- State publication step: "+cell(publish)+".\n"
    text += "- Discovery report status: "+cell(report.get("status", "NOT_REPORTED"))+".\n"
    if not current: text += "\nNo confirmed fresh discovery report from this run. Any rows below are loaded/previous state, not new findings.\n"
    coverage = report.get("coverage") or {}
    text += "- Pool: "+cell(coverage.get("pool_symbols"))+"; historical symbols covered: "+cell(coverage.get("historical_symbols_covered"))+".\n"
    text += "- Previous session: "+cell(coverage.get("previous_session"))+"; latest completed session: "+cell(coverage.get("latest_completed_session"))+".\n"
    refreshed = sum(r.get("observed_at") == report.get("updated_at") for r in entries) if current else 0
    text += "- Active watchlist rows in payload: "+str(len(entries))+"; discovered/refreshed this run: "+str(refreshed)+".\n"
    text += "\nDiscovery is not entry approval. Rankings cover the defined pool, not every US stock.\n"
    rows = []
    for r in entries:
        reasons = r.get("reasons") or []
        evidence = "; ".join(str(x.get("category", ""))+": "+number(x.get("change_pct"))+"% ("+str(x.get("session", ""))+")" for x in reasons[:4])
        rows.append([r.get("symbol"), evidence, r.get("observed_at"), r.get("expires_at")])
    text += table("Automatic watchlist findings", ["Ticker", "Evidence / price change / session", "Observed UTC", "Expires UTC"], rows)
    text += table("Source errors", ["Source", "Error type"], [[r.get("source"), r.get("error_type")] for r in report.get("errors", [])])
    if publish != "success": text += "\nPublication is not confirmed; do not assume these findings reached the entry scanner.\n"
    return text


def paper(report, outcome, manual=None, automatic=None, universe=None):
    manual, automatic, universe = manual or {}, automatic or {}, universe or {}
    text = "# Alpaca scan / paper-entry findings\n\n"
    text += "- Runner step: "+cell(outcome)+"; report status: "+cell(report.get("status", "NO_REPORT"))+".\n"
    text += "- Mode: "+cell(report.get("mode"))+"; equity USD: "+number(report.get("equity"))+".\n"
    text += "- Existing planned risk at initial snapshot USD: "+number(report.get("planned_risk"))+".\n"
    if report.get("signal_session"):
        text += "- Signal session: "+cell(report["signal_session"])+"; next trading session: "+cell(report.get("next_trading_session"))+".\n"
    if not report or outcome != "success" or report.get("status") == "FAILED_CLOSED":
        text += "\nRun did not confirm successful completion. Order outcome may be unknown; inspect the Alpaca paper dashboard.\n"
    if report.get("status") in {"MARKET_CLOSED", "LATE_SESSION_NO_ENTRIES", "WAITING_FOR_COMPLETED_SESSION", "NEW_ENTRIES_DISABLED", "POSITION_CAPACITY", "BOOK_MANAGED_RECHECK_NEXT_RUN"}:
        text += "\nThis status can stop the run before a new setup scan. It is not proof that no setups exist.\n"
    candidates = report.get("candidates")
    if isinstance(candidates, list):
        text += "\nCandidates returned: "+str(len(candidates))+". Research candidates are not approved or filled orders.\n"
        rows = []
        for i,r in enumerate(candidates,1):
            h = r.get("history") or {}
            rows.append([r.get("rank",i), r.get("symbol"), number(r.get("reference_close",r.get("reference"))),
                number(r.get("stop")), number(r.get("reference_target",r.get("target"))), h.get("samples"), number(h.get("score"),3), number(r.get("quality"),3)])
        text += table("Ranked setup candidates", ["Rank","Ticker","Reference close","Stop","Reference target","Samples","Historical score R","Quality"], rows)
    else: text += "\nCandidate scan results were not recorded for this run.\n"
    orders = report.get("submitted")
    if isinstance(orders,list):
        text += "\nPaper order submissions recorded: "+str(len(orders))+". Submission is not a fill confirmation.\n"
        rows = []
        for r in orders:
            b = r.get("body") or {}
            rows.append([b.get("symbol"), b.get("qty"), number(b.get("limit_price")), number((b.get("stop_loss") or {}).get("stop_price")),
                number((b.get("take_profit") or {}).get("limit_price")), number(r.get("planned_risk")), r.get("order_id")])
        text += table("Submitted paper orders", ["Ticker","Qty","Entry limit","Stop","Target","Planned risk USD","Order ID"], rows)
    text += table("Manual watchlist accepted", ["Ticker","Already in normal universe"], [[r.get("symbol"),r.get("already_in_normal_universe")] for r in manual.get("accepted",[])])
    text += table("Manual watchlist rejected", ["Ticker or line","Reason"], [[r.get("symbol",r.get("line")),r.get("reason")] for r in manual.get("rejected",[])])
    text += "\nAutomatic-list status: "+cell(automatic.get("status", "REPORTED" if automatic else "NOT_RECORDED"))+".\n"
    text += table("Automatic watchlist merged", ["Ticker"], [[s] for s in automatic.get("accepted",[])])
    text += table("Automatic watchlist rejected by asset validation", ["Ticker"], [[s] for s in automatic.get("rejected",[])])
    text += "\nUniverse source: "+cell(universe.get("source", "NOT_RECORDED"))+"; verified symbols: "+cell(universe.get("verified_symbols"))+".\n"
    diag = report.get("diagnostics") or {}
    if report.get("error_code") or diag.get("failed_stage"):
        text += "\nError code: "+cell(report.get("error_code"))+"; stage: "+cell(diag.get("failed_stage"))+".\n"
    text += "\n— means the field was not recorded or unavailable, not zero. Historical scores are not win probabilities. Close-based targets need fresh entry validation.\n"
    return text


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--kind", choices=("discovery","paper"), required=True)
    args = parser.parse_args()
    outcome = os.getenv("RUN_OUTCOME", "unknown")
    if args.kind == "discovery":
        text = discovery(load(".discovery-state/watchlist_auto.json"), outcome, os.getenv("PUBLISH_OUTCOME", "unknown"))
    else:
        text = paper(load("reports/paper/status.json"), outcome, load("reports/paper/manual_watchlist.json"),
            load_auto("reports/paper/runner.log"), load("reports/paper/universe_source.json"))
    if os.getenv("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f: f.write(text+"\n")
    print(text)
