"""Weekly backend keyword check for the report's ASIN-SKU rows (weekly pipeline stage; runs after extract,
before build_dataset).

Scope (business instruction 2026-09-29): ONLY the listings in data/keyword_scope.json = the 24 ASIN-SKU
rows of the weekly report (26 listings; a report row naming several SKUs is resolved per SKU). Never all
seller listings. Identity = ASIN + SKU + account (sub_source) + UK + listing id.

Every weekly DUE check of a listing (whatever its status, incl. "Live-verified" and "POST Accepted - Live
Verification Pending"):
  1 GET the latest live backend keywords (existing keyword_live_verify.lm_listing) - the source of truth
  2 run the existing fine-tuning (keyword_finetune.finetune, unchanged): keep every relevant word in order,
    remove only duplicate words / repeated entries / whitespace; nothing added, rewritten or truncated
  3 cleaned == live  -> no POST; the cleaned keywords are live -> Live Verified
                        ("proposed value is live" when it equals the earlier proposed payload)
  4 cleaned != live  -> validate, then POST the cleaned value through the existing keyword_live_update.post
                        (payload built from THIS fresh GET - an earlier payload is never re-sent as such),
                        read back: live == cleaned -> Live Verified; otherwise POST Accepted - Live
                        Verification Pending (next due week: fresh GET again and repeat)
  Every GET value, POST attempt, verification result and final live value is appended to the row history.
Due: at most one production check per listing per Monday-week, and not before 7 days after the listing's
last verification / last POST (never-submitted rows: from the first run). Recording an optimization never
makes a listing due early.
Monitoring: a listing's FIRST live verification starts its 7-day monitoring (performance_alert.py); routine
weekly re-verifications do not reset the decline streak. After a recorded Full Optimization, the first
production run in which all the ASIN's checked listings end Live Verified sets the optimization's
monitoring_baseline_date (the reset).
Only sku, sub_source, site and backend_keywords are ever sent - no visible listing content.

Usage:
    python scripts/optimization_cleanup.py            # dry run: GET + clean + validate, no POST (not the week's check)
    python scripts/optimization_cleanup.py --apply    # production weekly check (POST allowed)
    python scripts/optimization_cleanup.py --retry-keyword-cleanup ASIN   # team: re-check that ASIN's listings this week
"""
import argparse
import datetime as dt
import json
import pathlib
import re
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from keyword_finetune import finetune  # noqa: E402

BASE = pathlib.Path(__file__).resolve().parent.parent
LEDGER = BASE / "data" / "monitoring_cycles.json"
SCOPE = BASE / "data" / "keyword_scope.json"
EVID = BASE / "evidence" / "13_weekly_keyword_check.json"
SITE = "UK"
VERIFIED = "UPDATED / VERIFIED"
BYTE_GUIDANCE = 249          # Amazon's published backend search-term limit: observation only (no trimming rule)
PACE = 6                     # seconds between Listing Management calls (the tool returns 429 on fast calls)
POST_SETTLE = 10             # seconds before the read-back after a POST (as keyword_live_update.py)
_EDGE = re.compile(r"^[^\w]+|[^\w]+$", re.UNICODE)

S_VERIFIED = "Live Verified"
S_PENDING = "POST Accepted — Live Verification Pending"
S_UPDATE_FAILED = "Update Failed"
S_VERIFY_FAILED = "Verification Failed"
S_NO_KEYWORDS = "No backend keywords (nothing to clean)"
S_READ_FAILED = "Read Failed (not in the Listing Management feed / identity mismatch)"
S_VALIDATION = "Update Failed (cleaned payload failed validation)"
S_DRY = "Listing Update Pending (dry run: not sent)"
S_NOT_CHECKED = "Awaiting first weekly check"


def norm(s):
    return " ".join((s or "").split())


def words(text):
    return [w for w in norm(text).split() if _EDGE.sub("", w)]


def uniq(text):
    return {_EDGE.sub("", w).casefold() for w in words(text)}


def week_key(d):
    """Monday of the calendar week containing d: one production check per listing per Monday-week."""
    return (d - dt.timedelta(days=d.weekday())).isoformat()


def row_key(r):
    return f'{r["asin"]}|{r["sku"]}|{r["sub_source"]}'


class ListingManagement:
    """Production I/O: the existing GET and POST, imported lazily (tests inject fakes instead)."""
    def __init__(self, pace=PACE, settle=POST_SETTLE):
        from keyword_live_update import post
        from keyword_live_verify import lm_listing
        self._get, self._post, self.pace, self.settle = lm_listing, post, pace, settle

    def read(self, sub_source, product_id):
        row = self._get(sub_source, product_id)
        time.sleep(self.pace)
        return row

    def post(self, payload):
        res = self._post(payload)
        time.sleep(self.settle)
        return res


