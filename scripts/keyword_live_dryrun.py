"""READ-ONLY dry run: match the dashboard's backend keyword fine-tuning to the live listings.

No write of any kind. Live state is read from the Listing Management Tool's own tables on ledsone
(listings.amazon_listings + listings.amazon_listing_search_engine_keywords; DSN WLP_SOURCE_DB_URL,
read-only transaction). Output: evidence/06_live_keyword_dry_run.json and .csv.
"""
import csv
import datetime as dt
import json
import os
import pathlib
import sys
from collections import Counter

import psycopg

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from keyword_finetune import finetune  # noqa: E402

BASE = pathlib.Path(__file__).resolve().parent.parent
DS = json.loads((BASE / "data" / "report_dataset.json").read_text(encoding="utf-8"))
OUT = BASE / "evidence" / "06_live_keyword_dry_run"
SITE = "UK"
PH_USER_ID = 49  # Paulroshan
ACCOUNTS = {6: "amazon Dcvoltage", 8: "amazon Ledsone"}


def norm(s):
    return " ".join((s or "").split())


def assess(cur, k):
    rec = {"asin": k["asin"], "sku": k.get("sku"), "product_id": k.get("product_id"),
           "account": k.get("account"), "sub_source": None, "site": SITE,
           "issue_detected": k.get("issue_detected"), "issue_found": k.get("issue_found"),
           "fine_tuning_action": k.get("action_taken"),
           "live_before": None, "dashboard_original": k.get("original"), "proposed": k.get("cleaned"),
           "validation": "", "decision": None}
    if not k.get("product_id") or not k.get("sku") or not k.get("original"):
        return {**rec, "decision": "SKIPPED - MISSING DATA", "validation": "no listing keywords on record"}
    cur.execute("""SELECT asin, sku, sub_source, site, is_ended FROM listings.amazon_listings
                   WHERE id=%s""", (int(k["product_id"]),))
    row = cur.fetchone()
    if not row:
        return {**rec, "decision": "SKIPPED - MISSING DATA", "validation": "listing row not found"}
    asin, sku, sub, site, ended = row
    rec["sub_source"] = sub
    cur.execute("""SELECT keyword FROM listings.amazon_listing_search_engine_keywords
                   WHERE product_id=%s ORDER BY view_order, id""", (int(k["product_id"]),))
    entries = [r[0] for r in cur.fetchall()]
    live = finetune(entries)
    rec["live_before"], rec["live_entries"] = live["original"], entries
    cur.execute("SELECT count(*) FROM listings.amazon_listings WHERE sku=%s AND sub_source=%s AND site=%s",
                (sku, sub, SITE))
    same_sku = cur.fetchone()[0]
    cur.execute("""SELECT count(*) FROM staff.ph_category_products p JOIN staff.ph_categories c
                   ON c.id=p.ph_category_id WHERE p.source_id=1 AND p.ref_id=%s AND c.user_id=%s""",
                (k["asin"], PH_USER_ID))
    is_ph = cur.fetchone()[0] == 1
    checks = {"ASIN": asin == k["asin"], "SKU": sku == k["sku"], "site UK": site == SITE,
              "account": ACCOUNTS.get(sub) == k.get("account"), "not ended": not ended,
              "Paulroshan ASIN": is_ph}
    if not k.get("needs_change"):
        return {**rec, "decision": "SKIPPED - NO CHANGE", "validation": "no duplicate or repeated terms"}
    if not k.get("cleaned"):
        return {**rec, "decision": "SKIPPED - MISSING DATA", "validation": "proposed keywords missing"}
    if not all(checks.values()):
        return {**rec, "decision": "SKIPPED - VERIFICATION FAILED",
                "validation": "failed: " + ", ".join(c for c, ok in checks.items() if not ok)}
    if same_sku != 1:
        return {**rec, "decision": "SKIPPED - AMBIGUOUS LISTING",
                "validation": f"{same_sku} listing rows share sku/sub_source/site"}
    if live["original"] != norm(k["original"]):
        return {**rec, "decision": "SKIPPED - LIVE KEYWORD MISMATCH",
                "validation": "live backend keywords differ from the dashboard original"}
    if norm(k["cleaned"]) == live["original"]:
        return {**rec, "decision": "SKIPPED - NO CHANGE", "validation": "proposed equals live"}
    if live["cleaned"] != k["cleaned"]:
        return {**rec, "decision": "SKIPPED - VERIFICATION FAILED",
                "validation": "fine-tune of live keywords does not reproduce the proposal"}
    return {**rec, "decision": "READY_FOR_UPDATE",
            "validation": "ASIN, SKU, account, site, PH, unique listing, live = dashboard original: all PASS"}


def main():
    assert DS["meta"]["ph_scope"]["user_id"] == PH_USER_ID
    with psycopg.connect(os.environ["WLP_SOURCE_DB_URL"]) as con:
        con.read_only = True
        cur = con.cursor()
        results = [assess(cur, k) for k in DS["keyword_analysis"]]
    OUT.with_suffix(".json").write_text(json.dumps(
        {"run_at": dt.datetime.now().isoformat(timespec="seconds"), "mode": "dry-run (read-only)",
         "live_read_source": "ledsone listings.amazon_listings + amazon_listing_search_engine_keywords",
         "records": results}, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    cols = ["asin", "sku", "account", "sub_source", "site", "live_before", "dashboard_original", "proposed",
            "validation", "decision"]
    with OUT.with_suffix(".csv").open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(results)
    print(Counter(r["decision"] for r in results))
    for r in results:
        print(f'{r["decision"]:32} {r["asin"]} {r["sku"]} sub={r["sub_source"]} | {r["validation"]}')


if __name__ == "__main__":
    main()
