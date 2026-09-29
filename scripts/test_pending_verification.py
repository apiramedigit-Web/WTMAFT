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
    check("P12", "build: newly verified pending submission -> Live-verified, dated by the verification; 22-submission history kept",
          cr[target]["status"] == "Live-verified" and cr[target]["submission"]["checked_at_utc"] == "2026-10-02T04:00:00Z"
          and sum(1 for c in cr.values() if c.get("submission")) == 22
          and ds["kpi"]["live_verified"] == 3 and ds["kpi"]["posts_accepted"] == 22, (cr[target]["status"], ds["kpi"]["live_verified"]))
    tracked = pa.tracked_asins(ds)
    check("P13", "it enters normal monitoring: tracked, new 7-day cycle from the verification (next Sunday 4 Oct)",
          "B0D7944QDY" in tracked and tracked["B0D7944QDY"]["last_live_verification_date"] == "2026-10-02"
          and pa.monitoring_start("2026-10-02") == "2026-10-04", tracked.get("B0D7944QDY"))
    check("P14", "the other pending submissions stay unverified (not tracked for monitoring yet)",
          sum(1 for c in cr.values() if c["status"] == "Live-verified") == 3 and len(tracked) == 3)

    sys.path.insert(0, str(BASE / "automation"))
    import run as runner
    prod = runner.pipeline(False)
    check("P15", "pending rows are handled by the weekly keyword check (fresh GET + POST if needed); no separate read-only stage",
          [n for n, _ in prod] == ["extract", "weekly_keyword_check", "build_dataset", "performance_alert", "render", "validate"])

    p_, f_ = sum(r["result"] == "PASS" for r in R), sum(r["result"] == "FAIL" for r in R)
    (BASE / "evidence" / "14_pending_verification_test_results.json").write_text(json.dumps(
        {"run_at": dt.datetime.now().isoformat(timespec="seconds"), "mode": "fake DB/Listing Management reads, temp evidence copies; read-only path",
         "passed": p_, "failed": f_, "results": R}, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"{p_} PASS / {f_} FAIL")
    return 0 if f_ == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