def load_scope():
    return json.loads(SCOPE.read_text(encoding="utf-8"))["listings"]


def prior_states_from_evidence():
    """Latest verification state of the original submissions (07 weekly over the legacy one-off 09)."""
    from keyword_live_verify import latest_states
    return latest_states([BASE / "evidence" / "09_remaining_20_live_verification.json",
                          BASE / "evidence" / "07_live_keyword_verification.json"])


def seed(ledger, scope, prior):
    """Creates the per-listing ledger rows once, from the existing submission history (never overwritten)."""
    rows = ledger.setdefault("keyword_rows", {})
    for r in scope:
        k = row_key(r)
        if k in rows:
            rows[k].update(proposed=r.get("proposed"), submission_id=r.get("submission_id"))
            continue
        p = prior.get(f'{r["asin"]}|{r["sku"]}') or {}
        verified = p.get("state") == VERIFIED
        rows[k] = {**{x: r.get(x) for x in ("asin", "sku", "sub_source", "account", "product_id", "report_row",
                                             "submission_id", "proposed")},
                   "status": S_VERIFIED if verified else (S_PENDING if r.get("submission_id") else S_NOT_CHECKED),
                   "first_verified_at": p.get("verified_at_utc") if verified else None,
                   "last_verified_at": p.get("verified_at_utc") if verified else None,
                   "last_post_at": r.get("post_timestamp"), "last_check_week": None, "post_attempts": 1 if r.get("submission_id") else 0,
                   "history": [{"event": "original submission", "at": r.get("post_timestamp"), "submission_id": r.get("submission_id"),
                                "proposed": r.get("proposed"), "state_at_seed": p.get("state")}] if r.get("submission_id") else []}
    return rows


def next_due(row):
    anchor = max(filter(None, [(row.get("last_verified_at") or "")[:10], (row.get("last_post_at") or "")[:10]]), default=None)
    first = dt.date.fromisoformat(anchor) + dt.timedelta(days=7) if anchor else None
    wk = dt.date.fromisoformat(row["last_check_week"]) + dt.timedelta(days=7) if row.get("last_check_week") else None
    cands = [d for d in (first, wk) if d]
    return max(cands).isoformat() if cands else None       # None = due at the first run


def _entries(lm_row):
    kws = sorted((lm_row or {}).get("amz_platinum_keywords") or [], key=lambda k: (int(k.get("view_order") or 0), k["id"]))
    return [k["keyword"] for k in kws]


def _identity(lm_row, r):
    checks = {"ASIN": lm_row.get("item_id") == r["asin"], "SKU": lm_row.get("sku") == r["sku"], "site UK": lm_row.get("site") == SITE,
              "account": lm_row.get("channel") == r["account"], "listing id": int(lm_row.get("id") or -1) == int(r["product_id"])}
    return [k for k, ok in checks.items() if not ok]


def validate_payload(original, cleaned):
    """Returns (errors, warnings). Errors block the update; warnings are surfaced only."""
    errors, warnings = [], []
    if not norm(cleaned):
        errors.append("cleaned keywords are empty")
    lost, new = sorted(uniq(original) - uniq(cleaned)), sorted(uniq(cleaned) - uniq(original))
    if lost:
        errors.append(f"relevant words would be removed: {lost[:10]}")
    if new:
        errors.append(f"words not in the live keywords would be added: {new[:10]}")
    if len(words(cleaned)) > len(words(original)):
        errors.append("cleaned keywords are longer than the original")
    b = len(norm(cleaned).encode("utf-8"))
    if b > BYTE_GUIDANCE:
        warnings.append(f"cleaned field is {b} bytes, over Amazon's {BYTE_GUIDANCE}-byte backend guidance; "
                        "NOT truncated (no documented trimming rule) - review manually")
    return errors, warnings


def _accepted(status, js, sku):
    """Same acceptance criteria as keyword_live_update.py."""
    resp = js.get("response") if isinstance(js, dict) else None
    ok = (status == 200 and isinstance(js, dict) and js.get("success") is True and isinstance(resp, dict)
          and resp.get("status") == "ACCEPTED" and resp.get("sku") == sku and not resp.get("issues")
          and bool(resp.get("submissionId")))
    return ok, (resp or {}).get("submissionId") if ok else None


