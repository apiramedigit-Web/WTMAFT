"""LIVE backend keyword update for Paulroshan's READY_FOR_UPDATE listings (user-approved 2026-09-28).

Reuses the read-only checks in keyword_live_dryrun.assess(), re-run immediately before each POST.
POST goes to the endpoint the PH-KOIBM / TCH-UAAT n8n workflows use (Content-Type header only;
no credential exists or is stored here):
    POST https://listings.vintageinterior.co.uk/api/edit-amazon-listing
    {"sku": <Listing SKU>, "sub_source": "<id>", "site": "UK", "backend_keywords": "<final>"}

The API submits to Amazon asynchronously (response.status ACCEPTED + submissionId); the tool's
tables (ledsone, read-only here) only show the new keywords after the next Amazon sync. So after
each POST the listing is re-read once:
    new keywords visible and guarded fields unchanged -> UPDATED / VERIFIED
    still the old keywords                            -> POST_ACCEPTED_PENDING_SYNC
The run stops at the first unexpected API response, identity/marketplace mismatch, live keyword
change since the dry run, or guarded-field change. Listings already POSTed (ALREADY_POSTED) are
never sent again. One record at a time, 10 s apart.

Outputs: evidence/06_live_keyword_update.json/.csv; verified updates -> data/change_records.json
(Status Monitoring, Date Changed = actual POST date).
"""
import csv
import datetime as dt
import json
import os
import pathlib
import sys
import time
import urllib.error
import urllib.request
from collections import Counter

import psycopg

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from keyword_finetune import finetune  # noqa: E402
from keyword_live_dryrun import DS, PH_USER_ID, SITE, assess, norm  # noqa: E402

BASE = pathlib.Path(__file__).resolve().parent.parent
EVID = BASE / "evidence" / "06_live_keyword_update"
RECORDS = BASE / "data" / "change_records.json"
API = "https://listings.vintageinterior.co.uk/api/edit-amazon-listing"
GUARD = ("asin", "sku", "sub_source", "site", "parent_sku", "mapped_sku", "price", "title",
         "product_description", "quantity", "main_image_url", "category_id", "product_type",
         "status", "is_ended", "fulfilment")
# POSTed in run 1 (2026-09-28): Amazon ACCEPTED, submissionId 85692fe943e24cc98603443ba73fbbdb.
ALREADY_POSTED = {"B0DH4KYFPD|WCDTBM2PK+RPR44WH2PK"}


def snapshot(con, pid):
    con.commit()  # new snapshot
    cur = con.cursor()
    cur.execute(f"SELECT {', '.join(GUARD)} FROM listings.amazon_listings WHERE id=%s", (pid,))
    guard = {g: str(v) for g, v in zip(GUARD, cur.fetchone())}
    cur.execute("""SELECT keyword FROM listings.amazon_listing_search_engine_keywords
                   WHERE product_id=%s ORDER BY view_order, id""", (pid,))
    return guard, [r[0] for r in cur.fetchall()]


