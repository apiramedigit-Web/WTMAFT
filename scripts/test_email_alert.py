"""E-mail alerting is RETIRED (business instruction 2026-10-02): these tests prove the production workflow
contains no e-mail path at all. They replace the earlier Gmail alert tests, whose results stay untouched as
historical evidence in evidence/10_email_alert_test_results.json (the old Gmail test e-mail record,
evidence/10_gmail_test_email.json, is also kept as history and is read by nothing in production).

Checks: the pipeline has no e-mail stage and no --send; the runner refuses e-mail stages; performance_alert.py
--send / --validate are refused without any network use; no production module imports the e-mail / Gmail code;
the scheduler registration carries no Gmail settings; a monitoring run with a Performance Decline sends nothing and
creates no Gmail token; the dashboard carries no e-mail record. No network, no real e-mail, temp files only.
Output: evidence/18_no_email_alert_test_results.json
"""
import contextlib
import datetime as dt
import io
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import performance_alert as pa  # noqa: E402

BASE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "automation"))
import run as runner  # noqa: E402

PRODUCTION = ["automation/run.py", "automation/scheduler.ps1", "scripts/extract.py", "scripts/optimization_cleanup.py",
              "scripts/build_dataset.py", "scripts/performance_alert.py", "scripts/render.py", "scripts/validate.py",
              "scripts/report_template.html", "scripts/keyword_finetune.py", "scripts/keyword_live_verify.py",
              "scripts/keyword_live_update.py"]
RETIRED = ("email_alert", "send_test_email", "gmail_authorize")
RESULTS = []


def check(tid, name, cond, detail=""):
    RESULTS.append({"id": tid, "test": name, "result": "PASS" if cond else "FAIL", "detail": str(detail)[:400]})
    print(f'{"PASS" if cond else "FAIL"}  {tid}  {name}  {detail if not cond else ""}')


def src(rel):
    return (BASE / rel).read_text(encoding="utf-8")


