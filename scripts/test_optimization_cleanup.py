"""Deterministic tests: WEEKLY backend keyword check of the report's ASIN-SKU rows (optimization_cleanup.py)
and its hand-off to the 7-day monitoring / Full Optimization cycle (performance_alert.py).

Rule (2026-09-29): every weekly due check GETs the latest live keywords (source of truth), runs the
existing fine-tuning, POSTs through the existing API only if the cleaned value differs from the live value
(payload always built from that fresh GET), live-verifies, and records everything in the audit history.
A pending row is NOT permanently blocked: it is re-processed from a fresh GET each due week.

Temp ledgers, FAKE Listing Management (GET + POST), FAKE Gmail, temp dashboards. No real POST, no real
e-mail, no network. Output: evidence/13_optimization_cleanup_test_results.json
"""
import datetime as dt
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import optimization_cleanup as oc  # noqa: E402
from keyword_finetune import finetune  # noqa: E402
from test_e2e_monitoring import ASIN, LISTING, PROD_FILES, SCOPE_ROW, Box, Dash, FakeLM, sha, week  # noqa: E402

BASE = pathlib.Path(__file__).resolve().parent.parent
RESULTS = []
DUP = ["Wire Cage Pendant Light Cage pendant light shade", "industrial cage lamp", "industrial cage lamp"]
DUP_LIVE = " ".join(" ".join(e.split()) for e in DUP)
DUP_CLEAN = finetune(DUP)["cleaned"]          # what the existing rules produce (not re-implemented here)
CLEAN = ["Wire Cage Pendant Light", "industrial lamp shade"]
MON = ["2026-10-12", "2026-10-19", "2026-10-26", "2026-11-02", "2026-11-09"]   # first due: 6 Oct (+7 from 29 Sep)
PENDING_ROW = {**SCOPE_ROW, "proposed": DUP_CLEAN}


def check(case, name, cond, detail=""):
    RESULTS.append({"case": case, "test": name, "result": "PASS" if cond else "FAIL", "detail": str(detail)[:500]})
    print(f'{"PASS" if cond else "FAIL"}  {case}  {name}  {"" if cond else detail}')


class LogLM(FakeLM):
    """FakeLM that logs the call order so 'every POST follows a fresh GET in the same run' can be proven."""
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.log = []

    def read(self, sub, pid):
        self.log.append("GET")
        return super().read(sub, pid)

    def post(self, payload):
        self.log.append("POST")
        return super().post(payload)


def box(weeks=(("2026-10-04", 10, 8), ("2026-10-11", 8, 6)), state="UPDATED / VERIFIED"):
    b = Box()
    for w, pv, cu in weeks:
        ds = week(w, pv, cu)
        ds["change_record"][0]["submission"]["state"] = state
        b.run(ds, "send")
    return b


def row(b):
    return b.led()["keyword_rows"][oc.row_key(SCOPE_ROW)]


def checks(b, mode="apply"):
    return [e for e in row(b)["history"] if e.get("event") == "weekly check" and e.get("mode") == mode]


def last(b):
    c = checks(b)
    return c[-1] if c else {}


