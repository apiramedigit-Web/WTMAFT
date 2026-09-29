"""Safe tests for the Gmail API Full Optimization Review alert. NO real e-mail, NO network and NO
real credential: every Gmail/Google request goes to a fake transport, with fake tokens and fake
credential files in a temp folder. Output: evidence/10_email_alert_test_results.json
"""
import base64
import contextlib
import copy
import datetime as dt
import email
import email.policy
import io
import json
import os
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import email_alert  # noqa: E402
import performance_alert as pa  # noqa: E402

BASE = pathlib.Path(__file__).resolve().parent.parent
FAKE_ACCESS = "ya29.FAKEaccessTOKEN_0123456789"
FAKE_REFRESH = "1//FAKErefreshTOKEN_0123456789"
FAKE_SECRET = "GOCSPX-FAKEclientSECRET0123"
RESULTS = []


def check(tid, name, cond, detail=""):
    RESULTS.append({"id": tid, "test": name, "result": "PASS" if cond else "FAIL", "detail": str(detail)[:400]})
    print(f'{"PASS" if cond else "FAIL"}  {tid}  {name}  {detail if not cond else ""}')


class FakeGmail:
    """Answers like Google tokeninfo + Gmail API; records every request."""
    def __init__(self, email_addr=email_alert.SENDER, scopes=(email_alert.GMAIL_SEND, "openid", "email"),
                 token_ok=True, api="enabled", send_status=200, echo_secret=False):
        self.calls, self.n = [], 0
        self.email, self.scopes, self.token_ok, self.api = email_addr, scopes, token_ok, api
        self.send_status, self.echo_secret = send_status, echo_secret

    def __call__(self, method, url, headers, body):
        self.calls.append({"method": method, "url": url, "headers": headers, "body": body})
        if url == email_alert.TOKENINFO:
            if not self.token_ok:
                return 400, json.dumps({"error": "invalid_token"})
            return 200, json.dumps({"email": self.email, "email_verified": "true", "scope": " ".join(self.scopes)})
        if url.endswith("/profile"):
            if self.api == "disabled":
                return 403, json.dumps({"error": {"code": 403, "status": "PERMISSION_DENIED",
                                                  "details": [{"reason": "SERVICE_DISABLED"}]}})
            return 403, json.dumps({"error": {"code": 403, "message": "Request had insufficient authentication "
                                                                      "scopes.", "status": "PERMISSION_DENIED"}})
        self.n += 1
        if self.send_status != 200:
            extra = f' {headers["Authorization"]} "refresh_token": "{FAKE_REFRESH}" {FAKE_SECRET}' \
                if self.echo_secret else ""
            return self.send_status, json.dumps({"error": {"code": self.send_status, "message": "bad" + extra}})
        return 200, json.dumps({"id": f"fake-gmail-{self.n:04d}", "threadId": "t1", "labelIds": ["SENT"]})


def fake_cfg():
    return {"sender": email_alert.SENDER, "get_token": lambda: FAKE_ACCESS}


def ds_for(cur_start, prev_orders, curr_orders, verified="2026-09-29", in_dataset=True):
    cs = dt.date.fromisoformat(cur_start)
    fmt = lambda d: d.isoformat()  # noqa: E731
    perf = [{"asin": "B0TEST00001", "account": "amazon Ledsone", "prev_orders": prev_orders,
             "curr_orders": curr_orders, "order_chg": round((curr_orders - prev_orders) / prev_orders * 100, 1),
             "prev_impressions": 1000, "curr_impressions": 900, "impression_chg": -10.0, "prev_clicks": 50,
             "curr_clicks": 40, "ctr_chg": -11.1, "cvr_chg": -5.0}] if in_dataset else []
    return {"meta": {"marketplace": "Amazon UK", "current_7d": [fmt(cs), fmt(cs + dt.timedelta(6))],
                     "previous_7d": [fmt(cs - dt.timedelta(7)), fmt(cs - dt.timedelta(1))]},
            "performance": perf,
            "keyword_analysis": [{"asin": "B0TEST00001", "sku": "WCTEST2PK", "account": "amazon Ledsone",
                                  "backend_keyword_status": "Requires Review", "duplicate_words_removed": 7}],
            "change_record": [{"asin": "B0TEST00001", "sku": "WCTEST2PK", "date_changed": "2026-09-28",
                               "submission": {"state": "UPDATED / VERIFIED", "checked_at_utc": verified + "T04:00:00Z",
                                              "post_date": "2026-09-28"}}]}


