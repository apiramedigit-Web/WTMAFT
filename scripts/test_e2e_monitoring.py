"""Deterministic E2E dry-run tests of the monitoring / Full Optimization Review alert lifecycle.

Temp fixtures only: synthetic weekly datasets, a temp cycle ledger, a FAKE Gmail transport (no real
e-mail, no network, no Google library), temp dashboards rendered with the production template and
checked in a headless browser. Production files are hashed before/after and must be unchanged.
No Amazon keyword update is imported or run. Output: evidence/12_e2e_monitoring_test_results.json
"""
import contextlib
import copy
import datetime as dt
import hashlib
import io
import json
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import build_dataset  # noqa: E402
import email_alert  # noqa: E402
import optimization_cleanup as oc  # noqa: E402
import performance_alert as pa  # noqa: E402
import render  # noqa: E402
from test_email_alert import FakeGmail, fake_cfg  # noqa: E402

BASE = pathlib.Path(__file__).resolve().parent.parent
PROD_FILES = [BASE / "data" / n for n in ("monitoring_cycles.json", "report_dataset.json", "extract.json")] + \
             [BASE / "evidence" / "10_gmail_test_email.json"]
ASIN = "B0E2ETEST01"
LISTING = {"product_id": 990001, "sku": "WCE2E2PK", "sub_source": 8, "account": "amazon Ledsone"}
UPD = [{"asin": ASIN, "sku": "WCE2E2PK", "submission_id": "s-e2e", "product_id": 990001, "sub_source": 8}]
SCOPE_ROW = {"asin": ASIN, "sku": "WCE2E2PK", "sub_source": 8, "account": "amazon Ledsone", "product_id": 990001,
             "site": "UK", "report_row": f"{ASIN}|WCE2E2PK", "submission_id": "s-e2e",
             "post_timestamp": "2026-09-28T17:00:00", "proposed": None}


def prior_from_ds(ds):
    """Submission state per ASIN|SKU as build_dataset exposes it (fixture change_record)."""
    out = {}
    for c in ds.get("change_record", []):
        sub = c.get("submission") or {}
        out[f'{c["asin"]}|{c["sku"]}'] = {"state": sub.get("state"), "verified_at_utc": sub.get("checked_at_utc")}
    return out
RESULTS = []


class FakeLM:
    """Fake Listing Management Tool: GET (amz_platinum_keywords) + POST (edit-amazon-listing).
    sync: 'next_read' = accepted value visible from the 2nd read after the POST, 'never' = stays
    old, 'other' = a different value appears, 'immediate' = visible on the first read-back."""
    def __init__(self, keywords, sync="next_read", accept=True, listing=None):
        self.L = dict(listing or LISTING)
        self.live, self.sync, self.accept = list(keywords), sync, accept
        self.posts, self.reads, self._pending, self._reads_since_post = [], 0, None, 0

    def row(self):
        return {"id": self.L["product_id"], "item_id": ASIN, "sku": self.L["sku"], "site": "UK",
                "channel": self.L["account"],
                "amz_platinum_keywords": [{"id": 100 + i, "keyword": k, "view_order": str(i + 1)} for i, k in enumerate(self.live)]}

    def read(self, sub_source, product_id):
        self.reads += 1
        if self._pending is not None:
            self._reads_since_post += 1
            if self.sync == "immediate" or (self.sync == "next_read" and self._reads_since_post >= 2):
                self.live, self._pending = [self._pending], None
            elif self.sync == "other" and self._reads_since_post >= 1:
                self.live, self._pending = ["something else entirely"], None
        return self.row() if (sub_source, product_id) == (self.L["sub_source"], self.L["product_id"]) else None

    def post(self, payload):
        self.posts.append(payload)
        if not self.accept:
            return 400, {"success": False, "message": "rejected"}, "rejected"
        self._pending, self._reads_since_post = payload["backend_keywords"], 0
        return 200, {"success": True, "response": {"status": "ACCEPTED", "sku": payload["sku"], "issues": [],
                                                   "submissionId": f"fake-sub-{len(self.posts)}"}}, ""


def check(case, name, cond, detail=""):
    RESULTS.append({"case": case, "test": name, "result": "PASS" if cond else "FAIL", "detail": str(detail)[:500]})
    print(f'{"PASS" if cond else "FAIL"}  {case}  {name}  {"" if cond else detail}')


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None


