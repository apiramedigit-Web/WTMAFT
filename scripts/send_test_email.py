"""One clearly marked TEST e-mail through the production Gmail transport (user-approved 2026-09-29).

* Recipient is restricted to the sender account (apiramedigit@gmail.com) ONLY. The production
  recipient list (both addresses) in email_alert.py is not used or changed.
* Content = the production alert body built from a real ledger row, with a TEST banner and
  "[TEST]" subject prefix. The row has NOT met the alert condition; this is a delivery test only.
* Recorded in evidence/10_gmail_test_email.json; NOT written to data/monitoring_cycles.json,
  so the production alert log and duplicate protection are unaffected.
* Refuses to send twice unless --again is passed.
"""
import argparse
import base64
import datetime as dt
import json
import pathlib
import sys
from email.message import EmailMessage

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import email_alert  # noqa: E402
import performance_alert as pa  # noqa: E402

BASE = pathlib.Path(__file__).resolve().parent.parent
EVID = BASE / "evidence" / "10_gmail_test_email.json"
TEST_TO = "apiramedigit@gmail.com"
BANNER = ("*** TEST EMAIL - NOT A REAL ALERT ***\nDelivery test of the Full Optimization Review alert via the Gmail "
          "API. The ASIN below has NOT met the alert condition (2 consecutive 7D declines after live verification). "
          "No action is required.\n\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--again", action="store_true")
    a = ap.parse_args()
    assert TEST_TO == email_alert.SENDER, "test recipient must be the sender account only"
    if EVID.exists() and json.loads(EVID.read_text(encoding="utf-8")).get("status") == "SENT" and not a.again:
        print("A TEST e-mail was already sent (see evidence/10_gmail_test_email.json). Not sending again.")
        return 1
    cfg = email_alert.load_config()
    rep = email_alert.validate_gmail(cfg)
    if not rep["ready"]:
        print("NOT SENDING - Gmail configuration not ready:", rep["blocker"])
        return 2
    ledger = json.loads(pa.LEDGER.read_text(encoding="utf-8"))
    row = sorted(ledger["cycles"].values(), key=lambda c: (c["cycle_id"], c["asin"]))[-1]
    alert = email_alert.build_alert(pa.alert_context(row))
    msg = EmailMessage()
    msg["From"] = email_alert.SENDER
    msg["To"] = TEST_TO
    msg["Subject"] = "[TEST] " + alert["subject"]
    msg["X-WTMA-Alert-Key"] = f"wtma-foa-TEST-{row['asin']}-{row['cycle_id']}"
    msg.set_content(BANNER + alert["text"])
    msg.add_alternative('<p style="background:#fde68a;padding:8px;font-family:Arial"><b>TEST EMAIL – NOT A REAL '
                        'ALERT.</b> Delivery test only; this ASIN has NOT met the alert condition. No action is '
                        'required.</p>' + alert["html"], subtype="html")
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
    tok = email_alert.access_token(cfg)
    status, js = email_alert._http("POST", f"{email_alert.GMAIL_API}/messages/send",
                                   {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"},
                                   json.dumps({"raw": raw}).encode("utf-8"))
    ok = status == 200 and isinstance(js, dict) and bool(js.get("id"))
    rec = {"type": "TEST (not a production alert; not in data/monitoring_cycles.json)",
           "status": "SENT" if ok else "FAILED", "sent_at": dt.datetime.now().isoformat(timespec="seconds"),
           "from": email_alert.SENDER, "to": [TEST_TO], "subject": msg["Subject"],
           "sample_row": {"asin": row["asin"], "cycle_id": row["cycle_id"],
                          "consecutive_decline_count": row.get("consecutive_decline_count")},
           "http_status": status, "gmail_message_id": js.get("id") if ok else None,
           "gmail_thread_id": js.get("threadId") if ok else None, "gmail_labels": js.get("labelIds") if ok else None,
           "error": None if ok else email_alert.redact(js)}
    EVID.write_text(json.dumps(rec, indent=1, ensure_ascii=False), encoding="utf-8")
    print(email_alert.redact(json.dumps(rec, ensure_ascii=False)))
    return 0 if ok else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except email_alert.AlertConfigError as e:
        print("CONFIG ERROR:", email_alert.redact(e))
        sys.exit(2)
