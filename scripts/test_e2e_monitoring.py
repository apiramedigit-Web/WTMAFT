"""Deterministic E2E tests of the post-update monitoring lifecycle (business rule 2026-10-05):

  POST Accepted = updated for the user -> monitoring starts on the update date D (the 22 original submissions:
  2026-09-29) WITHOUT a GET gate -> Week 1 = D..D+6, Week 2 = D+7..D+13 (orders, both accounts) -> Week 2 compared
  with Week 1 only when Week 2 is complete -> only a genuinely new update starts a new cycle; GET is audit only and
  an accepted payload is never re-sent because a GET still shows the previous keywords.
NO e-mail of any kind: the retired --send path is refused and nothing in the pipeline e-mails.

Temp fixtures only: synthetic daily-order datasets, a temp ledger, a FAKE Listing Management Tool (GET + POST),
temp dashboards rendered with the production template and checked in a headless browser. Production files are
hashed before/after and must be unchanged. No network, no real POST, no e-mail.
Output: evidence/12_e2e_monitoring_test_results.json
(The helpers ASIN / LISTING / SCOPE_ROW / Box / Dash / FakeLM / sha / daily are shared with test_optimization_cleanup.py.)
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
import optimization_cleanup as oc  # noqa: E402
import performance_alert as pa  # noqa: E402
import render  # noqa: E402

BASE = pathlib.Path(__file__).resolve().parent.parent
PROD_FILES = [BASE / "data" / n for n in ("monitoring_cycles.json", "report_dataset.json", "extract.json")]
ASIN = "B0E2ETEST01"
LISTING = {"product_id": 990001, "sku": "WCE2E2PK", "sub_source": 8, "account": "amazon Ledsone"}
ORIGINAL_ENTRIES = ["wire cage pendant light", "cage pendant"]          # live before the accepted POST (duplicates)
ORIGINAL = " ".join(ORIGINAL_ENTRIES)
PROPOSED = "wire cage pendant light"                                       # the accepted (cleaned) payload
SCOPE_ROW = {"asin": ASIN, "sku": "WCE2E2PK", "sub_source": 8, "account": "amazon Ledsone", "product_id": 990001,
             "site": "UK", "report_row": f"{ASIN}|WCE2E2PK", "submission_id": "s-e2e",
             "post_timestamp": "2026-09-28T17:00:00", "proposed": PROPOSED, "original": ORIGINAL}
KEY = f"{ASIN}|WCE2E2PK"
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


def days(start, n):
    s = dt.date.fromisoformat(start)
    return [(s + dt.timedelta(i)).isoformat() for i in range(n)]


def daily(through, orders=None, start="2026-09-01", gaps=()):
    """A dataset with only what the monitoring stage reads: daily ASIN orders, loaded for both accounts
    from `start` to `through` (minus `gaps`)."""
    av = [d for d in days(start, (dt.date.fromisoformat(through) - dt.date.fromisoformat(start)).days + 1) if d not in gaps]
    return {"monitoring_data": {"daily_from": start, "available_dates": av, "available_through": through,
                                "orders": {ASIN: {d: n for d, n in (orders or {}).items() if d in av}}}}


def per_day(start, n, value):
    return {d: value for d in days(start, n)}


VERIFIED_29 = {f"{ASIN}|WCE2E2PK": {"state": oc.VERIFIED, "verified_at_utc": "2026-09-29T04:00:00Z"}}
PENDING_P = {f"{ASIN}|WCE2E2PK": {"state": "POST_ACCEPTED_PENDING_SYNC"}}


class Box:
    """One isolated lifecycle: temp ledger + temp evidence dir. Nothing is shared with production."""
    def __init__(self, prior=None, scope=None):
        self.dir = pathlib.Path(tempfile.mkdtemp())
        self.ledger, self.data = self.dir / "cycles.json", self.dir / "ds.json"
        self.prior, self.scope = (VERIFIED_29 if prior is None else prior), scope or [SCOPE_ROW]
        led = {}
        oc.seed(led, self.scope, self.prior)
        self.save(led)

    def save(self, led):
        self.ledger.write_text(json.dumps(led), encoding="utf-8")

    def led(self):
        return json.loads(self.ledger.read_text(encoding="utf-8"))

    def run(self, ds, now="2026-12-31T12:00:00"):
        """The monitoring stage (performance_alert.run) on a temp dataset + temp ledger."""
        self.data.write_text(json.dumps(ds), encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            return pa.run(dataset=self.data, ledger_path=self.ledger, evid_dir=self.dir, now=dt.datetime.fromisoformat(now))

    def mon(self, live=None):
        m = self.led().get("monitoring", {})
        return m if live is None else m.get(f"{ASIN}|{live}")

    def row(self):
        return self.led()["keyword_rows"][oc.row_key(self.scope[0])]

    def cleanup(self, lm, when, apply=True, at="12:00:00", scope=None):
        """One weekly keyword-check run (optimization_cleanup.process) at `when`."""
        led = self.led()
        res = oc.process(led, scope or self.scope, apply=apply, io=lm, now=dt.datetime.fromisoformat(f"{when}T{at}"),
                         prior=self.prior)
        self.save(led)
        return res

    def record(self, date, today="2026-12-31", sku=None):
        led = self.led()
        e = oc.record_live_update(led, self.scope, ASIN, date, sku, "e2e", today=dt.date.fromisoformat(today),
                                  now=dt.datetime.fromisoformat(today + "T10:00:00"), prior=self.prior)
        self.save(led)
        return e


class Dash:
    """Renders the production template with a ledger and reads Section 12 (cycle history + keyword rows)."""
    def __init__(self, pw):
        self.br = pw.chromium.launch()
        self.base_ds = json.loads((BASE / "data" / "report_dataset.json").read_text(encoding="utf-8"))

    def read(self, ledger):
        out = pathlib.Path(tempfile.mkdtemp()) / "dash.html"
        render.render(self.base_ds, out, ledger)
        pg = self.br.new_page(viewport={"width": 1366, "height": 900})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(out.as_uri())
        hist = sorted(tuple(x) for x in pg.eval_on_selector_all(
            "#t13h tbody tr[data-asin]", "e => e.map(r => [r.dataset.asin, r.dataset.live, r.dataset.status])"))
        cleanup = sorted(tuple(x) for x in pg.eval_on_selector_all(
            "#t13o tbody tr[data-asin]", "e => e.map(r => [r.dataset.asin, r.dataset.cleanup, r.dataset.status])"))
        body = pg.evaluate("[...document.querySelectorAll('section')].map(e => e.textContent).join(' ')")
        htext = pg.eval_on_selector_all(f'#t13h tbody tr[data-asin="{ASIN}"]', "e => e.map(r => r.textContent)")
        res = {"hist": [h for h in hist if h[0] == ASIN], "hist_text": htext, "cleanup": cleanup,
               "cleanup_text": pg.inner_text("#t13o"), "body": body, "errors": errs}
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
    check("SAFETY", "production ledger, dataset and extract unchanged by the E2E run", before == after,
          {k: (before[k] == after[k]) for k in before})
    p_, f_ = sum(r["result"] == "PASS" for r in RESULTS), sum(r["result"] == "FAIL" for r in RESULTS)
    (BASE / "evidence" / "12_e2e_monitoring_test_results.json").write_text(json.dumps(
        {"run_at": dt.datetime.now().isoformat(timespec="seconds"),
         "rule": "POST Accepted starts monitoring (common anchor 2026-09-29 for the 22 original submissions); Week 1 / Week 2; GET audit only; no e-mail (2026-10-05)",
         "mode": "deterministic: temp fixtures + temp ledger + fake Listing Management + temp dashboards; "
                 "no network, no real POST, no e-mail",
         "passed": p_, "failed": f_, "results": RESULTS}, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"{p_} PASS / {f_} FAIL")
    return 0 if f_ == 0 else 1


def run_cases(dash):
    W1 = per_day("2026-09-29", 7, 2)                      # Week 1 = 14 orders
    W2_DOWN, W2_UP = per_day("2026-10-06", 7, 1), per_day("2026-10-06", 7, 3)
    ANCHOR = oc.ORIGINAL_ANCHOR

    # ---- M1 anchor + windows: POST Accepted -> common update date 2026-09-29 (not the 28 Sep POST / report week) --
    b = Box()
    b.run(daily("2026-09-27"))
    c = b.mon("2026-09-29")
    check("M1", "anchor = the common update date 29 Sep for the accepted original submission (POST 28 Sep and the "
          "report week are NOT used)", ANCHOR == "2026-09-29" and c and c["live_update_date"] == ANCHOR
          and not b.mon("2026-09-28") and len(b.mon()) == 1, b.mon())
    check("M1", "Week 1 = 29 Sep..5 Oct, Week 2 = 6..12 Oct (7 days each, the update day included)",
          (c["week1_start"], c["week1_end"], c["week2_start"], c["week2_end"]) == ("2026-09-29", "2026-10-05", "2026-10-06", "2026-10-12"))
    check("M2", "no day of Week 1 loaded yet -> not started, no orders, no verdict",
          c["monitoring_status"] == pa.S_NOT_STARTED and c["week1_orders"] is None and c["performance_status"] == pa.P_NOT_STARTED, c)

    # ---- M3 Week 1 partial / M4 Week 2 partial (incomplete Week 2 -> no comparison) ------------------------
    b.run(daily("2026-10-02", W1))
    c = b.mon("2026-09-29")
    check("M3", "Week 1 in progress (4/7 days): 'Monitoring — Week 1', orders not totalled (8 so far), no verdict",
          c["monitoring_status"] == pa.S_WEEK1 and c["week1_days_loaded"] == 4 and c["week1_orders"] is None
          and c["week1_orders_so_far"] == 8 and c["performance_status"] == pa.P_WAIT, c)
    b.run(daily("2026-10-09", {**W1, **W2_DOWN}))
    c = b.mon("2026-09-29")
    check("M4", "Week 1 complete (14), Week 2 4/7 days: 'Monitoring — Week 2', NO decline computed although Week 2 is lower so far",
          c["monitoring_status"] == pa.S_WEEK2 and c["week1_orders"] == 14 and c["week2_orders"] is None
          and c["week2_days_loaded"] == 4 and c["order_change"] is None and c["performance_status"] == pa.P_WAIT, c)
    b.run(daily("2026-10-12", {**W1, **W2_DOWN}, gaps=("2026-10-08",)))
    c = b.mon("2026-09-29")
    check("M4", "a missing (not loaded) day inside Week 2 keeps it incomplete (6/7) - never a partial total",
          c["monitoring_status"] == pa.S_WEEK2 and c["week2_days_loaded"] == 6 and c["week2_orders"] is None, c)

    # ---- M5 complete -> comparison --------------------------------------------------------------------------
    b.run(daily("2026-10-12", {**W1, **W2_DOWN}))
    c = b.mon("2026-09-29")
    check("M5", "Week 2 complete and lower (14 -> 7): Monitoring Complete, Performance Decline, -50.0%",
          c["monitoring_status"] == pa.S_COMPLETE and (c["week1_orders"], c["week2_orders"]) == (14, 7)
          and c["order_change"] == -7 and c["order_change_pct"] == -50.0 and c["performance_status"] == pa.P_DECLINE, c)
    bu = Box()
    bu.run(daily("2026-10-12", {**W1, **W2_UP}))
    be = Box()
    be.run(daily("2026-10-12", {**W1, **per_day("2026-10-06", 7, 2)}))
    check("M5", "Week 2 higher (21) -> Performance Improved; equal (14 = 14) -> Performance Stable",
          bu.mon("2026-09-29")["performance_status"] == pa.P_IMPROVED and be.mon("2026-09-29")["performance_status"] == pa.P_STABLE)
    b.run(daily("2026-11-30", {}, start="2026-10-20"))   # later builds no longer contain Sep/Oct daily data
    check("M6", "a completed cycle is frozen: later daily-data windows never rewrite its Week 1 / Week 2 figures",
          b.mon("2026-09-29")["week1_orders"] == 14 and b.mon("2026-09-29")["performance_status"] == pa.P_DECLINE)

    # ---- M7 POST Accepted, GET still shows the previous keywords: monitored anyway (no GET gate) -------------
    bp = Box(prior=PENDING_P)
    bp.run(daily("2026-10-12", {**W1, **W2_DOWN}))
    m = bp.mon()
    check("M7", "POST Accepted with the audit GET still showing the previous keywords -> monitored from 29 Sep, "
          "no 'Update Pending' entry, ledger status not 'Live Verification Pending'",
          list(m) == [f"{ASIN}|2026-09-29"] and m[f"{ASIN}|2026-09-29"]["monitoring_status"] == pa.S_COMPLETE
          and bp.row()["status"] == oc.S_ACCEPTED, (m, bp.row()["status"]))

    # ---- M8 next due check: GET shows the accepted value -> audit Live Verified, no POST, anchor unchanged ------
    lm = FakeLM([PROPOSED])
    bp.cleanup(lm, "2026-10-05")
    r = bp.row()
    check("M8", "GET shows the accepted keywords -> NO POST, audit Live Verified, anchor stays 29 Sep (no new cycle)",
          lm.posts == [] and r["status"] == oc.S_VERIFIED and [u["date"] for u in r["live_updates"]] == [ANCHOR], r.get("live_updates"))

    # ---- M9 GET still shows the pre-POST keywords (with duplicates) -> NOT re-sent ------------------------------
    bq = Box(prior=PENDING_P)
    lm = FakeLM(ORIGINAL_ENTRIES)
    bq.cleanup(lm, "2026-10-05")
    r = bq.row()
    check("M9", "GET still shows the keywords from before the accepted POST -> the same payload is NOT re-POSTed; "
          "audit only, status POST Accepted — Monitoring, anchor stays 29 Sep",
          lm.posts == [] and r["status"] == oc.S_ACCEPTED and [u["date"] for u in r["live_updates"]] == [ANCHOR]
          and "NOT re-sent" in r["history"][-1]["detail"], (lm.posts, r["status"], r["history"][-1].get("detail")))
    lm = FakeLM(["brass cage pendant", "brass cage", "industrial lamp"], sync="never")   # a genuinely new value
    bq.cleanup(lm, "2026-10-12")
    r = bq.row()
    check("M9", "a genuinely NEW live value with duplicates -> cleaned from the fresh GET, POSTed once; POST Accepted "
          "starts a new cycle on the POST date even though the read-back still shows the old value (audit)",
          len(lm.posts) == 1 and lm.posts[0]["backend_keywords"] == oc.finetune(["brass cage pendant", "brass cage", "industrial lamp"])["cleaned"]
          and r["status"] == oc.S_ACCEPTED and [(u["date"], u["source"]) for u in r["live_updates"]] == [(ANCHOR, oc.SRC_ORIGINAL), ("2026-10-12", oc.SRC_POST)],
          (lm.posts, r["status"], r.get("live_updates")))
    bq.cleanup(lm, "2026-10-19")
    check("M9", "next due week: GET still shows the value from before THAT accepted POST -> not re-sent (still 1 POST)",
          len(lm.posts) == 1 and len(bq.row()["live_updates"]) == 2)

    # ---- M10 manual user change (clean) -> new anchor, new cycle; old cycle closed, not mixed ----------------
    bm = Box()
    bm.cleanup(FakeLM([PROPOSED]), "2026-10-06")          # routine re-check, same value
    r0 = bm.row()
    check("M10", "routine re-check of the accepted value: no POST, NO new anchor (stays 29 Sep)",
          [u["date"] for u in r0["live_updates"]] == [ANCHOR])
    lm = FakeLM(["vintage edison pendant cage"])          # the user changed the keywords manually
    bm.cleanup(lm, "2026-10-13")
    r1 = bm.row()
    bm.run(daily("2026-10-25", {**W1, **W2_DOWN, **per_day("2026-10-13", 13, 1)}))
    old, new = bm.mon("2026-09-29"), bm.mon("2026-10-13")
    check("M10", "manual change detected by the weekly GET: clean -> no POST; new live value = source of truth; new anchor 13 Oct",
          lm.posts == [] and r1["live_value"] == "vintage edison pendant cage"
          and [(u["date"], u["source"]) for u in r1["live_updates"]] == [(ANCHOR, oc.SRC_ORIGINAL), ("2026-10-13", oc.SRC_MANUAL)])
    check("M10", "new cycle: Week 1 13..19 Oct, Week 2 20..26 Oct; old 29 Sep cycle kept (complete before the change, not mixed)",
          (new["week1_start"], new["week2_end"]) == ("2026-10-13", "2026-10-26") and new["latest"] and not old["latest"]
          and old["monitoring_status"] == pa.S_COMPLETE and old["superseded_by"] is None and new["week1_orders"] == 7, (old, new))
    bs = Box()
    bs.run(daily("2026-10-02", W1))
    lm = FakeLM(["brand new manual keywords"])
    bs.cleanup(lm, "2026-10-06")                           # change made inside the old Week 2
    bs.run(daily("2026-10-12", {**W1, **W2_DOWN}))
    old = bs.mon("2026-09-29")
    check("M10", "change during the old Week 2 -> old cycle 'Superseded', its Week 2 not evaluated (no mixed decline)",
          old["monitoring_status"] == pa.S_SUPERSEDED and old["week2_valid"] is False and old["performance_status"] == pa.P_NOT_EVALUATED
          and old["week1_orders"] == 14 and bs.mon("2026-10-06")["monitoring_status"] == pa.S_WEEK2
          and bs.mon("2026-10-06")["week1_orders"] == 7, (old, bs.mon("2026-10-06")))

    # ---- M11 manual change with duplicates -> clean the LATEST live value, POST, new anchor = POST date ---------
    bd = Box()
    user = ["Vintage cage pendant", "vintage", "brass holder", "brass holder"]
    lm = FakeLM(user, sync="immediate")
    bd.cleanup(lm, "2026-10-06")
    r = bd.row()
    check("M11", "user's value has repetitions -> cleanup operates on the latest live value: relevant words kept, "
          "nothing invented, POST once, new anchor 6 Oct (POST Accepted date)",
          len(lm.posts) == 1 and oc.uniq(lm.posts[0]["backend_keywords"]) == oc.uniq(" ".join(user))
          and PROPOSED not in lm.posts[0]["backend_keywords"] and r["status"] == oc.S_VERIFIED
          and [u["date"] for u in r["live_updates"]] == [ANCHOR, "2026-10-06"], (lm.posts, r["live_updates"]))

    # ---- M12 --record-live-update: the user-reported date is the anchor -------------------------------------
    br_ = Box()
    br_.record("2026-10-03", today="2026-10-05")
    br_.cleanup(FakeLM(["user typed keywords"]), "2026-10-05")
    check("M12", "user recorded a manual change on 3 Oct; the next fresh GET confirms it -> anchor 3 Oct (not the GET date)",
          [u["date"] for u in br_.row()["live_updates"]] == [ANCHOR, "2026-10-03"])
    bc = Box()
    bc.cleanup(FakeLM(["detected later"]), "2026-10-06")
    bc.record("2026-10-04", today="2026-10-07")
    check("M12", "change already detected on 6 Oct, user reports the real date 4 Oct -> the anchor is corrected to 4 Oct",
          [u["date"] for u in bc.row()["live_updates"]] == [ANCHOR, "2026-10-04"])
    bad = []
    for d, today in (("2026-10-20", "2026-10-07"), ("2026-09-20", "2026-10-07"), ("05/10/2026", "2026-10-07")):
        try:
            Box().record(d, today=today)
            bad.append(d)
        except ValueError:
            pass
    check("M12", "future date, date before the current anchor and malformed date are rejected", not bad, bad)

    # ---- M13 idempotent daily execution ---------------------------------------------------------------------
    bi = Box(prior=PENDING_P)
    lm = FakeLM(ORIGINAL_ENTRIES, sync="never")           # Amazon keeps showing the previous keywords
    snaps = []
    for d in days("2026-10-05", 15):                     # run the full pipeline stages DAILY for 15 days
        bi.cleanup(lm, d, at="15:00:00")
        bi.cleanup(lm, d, at="18:00:00")                 # and twice on the same day
        bi.run(daily(d, W1), now=d + "T18:30:00")
        snaps.append(sorted(bi.mon()))
    check("M13", "daily runs for 15 days (twice a day) while GET shows the previous keywords: 0 POSTs (no retry of the "
          "accepted payload), at most one check per Monday-week, monitoring never restarts (one 29 Sep cycle)",
          lm.posts == [] and all(s == [f"{ASIN}|2026-09-29"] for s in snaps)
          and len([e for e in bi.row()["history"] if e.get("event") == "weekly check"]) == 3, (len(lm.posts), snaps[-1]))
    bj = Box()
    for d in days("2026-10-05", 10):
        bj.cleanup(FakeLM([PROPOSED]), d)
        bj.run(daily(d, W1), now=d + "T18:30:00")
    strip = lambda m: {k: {x: y for x, y in c.items() if x not in ("computed_at", "data_through")} for k, c in m.items()}
    m1 = strip(bj.mon())
    bj.cleanup(FakeLM([PROPOSED]), "2026-10-14", at="20:00:00")
    bj.run(daily("2026-10-14", W1), now="2026-10-14T20:30:00")
    check("M13", "re-running the same day changes nothing; the anchor never restarts on routine daily checks (one cycle, 29 Sep)",
          strip(bj.mon()) == m1 and list(bj.mon()) == [f"{ASIN}|2026-09-29"]
          and [u["date"] for u in bj.row()["live_updates"]] == [ANCHOR])

    # ---- M14 no e-mail -------------------------------------------------------------------------------------
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = pa.main(["--send"])
    src = (BASE / "scripts" / "performance_alert.py").read_text(encoding="utf-8")
    check("M14", "performance_alert.py --send is REFUSED (exit 2, nothing sent); the module never imports the e-mail code",
          rc == 2 and "REFUSED" in out.getvalue() and "import email_alert" not in src and "gmail" not in src.lower(), out.getvalue())
    bx = Box()
    bx.run(daily("2026-10-12", {**W1, **W2_DOWN}))       # a Performance Decline cycle
    led = bx.led()
    keys = {k for c in led["monitoring"].values() for k in c}
    check("M14", "a Performance Decline creates no alert / e-mail state (monitoring fields only, no alerts written)",
          led["monitoring"][f"{ASIN}|2026-09-29"]["performance_status"] == pa.P_DECLINE
          and not [k for k in keys if "alert" in k or "mail" in k] and not led.get("alerts"), sorted(keys))

    # ---- M15 dashboard (Section 12 audit: cycle history + keyword rows) ---------------------------------------
    d = dash.read(b.led())
    check("M15", "Section 12 cycle history shows the 29 Sep cycle complete with both weeks' orders and the Decline result",
          d["hist"] == [(ASIN, "2026-09-29", pa.S_COMPLETE)] and d["hist_text"] and "14 orders (7/7 days)" in d["hist_text"][0]
          and "7 orders (7/7 days)" in d["hist_text"][0] and pa.P_DECLINE in d["hist_text"][0], d["hist_text"])
    d2 = dash.read(bs.led())
    check("M15", "after a manual change: the history keeps the superseded 29 Sep cycle and shows the new 6 Oct cycle",
          (ASIN, "2026-09-29", pa.S_SUPERSEDED) in d2["hist"] and (ASIN, "2026-10-06", pa.S_WEEK2) in d2["hist"], d2["hist"])
    d3 = dash.read(bq.led())
    check("M15", "keyword rows show the audit status (POST Accepted — Monitoring), no e-mail alert status, no JavaScript errors",
          d3["cleanup"] == [(ASIN, "WCE2E2PK", oc.S_ACCEPTED)]
          and all(not x["errors"] for x in (d, d2, d3)) and not any(w in x["body"] for x in (d, d2, d3)
                                                              for w in ("Alert Status", "Gmail", "Alert sent")),
          [x["errors"] for x in (d, d2, d3)] + [d3["cleanup"]])


if __name__ == "__main__":
    sys.exit(main())
