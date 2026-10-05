"""Tests for the weekly READ-ONLY re-check of the 20 pending keyword submissions
(keyword_live_verify.py --pending-only) and its hand-off into monitoring.

Fake DB + fake Listing Management reads; temp copies of the evidence files for the build-level test.
No POST exists in this path; no network. Output: evidence/14_pending_verification_test_results.json
"""
import contextlib
import copy
import datetime as dt
import io
import json
import pathlib
import shutil
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import build_dataset  # noqa: E402
import keyword_live_verify as klv  # noqa: E402
import optimization_cleanup as oc  # noqa: E402
import performance_alert as pa  # noqa: E402

BASE = pathlib.Path(__file__).resolve().parent.parent
R = []


def check(tid, name, ok, detail=""):
    R.append({"id": tid, "test": name, "result": "PASS" if ok else "FAIL", "detail": str(detail)[:400]})
    print(f'{"PASS" if ok else "FAIL"}  {tid}  {name}  {"" if ok else detail}')


def sub(asin, sku, pid, orig, prop):
    return {"asin": asin, "sku": sku, "sub_source": 8, "account": "amazon Ledsone", "product_id": pid,
            "submission_id": f"s-{pid}", "post_timestamp": "2026-09-28T17:21:17", "dashboard_original": orig, "proposed": prop}


POSTED = [sub("B0AVERIFIED", "SKU-A", 1, "a a b", "a b"),          # verified earlier
          sub("B0BNOWMATCH", "SKU-B", 2, "c c d", "c d"),          # pending -> now matches
          sub("B0CSTILLOLD", "SKU-C", 3, "e e f", "e f"),          # pending -> still original
          sub("B0DNEITHER", "SKU-D", 4, "g g h", "g h"),           # pending -> neither value
          sub("B0EIDENTITY", "SKU-E", 5, "i i j", "i j"),          # pending -> LM shows another SKU
          sub("B0FTITLECHG", "SKU-F", 6, "k k l", "k l")]          # pending -> matches, title changed since POST
LIVE = {1: "a b", 2: "c d", 3: "e e f", 4: "something else", 5: "i j", 6: "k l"}
BASELINE = {str(r["product_id"]): {"asin": r["asin"], "sku": r["sku"], "sub_source": 8, "status": "BUYABLE",
                                   "is_parent": 0, "wrong_sku": 0, "is_ended": 0, "title": "T"} for r in POSTED}


class IO:
    def __init__(self, live):
        self.live, self.db_reads, self.lm_reads = dict(live), [], []

    def read_db(self, pid):
        self.db_reads.append(pid)
        r = next(x for x in POSTED if x["product_id"] == pid)
        row = {"asin": r["asin"], "sku": r["sku"], "sub_source": 8, "status": "Active", "is_parent": 0, "wrong_sku": 0,
               "is_ended": 0, "title": "NEW TITLE" if pid == 6 else "T", "site": "UK", "updated_at": "2026-10-01"}
        return row, self.live[pid]

    def read_lm(self, sub_source, pid):
        self.lm_reads.append(pid)
        r = next(x for x in POSTED if x["product_id"] == pid)
        return {"id": pid, "item_id": r["asin"], "sku": "OTHER-SKU" if pid == 5 else r["sku"], "site": "UK",
                "channel": "amazon Ledsone", "amz_platinum_keywords": [{"id": 1, "keyword": self.live[pid], "view_order": "1"}]}


