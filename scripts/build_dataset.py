"""Build the validated report dataset from data/extract.json -> data/report_dataset.json.

Business rules (see evidence/02_data_mapping_and_rules.md):
  * TOP_MOVING_N / ranking  - DERIVED (no documented project rule found); pending approval.
                              Rank by total orders over the complete weeks before Current 7D.
  * Performance drop        - Order Change % < 0 (no threshold invented).
  * Issue Detected          - most negative of Impression / CTR / CVR change;
                              "Overall Performance Drop" when all three declined.
  * Keyword review          - drop ASIN whose backend keywords contain duplicate/repeated terms.
  * Upload / change date    - "Live-verified" + POST date only when the Listing Management Tool shows
                              the proposed keywords (evidence/07 + 09); otherwise "Proposed", date "—".
"""
import collections
import datetime as dt
import json
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from keyword_finetune import finetune  # noqa: E402
from keyword_live_verify import latest_states  # noqa: E402  (read-only helper, no I/O at import)

BASE = pathlib.Path(__file__).resolve().parent.parent
SRC = BASE / "data" / "extract.json"
OUT = BASE / "data" / "report_dataset.json"

TOP_MOVING_N = 50  # DERIVED: count shown in the requirement's KPI example; flagged for approval
# Report scope: Paulroshan's PH ASINs only (business instruction 2026-09-28).
# staff.users.id 49 (paulr) -> staff.ph_categories 26 "Wire Cage".
PH_USER_ID = 49
CHANGE_RECORDS = pathlib.Path(os.environ.get("WTMA_CHANGE_RECORDS", BASE / "data" / "change_records.json"))
STATUSES = ("Proposed", "Live-verified", "Monitoring", "Completed")
LEDGER = BASE / "data" / "monitoring_cycles.json"  # written by performance_alert.py


def load_saved_records():
    """Change records exported from the report page (Section 7 -> Export saved records)."""
    if not CHANGE_RECORDS.exists():
        return []
    data = json.loads(CHANGE_RECORDS.read_text(encoding="utf-8"))
    return data.get("records", data) if isinstance(data, dict) else data


def validate_saved(records):
    out = []
    for r in records:
        if r.get("status") not in STATUSES or not r.get("key") or not r.get("asin"):
            raise ValueError(f"invalid change record: {r}")
        if r["status"] != "Proposed":
            dt.date.fromisoformat(r.get("date_changed") or "")  # Monitoring/Completed need a real date
        out.append(r)
    return out


def pct(cur, prev):
    if cur is None or prev is None or prev == 0:
        return None
    return round((cur - prev) / prev * 100, 1)


def ratio(num, den):
    if num is None or den is None or den == 0:
        return None
    return num / den * 100


def classify(imp_chg, ctr_chg, cvr_chg):
    comps = {"Impression Drop": imp_chg, "CTR Drop": ctr_chg, "Conversion Drop": cvr_chg}
    known = {k: v for k, v in comps.items() if v is not None}
    if len(known) == 3 and all(v < 0 for v in known.values()):
        return "Overall Performance Drop"
    neg = {k: v for k, v in known.items() if v < 0}
    if neg:
        return min(neg, key=neg.get)
    return ("Order Drop (traffic stable)" if known
            else "Order Drop (no search data)")


