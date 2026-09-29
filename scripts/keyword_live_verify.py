"""READ-ONLY verification of the submitted backend keyword updates. No write of any kind.

For every POSTed record in evidence/06_live_keyword_update.json, read the listing's current
backend keywords from the Listing Management Tool itself (GET get-all-active-listings-data,
field amz_platinum_keywords; the ledsone listings.* mirror lags behind the tool, verified
2026-09-29) and classify:
  VERIFICATION FAILED        ASIN / SKU / sub_source / site differ, or a guarded field changed
  UPDATED / VERIFIED         live backend keywords == proposed
  POST_ACCEPTED_PENDING_SYNC live backend keywords == original (Amazon sync not yet reflected)
  MISMATCH                   live backend keywords are neither
Guard baseline = the listing rows captured before any POST (2026-09-28 11:44), frozen in
evidence/06_guard_baseline.json (data/extract.json is re-extracted weekly): asin, sku, sub_source,
site, status, is_parent, wrong_sku, is_ended, title (read from the ledsone mirror, read-only transaction).
The mirror's keyword value is recorded as db_live_now for audit only.

Modes:
  python scripts/keyword_live_verify.py                 # full check of all 22 submissions (as before)
  python scripts/keyword_live_verify.py --pending-only  # WEEKLY PIPELINE STAGE (business instruction
      2026-09-29): re-check only submissions not yet UPDATED / VERIFIED. Never re-submits anything.
      Live keywords == proposed payload and identity matches -> UPDATED / VERIFIED (verified_at_utc
      recorded; that ASIN's 7-day monitoring starts from it). Otherwise it stays unverified and is
      re-checked next week. Guarded-field changes are recorded as observations and do not block
      verification in this mode (weeks after the POST, titles/status legitimately change, e.g. by the
      user's manual optimization); ASIN/SKU/account/site identity still blocks. Records already
      verified are kept unchanged (22-submission history preserved); the previous 07 file is archived.
Output: evidence/07_live_keyword_verification.json and .csv.
"""
import argparse
import csv
import datetime as dt
import json
import os
import pathlib
import shutil
import sys
import time
import urllib.error
import urllib.request
from collections import Counter

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from keyword_finetune import finetune  # noqa: E402

BASE = pathlib.Path(__file__).resolve().parent.parent
UPD_PATH = BASE / "evidence" / "06_live_keyword_update.json"
BASELINE_PATH = BASE / "evidence" / "06_guard_baseline.json"
EXTRACT_PATH = BASE / "data" / "extract.json"
OUT = BASE / "evidence" / "07_live_keyword_verification"
REM = BASE / "evidence" / "09_remaining_20_live_verification.json"
GUARD = ("asin", "sku", "sub_source", "status", "is_parent", "wrong_sku", "is_ended", "title")
VERIFIED = "UPDATED / VERIFIED"


LM_API = "https://listings.vintageinterior.co.uk/api/get-all-active-listings-data"
LM_PACE = 6  # seconds between GETs; the tool returns HTTP 429 after ~7 fast calls


def norm(s):
    return " ".join((s or "").split())


def lm_listing(sub_source, pid):
    """One listing from the Listing Management Tool (GET only). The cursor is exclusive
    (returns id > next_page_id), so next_page_id = id-1 with per_page=1 returns exactly that id."""
    url = f"{LM_API}?channel=amazon&account={sub_source}&per_page=1&next_page_id={pid - 1}"
    for wait in (0, 20, 40, 60, 90):
        time.sleep(wait)
        try:
            with urllib.request.urlopen(urllib.request.Request(url, method="GET"), timeout=60) as resp:
                rows = json.loads(resp.read().decode("utf-8")).get("data") or []
            return rows[0] if rows and rows[0]["id"] == pid else None
        except urllib.error.HTTPError as e:
            if e.code != 429:
                raise
    raise RuntimeError(f"Listing Management rate limit persisted for listing {pid}")


def guard_baseline():
    """product_id -> listing row captured before any POST (frozen file; falls back to the extract)."""
    if BASELINE_PATH.exists():
        rows = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))["listings"]
    else:
        rows = json.loads(EXTRACT_PATH.read_text(encoding="utf-8"))["listings"]
    return {str(l["product_id"]): l for l in rows}


def check_time(r, file_checked_at=None):
    return r.get("read_at_utc") or r.get("verified_at_utc") or file_checked_at or ""