def main():
    prior = {"B0AVERIFIED|SKU-A": {"state": klv.VERIFIED, "verified_at_utc": "2026-09-29T04:14:40Z"}}
    for r in POSTED[1:]:
        prior[f'{r["asin"]}|{r["sku"]}'] = {"state": "POST_ACCEPTED_PENDING_SYNC", "verified_at_utc": None}
    previous = {"B0AVERIFIED|SKU-A": {"asin": "B0AVERIFIED", "sku": "SKU-A", "classification": klv.VERIFIED,
                                      "read_at_utc": "2026-09-29T04:14:40Z", "detail": "live backend keywords = proposed"}}
    io1 = IO(LIVE)
    out, rechecked = klv.recheck_pending(POSTED, previous, prior, io1.read_db, io1.read_lm, lambda: "2026-10-02T09:31:00Z", BASELINE)
    by = {f'{o["asin"]}|{o["sku"]}': o for o in out}
    check("P1", "already-verified submission is kept as is and NOT re-read", 1 not in io1.lm_reads and 1 not in io1.db_reads
          and by["B0AVERIFIED|SKU-A"]["classification"] == klv.VERIFIED
          and by["B0AVERIFIED|SKU-A"]["verified_at_utc"] == "2026-09-29T04:14:40Z")
    check("P2", "pending whose live keywords now = proposed payload -> UPDATED / VERIFIED, verification date recorded",
          by["B0BNOWMATCH|SKU-B"]["classification"] == klv.VERIFIED and by["B0BNOWMATCH|SKU-B"]["verified_at_utc"] == "2026-10-02T09:31:00Z")
    check("P3", "live still original -> stays pending (POST_ACCEPTED_PENDING_SYNC), no verification date",
          by["B0CSTILLOLD|SKU-C"]["classification"] == "POST_ACCEPTED_PENDING_SYNC" and by["B0CSTILLOLD|SKU-C"]["verified_at_utc"] is None)
    check("P4", "live matches neither -> not verified (MISMATCH), re-checked next week",
          by["B0DNEITHER|SKU-D"]["classification"] == "MISMATCH" and "B0DNEITHER|SKU-D" in rechecked)
    check("P5", "identity mismatch (Listing Management SKU differs) blocks verification even though keywords match",
          by["B0EIDENTITY|SKU-E"]["classification"] == "VERIFICATION FAILED" and "lm_sku" in by["B0EIDENTITY|SKU-E"]["detail"])
    check("P6", "title changed since the POST: recorded as an observation, keywords match -> verified",
          by["B0FTITLECHG|SKU-F"]["classification"] == klv.VERIFIED and "title" in (by["B0FTITLECHG|SKU-F"]["guard_observation"] or ""))
    check("P7", "every submission accounted for (history preserved), 5 re-checked + 1 kept",
          len(out) == len(POSTED) and len(rechecked) == 5)

    # next weekly run: newly verified ones are final; still-unverified ones re-checked again
    prior2 = {f'{o["asin"]}|{o["sku"]}': {"state": o["classification"], "verified_at_utc": o.get("verified_at_utc")} for o in out}
    io2 = IO({**LIVE, 3: "e f"})                              # the pending one syncs this week
    out2, re2 = klv.recheck_pending(POSTED, by, prior2, io2.read_db, io2.read_lm, lambda: "2026-10-09T09:31:00Z", BASELINE)
    by2 = {f'{o["asin"]}|{o["sku"]}': o for o in out2}
    check("P8", "next week: previously verified are not re-read and keep their FIRST verification date",
          2 not in io2.lm_reads and by2["B0BNOWMATCH|SKU-B"]["verified_at_utc"] == "2026-10-02T09:31:00Z")
    check("P9", "next week: the still-pending one is re-checked and now verifies (dated this run)",
          by2["B0CSTILLOLD|SKU-C"]["classification"] == klv.VERIFIED and by2["B0CSTILLOLD|SKU-C"]["verified_at_utc"] == "2026-10-09T09:31:00Z")

    src = (BASE / "scripts" / "keyword_live_verify.py").read_text(encoding="utf-8")
    check("P10", "read-only: keyword_live_verify.py contains no POST / write request", 'method="POST"' not in src
          and "data=" not in src and "edit-amazon-listing" not in src)

    # latest_states: stale legacy 09 can never override a weekly verified result; verified is final
    tmp = pathlib.Path(tempfile.mkdtemp())
    def f(name, recs, at):
        p = tmp / name
        p.write_text(json.dumps({"checked_at_utc": at, "records": recs}), encoding="utf-8")
        return p
    r9 = f("09.json", [{"asin": "X", "sku": "1", "classification": "POST_ACCEPTED_PENDING_SYNC", "verified_at_utc": "2026-09-29T08:57:51Z"},
                       {"asin": "Y", "sku": "1", "classification": klv.VERIFIED, "verified_at_utc": "2026-09-29T08:50:00Z"}], "2026-09-29T09:00:09Z")
    r7 = f("07.json", [{"asin": "X", "sku": "1", "classification": klv.VERIFIED, "read_at_utc": "2026-10-02T04:00:00Z", "verified_at_utc": "2026-10-02T04:00:00Z"},
                       {"asin": "Y", "sku": "1", "classification": "POST_ACCEPTED_PENDING_SYNC", "read_at_utc": "2026-10-02T04:01:00Z"}], "2026-10-02T04:05:00Z")
    st = klv.latest_states([r9, r7])
    check("P11", "weekly 07 verified beats stale 09 pending; a verification is final (09 verified not undone by 07 pending)",
          st["X|1"]["state"] == klv.VERIFIED and st["X|1"]["verified_at_utc"] == "2026-10-02T04:00:00Z"
          and st["Y|1"]["state"] == klv.VERIFIED, st)

    # build-level: a pending submission verified in the weekly 07 becomes Live-verified and enters monitoring
    target = "B0D7944QDY|WCB6BM2PK"
    tb = pathlib.Path(tempfile.mkdtemp())
    (tb / "evidence").mkdir()
    (tb / "data").mkdir()
    for n in ("06_live_keyword_update.json", "07_live_keyword_verification.json", "09_remaining_20_live_verification.json"):
        shutil.copyfile(BASE / "evidence" / n, tb / "evidence" / n)
    v7 = json.loads((tb / "evidence" / "07_live_keyword_verification.json").read_text(encoding="utf-8"))
    for r in v7["records"]:
        if f'{r["asin"]}|{r["sku"]}' == target:
            r.update(classification=klv.VERIFIED, read_at_utc="2026-10-02T04:00:00Z", verified_at_utc="2026-10-02T04:00:00Z")
    (tb / "evidence" / "07_live_keyword_verification.json").write_text(json.dumps(v7), encoding="utf-8")
    saved = (build_dataset.BASE, build_dataset.OUT, build_dataset.LEDGER)
    build_dataset.BASE, build_dataset.OUT, build_dataset.LEDGER = tb, tb / "data" / "ds.json", tb / "data" / "none.json"
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            build_dataset.main()
    finally:
        build_dataset.BASE, build_dataset.OUT, build_dataset.LEDGER = saved
    ds = json.loads((tb / "data" / "ds.json").read_text(encoding="utf-8"))
    cr = {c["key"]: c for c in ds["change_record"]}
    # baseline = the audit verifications already in the real evidence (B0DH4KYFPD, B0GXB7RGZK, B0C43L42DK)
    real = klv.latest_states([BASE / "evidence" / "09_remaining_20_live_verification.json",
                              BASE / "evidence" / "07_live_keyword_verification.json"])
    base_verified = sum(1 for v in real.values() if v["state"] == klv.VERIFIED)
    check("P12", "build (rule 2026-10-05): a later GET verification is AUDIT only - the submission stays Monitoring from the "
          "common update date 2026-09-29, like all 22 POST Accepted submissions; audit count +1, 0 pending",
          cr[target]["status"] == "Monitoring" and cr[target]["date_changed"] == "2026-09-29"
          and cr[target]["submission"]["get_verified_date"] == "2026-10-02"
          and sum(1 for c in cr.values() if c.get("submission")) == 22
          and all((c["status"], c["date_changed"]) == ("Monitoring", "2026-09-29") for c in cr.values() if c.get("submission"))
          and ds["kpi"]["get_audit_shows_submitted"] == base_verified + 1 and ds["kpi"]["live_verification_pending"] == 0
          and ds["kpi"]["posts_accepted"] == 22,
          (cr[target]["status"], cr[target]["date_changed"], ds["kpi"]["get_audit_shows_submitted"], base_verified))
    # reconcile the ledger from the temp evidence (as performance_alert.py does in production), then monitor
    led = {}
    prior = klv.latest_states([tb / "evidence" / "09_remaining_20_live_verification.json",
                               tb / "evidence" / "07_live_keyword_verification.json"])
    oc.seed(led, oc.load_scope(), prior)
    mon = pa.update(led, ds, now=dt.datetime(2026, 10, 5, 12))
    cyc = mon.get("B0D7944QDY|2026-09-29") or {}
    loaded = sum(1 for d in ds["monitoring_data"]["available_dates"] if "2026-09-29" <= d <= "2026-10-05")
    exp_status = pa.S_NOT_STARTED if loaded == 0 else pa.S_WEEK1 if loaded < 7 else None
    check("P13", "monitoring anchored on 2026-09-29 (not the 2 Oct GET date): Week 1 = 29 Sep..5 Oct, Week 2 = 6..12 Oct; "
          "status follows the loaded days, no Week 1 total before 7/7 days",
          (cyc.get("week1_start"), cyc.get("week1_end"), cyc.get("week2_start"), cyc.get("week2_end"))
          == ("2026-09-29", "2026-10-05", "2026-10-06", "2026-10-12") and "B0D7944QDY|2026-10-02" not in mon
          and (exp_status is None or cyc.get("monitoring_status") == exp_status)
          and cyc.get("week1_days_loaded") == loaded and (loaded == 7 or cyc.get("week1_orders") is None), cyc)
    cycles = {k: c for k, c in mon.items() if c["kind"] == "cycle"}
    asins = {c["asin"] for c in cr.values() if c.get("submission")}
    check("P14", "every POST Accepted submission is monitored without a GET gate: one 2026-09-29 cycle per ASIN (21 ASINs / 22 "
          "listings), no 'Update Pending' entry, ledger rows not left as Live Verification Pending",
          set(cycles) == {f"{a}|2026-09-29" for a in asins} and len(asins) == 21
          and not [c for c in mon.values() if c["kind"] == "pending"]
          and not [r for r in led["keyword_rows"].values() if r["status"] == oc.LEGACY_PENDING], sorted(cycles)[:3])

    sys.path.insert(0, str(BASE / "automation"))
    import run as runner
    prod = runner.pipeline(False)
    check("P15", "pending rows are handled by the weekly keyword check (fresh GET + POST if needed); no separate read-only stage, no e-mail stage",
          [n for n, _ in prod] == ["extract", "weekly_keyword_check", "build_dataset", "performance_monitoring", "render", "validate"])

    p_, f_ = sum(r["result"] == "PASS" for r in R), sum(r["result"] == "FAIL" for r in R)
    (BASE / "evidence" / "14_pending_verification_test_results.json").write_text(json.dumps(
        {"run_at": dt.datetime.now().isoformat(timespec="seconds"), "mode": "fake DB/Listing Management reads, temp evidence copies; read-only path",
         "passed": p_, "failed": f_, "results": R}, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"{p_} PASS / {f_} FAIL")
    return 0 if f_ == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