def main():
    d = json.loads(SRC.read_text(encoding="utf-8"))
    cw, pw = d["current_week"][0], d["previous_week"][0]
    acct = {int(k): v for k, v in d["accounts"].items()}

    # Report grain = ASIN. An ASIN can sell under both accounts (orders are per seller
    # account and do not overlap, so they are summed); the Search Catalog report holds each
    # ASIN once per week (verified unique), so impressions/clicks are not double counted.
    orders = collections.defaultdict(lambda: collections.defaultdict(int))
    acct_orders = collections.defaultdict(lambda: collections.defaultdict(int))
    for r in d["weekly_orders"]:
        if r["asin"]:
            orders[r["asin"]][r["week_start"]] += int(r["orders"] or 0)
            acct_orders[r["asin"]][(r["sub_source"], r["week_start"])] += int(r["orders"] or 0)
    cat = {(r["asin"], r["week_start"]): r for r in d["catalog"]}
    assert len(cat) == len(d["catalog"]), "catalog not unique per ASIN-week"

    # Portfolio Holder from staff.ph_category_products; an ASIN with 2+ PHs would be a
    # conflicting mapping, so fail rather than pick one.
    ph = {}
    for m in d["ph_map"]:
        assert m["asin"] not in ph, f"ASIN {m['asin']} mapped to more than one PH category"
        ph[m["asin"]] = m
    scope = [m for m in ph.values() if m["ph_user_id"] == PH_USER_ID]
    assert scope, f"no ASINs mapped to PH user {PH_USER_ID}"

    # --- top-moving selection (DERIVED rule) ---------------------------------------------
    # Ranked on the weeks BEFORE the current week (not the previous week alone), so a
    # single unusually good week does not manufacture a "drop" (regression to the mean).
    base_weeks = sorted(w for w in {w for v in orders.values() for w in v} if w < cw)

    def base_orders(k):
        return sum(orders[k].get(w, 0) for w in base_weeks)

    def prev_imp(k):
        c = cat.get((k, pw))
        return int(c["impressions"]) if c else 0
    in_scope = {m["asin"] for m in scope}
    ranked = sorted((k for k in orders if k in in_scope and base_orders(k) > 0),
                    key=lambda k: (-base_orders(k), -orders[k].get(pw, 0), -prev_imp(k), k))
    top = ranked[:TOP_MOVING_N]

    listings = collections.defaultdict(list)
    for l in d["listings"]:
        listings[l["asin"]].append(l)
    kw = collections.defaultdict(list)
    for k in d["keywords"]:
        kw[k["product_id"]].append(k["keyword"])
    issues = collections.defaultdict(list)
    for i in d["listing_issues"]:
        issues[i["asin"]].append(i)

    def metrics(asin):
        """Current 7D vs Previous 7D metrics for one ASIN (same rules for top-50 and monitored ASINs)."""
        po, co = orders[asin].get(pw, 0), orders[asin].get(cw, 0)
        pc, cc = cat.get((asin, pw)), cat.get((asin, cw))
        sold_in = sorted({s for (s, _w), v in acct_orders[asin].items() if v > 0})
        pi = int(pc["impressions"]) if pc else None
        ci = int(cc["impressions"]) if cc else None
        pk = int(pc["clicks"]) if pc else None
        ck = int(cc["clicks"]) if cc else None
        p_ctr, c_ctr = ratio(pk, pi), ratio(ck, ci)
        p_cvr, c_cvr = ratio(po, pk), ratio(co, ck)
        m = ph.get(asin)
        return {
            "asin": asin,
            "ph_name": m["ph_name"] if m else None, "ph_user_id": m["ph_user_id"] if m else None,
            "ph_category": m["ph_category"] if m else None,
            "ph_category_id": m["ph_category_id"] if m else None,
            "account": ", ".join(acct[s] for s in sold_in),
            "catalog_account": acct[(pc or cc)["sub_source"]] if (pc or cc) else None,
            "orders_by_account": {acct[s]: {"previous": acct_orders[asin].get((s, pw), 0),
                                            "current": acct_orders[asin].get((s, cw), 0)}
                                  for s in sold_in},
            "baseline_orders": base_orders(asin),
            "prev_orders": po, "curr_orders": co, "order_chg": pct(co, po),
            "prev_impressions": pi, "curr_impressions": ci, "impression_chg": pct(ci, pi),
            "prev_clicks": pk, "curr_clicks": ck, "click_chg": pct(ck, pk),
            "prev_ctr": None if p_ctr is None else round(p_ctr, 2),
            "curr_ctr": None if c_ctr is None else round(c_ctr, 2),
            "ctr_chg": pct(c_ctr, p_ctr),
            "prev_cvr": None if p_cvr is None else round(p_cvr, 2),
            "curr_cvr": None if c_cvr is None else round(c_cvr, 2),
            "cvr_chg": pct(c_cvr, p_cvr),
            "is_drop": co < po,
        }

    perf_rows, kw_rows = [], []
    for rank, key in enumerate(top, 1):
        asin = key
        row = {"rank": rank, **metrics(asin)}
        po, co = row["prev_orders"], row["curr_orders"]
        row["issue_detected"] = (classify(row["impression_chg"], row["ctr_chg"], row["cvr_chg"])
                                 if row["is_drop"] else "No Drop")

        # Prefer child rows; if the ASIN only has is_parent=1 rows in UK (standalone listing
        # selling as its own child_asin), fall back to those rather than leave SKU blank.
        not_ended = [l for l in listings.get(key, []) if not l["is_ended"]]
        live = [l for l in not_ended if l["is_parent"] != 1] or not_ended
        good = [l for l in live if (l["wrong_sku"] or 0) == 0] or live
        row["skus"] = sorted({l["sku"] for l in good if l["sku"]})
        row["sku"] = ", ".join(row["skus"]) if row["skus"] else None
        iss = issues.get(key, [])
        row["listing_issue_count"] = len(iss)
        row["content_status"] = ("No listing issues reported" if not iss else
                                 f"{len(iss)} listing issue(s) reported: " +
                                 ", ".join(sorted({(i['severity'] or '?') for i in iss})))
        perf_rows.append(row)

        if not row["is_drop"]:
            continue
        with_kw = [l for l in good if kw.get(l["product_id"])]
        if not with_kw:
            kw_rows.append({"asin": asin, "sku": row["sku"], "account": row["account"],
                            "content_status": row["content_status"],
                            "backend_keyword_status": "No backend keywords on record",
                            "issue_found": "Backend keywords missing in listing data",
                            "action_taken": "None (nothing to fine-tune)", "needs_change": False,
                            "original": None, "cleaned": None, "removed_words": [],
                            "repeated_entries": [], "duplicate_words_removed": 0,
                            "issue_detected": row["issue_detected"]})
            continue
        for l in sorted(with_kw, key=lambda x: x["sku"] or ""):
            f = finetune(kw[l["product_id"]])
            kw_rows.append({
                "asin": asin, "sku": l["sku"], "account": acct[l["sub_source"]],
                "product_id": l["product_id"],
                "content_status": row["content_status"],
                "backend_keyword_status": "Requires Review" if f["needs_change"] else "No Duplicates Found",
                "issue_found": f["issue"], "action_taken": f["action"],
                "issue_detected": row["issue_detected"], **f})

    review = [k for k in kw_rows if k["needs_change"]]
    # Section 7 rows. Status/Date Changed are NOT decided here: they come only from records the
    # user saved in the report (browser storage) or exported to data/change_records.json.
    change_record = [{
        "key": f'{k["asin"]}|{k["sku"]}', "asin": k["asin"], "sku": k["sku"],
        "issue_detected": k["issue_detected"], "optimization_action": k["action_taken"],
        "change_type": "Backend Keyword Fine-Tuning", "date_changed": None, "status": "Proposed",
        "carried_forward": False} for k in review]
    saved = validate_saved(load_saved_records())
    keys = {c["key"] for c in change_record}
    for s in saved:  # user-confirmed changes from earlier weeks stay tracked even if no longer proposed
        if s["key"] not in keys:
            change_record.append({"key": s["key"], "asin": s["asin"], "sku": s["sku"],
                                  "issue_detected": s.get("issue_detected"),
                                  "optimization_action": s.get("optimization_action"),
                                  "change_type": s.get("change_type") or "Backend Keyword Fine-Tuning",
                                  "date_changed": None, "status": "Proposed", "carried_forward": True})

    # Amazon API submissions (evidence/06) and the read-only Listing Management checks: evidence/07
    # (all submissions) overlaid by evidence/09 (latest re-check of the pending ones).
    # Only a Listing Management read of the proposed keywords (UPDATED / VERIFIED) sets a row to
    # "Live-verified", dated with its POST date; monitoring still starts only from a user save.
    submissions, sync_info = {}, None
    upd = BASE / "evidence" / "06_live_keyword_update.json"
    ver = BASE / "evidence" / "07_live_keyword_verification.json"
    rem = BASE / "evidence" / "09_remaining_20_live_verification.json"
    if upd.exists():
        # latest state per submission across 07 (weekly pending re-check) and 09 (one-off re-check):
        # a verification is final (its first verification time is the live-verification date);
        # otherwise the most recent read wins, so a stale file can never override a newer result.
        checked = latest_states([rem, ver])   # legacy one-off 09 first, weekly 07 last (07 wins unless 09 verified)
        # the weekly keyword check (optimization_cleanup.py, fresh GET each week) is the newest source: a
        # listing it confirmed live is verified from its FIRST verification; otherwise the state above stands
        if LEDGER.exists():
            for kr in json.loads(LEDGER.read_text(encoding="utf-8")).get("keyword_rows", {}).values():
                if kr.get("submission_id") and kr.get("first_verified_at"):
                    checked[f'{kr["asin"]}|{kr["sku"]}'] = {"state": "UPDATED / VERIFIED", "checked_at_utc": kr["first_verified_at"],
                                                          "verified_at_utc": kr["first_verified_at"],
                                                          "evidence": "monitoring_cycles.json (weekly keyword check)"}
        for c_ in checked.values():
            if c_["state"] == "UPDATED / VERIFIED":
                c_["checked_at_utc"] = c_["verified_at_utc"]
        if checked:
            sync_info = {"last_checked": max(c["checked_at_utc"] for c in checked.values()),
                         "source": "Listing Management Tool (amz_platinum_keywords, GET only)",
                         "evidence": sorted({c["evidence"] for c in checked.values()})}
        for r in json.loads(upd.read_text(encoding="utf-8"))["records"]:
            if not r.get("submission_id"):
                continue
            k = f'{r["asin"]}|{r["sku"]}'
            ts = r.get("post_timestamp") or ""
            chk = checked.get(k, {})
            submissions[k] = {"submission_id": r["submission_id"], "api_status": r.get("api_status"),
                              "post_timestamp": ts if ts[:1].isdigit() and "T" in ts else None,
                              "post_date": ts[:10],
                              "state": chk.get("state", r.get("final_status")),
                              "checked_at_utc": chk.get("checked_at_utc"), "evidence": chk.get("evidence")}
    keys = {c["key"] for c in change_record}
    # A submitted update stays tracked after its keywords are clean or the ASIN leaves the top 50
    # (monitoring continuity); it is carried forward exactly like a saved record.
    upd_rec = {f'{r["asin"]}|{r["sku"]}': r for r in json.loads(upd.read_text(encoding="utf-8"))["records"]}         if upd.exists() else {}
    for k in sorted(set(submissions) - keys):
        r = upd_rec[k]
        change_record.append({"key": k, "asin": r["asin"], "sku": r["sku"],
                              "issue_detected": r.get("issue_detected"),
                              "optimization_action": r.get("fine_tuning_action"),
                              "change_type": "Backend Keyword Fine-Tuning", "date_changed": None,
                              "status": "Proposed", "carried_forward": True})
    keys = {c["key"] for c in change_record}
    assert set(submissions) <= keys, f"submission for a row not in this build: {set(submissions) - keys}"
    for c in change_record:
        s = c["submission"] = submissions.get(c["key"])
        if s and s["state"] == "UPDATED / VERIFIED":
            dt.date.fromisoformat(s["post_date"])
            c["status"], c["date_changed"] = "Live-verified", s["post_date"]

    # Daily ASIN orders (both accounts summed, same source as the weekly figures) for every
    # tracked ASIN; a date is "available" only when both accounts have Business Report rows.
    tracked = sorted({c["asin"] for c in change_record})
    daily = {a: {} for a in tracked}
    for r in d["daily_orders"]:
        if r["asin"] in daily:
            daily[r["asin"]][r["date"][:10]] = int(r["orders"])
    available = [c["date"][:10] for c in d["day_coverage"] if c["accounts"] == len(acct)]
    monitoring_data = {"daily_from": d["daily_from"], "available_dates": available,
                       "available_through": max(available) if available else None, "orders": daily}

    # Monitored ASINs = change-record ASINs + ASINs already in the monitoring-cycle ledger, so an
    # ASIN keeps being measured after it leaves the top 50. Same metric code as Section 3.
    monitored = set(tracked)
    if LEDGER.exists():
        led_ = json.loads(LEDGER.read_text(encoding="utf-8"))
        monitored |= {c["asin"] for c in led_["cycles"].values()}
        monitored |= {r["asin"] for r in led_.get("keyword_rows", {}).values() if r.get("first_verified_at")}
    top_set = set(top)
    fine_tuned = {}
    for r in (json.loads(upd.read_text(encoding="utf-8"))["records"] if upd.exists() else []):
        if r.get("submission_id"):
            fine_tuned.setdefault(r["asin"], 0)
            fine_tuned[r["asin"]] += len(finetune([r.get("dashboard_original") or ""])["removed_words"])
    monitoring_performance = []
    for asin in sorted(monitored):
        row = metrics(asin)
        cur_kw = [finetune(kw[l["product_id"]]) for l in listings.get(asin, [])
                  if not l["is_ended"] and kw.get(l["product_id"])]
        row.update(in_top_moving=asin in top_set,
                   keyword_status=("No backend keywords on record" if not cur_kw else
                                   "Requires Review" if any(f["needs_change"] for f in cur_kw) else
                                   "No Duplicates Found"),
                   duplicate_words_now=sum(f["duplicate_words_removed"] for f in cur_kw),
                   duplicate_words_removed_at_fine_tuning=fine_tuned.get(asin))
        monitoring_performance.append(row)

    drops = [r for r in perf_rows if r["is_drop"]]
    kpi = {
        "top_moving_reviewed": len(perf_rows),
        "performance_drop": len(drops),
        "keyword_review_required": len({k["asin"] for k in review}),
        "finetune_proposed": len(review),
        "finetune_completed_verified": sum(1 for c in change_record if c["status"] != "Proposed"),
        "visible_content_changed": 0,
        "duplicate_words_removed": sum(k["duplicate_words_removed"] for k in review),
        "repeated_entries_removed": sum(len(k["repeated_entries"]) for k in review),
        "under_monitoring": len({c["asin"] for c in change_record if c["status"] == "Monitoring"}),
        "posts_accepted": sum(1 for s in submissions.values() if s["api_status"] == "ACCEPTED"),
        "live_verified": sum(1 for s in submissions.values() if s["state"] == "UPDATED / VERIFIED"),
        "live_verification_pending": sum(1 for s in submissions.values() if s["state"] == "POST_ACCEPTED_PENDING_SYNC"),
        "monitoring_period": "7 days after live verification",
        "next_review": "after live verification + 7-day monitoring period",
    }
    ds = {
        "meta": {
            "title": "Weekly Top-Moving ASIN Performance Drop & Backend Keyword Fine-Tuning Report",
            "marketplace": "Amazon UK", "accounts": list(acct.values()),
            "current_7d": d["current_week"], "previous_7d": d["previous_week"],
            "extracted_at": d["extracted_at"], "top_moving_n": TOP_MOVING_N,
            "top_moving_candidates": len(ranked),
            "ph_scope": {"user_id": PH_USER_ID, "name": scope[0]["ph_name"],
                         "categories": sorted({m["ph_category"] for m in scope}),
                         "asins_in_extract": len(in_scope)},
            "baseline_weeks": base_weeks,
            # How many top-moving ASINs each candidate drop cut-off would flag (for rule sign-off).
            "drop_sensitivity": {
                "order_change_lt_0": sum(1 for r in perf_rows if r["curr_orders"] < r["prev_orders"]),
                "order_change_le_-5 (Before/After convention)": sum(1 for r in perf_rows if r["order_chg"] is not None and r["order_chg"] <= -5),
                "order_change_le_-10 (WLPAF eBay convention)": sum(1 for r in perf_rows if r["order_chg"] is not None and r["order_chg"] <= -10),
                "order_change_le_-30 (BL-03 impression trigger)": sum(1 for r in perf_rows if r["order_chg"] is not None and r["order_chg"] <= -30),
            },
            "daily_totals": d["daily_totals"],
            "sync_info": sync_info,
        },
        "kpi": kpi, "performance": perf_rows, "keyword_analysis": kw_rows,
        "change_record": change_record, "saved_change_records": saved,
        "monitoring_data": monitoring_data,
        "monitoring_performance": monitoring_performance,
    }
    OUT.write_text(json.dumps(ds, indent=1, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(kpi, indent=1))
    print("issue mix:", collections.Counter(r["issue_detected"] for r in perf_rows))
    print("kw rows:", len(kw_rows), "review:", len(review))


if __name__ == "__main__":
    main()