def post(payload):
    req = urllib.request.Request(API, data=json.dumps(payload).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            status, body = r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        status, body = e.code, e.read().decode("utf-8", "replace")
    except (urllib.error.URLError, TimeoutError) as e:
        status, body = None, f"request error: {e}"
    try:
        js = json.loads(body)
    except ValueError:
        js = None
    return status, js, body[:500]


def key(r):
    return f'{r["asin"]}|{r["sku"]}'


def main():
    assert DS["meta"]["ph_scope"]["user_id"] == PH_USER_ID
    rows = DS["keyword_analysis"]
    prev = {}
    if EVID.with_suffix(".json").exists():
        prev = {key(r): r for r in json.loads(EVID.with_suffix(".json").read_text(encoding="utf-8"))["records"]}
    out = []
    with psycopg.connect(os.environ["WLP_SOURCE_DB_URL"]) as con:
        con.read_only = True
        stopped = None
        for n, k in enumerate(rows):
            if key(k) in ALREADY_POSTED:  # never resend; re-check whether the sync shows it yet
                rec = prev[key(k)]
                guard, entries = snapshot(con, int(rec["product_id"]))
                rec["live_after"], rec["live_entries_after"] = finetune(entries)["original"], entries
                visible = rec["live_after"] == norm(rec["proposed"])
                rec.update(api_status="ACCEPTED",
                           submission_id=json.loads(rec["post_result"].split(": ", 1)[1])["response"]["submissionId"],
                           post_timestamp=rec.get("post_timestamp") or "2026-09-28 (run 1)",
                           verification=("PASS: live keywords = proposed" if visible else
                                         "new keywords not yet visible in Listing Management (awaiting Amazon sync)"),
                           final_status="UPDATED / VERIFIED" if visible else "POST_ACCEPTED_PENDING_SYNC",
                           run="run 1 (not re-sent)")
                out.append(rec)
                continue
            rec = assess(con.cursor(), k)
            con.commit()
            rec.update(post_timestamp=None, post_result=None, api_status=None, submission_id=None,
                       verification=None, date_changed=None, live_after=None, run="run 2")
            if rec["decision"] != "READY_FOR_UPDATE":
                if rec["decision"] != "SKIPPED - MISSING DATA" and not stopped:
                    stopped = f'{rec["asin"]} {rec["sku"]}: {rec["decision"]} ({rec["validation"]})'
                    print("STOP:", stopped, flush=True)
                rec["final_status"] = rec["decision"]
                out.append(rec)
                continue
            if stopped:
                rec["final_status"] = "NOT RUN (stopped)"
                out.append(rec)
                continue
            if not norm(rec["proposed"]):
                stopped = f'{rec["asin"]}: proposed keywords empty'
                rec["final_status"] = "SKIPPED - MISSING DATA"
                out.append(rec)
                continue
            pid = int(rec["product_id"])
            guard_before, _ = snapshot(con, pid)
            payload = {"sku": rec["sku"], "sub_source": str(rec["sub_source"]), "site": SITE,
                       "backend_keywords": rec["proposed"]}
            status, js, body = post(payload)
            rec["post_timestamp"] = dt.datetime.now().isoformat(timespec="seconds")
            resp = js.get("response") if isinstance(js, dict) else None
            rec["post_result"] = f"HTTP {status}: " + (json.dumps(js, ensure_ascii=False) if js is not None else body)
            expected = (status == 200 and js.get("success") is True and isinstance(resp, dict)
                        and resp.get("status") == "ACCEPTED" and resp.get("sku") == rec["sku"]
                        and not resp.get("issues") and resp.get("submissionId")) if isinstance(js, dict) else False
            if not expected:
                rec.update(final_status="POST FAILED", verification="not attempted",
                           api_status=(resp or {}).get("status") if isinstance(resp, dict) else None)
                stopped = f'{rec["asin"]} {rec["sku"]}: unexpected API response'
                print("STOP:", stopped, rec["post_result"][:300], flush=True)
                out.append(rec)
                continue
            rec["api_status"], rec["submission_id"] = resp["status"], resp["submissionId"]
            time.sleep(10)  # also the delay between POSTs
            guard_after, entries = snapshot(con, pid)
            rec["live_after"], rec["live_entries_after"] = finetune(entries)["original"], entries
            changed = [g for g in GUARD if guard_after[g] != guard_before[g]]
            if changed:
                rec.update(verification="FAIL: other listing fields changed: " + ", ".join(changed),
                           final_status="POST SUCCESS - VERIFICATION FAILED")
                stopped = f'{rec["asin"]} {rec["sku"]}: guarded fields changed {changed}'
                print("STOP:", stopped, flush=True)
            elif rec["live_after"] == norm(rec["proposed"]):
                rec.update(verification="PASS: live keywords = proposed; guarded fields unchanged",
                           date_changed=rec["post_timestamp"], final_status="UPDATED / VERIFIED")
            else:
                rec.update(verification="new keywords not yet visible in Listing Management (awaiting Amazon sync)",
                           final_status="POST_ACCEPTED_PENDING_SYNC")
            print(f'{n + 1}/{len(rows)} {rec["final_status"]} {rec["asin"]} {rec["sku"]} {rec["submission_id"]}',
                  flush=True)
            out.append(rec)

    EVID.with_suffix(".json").write_text(json.dumps(
        {"run_at": dt.datetime.now().isoformat(timespec="seconds"), "mode": "LIVE apply (run 2)", "endpoint": API,
         "live_read_source": "ledsone listings.amazon_listings + amazon_listing_search_engine_keywords",
         "stopped": stopped, "records": out}, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    cols = ["asin", "sku", "sub_source", "site", "dashboard_original", "live_before", "proposed", "decision",
            "run", "post_timestamp", "api_status", "submission_id", "live_after", "verification",
            "date_changed", "final_status", "validation"]
    with EVID.with_suffix(".csv").open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(out)

    existing = []
    if RECORDS.exists():
        data = json.loads(RECORDS.read_text(encoding="utf-8"))
        existing = data.get("records", data) if isinstance(data, dict) else data
    by_key = {r["key"]: r for r in existing}
    for r in out:
        if r["final_status"] == "UPDATED / VERIFIED" and r.get("date_changed"):
            by_key[key(r)] = {"key": key(r), "asin": r["asin"], "sku": r["sku"],
                              "issue_detected": r["issue_detected"], "optimization_action": r["fine_tuning_action"],
                              "original_backend_keywords": r["dashboard_original"],
                              "final_backend_keywords": r["proposed"],
                              "change_type": "Backend Keyword Fine-Tuning",
                              "date_changed": r["date_changed"][:10], "updated_at": r["date_changed"],
                              "submission_id": r["submission_id"], "status": "Monitoring"}
    if by_key:
        RECORDS.write_text(json.dumps({"records": list(by_key.values())}, indent=1, ensure_ascii=False),
                           encoding="utf-8")
    print("stopped:", stopped)
    print(Counter(r["final_status"] for r in out))


if __name__ == "__main__":
    main()