def main():
    before = {p.name: sha(p) for p in PROD_FILES}
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        dash = Dash(pw)
        run(dash)
        dash.br.close()
    after = {p.name: sha(p) for p in PROD_FILES}
    check("SAFETY", "production ledger / dataset / extract / TEST-mail record unchanged", before == after)
    p_, f_ = sum(r["result"] == "PASS" for r in RESULTS), sum(r["result"] == "FAIL" for r in RESULTS)
    (BASE / "evidence" / "13_optimization_cleanup_test_results.json").write_text(json.dumps(
        {"run_at": dt.datetime.now().isoformat(timespec="seconds"),
         "mode": "deterministic: temp ledgers, fake Listing Management GET/POST, fake Gmail, temp dashboards; "
                 "no real POST, no real e-mail, no network", "passed": p_, "failed": f_, "results": RESULTS},
        indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"{p_} PASS / {f_} FAIL")
    return 0 if f_ == 0 else 1


def run(dash):
    # ---- B: before the listing's due date -> nothing read, nothing sent --------------------------------
    b = box()
    lm = LogLM(DUP)
    b.cleanup(lm, "2026-10-02")
    b.cleanup(lm, "2026-10-05")
    check("B", "before the listing's 7-day due date -> no GET, no POST; its own next due date is recorded",
          lm.log == [] and row(b)["next_due"] == "2026-10-06" and not checks(b), row(b)["next_due"])

    # ---- C + D: due Monday: fresh GET, duplicates cleaned, POST once, verified -------------------------
    lm = LogLM(DUP, sync="immediate")
    b.cleanup(lm, MON[0])
    e = last(b)
    check("C", "first due Monday: fresh GET of the live keywords (source of truth)", lm.log[0] == "GET" and e["live_before"] == DUP_LIVE)
    check("D", "cleaned with the existing rules: every relevant word kept, repeated entry removed, nothing added",
          e["cleaned"] == DUP_CLEAN and oc.uniq(e["cleaned"]) == oc.uniq(e["live_before"]) and e["repeated_entries"] == ["industrial cage lamp"])
    check("D", "cleaned != live -> ONE POST of the freshly cleaned value (sku/sub_source/site/backend_keywords only), then Live Verified",
          lm.posts == [{"sku": "WCE2E2PK", "sub_source": "8", "site": "UK", "backend_keywords": DUP_CLEAN}]
          and row(b)["status"] == oc.S_VERIFIED and e["action"] == "POST" and e["verified_at"], lm.posts)
    check("AUDIT", "audit history records the GET value, cleaned value, POST attempt, verification and final live value",
          all(k in e for k in ("live_before", "cleaned", "post_http", "submission_id", "verified_at", "live_after"))
          and e["live_after"] == DUP_CLEAN and row(b)["post_attempts"] == 2)   # original submission + this POST

    # ---- same week re-run -> not due -------------------------------------------------------------------
    b.cleanup(lm, "2026-10-13")
    check("M", "same weekly cycle re-run -> not due (no second GET/POST)", len(lm.posts) == 1 and len(checks(b)) == 1)

    # ---- E: next Monday, already clean -> no POST, still Live Verified --------------------------------
    lm2 = LogLM(CLEAN)
    b.cleanup(lm2, MON[1])
    check("E", "next Monday: fresh GET; cleaned == live -> NO POST, Live Verified (weekly recurrence)",
          lm2.log == ["GET"] and lm2.posts == [] and last(b)["action"] == "no POST" and row(b)["status"] == oc.S_VERIFIED)
    check("E", "routine re-verification does not move the monitoring start (first_verified_at unchanged)",
          row(b)["first_verified_at"] == "2026-09-29T04:00:00Z" and row(b)["last_verified_at"].startswith(MON[1]))

    # ---- PENDING rule: not a permanent block; each due week fresh GET + clean + POST if needed ---------
    bp = box(state="POST_ACCEPTED_PENDING_SYNC")
    lmp = LogLM(DUP, sync="never")                      # the earlier update never became visible
    bp.cleanup(lmp, "2026-10-02", scope=[PENDING_ROW])  # < 7 days after the 28 Sep POST: not due
    check("P1", "pending row: not re-processed before 7 days after its last POST", lmp.log == [])
    bp.cleanup(lmp, "2026-10-05", scope=[PENDING_ROW])
    r1 = bp.led()["keyword_rows"][oc.row_key(PENDING_ROW)]
    check("P2", "pending row, due: fresh GET -> live still has duplicates -> cleaned differs -> POST (from this GET) -> still pending",
          lmp.log[:1] == ["GET"] and lmp.posts and lmp.posts[0]["backend_keywords"] == DUP_CLEAN
          and r1["status"] == oc.S_PENDING, (lmp.log, r1["status"]))
    bp.cleanup(lmp, MON[0], scope=[PENDING_ROW])
    check("P3", "next due week: fresh GET again and repeat (a 2nd POST, each POST preceded by a GET in the same run)",
          len(lmp.posts) == 2 and lmp.log.count("GET") >= 2 * lmp.log.count("POST")
          and all(lmp.log[i - 1] == "GET" for i, x in enumerate(lmp.log) if x == "POST"), lmp.log)
    lmq = LogLM([DUP_CLEAN])                             # Amazon finally shows the proposed value
    bp.cleanup(lmq, MON[1], scope=[PENDING_ROW])
    r2 = bp.led()["keyword_rows"][oc.row_key(PENDING_ROW)]
    check("P4", "cleaned == live and it equals the previously proposed value -> NO POST, Live Verified ('proposed value is live')",
          lmq.posts == [] and r2["status"] == oc.S_VERIFIED and "proposed value is live" in r2["history"][-1]["detail"]
          and r2["first_verified_at"].startswith(MON[1]))
    check("P5", "verification date recorded and 7-day monitoring starts from it (listing becomes tracked)",
          r2["first_verified_at"] and ASIN in {r["asin"] for r in bp.led()["keyword_rows"].values() if r.get("first_verified_at")})
    hist = [h.get("action") or h.get("event") for h in r2["history"]]
    check("P6", "every attempt is in the audit history (original submission, 2 POSTs, final no-POST verification)",
          hist == ["original submission", "POST", "POST", "no POST"], hist)

    # ---- user edits a pending row: the new live value is the source of truth ---------------------------
    bu = box(state="POST_ACCEPTED_PENDING_SYNC")
    user_kw = ["Wire Cage Pendant", "vintage edison bulb holder", "Wire cage pendant"]
    lmu = LogLM(user_kw, sync="immediate")
    bu.cleanup(lmu, "2026-10-05", scope=[PENDING_ROW])
    check("P7", "user changed the keywords: cleaned from the NEW live value (their new words kept), old proposal never sent",
          lmu.posts and lmu.posts[0]["backend_keywords"] == finetune(user_kw)["cleaned"]
          and lmu.posts[0]["backend_keywords"] != DUP_CLEAN and {"vintage", "edison", "bulb", "holder"} <= oc.uniq(lmu.posts[0]["backend_keywords"]))

    # ---- Update Failed -> next due week: fresh GET, retry (not blind) ----------------------------------
    bf = box()
    lmf = LogLM(DUP, accept=False)
    bf.cleanup(lmf, MON[0])
    s1 = row(bf)["status"]
    lmf.accept, lmf.sync = True, "immediate"
    bf.cleanup(lmf, MON[1])
    check("R1", "rejected update -> Update Failed; next due week retried from a fresh GET (GET precedes each POST) -> Live Verified",
          s1 == oc.S_UPDATE_FAILED and len(lmf.posts) == 2 and row(bf)["status"] == oc.S_VERIFIED
          and all(lmf.log[i - 1] == "GET" for i, x in enumerate(lmf.log) if x == "POST"), (s1, lmf.log))

    # ---- verification failed (neither value) -----------------------------------------------------------
    bv = box()
    lmv = LogLM(DUP, sync="other")
    bv.cleanup(lmv, MON[0])
    lmv.sync = "immediate"
    check("L", "read-back matches neither value -> Verification Failed (no verification date)",
          row(bv)["status"] == oc.S_VERIFY_FAILED and not last(bv).get("verified_at"))

    # ---- K: keywords edited between the GET and the POST -> not sent -----------------------------------
    bk = box()
    lmk = LogLM(DUP, sync="never")
    orig, n = lmk.read, {"i": 0}

    def edit_between(sub, pid):
        n["i"] += 1
        if n["i"] == 2:
            lmk.live = ["user edited again"]
        return orig(sub, pid)
    lmk.read = edit_between
    bk.cleanup(lmk, MON[0])
    check("K", "keywords changed between the GET and the POST -> not sent", lmk.posts == [] and "changed between" in last(bk)["detail"])

    # ---- optimization: A, H, I -----------------------------------------------------------------------------
    ba = box()
    ba.cleanup(LogLM(CLEAN), MON[1])                     # Monday 19 Oct check
    ba.optimize("2026-10-20", verify=False)              # user records on Tuesday
    lmb = LogLM(DUP, sync="immediate")
    ba.cleanup(lmb, "2026-10-20")
    check("A", "optimization recorded -> no check / Amazon update until the next weekly cycle", lmb.log == [])
    ba.cleanup(lmb, MON[2])
    check("A", "next Monday: user's keywords cleaned, POSTed once, verified -> optimization completed, baseline set",
          len(lmb.posts) == 1 and ba.opt()["monitoring_baseline_date"] == MON[2] and ba.opt()["keyword_cleanup"]["status"] == "Monitoring Active")
    ba.run(week("2026-10-25", 6, 5), "send")
    ba.run(week("2026-11-01", 5, 4), "send")
    check("H", "streak reset: baseline week not counted, next week count 1, no alert",
          ba.cyc("2026-10-25")["streak_status"] == "baseline" and ba.cyc("2026-11-01")["consecutive_decline_count"] == 1 and len(ba.sends()) == 1)
    ba.run(week("2026-11-08", 4, 3), "send")
    check("I", "later pair of consecutive declines -> NEW alert", len(ba.sends()) == 2)

    # ---- F / G + regardless of performance --------------------------------------------------------------
    bg = box(weeks=(("2026-10-04", 8, 10), ("2026-10-11", 10, 12)))
    lmg = LogLM(DUP, sync="immediate")
    bg.cleanup(lmg, MON[0])
    check("D2", "keyword check + clean-up also when performance improved (no alert involved)", len(lmg.posts) == 1 and bg.sends() == [])
    bh = Box()
    bh.run(week("2026-10-04", 10, 8), "send")
    one = len(bh.sends())
    bh.run(week("2026-10-11", 8, 6), "send")
    check("F/G", "one decline -> no alert; two consecutive -> alert (unchanged)", one == 0 and len(bh.sends()) == 1)

    # ---- scope: other listings never touched ---------------------------------------------------------------
    bs = box()
    other = LogLM(DUP, listing={**LISTING, "product_id": 777777, "sku": "NOT-IN-SCOPE"})
    bs.cleanup(other, MON[0])
    check("SCOPE", "a listing that is not in the report scope is never read or updated",
          other.posts == [] and oc.row_key({"asin": ASIN, "sku": "NOT-IN-SCOPE", "sub_source": 8}) not in bs.led()["keyword_rows"])
    wrong = LogLM(DUP, listing={**LISTING, "account": "amazon Dcvoltage"})
    by = box()
    by.cleanup(wrong, MON[0])
    check("SCOPE", "identity mismatch (account) -> Update Failed, no POST", wrong.posts == [] and row(by)["status"] == oc.S_READ_FAILED)

    # ---- dry run ---------------------------------------------------------------------------------------------
    bd = box()
    lmd = LogLM(DUP, sync="immediate")
    bd.cleanup(lmd, MON[0], apply=False)
    dry_posts, dry_status = list(lmd.posts), row(bd)["status"]
    bd.cleanup(lmd, MON[0], at="15:00:00")
    check("DRY", "dry run: GET + clean + validate, NO POST, status unchanged; the production run the same Monday still sends once",
          dry_posts == [] and dry_status == oc.S_VERIFIED and len(lmd.posts) == 1 and len(checks(bd)) == 1)

    # ---- validation / byte guidance ----------------------------------------------------------------------------
    long_kw = [" ".join(f"term{i}" for i in range(60))]
    f = finetune(long_kw + long_kw)
    errs, warns = oc.validate_payload(f["original"], f["cleaned"])
    e1, _ = oc.validate_payload("pendant light cage", "pendant light")
    e2, _ = oc.validate_payload("pendant light cage", "pendant light cage lamp")
    check("SAFE", "> 249 bytes -> warning only, not truncated; dropping/adding a word blocks the POST",
          not errs and warns and f["cleaned"] == " ".join(long_kw[0].split()) and e1 and e2)

    # ---- TC8: dashboard reflects the ledger ----------------------------------------------------------------------
    b8a, b8b = box(), box()
    b8a.cleanup(LogLM(DUP, sync="never"), MON[0])
    b8b.cleanup(LogLM(CLEAN), MON[0])
    da, db = dash.read(b8a.led()), dash.read(b8b.led())
    ea = last(b8a)
    check("TC8", "weekly keyword rows show the ledger status per ASIN-SKU (two different ledgers)",
          da["cleanup"] == [(ASIN, "WCE2E2PK", oc.S_PENDING)] and db["cleanup"] == [(ASIN, "WCE2E2PK", oc.S_VERIFIED)],
          (da["cleanup"], db["cleanup"]))
    check("TC8", "displayed counts, removed words, attempts and submission id come from the ledger",
          f'{ea["original_meta"]["words"]} words · {ea["original_meta"]["bytes"]} bytes' in da["cleanup_text"]
          and " ".join(ea["removed_words"]) in da["cleanup_text"] and ea["submission_id"] in da["cleanup_text"]
          and "2 attempt(s)" in da["cleanup_text"])


if __name__ == "__main__":
    sys.exit(main())
