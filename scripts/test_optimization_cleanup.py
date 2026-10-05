"""Deterministic tests: WEEKLY backend keyword check of the report's ASIN-SKU rows (optimization_cleanup.py)
and its hand-off to the Week 1 / Week 2 monitoring from the actual live update date (performance_alert.py).

Rule (2026-09-29): every weekly due check GETs the latest live keywords (source of truth), runs the
existing fine-tuning, POSTs through the existing API only if the cleaned value differs from the live value
(payload always built from that fresh GET), and records everything in the audit history.
Rule (2026-10-05): POST Accepted = updated for the user -> monitoring starts (the 22 original submissions: common
anchor 2026-09-29); GET read-backs are audit only; an accepted payload is never re-sent because a GET still shows the
previous keywords; only a genuinely new update (POST Accepted / the user's manual change) is a new anchor. No e-mail.

Temp ledgers, FAKE Listing Management (GET + POST), temp dashboards. No real POST, no e-mail, no network. Output: evidence/13_optimization_cleanup_test_results.json
"""
import datetime as dt
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import optimization_cleanup as oc  # noqa: E402
from keyword_finetune import finetune  # noqa: E402
from test_e2e_monitoring import (ASIN, LISTING, ORIGINAL_ENTRIES, PROD_FILES, PROPOSED, SCOPE_ROW, Box, Dash, FakeLM,  # noqa: E402
                                 daily, per_day, sha)

BASE = pathlib.Path(__file__).resolve().parent.parent
RESULTS = []
DUP = ["Wire Cage Pendant Light Cage pendant light shade", "industrial cage lamp", "industrial cage lamp"]
DUP_LIVE = " ".join(" ".join(e.split()) for e in DUP)
DUP_CLEAN = finetune(DUP)["cleaned"]          # what the existing rules produce (not re-implemented here)
CLEAN = ["Wire Cage Pendant Light", "industrial lamp shade"]
MON = ["2026-10-12", "2026-10-19", "2026-10-26", "2026-11-02", "2026-11-09"]   # first due: 6 Oct (+7 from 29 Sep)


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