def latest_states(paths):
    """key 'ASIN|SKU' -> {state, checked_at_utc, verified_at_utc, evidence}. A verified result is final
    (earliest verification kept). Otherwise the result of the LAST file in `paths` that has the key wins
    (callers pass the legacy one-off 09 first and the weekly 07 last). File order rather than timestamps:
    evidence/09 stores local (UTC+5:30) times labelled 'Z', so its timestamps are not comparable."""
    out = {}
    for path in paths:
        if not path.exists():
            continue
        v = json.loads(path.read_text(encoding="utf-8"))
        at = v.get("checked_at_utc") or v.get("checked_at")
        for r in v["records"]:
            k, t = f'{r["asin"]}|{r["sku"]}', check_time(r, at)
            cur = out.get(k)
            new = {"state": r["classification"], "checked_at_utc": t, "evidence": path.name,
                   "verified_at_utc": (r.get("verified_at_utc") or t) if r["classification"] == VERIFIED else None}
            if cur is None:
                out[k] = new
            elif cur["state"] == VERIFIED:
                if new["state"] == VERIFIED and new["verified_at_utc"] < cur["verified_at_utc"]:
                    out[k] = new
            else:
                out[k] = new
    return out


def classify(r, row, lm, b, read_at, db_live, guards_block=True):
    """Classification of one submission (unchanged rules; guards_block=False = weekly pending mode)."""
    kws = sorted((lm or {}).get("amz_platinum_keywords") or [], key=lambda k: (int(k["view_order"] or 0), k["id"]))
    entries = [k["keyword"] for k in kws]
    live = finetune(entries)["original"]
    identity = {"asin": row["asin"] == r["asin"], "sku": row["sku"] == r["sku"],
                "sub_source": row["sub_source"] == r["sub_source"], "site": row["site"] == "UK"}
    if lm:
        identity.update({"lm_asin": lm["item_id"] == r["asin"], "lm_sku": lm["sku"] == r["sku"],
                         "lm_site": lm["site"] == "UK", "lm_account": lm["channel"] == r["account"]})
    changed = [g for g in GUARD if str(row[g]) != str(b.get(g))]
    # Status rule (business instruction 2026-09-29): a status representation change such as
    # BUYABLE -> Active, with identity matching and is_ended = 0, is an observation, not a failure.
    status_obs = (f'status {b.get("status")} -> {row["status"]} (representation change; is_ended=0)'
                  if "status" in changed and row["status"] == "Active"
                  and b.get("status") in ("BUYABLE", "DISCOVERABLE") and row["is_ended"] == 0 else None)
    if status_obs:
        changed = [g for g in changed if g != "status"]
    guard_obs = None
    if not guards_block and changed:
        guard_obs, changed = f"guarded fields changed since the POST (observation only): {', '.join(changed)}", []
    if not lm or not entries:
        status, why = "NOT FOUND", ("listing not in the Listing Management active feed" if not lm
                                    else "no backend keyword entries in Listing Management")
    elif not all(identity.values()) or changed:
        status = "VERIFICATION FAILED"
        why = ("identity mismatch: " + ", ".join(k for k, ok in identity.items() if not ok)
               if not all(identity.values()) else "guarded fields changed: " + ", ".join(changed))
    elif live == norm(r["proposed"]):
        status, why = VERIFIED, "live backend keywords = proposed"
    elif live == norm(r["dashboard_original"]):
        status, why = "POST_ACCEPTED_PENDING_SYNC", "live still shows the original keywords"
    else:
        status, why = "MISMATCH", "live keywords are neither the original nor the proposed value"
    return {"asin": r["asin"], "sku": r["sku"], "sub_source": r["sub_source"], "site": "UK",
            "submission_id": r["submission_id"], "post_timestamp": r.get("post_timestamp"),
            "listing_updated_at": row["updated_at"], "proposed": r["proposed"],
            "original": r["dashboard_original"], "live_now": live, "live_entries_now": entries,
            "lm_keyword_row_ids": [k["id"] for k in kws], "read_at_utc": read_at,
            "verified_at_utc": read_at if status == VERIFIED else None,
            "db_live_now": db_live, "db_matches": ("proposed" if db_live == norm(r["proposed"]) else
                                                   "original" if db_live == norm(r["dashboard_original"]) else "neither"),
            "identity_checks": identity, "guarded_fields_changed": changed,
            "status_observation": status_obs, "guard_observation": guard_obs,
            "classification": status, "detail": why}