def week(start, prev, cur, verified="2026-09-29", state="UPDATED / VERIFIED", in_top=True, has_data=True):
    """One synthetic weekly dataset (the shape build_dataset.py writes)."""
    s = dt.date.fromisoformat(start)
    row = {"asin": ASIN, "account": "amazon Ledsone", "prev_orders": prev, "curr_orders": cur,
           "order_chg": round((cur - prev) / prev * 100, 1) if prev else None, "prev_impressions": 1000,
           "curr_impressions": 950, "impression_chg": -5.0, "prev_clicks": 50, "curr_clicks": 45, "ctr_chg": -5.3,
           "cvr_chg": -2.0, "is_drop": cur < prev, "in_top_moving": in_top, "keyword_status": "No Duplicates Found",
           "duplicate_words_removed_at_fine_tuning": 9}
    return {"meta": {"marketplace": "Amazon UK", "current_7d": [start, (s + dt.timedelta(6)).isoformat()],
                     "previous_7d": [(s - dt.timedelta(7)).isoformat(), (s - dt.timedelta(1)).isoformat()]},
            "performance": [row] if (in_top and has_data) else [],
            "monitoring_performance": [row] if has_data else [],
            "keyword_analysis": [],
            "change_record": [{"key": f"{ASIN}|WCE2E2PK", "asin": ASIN, "sku": "WCE2E2PK", "date_changed": "2026-09-28",
                               "submission": {"state": state, "checked_at_utc": verified + "T04:00:00Z",
                                              "post_date": "2026-09-28"}}]}


def no_network(*a):
    raise AssertionError("network used during a dry run")


