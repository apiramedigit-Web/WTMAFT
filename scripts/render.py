"""Render data/report_dataset.json into the standalone HTML report (CSS, JS and data inline).

Sections 8 and 12 also embed the post-update monitoring cycles and the weekly keyword-check rows from the
ledger written by performance_alert.py / optimization_cleanup.py (data/monitoring_cycles.json). Neither is
written into report_dataset.json. No e-mail record is embedded (e-mail alerting removed 2026-10-02).
"""
import json
import pathlib

BASE = pathlib.Path(__file__).resolve().parent.parent
TEMPLATE = BASE / "scripts" / "report_template.html"
DATASET = BASE / "data" / "report_dataset.json"
LEDGER = BASE / "data" / "monitoring_cycles.json"
OUT = BASE / "output" / "weekly_top_moving_asin_backend_keyword_fine_tuning_report.html"
BS = chr(92)  # backslash, built at runtime so no editor can collapse the escape
EMPTY_LEDGER = {"cycles": {}, "alerts": {}}


def keyword_monitoring(ledger):
    """Only non-secret fields: monitoring cycles (dates, orders, statuses) and keyword-check rows."""
    return {"monitoring": sorted(ledger.get("monitoring", {}).values(),
                                 key=lambda c: (c["asin"], c.get("live_update_date") or "")),
            "keyword_rows": ledger.get("keyword_rows", {})}


def render(ds, out_path, ledger=None):
    ds = {**ds, "keyword_monitoring": keyword_monitoring(ledger or EMPTY_LEDGER)}
    payload = json.dumps(ds, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    # Keep the JSON inert inside <script>: no "</script>" or "<!--" can terminate/open markup.
    payload = payload.replace("</", "<" + BS + "/").replace("<!--", "<" + BS + "u0021--")
    html = TEMPLATE.read_text(encoding="utf-8")
    assert html.count("__REPORT_DATA__") == 1
    out_path.write_text(html.replace("__REPORT_DATA__", payload), encoding="utf-8", newline="\n")
    return out_path


def load_json(path, default=None):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def main():
    out = render(json.loads(DATASET.read_text(encoding="utf-8")), OUT, load_json(LEDGER, EMPTY_LEDGER))
    print(out, out.stat().st_size, "bytes")


if __name__ == "__main__":
    main()