def main():
    # N1 pipeline: no e-mail stage, no --send, in production and dry-run mode
    stages = runner.pipeline(False) + runner.pipeline(True)
    flat = [x for _, c in stages for x in c]
    check("N1", "production + dry-run pipeline: no e-mail stage, no --send / --validate, no Gmail script",
          not [x for x in flat if x in ("--send", "--validate") or any(r in x for r in RETIRED)]
          and [n for n, _ in runner.pipeline(False)] == ["extract", "weekly_keyword_check", "build_dataset",
                                                          "performance_monitoring", "render", "validate"], stages)

    # N2 runner refuses e-mail stages before anything runs
    tmp = pathlib.Path(tempfile.mkdtemp())
    refused = []
    for stage in (["scripts/performance_alert.py", "--send"], ["scripts/email_alert.py"],
                  ["scripts/send_test_email.py"], ["scripts/gmail_authorize.py"]):
        p = subprocess.run([sys.executable, str(BASE / "automation" / "run.py"), "--stages-json", json.dumps([["x"] + stage]),
                            "--log-dir", str(tmp / "logs"), "--evidence", str(tmp / "runs.json")],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        refused.append((stage, p.returncode, "REFUSED" in p.stdout))
    check("N2", "automation/run.py refuses performance_alert --send / email_alert / send_test_email / gmail_authorize stages",
          all(rc == 2 and ok for _, rc, ok in refused) and not (tmp / "runs.json").exists(), refused)

    # N3 performance_alert --send / --validate refused, with every network call made to fail loudly
    calls = []
    real = urllib.request.urlopen
    urllib.request.urlopen = lambda *a, **k: calls.append(a) or (_ for _ in ()).throw(AssertionError("network"))
    try:
        outs = []
        for flag in ("--send", "--validate"):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                outs.append((pa.main([flag]), out.getvalue()))
    finally:
        urllib.request.urlopen = real
    check("N3", "performance_alert.py --send / --validate -> REFUSED (exit 2), nothing sent, no network request",
          all(rc == 2 and "REFUSED" in o and "Nothing was sent" in o for rc, o in outs) and not calls, outs)

    # N4 no production module imports or calls the e-mail / Gmail code
    hits = {}
    for rel in PRODUCTION:
        s = src(rel)
        found = re.findall(r"^\s*(?:import|from)\s+(?:email_alert|send_test_email|gmail_authorize)\b", s, re.M)
        found += re.findall(r"googleapis\.com/gmail|gmail\.send|WTMA_GMAIL_\w+|smtplib", s)
        if found:
            hits[rel] = found
    check("N4", "no production module imports email_alert / send_test_email / gmail_authorize, or uses Gmail API / SMTP / Gmail env",
          not hits, hits)
    check("N5", "the runner needs no Gmail credentials (USER_ENV = WLP_SOURCE_DB_URL only)", runner.USER_ENV == ("WLP_SOURCE_DB_URL",),
          runner.USER_ENV)
    sched = src("automation/scheduler.ps1")
    check("N6", "scheduler registration: no --send, no Gmail paths; task action is run.py only",
          "--send" not in sched and "GMAIL" not in sched.upper().replace("NO E-MAIL", "") and "run.py" in sched)

    # N7 a Performance Decline creates no e-mail, no alert record and no Gmail token
    d = pathlib.Path(tempfile.mkdtemp())
    token = d / "gmail_token.json"
    os.environ["WTMA_GMAIL_TOKEN_FILE"] = str(token)
    led = {"keyword_rows": {"B0NOMAIL01|SKU|8": {
        "asin": "B0NOMAIL01", "sku": "SKU", "sub_source": 8, "status": "Live Verified",
        "live_updates": [{"date": "2026-09-29", "confirmed_at": "2026-09-29T04:00:00Z", "source": "test",
                          "user_reported_date": None, "value": "x"}]}}}
    (d / "led.json").write_text(json.dumps(led), encoding="utf-8")
    av = [(dt.date(2026, 9, 29) + dt.timedelta(i)).isoformat() for i in range(14)]
    ds = {"monitoring_data": {"available_dates": av, "available_through": av[-1],
                              "orders": {"B0NOMAIL01": {x: (3 if i < 7 else 1) for i, x in enumerate(av)}}}}
    (d / "ds.json").write_text(json.dumps(ds), encoding="utf-8")
    urllib.request.urlopen = lambda *a, **k: calls.append(a) or (_ for _ in ()).throw(AssertionError("network"))
    try:
        mon = pa.run(dataset=d / "ds.json", ledger_path=d / "led.json", evid_dir=d)
    finally:
        urllib.request.urlopen = real
        os.environ.pop("WTMA_GMAIL_TOKEN_FILE", None)
    c = mon["B0NOMAIL01|2026-09-29"]
    out_led = json.loads((d / "led.json").read_text(encoding="utf-8"))
    check("N7", "Week 2 < Week 1 -> Performance Decline is shown as a status only: no e-mail, no network, no alert record, no token file",
          c["performance_status"] == pa.P_DECLINE and not calls and not token.exists()
          and "alerts" not in out_led and not [k for k in c if "alert" in k or "mail" in k], c)

    # N8 dashboard + historical evidence
    html = src("output/weekly_top_moving_asin_backend_keyword_fine_tuning_report.html")
    check("N8", "dashboard embeds no e-mail record (no alert_monitoring / test_email / Gmail message id)",
          "alert_monitoring" not in html and "gmail_message_id" not in html and '"test_email"' not in html)
    hist = [BASE / "evidence" / n for n in ("10_email_alert_test_results.json", "10_gmail_test_email.json", "10_email_alerting.md")]
    check("N9", "historical e-mail evidence is kept as history (not rewritten) and read by no production module",
          all(p.exists() for p in hist) and not any(p.name in src(rel) for p in hist for rel in PRODUCTION),
          [p.name for p in hist if not p.exists()])

    p_, f_ = sum(r["result"] == "PASS" for r in RESULTS), sum(r["result"] == "FAIL" for r in RESULTS)
    (BASE / "evidence" / "18_no_email_alert_test_results.json").write_text(json.dumps(
        {"run_at": dt.datetime.now().isoformat(timespec="seconds"),
         "rule": "e-mail alerts removed from the production workflow (business instruction 2026-10-02)",
         "mode": "static source checks + refused CLI calls + temp-ledger monitoring run; network calls patched to fail",
         "passed": p_, "failed": f_, "results": RESULTS}, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"{p_} PASS / {f_} FAIL")
    return 0 if f_ == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
