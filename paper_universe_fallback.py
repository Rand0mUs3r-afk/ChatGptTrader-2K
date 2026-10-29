"""Validated public-source fallback; broker identity checks remain mandatory."""
from datetime import datetime, timezone
from functools import wraps
import json
from pathlib import Path
import re

URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"


class FallbackError(RuntimeError):
    pass


def parse_names(html, minimum=450):
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", id="constituents")
    if table is None: raise FallbackError("FALLBACK_TABLE_MISSING")
    header_row = table.find("tr")
    if header_row is None: raise FallbackError("FALLBACK_HEADER_MISSING")
    headers = [x.get_text(" ", strip=True) for x in header_row.find_all("th")]
    if "Symbol" not in headers or "Security" not in headers: raise FallbackError("FALLBACK_SCHEMA_CHANGED")
    names = {}
    for row in table.find_all("tr")[1:]:
        cells = row.find_all("td")
        if not cells: continue
        if len(cells) != len(headers): raise FallbackError("FALLBACK_ROW_INVALID")
        symbol = cells[headers.index("Symbol")].get_text(strip=True)
        name = cells[headers.index("Security")].get_text(" ", strip=True)
        if not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,9}", symbol) or not name or symbol in names:
            raise FallbackError("FALLBACK_SYMBOL_INVALID")
        names[symbol] = name
    if not minimum <= len(names) <= 550: raise FallbackError("FALLBACK_COUNT_INVALID")
    return names


def verified(names, assets, normalize, minimum=400):
    names = dict(names)
    assets = {a["symbol"]: a for a in assets}
    for symbol in ("SPY", "QQQ"):
        names[symbol] = assets.get(symbol, {}).get("name", "")
    selected = []
    for symbol, name in sorted(names.items()):
        a = assets.get(symbol, {})
        if symbol not in {"AMD", "AMZN"} and normalize(name) and normalize(name) == normalize(a.get("name", "")) and a.get("tradable") is True and a.get("class") == "us_equity" and a.get("status") == "active":
            selected.append(symbol)
    if len(selected) < minimum: raise FallbackError("FALLBACK_VERIFIED_COUNT_LOW")
    return selected


def save(report):
    out = Path("reports/paper"); out.mkdir(parents=True, exist_ok=True)
    (out/"universe_source.json").write_text(json.dumps(report, indent=2)+"\n")
    print(json.dumps({"universe_source": report}))


def with_fallback(primary):
    @wraps(primary)
    def universe(api):
        try:
            result = primary(api)
            save(dict(source="slickcharts", verified_symbols=len(result), fallback_used=False))
            return result
        except RuntimeError as exc:
            # Do not hide broker failures, identity failures or an inadequate verified list.
            if type(exc) is not RuntimeError or str(exc) != "Universe unavailable": raise
        except Exception as exc:
            import requests
            if not isinstance(exc, requests.exceptions.RequestException): raise
        import requests
        import paper_execution as base
        report = dict(source=URL, fallback_used=True, status="FAILED", observed_at_utc=datetime.now(timezone.utc).isoformat())
        try:
            # A fresh public request, never the credential-bearing broker session.
            response = requests.get(URL, headers={"User-Agent": "ChatGptTrader-2K/0.2 (personal paper-trading research)"}, timeout=25, allow_redirects=False)
            report["http_status"] = response.status_code
            if response.status_code != 200: raise FallbackError("FALLBACK_HTTP_ERROR")
            names = parse_names(response.text)
            assets = api.call("GET", "/v2/assets", params={"status": "active", "asset_class": "us_equity"})
            result = verified(names, assets, base.normalize)
            report.update(status="VERIFIED", source_symbols=len(names), verified_symbols=len(result))
            return result
        except FallbackError as exc:
            report["error_code"] = str(exc)
            raise
        finally:
            save(report)
    return universe


def main():
    import paper_execution as base
    import paper_stage_report as reporting
    original = base.universe
    base.universe = with_fallback(original)
    try:
        return reporting.main()
    finally:
        base.universe = original


if __name__ == "__main__":
    raise SystemExit(main())