def check_listing(row, r, apply, io, now):
    """One weekly check of one listing. Returns the audit entry (also appended to row history)."""
    stamp = now.isoformat(timespec="seconds")
    e = {"event": "weekly check", "week": week_key(now.date()), "at": stamp, "mode": "apply" if apply else "dry_run",
         "status_before": row["status"]}

    def done(status, **kw):
        e.update(status_after=status, **kw)
        if apply:
            row["status"] = status
            row["history"].append(e)
        return e

    lm = io.read(r["sub_source"], r["product_id"])                      # 1: fresh GET
    if not lm or _identity(lm, r):
        return done(S_READ_FAILED, detail=("listing not in the Listing Management active feed" if not lm
                                           else "identity check failed: " + ", ".join(_identity(lm, r))))
    entries = _entries(lm)
    f = finetune(entries)                                                 # 2: existing fine-tuning
    live, cleaned = f["original"], (f["cleaned"] if f["needs_change"] else f["original"])
    e.update(live_before=live, live_entries=entries, cleaned=cleaned, duplicate_issue=f["needs_change"], issue=f["issue"],
             removed_words=f["removed_words"], repeated_entries=f["repeated_entries"],
             original_meta={"entries": f["n_entries"], "words": f["original_word_count"], "bytes": f["original_bytes"]},
             cleaned_meta={"entries": 1 if f["needs_change"] else f["n_entries"], "words": f["cleaned_word_count"],
                           "bytes": len(cleaned.encode("utf-8"))})
    if not entries:
        return done(S_NO_KEYWORDS, action="no POST", detail="the live listing has no backend keywords")
    if cleaned == live:                                                   # 3: already clean -> no POST
        proposed_live = bool(row.get("proposed")) and live == norm(row["proposed"])
        if apply:
            row["first_verified_at"] = row.get("first_verified_at") or stamp
            row["last_verified_at"] = stamp
        return done(S_VERIFIED, action="no POST", verified_at=stamp, live_after=live,
                    detail="proposed value is live" if proposed_live else "live keywords already clean (no duplicates)")
    errors, warnings = validate_payload(live, cleaned)                    # 4: validate the payload
    e["warnings"] = warnings
    if errors:
        return done(S_VALIDATION, action="no POST", detail="; ".join(errors))
    if not apply:
        return done(S_DRY, action="no POST (dry run)", detail="validated; the Monday production check sends it")
    lm2 = io.read(r["sub_source"], r["product_id"])                       # the user may edit right before the POST
    if not lm2 or _identity(lm2, r) or finetune(_entries(lm2))["original"] != live:
        return done(row["status"], action="no POST", detail="live keywords changed between the read and the update: "
                                                             "not sent; fresh GET at the next due check")
    payload = {"sku": r["sku"], "sub_source": str(r["sub_source"]), "site": SITE, "backend_keywords": cleaned}
    status, js, body = io.post(payload)
    ok, sid = _accepted(status, js, r["sku"])
    row["post_attempts"] = row.get("post_attempts", 0) + 1
    row["last_post_at"] = stamp
    e.update(action="POST", post_http=status, submission_id=sid,
             post_result=(json.dumps(js, ensure_ascii=False) if js is not None else body)[:500])
    if not ok:
        return done(S_UPDATE_FAILED, detail=f"Listing Management did not accept the update (HTTP {status}); "
                                             "next due check starts again from a fresh GET")
    lm3 = io.read(r["sub_source"], r["product_id"])                       # 5: live verification (read-back)
    after = finetune(_entries(lm3))["original"] if lm3 and not _identity(lm3, r) else None
    e["live_after"] = after
    if after == norm(cleaned):
        row["first_verified_at"] = row.get("first_verified_at") or stamp
        row["last_verified_at"] = stamp
        return done(S_VERIFIED, verified_at=stamp, detail="update accepted and the cleaned keywords are live")
    if after == live or after is None:
        return done(S_PENDING, detail="update accepted; Listing Management still shows the previous keywords - "
                                      "next due check: fresh GET and repeat")
    return done(S_VERIFY_FAILED, detail="live keywords match neither the cleaned nor the previous value; "
                                        "next due check starts again from a fresh GET")