def box(state="UPDATED / VERIFIED"):
    """A temp ledger whose original submission was verified live on 29 Sep (or is still pending)."""
    return Box(prior={f"{ASIN}|WCE2E2PK": {"state": state, "verified_at_utc": "2026-09-29T04:00:00Z"
                                           if state == oc.VERIFIED else None}})


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
    check("SAFETY", "production ledger / dataset / extract unchanged", before == after)
    p_, f_ = sum(r["result"] == "PASS" for r in RESULTS), sum(r["result"] == "FAIL" for r in RESULTS)
    (BASE / "evidence" / "13_optimization_cleanup_test_results.json").write_text(json.dumps(
        {"run_at": dt.datetime.now().isoformat(timespec="seconds"),
         "mode": "deterministic: temp ledgers, fake Listing Management GET/POST, temp dashboards; "
                 "no real POST, no e-mail, no network", "passed": p_, "failed": f_, "results": RESULTS},
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

    # ---- POST Accepted rule (2026-10-05): GET is audit only; the accepted payload is never re-sent ----------------
    bp = box(state="POST_ACCEPTED_PENDING_SYNC")
    lmp = LogLM(ORIGINAL_ENTRIES, sync="never")         # Amazon still shows the keywords from before the POST
    bp.cleanup(lmp, "2026-10-02")                        # < 7 days after the 28 Sep POST: not due
    check("P1", "accepted row: not re-checked before 7 days after its last POST", lmp.log == [])
    bp.cleanup(lmp, "2026-10-05")
    r1 = bp.row()
    check("P2", "due: fresh GET still shows the pre-POST keywords (with duplicates) -> NO POST (no retry of the accepted payload); "
          "audit only, status POST Accepted — Monitoring", lmp.log == ["GET"] and lmp.posts == [] and r1["status"] == oc.S_ACCEPTED,
          (lmp.log, r1["status"]))
    bp.cleanup(lmp, MON[0])
    check("P3", "next due week: fresh GET again, still the previous keywords -> still no POST", lmp.log == ["GET", "GET"] and lmp.posts == [])
    lmq = LogLM([PROPOSED])                              # Amazon finally shows the accepted value
    bp.cleanup(lmq, MON[1])
    r2 = bp.row()
    check("P4", "GET shows the accepted value -> NO POST, audit Live Verified",
          lmq.posts == [] and r2["status"] == oc.S_VERIFIED and r2["first_verified_at"].startswith(MON[1]))
    check("P5", "the monitoring anchor never moved: the common update date 2026-09-29 (GET dates are audit only)",
          [(u["date"], u["source"]) for u in r2["live_updates"]] == [(oc.ORIGINAL_ANCHOR, oc.SRC_ORIGINAL)], r2.get("live_updates"))
    hist = [h.get("action") or h.get("event") for h in r2["history"]]
    check("P6", "every check is in the audit history (original submission, 3 no-POST checks)",
          hist == ["original submission", "no POST", "no POST", "no POST"], hist)

    # ---- user edits a pending row: the new live value is the source of truth ---------------------------
    bu = box(state="POST_ACCEPTED_PENDING_SYNC")
    user_kw = ["Wire Cage Pendant", "vintage edison bulb holder", "Wire cage pendant"]
    lmu = LogLM(user_kw, sync="immediate")
    bu.cleanup(lmu, "2026-10-05")
    check("P7", "user changed the keywords: cleaned from the NEW live value (their new words kept), old proposal never sent",
          lmu.posts and lmu.posts[0]["backend_keywords"] == finetune(user_kw)["cleaned"]
          and lmu.posts[0]["backend_keywords"] != PROPOSED and {"vintage", "edison", "bulb", "holder"} <= oc.uniq(lmu.posts[0]["backend_keywords"]))

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
    check("L", "read-back matches neither value -> still POST Accepted — Monitoring (read-back is audit; no verification date), "
          "new anchor = POST date", row(bv)["status"] == oc.S_ACCEPTED and not last(bv).get("verified_at")
          and row(bv)["live_updates"][-1]["date"] == MON[0])

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

    # ---- A: live-update anchors (monitoring starts from the actual confirmed live date) --------------------
    ba = box()
    ba.cleanup(LogLM(CLEAN), MON[1])                     # the user had changed the keywords manually (already clean)
    ra = ba.row()
    check("A1", "manual change found by the weekly GET (clean) -> no POST, new anchor = that GET date",
          [(u["date"], u["source"]) for u in ra["live_updates"]] == [("2026-09-29", oc.SRC_ORIGINAL), (MON[1], oc.SRC_MANUAL)])
    lmb = LogLM(DUP, sync="immediate")                   # the user changed them again, with duplicates
    ba.cleanup(lmb, MON[2])
    rb = ba.row()
    check("A2", "manual change with duplicates -> cleaned from the latest live value, POSTed once -> anchor = POST Accepted date",
          len(lmb.posts) == 1 and lmb.posts[0]["backend_keywords"] == DUP_CLEAN
          and [u["date"] for u in rb["live_updates"]] == ["2026-09-29", MON[1], MON[2]] and rb["live_updates"][-1]["source"] == oc.SRC_POST)
    ba.cleanup(LogLM([DUP_CLEAN]), MON[3])
    check("A3", "next weekly re-check of the same live value -> no POST, NO new anchor",
          len(ba.row()["live_updates"]) == 3)
    ba.run(daily(MON[3], per_day(MON[2], 8, 1)), now=MON[3] + "T18:00:00")
    cyc = {k: c for k, c in ba.mon().items() if c["kind"] == "cycle"}
    check("A4", "monitoring: one cycle per confirmed live update; only the newest is current; Week 1 of the newest = its live date",
          sorted(cyc) == sorted(f"{ASIN}|{d}" for d in ("2026-09-29", MON[1], MON[2]))
          and [c["live_update_date"] for c in cyc.values() if c["latest"]] == [MON[2]]
          and cyc[f"{ASIN}|{MON[2]}"]["week1_start"] == MON[2], {k: c["monitoring_status"] for k, c in cyc.items()})

    # ---- regardless of performance --------------------------------------------------------------------------
    bg = box()
    lmg = LogLM(DUP, sync="immediate")
    bg.cleanup(lmg, MON[0])
    check("D2", "keyword check + clean-up runs on every due week whatever the performance (no alert / e-mail involved)",
          len(lmg.posts) == 1 and "alerts" not in bg.led())

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
          da["cleanup"] == [(ASIN, "WCE2E2PK", oc.S_ACCEPTED)] and db["cleanup"] == [(ASIN, "WCE2E2PK", oc.S_VERIFIED)],
          (da["cleanup"], db["cleanup"]))
    check("TC8", "displayed counts, removed words, attempts and submission id come from the ledger",
          f'{ea["original_meta"]["words"]} words · {ea["original_meta"]["bytes"]} bytes' in da["cleanup_text"]
          and " ".join(ea["removed_words"]) in da["cleanup_text"] and ea["submission_id"] in da["cleanup_text"]
          and "2 attempt(s)" in da["cleanup_text"])


if __name__ == "__main__":
    sys.exit(main())