class Sandbox:
    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = pathlib.Path(self.tmp.name)
        self.ledger, self.data = self.dir / "cycles.json", self.dir / "ds.json"
        self.out = io.StringIO()

    def week(self, ds, mode="dry_run", cfg=None, transport=None):
        self.data.write_text(json.dumps(ds), encoding="utf-8")
        with contextlib.redirect_stdout(self.out):
            return pa.run(mode, dataset=self.data, ledger_path=self.ledger, evid_dir=self.dir, cfg=cfg,
                          transport=transport)

    def all_text(self):
        return self.out.getvalue() + "".join(p.read_text(encoding="utf-8-sig") for p in self.dir.iterdir())


def raises(fn, *a, **k):
    try:
        fn(*a, **k)
    except email_alert.AlertConfigError as e:
        return str(e)
    return None


def main():
    names = ("WTMA_GMAIL_CLIENT_FILE", "WTMA_GMAIL_TOKEN_FILE")
    saved_env = {k: os.environ.get(k) for k in names}
    for k in names:
        os.environ.pop(k, None)
    try:
        run_tests()
    finally:
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    p, f = sum(r["result"] == "PASS" for r in RESULTS), sum(r["result"] == "FAIL" for r in RESULTS)
    (BASE / "evidence" / "10_email_alert_test_results.json").write_text(json.dumps(
        {"run_at": dt.datetime.now().isoformat(timespec="seconds"), "transport": "Gmail API (OAuth 2.0)",
         "mode": "safe test mode: fake Gmail/Google transport, fake tokens, temp credential files; "
                 "no real e-mail, no network", "passed": p, "failed": f, "results": RESULTS},
        indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"{p} PASS / {f} FAIL")
    return 0 if f == 0 else 1


def run_tests():
    tmp = pathlib.Path(tempfile.mkdtemp())
    client = tmp / "client.json"
    client.write_text(json.dumps({"installed": {"client_id": "x", "client_secret": FAKE_SECRET}}), encoding="utf-8")
    token = tmp / "token.json"

    # A. missing configuration -> clean errors
    m = raises(email_alert.load_config, {})
    check("A1", "WTMA_GMAIL_CLIENT_FILE missing -> AlertConfigError", m and "WTMA_GMAIL_CLIENT_FILE is not set" in m, m)
    m = raises(email_alert.load_config, {"WTMA_GMAIL_CLIENT_FILE": str(client), "WTMA_GMAIL_TOKEN_FILE": str(token)})
    check("A2", "token not yet authorized -> AlertConfigError naming gmail_authorize.py",
          m and "gmail_authorize.py" in m, m)
    out, argv = io.StringIO(), sys.argv
    sys.argv = ["performance_alert.py", "--validate"]
    try:
        with contextlib.redirect_stdout(out):
            rc = pa.main()
    finally:
        sys.argv = argv
    check("A3", "CLI --validate without config -> exit 2 + CONFIG ERROR line, no traceback",
          rc == 2 and "CONFIG ERROR:" in out.getvalue(), out.getvalue())

    # B. invalid configuration -> clean errors / not ready
    inside = BASE / "data" / "token_should_not_be_here.json"
    m = raises(email_alert.gmail_paths, {"WTMA_GMAIL_CLIENT_FILE": str(client), "WTMA_GMAIL_TOKEN_FILE": str(inside)})
    check("B1", "token path inside the repository -> refused", m and "inside the repository" in m, m)
    m = raises(email_alert.gmail_paths, {"WTMA_GMAIL_CLIENT_FILE": "relative.json", "WTMA_GMAIL_TOKEN_FILE": str(token)})
    check("B2", "relative credential path -> refused", m and "absolute" in m, m)
    m = raises(email_alert.gmail_paths, {"WTMA_GMAIL_CLIENT_FILE": str(tmp / "nope.json"),
                                         "WTMA_GMAIL_TOKEN_FILE": str(token)})
    check("B3", "client file missing -> refused", m and "does not exist" in m, m)
    cfg = fake_cfg()
    r = email_alert.validate_gmail(cfg, FakeGmail(token_ok=False))
    check("B4", "token rejected by Google -> not ready", not r["ready"] and "rejected" in (r["blocker"] or ""), r)
    r = email_alert.validate_gmail(cfg, FakeGmail(email_addr="someone.else@gmail.com"))
    check("B5", "authorized account is not apiramedigit@gmail.com -> not ready",
          not r["ready"] and "not apiramedigit@gmail.com" in r["blocker"], r["blocker"])
    r = email_alert.validate_gmail(cfg, FakeGmail(scopes=("openid", "email")))
    check("B6", "gmail.send scope missing -> not ready", not r["ready"] and "gmail.send" in r["blocker"], r["blocker"])
    r = email_alert.validate_gmail(cfg, FakeGmail(api="disabled"))
    check("B7", "Gmail API disabled in project -> not ready", not r["ready"] and not r["gmail_api_reachable"], r)
    r = email_alert.validate_gmail(cfg, FakeGmail())
    check("B8", "correct sender + gmail.send + API reachable -> ready", r["ready"] and r["gmail_send_scope"], r)
    check("B9", "validation report contains no token", FAKE_ACCESS not in json.dumps(r))

    # C/D. decline cycles (dry run; a transport that fails if used)
    def no_network(*a):
        raise AssertionError("network used in dry run")
    s = Sandbox()
    s.week(ds_for("2026-09-27", 10, 5))
    res = s.week(ds_for("2026-10-04", 10, 6), transport=no_network)
    c = json.loads(s.ledger.read_text(encoding="utf-8"))["cycles"]
    check("C0", "cycle overlapping the verification date is not counted",
          c["B0TEST00001|2026-09-27_2026-10-03"]["eligible_after_live_verification"] is False)
    check("C1", "one decline cycle after live verification -> count 1, no alert",
          res["due"] == 0 and c["B0TEST00001|2026-10-04_2026-10-10"]["consecutive_decline_count"] == 1, res)
    res = s.week(ds_for("2026-10-11", 6, 4), transport=no_network)
    check("D1", "two consecutive decline cycles -> alert condition generated (dry run, not sent)",
          res["due"] == 1 and res["results"][0]["this_run"] == "DRY_RUN (not sent)"
          and res["results"][0]["consecutive_decline_count"] == 2, res["results"])
    check("D2", "alert subject exact", res["results"][0]["subject"] ==
          "⚠️ Full Optimization Review Required – ASIN B0TEST00001 – 2 Consecutive 7D Performance Drops")
    body = email_alert.build_alert(pa.alert_context(
        json.loads(s.ledger.read_text(encoding="utf-8"))["cycles"]["B0TEST00001|2026-10-11_2026-10-17"]))
    need = ["B0TEST00001", "WCTEST2PK", "amazon Ledsone", "Amazon UK", "2026-10-11 → 2026-10-17",
            "2026-10-04 → 2026-10-10", "Order Change %", "Impression Change %", "CTR Change %", "CVR Change %",
            "Consecutive decline count: 2", "Last backend keyword fine-tuning date: 2026-09-28",
            "Last live verification date: 2026-09-29", "Requires Review", "Duplicate words removed: 7",
            "ph_task id 1940", email_alert.NO_VISIBLE_CHANGE]
    missing = [n for n in need if n not in body["text"]]
    check("D3", "e-mail content has every required field + the no-visible-change message", not missing, missing)
    s2 = Sandbox()
    for start, p, cur in (("2026-10-04", 10, 6), ("2026-10-11", 6, 8), ("2026-10-18", 8, 5)):
        res = s2.week(ds_for(start, p, cur))
    check("D4", "decline, improve, decline -> count 1, no alert", res["due"] == 0 and
          json.loads(s2.ledger.read_text(encoding="utf-8"))["cycles"]["B0TEST00001|2026-10-18_2026-10-24"]
          ["consecutive_decline_count"] == 1)
    s3 = Sandbox()
    s3.week(ds_for("2026-10-04", 10, 6))
    res = s3.week(ds_for("2026-10-18", 6, 4))
    check("D5", "decline, missing week, decline -> not consecutive, no alert", res["due"] == 0)
    s4 = Sandbox()
    s4.week(ds_for("2026-10-04", 10, 6))
    s4.week(ds_for("2026-10-11", 6, 4, in_dataset=False))
    res = s4.week(ds_for("2026-10-18", 6, 4))
    check("D6", "week without performance data breaks the streak", res["due"] == 0)

    # E/F/H. send path with the fake Gmail transport
    fake = FakeGmail()
    s5 = Sandbox()
    s5.week(ds_for("2026-10-04", 10, 6), "send", cfg, fake)
    res1 = s5.week(ds_for("2026-10-11", 6, 4), "send", cfg, fake)
    res2 = s5.week(ds_for("2026-10-11", 6, 4), "send", cfg, fake)
    check("E1", "first send -> SENT", res1["results"][0]["this_run"] == "SENT", res1["results"])
    check("E2", "same ASIN/cycle again -> BLOCKED_DUPLICATE, no second Gmail request",
          res2["results"][0]["this_run"].startswith("BLOCKED_DUPLICATE") and fake.n == 1, (res2["results"], fake.n))
    sends = [c for c in fake.calls if c["url"].endswith("/messages/send")]
    mime = email.message_from_bytes(base64.urlsafe_b64decode(json.loads(sends[0]["body"])["raw"]),
                                    policy=email.policy.default)
    to = sorted(a.strip().lower() for a in mime["To"].split(","))
    check("F1", "ONE Gmail message with BOTH recipients in To",
          len(sends) == 1 and to == ["apiramedigit@gmail.com", "roshandigitweb@gmail.com"] and not mime["Cc"]
          and not mime["Bcc"], mime["To"])
    check("F2", "From = apiramedigit@gmail.com; subject exact; key header set",
          mime["From"] == "apiramedigit@gmail.com" and mime["Subject"] ==
          "⚠️ Full Optimization Review Required – ASIN B0TEST00001 – 2 Consecutive 7D Performance Drops"
          and mime["X-WTMA-Alert-Key"] == "wtma-foa-B0TEST00001-2026-10-11_2026-10-17", dict(mime.items()))
    plain = mime.get_body(("plain",)).get_content()
    check("F3", "body keeps the Full Optimization warning + alert fields",
          email_alert.NO_VISIBLE_CHANGE in plain and "Consecutive decline count: 2" in plain)
    check("F4", "recipient list must be exactly the two addresses",
          raises(email_alert.validate_recipients, ["apiramedigit@gmail.com"]) is not None)
    a = json.loads(s5.ledger.read_text(encoding="utf-8"))["alerts"]["B0TEST00001|2026-10-11_2026-10-17"]
    check("H1", "Gmail response recorded: message id, sent time, recipients, status",
          a["message_id"] == "fake-gmail-0001" and a["alert_sent_at"] and a["alert_status"] == "SENT"
          and len(a["recipients"]) == 2, a)
    fail = FakeGmail(send_status=401, echo_secret=True)
    s6 = Sandbox()
    s6.week(ds_for("2026-10-04", 10, 6), "send", cfg, fail)
    r6 = s6.week(ds_for("2026-10-11", 6, 4), "send", cfg, fail)
    check("E3", "failed send -> FAILED (retried next run, not marked sent)",
          r6["results"][0]["this_run"] == "FAILED", r6["results"])

    # G. no secret in logs/evidence (the failing transport even echoes token, refresh token and secret)
    secrets = (FAKE_ACCESS, FAKE_REFRESH, FAKE_SECRET)
    leaks = [(n, x) for n, sb in (("dry", s), ("send", s5), ("failed-send", s6)) for x in secrets if x in sb.all_text()]
    check("G1", "no access token / refresh token / client secret in stdout, ledger or evidence", not leaks, leaks)
    check("G2", "secrets echoed in an API error are redacted",
          not any(x in (r6["results"][0]["error"] or "") for x in secrets)
          and "[REDACTED]" in (r6["results"][0]["error"] or ""), r6["results"][0]["error"])
    check("G3", "redact() masks token-shaped strings and JSON secret fields",
          email_alert.redact('a ya29.abc-DEF_1 b 1//0gABCDEFGHIJK c GOCSPX-xyz {"refresh_token": "zzz"}')
          == 'a [REDACTED] b [REDACTED] c [REDACTED] {"refresh_token": "[REDACTED]"}')

    # Real dataset, dry run, isolated ledger: current state must not alert
    s7 = Sandbox()
    real = json.loads((BASE / "data" / "report_dataset.json").read_text(encoding="utf-8"))
    res = s7.week(copy.deepcopy(real), transport=no_network)
    check("R1", "real dataset (13-19 Sep): tracked = live-verified ASINs, 0 alerts due",
          res["due"] == 0 and res["tracked"] == len(pa.tracked_asins(real)), res)


if __name__ == "__main__":
    sys.exit(main())