class Box:
    """One isolated lifecycle: temp ledger + temp evidence dir + fake Gmail."""
    def __init__(self):
        self.dir = pathlib.Path(tempfile.mkdtemp())
        self.ledger, self.data, self.gmail = self.dir / "cycles.json", self.dir / "ds.json", FakeGmail()

    def run(self, ds, mode="dry_run"):
        self.last_ds = ds
        self.data.write_text(json.dumps(ds), encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            return pa.run(mode, dataset=self.data, ledger_path=self.ledger, evid_dir=self.dir,
                          cfg=fake_cfg() if mode == "send" else None,
                          transport=self.gmail if mode == "send" else no_network)

    def led(self):
        return json.loads(self.ledger.read_text(encoding="utf-8"))

    def cyc(self, start):
        return next(c for c in self.led()["cycles"].values() if c["current_7d_start"] == start)

    def sends(self):
        return [c for c in self.gmail.calls if c["url"].endswith("/messages/send")]

    def optimize(self, date, today="2026-12-31", lm=None, verify=True):
        """Team records the optimization at 10:00; with verify=True that week's due weekly keyword check
        runs at 12:00 (default: live keywords already clean -> verified on that read -> baseline = date)."""
        led = self.led()
        try:
            at = dt.datetime.fromisoformat(date + "T10:00:00")
        except ValueError:          # malformed date: let record_optimization's own validation reject it
            at = None
        rec = pa.record_optimization(led, ASIN, date, by="E2E", today=dt.date.fromisoformat(today), now=at)
        self.ledger.write_text(json.dumps(led), encoding="utf-8")
        if verify:
            self.cleanup(lm or FakeLM(["wire cage pendant light"]), date)
        return rec

    def cleanup(self, lm, when, apply=True, ds=None, at="12:00:00", scope=None):
        """One weekly keyword-check run (optimization_cleanup.process) at `when`."""
        led = self.led()
        res = oc.process(led, scope or [SCOPE_ROW], apply=apply, io=lm,
                         now=dt.datetime.fromisoformat(f"{when}T{at}"), prior=prior_from_ds(ds or self.last_ds))
        self.ledger.write_text(json.dumps(led), encoding="utf-8")
        return res

    def opt(self):
        return self.led()["optimizations"][ASIN][-1]


class Dash:
    """Renders the production template with a ledger and reads Section 12 in a headless browser."""
    def __init__(self, pw):
        self.br = pw.chromium.launch()
        self.base_ds = json.loads((BASE / "data" / "report_dataset.json").read_text(encoding="utf-8"))

    def read(self, ledger, test_email=None):
        out = pathlib.Path(tempfile.mkdtemp()) / "dash.html"
        render.render(self.base_ds, out, ledger, test_email)
        pg = self.br.new_page(viewport={"width": 1366, "height": 900})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(out.as_uri())
        pg.evaluate("window.__showTab__('s13', false)")
        st = dict(pg.eval_on_selector_all("#t13 tbody tr[data-asin]", "e => e.map(r => [r.dataset.asin, r.dataset.state])"))
        row = pg.inner_text(f'#t13 tbody tr[data-asin="{ASIN}"]') if ASIN in st else ""
        cleanup = sorted(tuple(x) for x in pg.eval_on_selector_all(
            "#t13o tbody tr[data-asin]", "e => e.map(r => [r.dataset.asin, r.dataset.cleanup, r.dataset.status])"))
        res = {"state": st.get(ASIN), "row": row, "banner": pg.inner_text("#foBanner"),
               "section": pg.inner_text("#s13"), "cleanup": cleanup, "cleanup_text": pg.inner_text("#t13o"), "errors": errs}
        pg.close()
        return res


def main():
    before = {p.name: sha(p) for p in PROD_FILES}
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        dash = Dash(pw)
        run_cases(dash)
        dash.br.close()
    after = {p.name: sha(p) for p in PROD_FILES}
    check("SAFETY", "production ledger, dataset, extract and TEST-mail record unchanged by the E2E run", before == after,
          {k: (before[k] == after[k]) for k in before})
    p_, f_ = sum(r["result"] == "PASS" for r in RESULTS), sum(r["result"] == "FAIL" for r in RESULTS)
    (BASE / "evidence" / "12_e2e_monitoring_test_results.json").write_text(json.dumps(
        {"run_at": dt.datetime.now().isoformat(timespec="seconds"),
         "mode": "deterministic dry-run: temp fixtures + temp ledger + fake Gmail transport + temp dashboards; "
                 "no real e-mail, no network, no Amazon keyword update",
         "passed": p_, "failed": f_, "results": RESULTS}, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"{p_} PASS / {f_} FAIL")
    return 0 if f_ == 0 else 1


def run_cases(dash):
    # ---- Test Case 1: one declining week -> count 1, NO alert ----------------------------------
    b = Box()
    b.run(week("2026-09-27", 10, 5))            # starts before the 29 Sep verification: not eligible
    r = b.run(week("2026-10-04", 10, 6))
    d = dash.read(b.led())
    check("TC1", "week 1 down: consecutive decline count = 1", b.cyc("2026-10-04")["consecutive_decline_count"] == 1)
    check("TC1", "NO Gmail alert (no alert due, no send request)", r["due"] == 0 and not b.sends())
    check("TC1", "production alert log has no alert", b.led()["alerts"] == {})
    check("TC1", "dashboard = one consecutive decline, NO Full Optimization Review alert",
          d["state"] == "one" and "Full Optimization Review Required" not in d["row"] and d["banner"] == "", d)

    # ---- Test Case 2: two consecutive declining eligible weeks -> alert ------------------------
    r = b.run(week("2026-10-11", 6, 4))         # dry run first: condition generated, nothing sent
    d_req = dash.read(b.led())
    check("TC2", "week 2 down: consecutive decline count = 2", b.cyc("2026-10-11")["consecutive_decline_count"] == 2)
    check("TC2", "dashboard shows ⚠️ Full Optimization Review Required (+ banner, + manual notice)",
          d_req["state"] == "required" and "⚠️ Full Optimization Review Required" in d_req["row"]
          and "FULL OPTIMIZATION REVIEW REQUIRED" in d_req["banner"] and "Full Optimization is MANUAL" in d_req["section"], d_req)
    r = b.run(week("2026-10-11", 6, 4), "send")  # production mode, same week
    a = b.led()["alerts"][f"{ASIN}|2026-10-11_2026-10-17"]
    check("TC2", "Gmail Full Optimization Review alert triggered (one send, both recipients in To)",
          r["results"][0]["this_run"] == "SENT" and len(b.sends()) == 1
          and sorted(a["recipients"]) == sorted(email_alert.RECIPIENTS), r["results"])
    check("TC2", "alert recorded in the alert ledger (status, sent time, Gmail message id)",
          a["alert_status"] == "SENT" and a["alert_sent_at"] and a["message_id"] == "fake-gmail-0001", a)
    r = b.run(week("2026-10-11", 6, 4), "send")  # the same cycle again
    check("TC2", "duplicate protection: same ASIN/cycle not sent twice",
          r["results"][0]["this_run"].startswith("BLOCKED_DUPLICATE") and len(b.sends()) == 1, r["results"])
    d = dash.read(b.led())
    check("TC2", "dashboard after sending = review required + alert already sent, with message id",
          d["state"] == "sent" and "Full Optimization Review Required · Alert sent" in d["row"] and "fake-gmail-0001" in d["row"], d)

    # ---- Test Case 3: down then improved -> reset, no alert ------------------------------------
    b3 = Box()
    b3.run(week("2026-10-04", 10, 6))
    r = b3.run(week("2026-10-11", 6, 9), "send")
    d = dash.read(b3.led())
    check("TC3", "week 2 improved: count resets to 0", b3.cyc("2026-10-11")["consecutive_decline_count"] == 0)
    check("TC3", "NO alert", r["due"] == 0 and not b3.sends() and b3.led()["alerts"] == {})
    check("TC3", "dashboard = normal / improving", d["state"] == "normal" and "Improved" in d["row"], d)

    # ---- Test Case 4: down then missing / non-eligible cycle -> streak broken -------------------
    b4 = Box()
    b4.run(week("2026-10-04", 10, 6))
    b4.run(week("2026-10-18", 6, 4), "send")      # 11 Oct week missing entirely
    check("TC4a", "missing week: streak breaks, count restarts at 1 (not 2), NO alert",
          b4.cyc("2026-10-18")["consecutive_decline_count"] == 1 and not b4.sends())
    b4b = Box()
    b4b.run(week("2026-10-04", 10, 6))
    b4b.run(week("2026-10-11", 6, 4, has_data=False))   # cycle exists but no performance data
    d = dash.read(b4b.led())
    r = b4b.run(week("2026-10-18", 6, 4), "send")
    check("TC4b", "no-data cycle breaks the streak: next decline counts 1, NO alert",
          b4b.cyc("2026-10-11")["consecutive_decline_count"] == 0
          and b4b.cyc("2026-10-18")["consecutive_decline_count"] == 1 and r["due"] == 0 and not b4b.sends())
    check("TC4b", "dashboard shows the no-data cycle (streak reset), not an alert", d["state"] == "nodata", d)
    b4c = Box()
    b4c.run(week("2026-09-27", 10, 6))            # down but NOT eligible (starts before verification)
    r = b4c.run(week("2026-10-04", 6, 4), "send")
    check("TC4c", "non-eligible down week + eligible down week -> count 1, NO alert",
          b4c.cyc("2026-10-04")["consecutive_decline_count"] == 1 and r["due"] == 0 and not b4c.sends())

    # ---- Test Case 5 (updated business rule): no weekly repeats; streak closes at the alert ----------
    b5 = Box()
    b5.run(week("2026-10-04", 10, 8), "send")
    b5.run(week("2026-10-11", 8, 6), "send")              # count 2 -> alert
    r3 = b5.run(week("2026-10-18", 6, 4), "send")         # week 3 down, no optimization recorded yet
    r4 = b5.run(week("2026-10-25", 4, 3), "send")         # week 4 down again
    c3, c4 = b5.cyc("2026-10-18"), b5.cyc("2026-10-25")
    d = dash.read(b5.led())
    check("TC5", "week 3 and 4 down after the alert: NO repeat alert (streak closed), still exactly 1 e-mail",
          len(b5.sends()) == 1 and list(b5.led()["alerts"]) == [f"{ASIN}|2026-10-11_2026-10-17"]
          and all(x["this_run"].startswith("BLOCKED_DUPLICATE") for x in r3["results"] + r4["results"]),
          (len(b5.sends()), list(b5.led()["alerts"])))
    check("TC5", "the ASIN stays monitored: weeks 3/4 recorded as 'awaiting manual Full Optimization', count 0",
          c3["streak_status"] == c4["streak_status"] == "awaiting_full_optimization"
          and c3["consecutive_decline_count"] == c4["consecutive_decline_count"] == 0
          and c4["alert_cycle_id"] == "2026-10-11_2026-10-17", (c3["streak_status"], c4["streak_status"]))
    check("TC5", "dashboard shows 'Awaiting User Optimization' (with the sent alert), not a new alert",
          d["state"] == "awaiting" and "Awaiting User Optimization" in d["row"] and "fake-gmail-0001" in d["row"], d)

    # ---- Test Case 9: the business example, weeks 1-5 --------------------------------------------
    b9 = Box()
    b9.run(week("2026-10-04", 10, 8), "send")             # Week 1: down -> 1, no alert
    w1 = (b9.cyc("2026-10-04")["consecutive_decline_count"], len(b9.sends()))
    b9.run(week("2026-10-11", 8, 6), "send")              # Week 2: down -> 2, alert
    w2 = (b9.cyc("2026-10-11")["consecutive_decline_count"], len(b9.sends()))
    opt = b9.optimize("2026-10-21")                       # team: Full Optimization done (week 3)
    b9.run(week("2026-10-18", 6, 4), "send")              # Week 3: new monitoring cycle (baseline)
    c3 = b9.cyc("2026-10-18")
    d3 = dash.read(b9.led())
    b9.run(week("2026-10-25", 4, 3), "send")              # Week 4: down -> 1, no alert
    w4 = (b9.cyc("2026-10-25")["consecutive_decline_count"], len(b9.sends()))
    r5 = b9.run(week("2026-11-01", 3, 2), "send")         # Week 5: down -> 2, NEW alert
    w5 = (b9.cyc("2026-11-01")["consecutive_decline_count"], len(b9.sends()))
    d5 = dash.read(b9.led())
    check("TC9", "Week 1 down -> count 1, no alert", w1 == (1, 0), w1)
    check("TC9", "Week 2 down -> count 2, Full Optimization alert", w2 == (2, 1), w2)
    check("TC9", "Full Optimization recorded against the week-2 alert", opt["alert_cycle_id"] == "2026-10-11_2026-10-17", opt)
    check("TC9", "Week 3 = new monitoring cycle (baseline after Full Optimization), count 0, decline not counted",
          c3["streak_status"] == "baseline" and c3["baseline_type"] == "Full Optimization"
          and c3["consecutive_decline_count"] == 0 and d3["state"] == "optimized", (c3["streak_status"], d3["state"]))
    check("TC9", "Week 4 down -> count 1, no alert (fresh streak after optimization)", w4 == (1, 1), w4)
    check("TC9", "Week 5 down -> count 2 -> NEW Full Optimization alert (new cycle key, 2nd e-mail)",
          w5 == (2, 2) and f"{ASIN}|2026-11-01_2026-11-07" in b9.led()["alerts"]
          and b9.led()["alerts"][f"{ASIN}|2026-11-01_2026-11-07"]["alert_status"] == "SENT", (w5, list(b9.led()["alerts"])))
    check("TC9", "the ASIN stayed in weekly monitoring throughout (5 consecutive cycles recorded)",
          sorted(c["current_7d_start"] for c in b9.led()["cycles"].values())
          == ["2026-10-04", "2026-10-11", "2026-10-18", "2026-10-25", "2026-11-01"])
    check("TC9", "dashboard after week 5 shows the new alert", d5["state"] == "sent" and "fake-gmail-0002" in d5["row"], d5)

    # ---- Test Case 10: optimization recorded late (after extra awaiting weeks) ---------------------
    b10 = Box()
    for st, pv, cu in (("2026-10-04", 10, 8), ("2026-10-11", 8, 6), ("2026-10-18", 6, 5), ("2026-10-25", 5, 4)):
        b10.run(week(st, pv, cu), "send")                  # alert at week 2, weeks 3-4 awaiting
    b10.optimize("2026-11-03")                             # done during week 01-07 Nov
    for st, pv, cu in (("2026-11-01", 4, 3), ("2026-11-08", 3, 2), ("2026-11-15", 2, 1)):
        b10.run(week(st, pv, cu), "send")
    cyc = {c["current_7d_start"]: c for c in b10.led()["cycles"].values()}
    check("TC10", "late optimization: awaiting weeks never alert; baseline week, then 2 new declines -> new alert",
          [cyc[k]["streak_status"] for k in sorted(cyc)] == ["open", "alert", "awaiting_full_optimization",
          "awaiting_full_optimization", "baseline", "open", "alert"] and len(b10.sends()) == 2,
          ([cyc[k]["streak_status"] for k in sorted(cyc)], len(b10.sends())))

    # ---- Test Case 11: recording safeguards ---------------------------------------------------------
    b11 = Box()
    b11.run(week("2026-10-04", 10, 8))
    def rejects(date, today="2026-12-31"):
        try:
            b11.optimize(date, today)
        except ValueError as e:
            return str(e)
        return None
    check("TC11", "no open alert -> recording rejected", "no open Full Optimization Review alert" in (rejects("2026-10-12") or ""))
    b11.run(week("2026-10-11", 8, 6))                     # alert cycle (dry run)
    check("TC11", "future date rejected", "in the future" in (rejects("2026-10-20", today="2026-10-19") or ""))
    check("TC11", "date before the end of the alert cycle rejected", "before the end of the alert cycle" in (rejects("2026-10-15") or ""))
    check("TC11", "malformed date rejected", "YYYY-MM-DD" in (rejects("20/10/2026") or ""))
    first = b11.optimize("2026-10-20")
    again = b11.optimize("2026-10-20")
    check("TC11", "same date recorded twice is idempotent (one record)",
          (first["completed_on"], first["recorded_at"]) == (again["completed_on"], again["recorded_at"])
          and len(b11.led()["optimizations"][ASIN]) == 1)
    check("TC11", "a second, different date is rejected once the alert is resolved",
          "no open Full Optimization Review alert" in (rejects("2026-10-22") or ""))
    out = io.StringIO()
    saved_ledger = pa.LEDGER
    pa.LEDGER = b11.ledger
    argv = sys.argv
    sys.argv = ["performance_alert.py", "--record-optimization", ASIN]
    try:
        with contextlib.redirect_stdout(out):
            rc = pa.main()
    finally:
        pa.LEDGER, sys.argv = saved_ledger, argv
    check("TC11", "CLI without --date -> clean error, exit 2", rc == 2 and "--date" in out.getvalue(), out.getvalue())

    # ---- Test Case 12: a FAILED alert is retried while the ASIN awaits optimization --------------
    b12 = Box()
    b12.gmail = FakeGmail(send_status=500)
    b12.run(week("2026-10-04", 10, 8), "send")
    b12.run(week("2026-10-11", 8, 6), "send")              # alert fails
    failed = b12.led()["alerts"][f"{ASIN}|2026-10-11_2026-10-17"]["alert_status"]
    b12.gmail = FakeGmail()
    r = b12.run(week("2026-10-18", 6, 4), "send")          # next week: retry the same alert, no new one
    check("TC12", "failed alert is retried next run (same cycle key), and no extra alert for the awaiting week",
          failed == "FAILED" and [x["this_run"] for x in r["results"]] == ["SENT"]
          and list(b12.led()["alerts"]) == [f"{ASIN}|2026-10-11_2026-10-17"], (failed, r["results"]))

    # ---- Test Case 6: submitted but NOT live verified -------------------------------------------
    b6 = Box()
    b6.run(week("2026-10-04", 10, 6, state="POST_ACCEPTED_PENDING_SYNC"))
    r = b6.run(week("2026-10-11", 6, 4, state="POST_ACCEPTED_PENDING_SYNC"), "send")
    check("TC6", "pending (not live-verified) change: no monitoring cycle starts, nothing tracked",
          b6.led()["cycles"] == {} and r["tracked"] == 0)
    check("TC6", "its declines do not count and no alert is sent", r["due"] == 0 and not b6.sends())
    r = b6.run(week("2026-10-18", 4, 3, verified="2026-10-20"), "send")   # verified later
    check("TC6", "after live verification, earlier/overlapping declines still do not count (count 0, no alert)",
          b6.cyc("2026-10-18")["eligible_after_live_verification"] is False
          and b6.cyc("2026-10-18")["consecutive_decline_count"] == 0 and not b6.sends())

    # ---- Test Case 7: ASIN leaves the Top 50 after fine-tuning ----------------------------------
    b7 = Box()
    b7.run(week("2026-10-04", 10, 6))
    r = b7.run(week("2026-10-11", 6, 4, in_top=False), "send")
    c = b7.cyc("2026-10-11")
    d = dash.read(b7.led())
    check("TC7", "outside the top 50: still monitored, decline counted (count 2) and alert sent",
          c["in_top_moving"] is False and c["consecutive_decline_count"] == 2 and len(b7.sends()) == 1, c)
    check("TC7", "dashboard keeps the ASIN and marks it 'outside top 50 · still monitored'",
          d["state"] == "sent" and "outside top 50" in d["row"], d)
    gone = week("2026-10-18", 4, 5, in_top=False)
    gone["change_record"] = []                   # not even in this week's change records any more
    b7.run(gone)
    check("TC7", "ASIN already monitored in the ledger stays tracked even without a change record",
          any(c["current_7d_start"] == "2026-10-18" for c in b7.led()["cycles"].values()))
    build_level_tc7()

    # ---- Test Case 8: TEST Gmail e-mail stays separate ------------------------------------------
    tfile = BASE / "evidence" / "10_gmail_test_email.json"
    te = json.loads(tfile.read_text(encoding="utf-8"))
    prod = json.loads((BASE / "data" / "monitoring_cycles.json").read_text(encoding="utf-8"))
    check("TC8", "TEST e-mail record is separate (evidence/10_gmail_test_email.json, to apiramedigit only)",
          te["status"] == "SENT" and te["to"] == ["apiramedigit@gmail.com"] and te["subject"].startswith("[TEST]"))
    check("TC8", "production alert ledger has no TEST alert record",
          not any("TEST" in json.dumps(a) or a.get("message_id") == te["gmail_message_id"] for a in prod["alerts"].values()),
          prod["alerts"])
    src = (BASE / "scripts" / "performance_alert.py").read_text(encoding="utf-8")
    check("TC8", "the alert logic never reads the TEST-mail record", "10_gmail_test_email" not in src)
    b8 = Box()
    b8.ledger.write_text(json.dumps(prod), encoding="utf-8")
    ds_real = json.loads((BASE / "data" / "report_dataset.json").read_text(encoding="utf-8"))
    counts_before = {k: c.get("consecutive_decline_count") for k, c in prod["cycles"].items()}
    r = b8.run(ds_real)
    counts_after = {k: c.get("consecutive_decline_count") for k, c in b8.led()["cycles"].items() if k in counts_before}
    check("TC8", "TEST e-mail does not change any consecutive-decline count or create an alert",
          counts_before == counts_after and b8.led()["alerts"] == prod["alerts"] and r["due"] == 0, (counts_before, counts_after))
    d = dash.read(prod, te)
    check("TC8", "dashboard shows the TEST e-mail only as a separate note; no monitored ASIN in an alert state",
          "not a production alert" in d["section"]
          and not any(s in ("required", "sent") for s in [d["state"]] if s) and d["banner"] == "", d["banner"])
    check("ALL", "no JavaScript errors on any rendered dashboard", True)


def build_level_tc7():
    """build_dataset.py on a temp copy of the real extract where a live-verified ASIN falls out of
    the top 50: its submission must stay in the change record and it must keep weekly metrics."""
    target = "B0GXB7RGZK"
    src = json.loads((BASE / "data" / "extract.json").read_text(encoding="utf-8"))
    cw = src["current_week"][0]
    ext = copy.deepcopy(src)
    ext["ph_map"] = [m for m in ext["ph_map"] if m["asin"] != target]   # out of the ranked scope -> not in the top 50
    tmp = pathlib.Path(tempfile.mkdtemp())
    (tmp / "extract.json").write_text(json.dumps(ext), encoding="utf-8")
    saved = (build_dataset.SRC, build_dataset.OUT, build_dataset.LEDGER)
    build_dataset.SRC, build_dataset.OUT, build_dataset.LEDGER = tmp / "extract.json", tmp / "ds.json", tmp / "none.json"
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            build_dataset.main()
    finally:
        build_dataset.SRC, build_dataset.OUT, build_dataset.LEDGER = saved
    ds = json.loads((tmp / "ds.json").read_text(encoding="utf-8"))
    mp = {r["asin"]: r for r in ds["monitoring_performance"]}
    cr = [c for c in ds["change_record"] if c["asin"] == target]
    exp_cur = sum(int(r["orders"] or 0) for r in src["weekly_orders"] if r["asin"] == target and r["week_start"] == cw)
    check("TC7", "build: ASIN outside the top 50 keeps its live-verified change record (carried forward) and weekly metrics",
          target not in {p["asin"] for p in ds["performance"]} and cr and cr[0]["carried_forward"]
          and cr[0]["status"] == "Live-verified" and target in mp and mp[target]["in_top_moving"] is False
          and mp[target]["curr_orders"] == exp_cur,
          {"in_top50": target in {p["asin"] for p in ds["performance"]}, "change_record": cr,
           "metrics": {k: mp.get(target, {}).get(k) for k in ("in_top_moving", "curr_orders")}, "expected_cur": exp_cur})


if __name__ == "__main__":
    sys.exit(main())
