"""Render data/report_dataset.json into the standalone HTML report (CSS, JS and data inline).

The Full Optimization Review section (12) also embeds the monitoring-cycle ledger written by
performance_alert.py (data/monitoring_cycles.json) and, for reference only, the separate TEST
e-mail record (evidence/10_gmail_test_email.json). Neither is written into report_dataset.json.
"""
import json
import pathlib

BASE = pathlib.Path(__file__).resolve().parent.parent
TEMPLATE = BASE / "scripts" / "report_template.html"
DATASET = BASE / "data" / "report_dataset.json"
LEDGER = BASE / "data" / "monitoring_cycles.json"
TEST_EMAIL = BASE / "evidence" / "10_gmail_test_email.json"
OUT = BASE / "output" / "weekly_top_moving_asin_backend_keyword_fine_tuning_report.html"
BS = chr(92)  # backslash, built at runtime so no editor can collapse the escape
EMPTY_LEDGER = {"cycles": {}, "alerts": {}}


def alert_monitoring(ledger, test_email=None):
    """Only non-secret fields: the ledger holds metrics, dates, statuses, recipients and message ids."""
    t = None
    if test_email:
        t = {k: test_email.get(k) for k in ("status", "sent_at", "to", "subject", "gmail_message_id")}
    opts = [o for asin in sorted(ledger.get("optimizations", {})) for o in ledger["optimizations"][asin]]
    return {"cycles": sorted(ledger.get("cycles", {}).values(), key=lambda c: (c["asin"], c["cycle_id"])),
            "alerts": sorted(ledger.get("alerts", {}).values(), key=lambda a: (a["asin"], a["cycle_id"])),
            "optimizations": opts, "keyword_rows": ledger.get("keyword_rows", {}), "test_email": t}


def render(ds, out_path, ledger=None, test_email=None):
    ds = {**ds, "alert_monitoring": alert_monitoring(ledger or EMPTY_LEDGER, test_email)}
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
    out = render(json.loads(DATASET.read_text(encoding="utf-8")), OUT,
                 load_json(LEDGER, EMPTY_LEDGER), load_json(TEST_EMAIL))
    print(out, out.stat().st_size, "bytes")


if __name__ == "__main__":
    main()