def recheck_pending(posted, previous, prior_states, read_db, read_lm, now_utc, baseline):
    """Weekly pending re-check (pure; I/O injected). Returns the merged 22-record list and the re-checked keys.
    Records already UPDATED / VERIFIED (per prior_states) are kept as they are and never re-read."""
    out, rechecked = [], []
    for r in posted:
        k = f'{r["asin"]}|{r["sku"]}'
        if prior_states.get(k, {}).get("state") == VERIFIED:
            prev = dict(previous.get(k) or {})
            prev.setdefault("verified_at_utc", prior_states[k]["verified_at_utc"])
            out.append(prev)
            continue
        row, db_live = read_db(int(r["product_id"]))
        lm = read_lm(r["sub_source"], int(r["product_id"]))
        rec = classify(r, row, lm, baseline[str(r["product_id"])], now_utc(), db_live, guards_block=False)
        rec["previous_state"] = prior_states.get(k, {}).get("state")
        out.append(rec)
        rechecked.append(k)
    return out, rechecked


def write_out(out, mode, extra=None):
    OUT.with_suffix(".json").write_text(json.dumps(
        {"checked_at": dt.datetime.now().isoformat(timespec="seconds"),
         "checked_at_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
         "mode": mode,
         "source": "Listing Management Tool GET /api/get-all-active-listings-data, field amz_platinum_keywords",
         "mirror": "ledsone listings.* (read-only) for identity/guard checks; its keyword value is db_live_now (may lag the tool)",
         "guard_baseline": "evidence/06_guard_baseline.json (listing rows captured 2026-09-28 11:44, before any POST)",
         **(extra or {}), "records": out}, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    cols = ["asin", "sku", "sub_source", "site", "submission_id", "post_timestamp", "read_at_utc", "verified_at_utc",
            "listing_updated_at", "original", "proposed", "live_now", "db_matches", "guarded_fields_changed",
            "guard_observation", "classification", "detail"]
    with OUT.with_suffix(".csv").open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pending-only", action="store_true", help="weekly: re-check only not-yet-verified submissions")
    a = ap.parse_args()
    import psycopg

    upd = json.loads(UPD_PATH.read_text(encoding="utf-8"))
    posted = [r for r in upd["records"] if r.get("submission_id")]
    baseline = guard_baseline()
    with psycopg.connect(os.environ["WLP_SOURCE_DB_URL"]) as con:
        con.read_only = True
        cur = con.cursor()

        def read_db(pid):
            cur.execute(f"SELECT {', '.join(GUARD)}, site, updated_at FROM listings.amazon_listings WHERE id=%s", (pid,))
            row = dict(zip(GUARD + ("site", "updated_at"), cur.fetchone()))
            cur.execute("""SELECT keyword FROM listings.amazon_listing_search_engine_keywords
                           WHERE product_id=%s ORDER BY view_order, id""", (pid,))
            return row, finetune([x[0] for x in cur.fetchall()])["original"]

        def read_lm(sub, pid):
            lm = lm_listing(sub, pid)
            time.sleep(LM_PACE)
            return lm

        def now_utc():
            return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        if a.pending_only:
            prior = latest_states([REM, OUT.with_suffix(".json")])
            previous = ({f'{r["asin"]}|{r["sku"]}': r for r in json.loads(OUT.with_suffix(".json").read_text(encoding="utf-8"))["records"]}
                        if OUT.with_suffix(".json").exists() else {})
            out, rechecked = recheck_pending(posted, previous, prior, read_db, read_lm, now_utc, baseline)
            if OUT.with_suffix(".json").exists():   # keep every earlier verification snapshot (audit trail)
                old_at = json.loads(OUT.with_suffix(".json").read_text(encoding="utf-8")).get("checked_at_utc", "unknown")
                arch = OUT.parent / f"07_live_keyword_verification_{old_at.replace(':', '').replace('-', '')}_archived.json"
                if not arch.exists():
                    shutil.copyfile(OUT.with_suffix(".json"), arch)
            write_out(out, "weekly pending re-check (read-only GET; nothing re-submitted)",
                      {"rechecked": rechecked, "kept_verified": len(out) - len(rechecked)})
        else:
            out = []
            for r in posted:
                row, db_live = read_db(int(r["product_id"]))
                lm = read_lm(r["sub_source"], int(r["product_id"]))
                out.append(classify(r, row, lm, baseline[str(r["product_id"])], now_utc(), db_live))
            write_out(out, "read-only verification (GET only; no POST, no sync triggered)")
    print(len(out), Counter(o["classification"] for o in out))
    for o in out:
        print(f'{o["classification"]:28} {o["asin"]} {o["sku"]} verified_at={o.get("verified_at_utc")} | {o["detail"]}')


if __name__ == "__main__":
    main()