def link_optimizations(ledger):
    """A recorded Full Optimization is completed by the first production run after it in which all of the
    ASIN's checked listings ended Live Verified; that date is the new monitoring baseline (the reset)."""
    rows = ledger.get("keyword_rows", {})
    for asin, opts in ledger.get("optimizations", {}).items():
        mine = [r for r in rows.values() if r["asin"] == asin]
        runs = {}
        for r in mine:
            for e in r["history"]:
                if e.get("event") == "weekly check" and e.get("mode") == "apply":
                    runs.setdefault(e["at"][:16], []).append(e)
        nxt = min((r.get("next_due") or "") for r in mine) if mine else None
        for o in opts:
            after = [(t, es) for t, es in sorted(runs.items()) if t >= o.get("recorded_at", o["completed_on"])[:16]]
            done = next(((t, es) for t, es in after if all(x["status_after"] == S_VERIFIED for x in es)), None)
            if done:
                o["monitoring_baseline_date"] = max(x["verified_at"] for x in done[1])[:10]
                o["keyword_cleanup"] = {"status": "Monitoring Active", "check_week": done[1][0]["week"],
                                        "live_verified_at": max(x["verified_at"] for x in done[1])}
            elif after:
                sts = [x["status_after"] for x in after[-1][1]]
                bad = [s for s in sts if s.startswith(("Update Failed", "Verification Failed"))]
                o["keyword_cleanup"] = {"status": (bad[0].split(" (")[0] if bad else
                                                   S_PENDING if S_PENDING in sts else sts[0]),
                                        "check_week": after[-1][1][0]["week"]}
            else:
                o["keyword_cleanup"] = {"status": "User Optimization Completed",
                                        "detail": f"awaiting the next weekly keyword check (due {nxt or 'next run'})"}


def process(ledger, scope, apply=False, io=None, now=None, prior=None):
    """One weekly run over the scope. Returns the audit entries of this run."""
    now = now or dt.datetime.now()
    today, wk = now.date(), week_key(now.date())
    rows = seed(ledger, scope, prior if prior is not None else prior_states_from_evidence())
    retry = ledger.setdefault("keyword_retry", {})
    out = []
    for r in scope:
        row = rows[row_key(r)]
        nd = next_due(row)
        forced = bool(retry.get(r["asin"]))
        due = forced or ((nd is None or today.isoformat() >= nd) and row.get("last_check_week") != wk)
        row["next_due"] = nd
        if not due:
            continue
        io = io or ListingManagement()
        e = check_listing(row, r, apply, io, now)
        out.append({"asin": r["asin"], "sku": r["sku"], **{k: e.get(k) for k in ("status_before", "status_after", "action",
                                                                                 "submission_id", "detail")}})
        if apply:
            row["last_check_week"] = wk
            row["next_due"] = next_due(row)
        else:
            row["last_dry_run"] = e
    if apply:
        for a in list(retry):
            retry.pop(a)
    link_optimizations(ledger)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="production weekly check: POST allowed")
    ap.add_argument("--retry-keyword-cleanup", metavar="ASIN")
    a = ap.parse_args()
    ledger = json.loads(LEDGER.read_text(encoding="utf-8")) if LEDGER.exists() else {"cycles": {}, "alerts": {}}
    ledger.setdefault("optimizations", {})
    scope = load_scope()
    if a.retry_keyword_cleanup:
        asin = a.retry_keyword_cleanup.strip().upper()
        if asin not in {r["asin"] for r in scope}:
            print(f"NOT RETRIED: {asin} is not in the weekly report scope")
            return 2
        ledger.setdefault("keyword_retry", {})[asin] = dt.datetime.now().isoformat(timespec="seconds")
        LEDGER.write_text(json.dumps(ledger, indent=1, ensure_ascii=False), encoding="utf-8")
        print(f"{asin}: its listings will be re-checked (fresh GET) on the next run, even within this week.")
        return 0
    out = process(ledger, scope, apply=a.apply)
    LEDGER.write_text(json.dumps(ledger, indent=1, ensure_ascii=False), encoding="utf-8")
    rows = ledger["keyword_rows"]
    EVID.write_text(json.dumps({"run_at": dt.datetime.now().isoformat(timespec="seconds"),
                                "mode": "apply (POST allowed)" if a.apply else "dry run (no POST)",
                                "this_run": out, "keyword_rows": rows}, indent=1, ensure_ascii=False), encoding="utf-8")
    from collections import Counter
    print(f"scope listings: {len(scope)}; checked this run: {len(out)}; status: {dict(Counter(r['status'] for r in rows.values()))}")
    for r in out:
        print(f'  {r["asin"]} {r["sku"]}: {r["status_before"]} -> {r["status_after"]} [{r["action"]}] {r["detail"]}')
    return 0   # per-listing failures are business states on the dashboard; crashes still exit non-zero


if __name__ == "__main__":
    sys.exit(main())
