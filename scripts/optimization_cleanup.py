"""Weekly backend keyword check for the report's ASIN-SKU rows (weekly pipeline stage; runs after extract,
before build_dataset).

Scope (business instruction 2026-09-29): ONLY the listings in data/keyword_scope.json = the 24 ASIN-SKU
rows of the weekly report (26 listings; a report row naming several SKUs is resolved per SKU). Never all
seller listings. Identity = ASIN + SKU + account (sub_source) + UK + listing id.

Every weekly DUE check of a listing:
  1 GET the latest live backend keywords (existing keyword_live_verify.lm_listing) - the source of truth
  2 GET shows the ACCEPTED payload, or still the value from before that accepted POST -> AUDIT only: no POST,
    no new anchor (an accepted payload is never re-sent as a retry; business rule 2026-10-05)
  3 run the existing fine-tuning (keyword_finetune.finetune, unchanged): keep every relevant word in order,
    remove only duplicate words / repeated entries / whitespace; nothing added, rewritten or truncated
  4 cleaned == live  -> no POST (a NEW clean value = the user's manual change -> new anchor = GET date)
  5 cleaned != live  -> validate, then POST the cleaned value through the existing keyword_live_update.post
                        (payload built from THIS fresh GET). POST Accepted = updated for the user -> monitoring
                        starts on the POST Accepted date; the read-back is recorded as audit only
  Every GET value, POST attempt, read-back and final live value is appended to the row history.
Due: at most one production check per listing per Monday-week, and not before 7 days after the listing's
last verification / last POST (never-submitted rows: from the first run).

Monitoring anchors (business instruction 2026-10-05): every backend keyword update is appended to the row's
live_updates; its date is the anchor used by performance_alert.py (Week 1 = anchor .. anchor+6,
Week 2 = anchor+7 .. anchor+13). Anchors:
  * the 22 original submissions (POST Accepted 28 Sep 2026): ONE common anchor ORIGINAL_ANCHOR = 2026-09-29
    (reconciled by seed(); GET verifications of them are audit only and never move it)
  * a weekly POST Accepted                                                    - the POST date
  * a weekly GET that finds a NEW clean live value (the user's manual change) - the GET date, or the date the
    user recorded with --record-live-update (must be after the previous anchor and not in the future)
A routine re-check of the same value is NOT a new update and never moves the anchor. The first GET of a
listing this workflow never updated only records its value (nothing changed -> nothing monitored).
Only sku, sub_source, site and backend_keywords are ever sent - no visible listing content.

Usage:
    python scripts/optimization_cleanup.py            # dry run: GET + clean + validate, no POST (not the week's check)
    python scripts/optimization_cleanup.py --apply    # production weekly check (POST allowed)
    python scripts/optimization_cleanup.py --retry-keyword-cleanup ASIN   # team: re-check that ASIN's listings this week
    python scripts/optimization_cleanup.py --record-live-update ASIN --date YYYY-MM-DD [--sku SKU] [--note TEXT]
        # team: the user changed the backend keywords manually on DATE; the next run's fresh GET confirms the new
        # value and DATE becomes the new monitoring anchor (if the change was already detected, its date is corrected)
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
S_ACCEPTED = "POST Accepted — Monitoring"      # accepted = updated for the user; GET read-back is audit only
LEGACY_PENDING = "POST Accepted — Live Verification Pending"   # pre-2026-10-05 label, migrated by reconcile()
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


# Business instruction 2026-10-05: the 22 original submissions (POST Accepted 28 Sep) share ONE monitoring anchor.
ORIGINAL_ANCHOR = "2026-09-29"
SRC_ORIGINAL = "original submission POST Accepted (common monitoring anchor)"
SRC_POST = "cleaned keywords POSTed (POST Accepted)"
SRC_MANUAL = "new live keyword value detected by the weekly GET (manual change)"
# earlier GET-confirmation sources (2026-10-02 rule); replaced by the POST Accepted anchor on reconciliation
LEGACY_SOURCES = ("original submission confirmed live (Listing Management GET)", "submitted value confirmed live by the weekly GET")


def add_live_update(row, at, source, value, user_date=None, date=None):
    """Appends one backend keyword update (= a monitoring anchor) and the value it set."""
    row.setdefault("live_updates", []).append({"date": date or user_date or str(at)[:10], "confirmed_at": at, "source": source,
                                               "user_reported_date": user_date, "value": norm(value)})
    row["live_value"] = norm(value)


_ORIGINALS = {}


def original_values():
    """ASIN|SKU -> the backend keywords live BEFORE the original submission (evidence/06 dashboard_original)."""
    if not _ORIGINALS:
        p = BASE / "evidence" / "06_live_keyword_update.json"
        if p.exists():
            for r in json.loads(p.read_text(encoding="utf-8"))["records"]:
                if r.get("submission_id"):
                    _ORIGINALS[f'{r["asin"]}|{r["sku"]}'] = norm(r.get("dashboard_original"))
    return _ORIGINALS


def reconcile(row, p, original=None):
    """Migration / reconciliation (2026-10-05 rule). POST Accepted = the update is done for the user: an accepted
    original submission is monitored from ORIGINAL_ANCHOR whatever the GET shows. A verification recorded in the
    evidence (07 weekly / 09) is kept as AUDIT (row Live Verified + first_verified_at); it never sets an anchor."""
    if row["status"] == LEGACY_PENDING:
        row["status"] = S_ACCEPTED
    ver = p.get("verified_at_utc") if p.get("state") == VERIFIED else None
    weekly = any(e.get("event") == "weekly check" and e.get("mode") == "apply" for e in row["history"])
    if ver and not row.get("first_verified_at") and not weekly:
        row.update(status=S_VERIFIED, first_verified_at=ver, last_verified_at=ver)
        row["history"].append({"event": "evidence reconciliation", "at": ver, "status_after": S_VERIFIED,
                               "detail": f"audit: Listing Management showed the submitted keywords ({p.get('evidence')})"})
        row["next_due"] = next_due(row)
    ups = row.setdefault("live_updates", [])
    row.setdefault("live_value", None)
    if row.get("submission_id") and row.get("proposed"):
        row.setdefault("accepted_value", norm(row["proposed"]))     # the accepted payload: never re-sent as a retry
        if original and not row.get("pre_post_value"):
            row["pre_post_value"] = norm(original)                    # what was live before the accepted POST
        ups[:] = [u for u in ups if u["source"] not in LEGACY_SOURCES]
        if not any(u["source"] == SRC_ORIGINAL for u in ups):
            ups.insert(0, {"date": ORIGINAL_ANCHOR, "confirmed_at": row.get("last_post_at") or ORIGINAL_ANCHOR,
                           "source": SRC_ORIGINAL, "user_reported_date": None, "value": norm(row["proposed"])})
        row["live_value"] = ups[-1]["value"]


def seed(ledger, scope, prior):
    """Creates the per-listing ledger rows once, from the existing submission history (never overwritten),
    then reconciles them with the latest verification evidence."""
    rows = ledger.setdefault("keyword_rows", {})
    for r in scope:
        k = row_key(r)
        p = prior.get(f'{r["asin"]}|{r["sku"]}') or {}
        original = r.get("original") or original_values().get(f'{r["asin"]}|{r["sku"]}')
        if k in rows:
            rows[k].update(proposed=r.get("proposed"), submission_id=r.get("submission_id"))
            reconcile(rows[k], p, original)
            continue
        verified = p.get("state") == VERIFIED
        rows[k] = {**{x: r.get(x) for x in ("asin", "sku", "sub_source", "account", "product_id", "report_row",
                                             "submission_id", "proposed")},
                   "status": S_VERIFIED if verified else (S_ACCEPTED if r.get("submission_id") else S_NOT_CHECKED),
                   "first_verified_at": p.get("verified_at_utc") if verified else None,
                   "last_verified_at": p.get("verified_at_utc") if verified else None,
                   "last_post_at": r.get("post_timestamp"), "last_check_week": None, "post_attempts": 1 if r.get("submission_id") else 0,
                   "history": [{"event": "original submission", "at": r.get("post_timestamp"), "submission_id": r.get("submission_id"),
                                "proposed": r.get("proposed"), "state_at_seed": p.get("state")}] if r.get("submission_id") else []}
        reconcile(rows[k], p, original)
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
    acc, pre = row.get("accepted_value"), row.get("pre_post_value")
    if acc and live == norm(acc):                                         # 2a: accepted value shown -> audit only
        if apply:
            row["first_verified_at"] = row.get("first_verified_at") or stamp
            row["last_verified_at"] = stamp
        return done(S_VERIFIED, action="no POST", verified_at=stamp, live_after=live,
                    detail="audit: GET shows the accepted keywords; monitoring continues (no new anchor)")
    if acc and pre and live == norm(pre):                                 # 2b: GET still shows the pre-POST value
        return done(S_ACCEPTED, action="no POST", live_after=live,
                    detail="audit: GET still shows the keywords from before the accepted POST; the accepted payload is "
                           "NOT re-sent and monitoring continues from its anchor")
    if cleaned == live:                                                   # 3: already clean -> no POST
        detail = "live keywords already clean (no duplicates)"
        if apply:
            row["first_verified_at"] = row.get("first_verified_at") or stamp
            row["last_verified_at"] = stamp
            if live != row.get("live_value"):
                if row.get("live_value") is not None or row.get("submission_id"):
                    # a new live value (the user's manual change): a genuine new update -> new anchor
                    add_live_update(row, stamp, SRC_MANUAL, live, take_user_date(row, now, e))
                    e["live_update_date"] = row["live_updates"][-1]["date"]
                    detail += f"; new live keyword value -> new monitoring cycle from {e['live_update_date']}"
                else:
                    row["live_value"] = live              # first read of a listing this workflow never updated
        return done(S_VERIFIED, action="no POST", verified_at=stamp, live_after=live, detail=detail)
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
    # POST Accepted = the update is done for the user: monitoring starts on the POST Accepted date (the anchor);
    # the accepted payload and the value it replaced are kept so a later GET never triggers a retry of it.
    u = row.pop("user_live_update", None)
    if u:   # the user's value still had duplicates: the cleaned version of it is what this POST set
        e["user_reported_date"] = u["date"]
    row["accepted_value"], row["pre_post_value"] = norm(cleaned), live
    add_live_update(row, stamp, SRC_POST, cleaned)
    e["live_update_date"] = stamp[:10]
    lm3 = io.read(r["sub_source"], r["product_id"])                       # 5: read-back = AUDIT only
    after = finetune(_entries(lm3))["original"] if lm3 and not _identity(lm3, r) else None
    e["live_after"] = after
    if after == norm(cleaned):
        row["first_verified_at"] = row.get("first_verified_at") or stamp
        row["last_verified_at"] = stamp
        return done(S_VERIFIED, verified_at=stamp, detail=f"POST Accepted; monitoring from {stamp[:10]}; "
                                                          "audit: read-back shows the cleaned keywords")
    return done(S_ACCEPTED, detail=f"POST Accepted; monitoring from {stamp[:10]}; audit: read-back shows "
                                   + ("the previous keywords" if after in (live, None) else "a different value"))


def take_user_date(row, now, e):
    """Consumes the date recorded with --record-live-update for the GET that confirms the manual change. It is the
    anchor only if it is after the previous anchor and not in the future; otherwise the GET date is used."""
    u = row.pop("user_live_update", None)
    if not u:
        return None
    prev = max((x["date"] for x in row.get("live_updates") or []), default="")
    if prev < u["date"] <= now.date().isoformat():
        e["user_reported_date"] = u["date"]
        return u["date"]
    e["user_date_ignored"] = f'recorded date {u["date"]} is not after the previous live update ({prev or "none"}) or is in the future'
    return None


def record_live_update(ledger, scope, asin, date, sku=None, note=None, today=None, now=None, prior=None):
    """Team input: the user changed the ASIN's backend keywords manually on `date`. If the weekly GET already
    detected that change (anchor = GET date), the anchor is corrected to `date`; otherwise `date` is kept for the
    next run, whose fresh GET must confirm a new live value (forced re-check). Nothing is sent to Amazon here."""
    today, now = today or dt.date.today(), now or dt.datetime.now()
    try:
        d = dt.date.fromisoformat(date).isoformat()
    except ValueError:
        raise ValueError(f"--date must be YYYY-MM-DD, got {date!r}") from None
    if d > today.isoformat():
        raise ValueError(f"live update date {d} is in the future")
    mine = [r for r in scope if r["asin"] == asin and (sku is None or r["sku"] == sku)]
    if not mine:
        raise ValueError(f"{asin}{' / ' + sku if sku else ''} is not in the weekly report scope")
    if len(mine) > 1:
        raise ValueError(f"{asin} has {len(mine)} listings in scope ({', '.join(r['sku'] for r in mine)}): pass --sku")
    row = seed(ledger, scope, prior if prior is not None else prior_states_from_evidence())[row_key(mine[0])]
    ups = row.get("live_updates") or []
    last, prev = (ups[-1] if ups else None), (ups[-2]["date"] if len(ups) > 1 else "")
    entry = {"event": "user reported live update", "at": now.isoformat(timespec="seconds"), "date": d, "note": note}
    if last and last["source"] == SRC_MANUAL and prev < d <= last["confirmed_at"][:10]:
        entry["detail"] = f"corrects the anchor of the change detected on {last['confirmed_at'][:10]} ({last['date']} -> {d})"
        last.update(date=d, user_reported_date=d)
    elif last and d <= last["date"]:
        raise ValueError(f"{d} is not after the current live update date {last['date']} of {row['sku']}")
    else:
        row["user_live_update"] = {"date": d, "recorded_at": entry["at"], "note": note}
        ledger.setdefault("keyword_retry", {})[asin] = entry["at"]
        entry["detail"] = "the next run's fresh GET confirms the new live value; this date becomes its monitoring anchor"
    row["history"].append(entry)
    return entry


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
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="production weekly check: POST allowed")
    ap.add_argument("--retry-keyword-cleanup", metavar="ASIN")
    ap.add_argument("--record-live-update", metavar="ASIN", help="team: the user changed this ASIN's keywords manually")
    ap.add_argument("--date", help="manual live update date YYYY-MM-DD (with --record-live-update)")
    ap.add_argument("--sku", help="listing SKU (required when the ASIN has several listings in scope)")
    ap.add_argument("--note", help="short note (optional)")
    a = ap.parse_args()
    ledger = json.loads(LEDGER.read_text(encoding="utf-8")) if LEDGER.exists() else {"cycles": {}, "alerts": {}}
    scope = load_scope()
    if a.record_live_update:
        if not a.date:
            print("ERROR: --date YYYY-MM-DD is required with --record-live-update")
            return 2
        try:
            e = record_live_update(ledger, scope, a.record_live_update.strip().upper(), a.date, a.sku, a.note)
        except ValueError as err:
            print("NOT RECORDED:", err)
            return 2
        LEDGER.write_text(json.dumps(ledger, indent=1, ensure_ascii=False), encoding="utf-8")
        print("RECORDED:", json.dumps(e, ensure_ascii=False))
        return 0
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
