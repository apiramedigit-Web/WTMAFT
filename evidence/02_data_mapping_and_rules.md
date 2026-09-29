# 02 — Data Sources, Mapping & Business Rules

All reads are SELECT-only in a READ ONLY transaction. No production table was written.

## A. Selected sources (ledsone DB, DSN `WLP_SOURCE_DB_URL`, user tech_user)

| Source | Relevant columns | Grain | Date field | Filters | Limitations |
|---|---|---|---|---|---|
| `business_reports.amz_sales_and_traffic_by_asin` | `child_asin`, `total_order_items`, `units_ordered`, `sessions`, `sub_source`, `market_place` | ASIN × account × day (verified unique) | `date` | `market_place=23` (UK), `sub_source IN (6,8)` | Latest date 2026-09-23. Dcvoltage has no UK activity on 03–06 Sep (see D). |
| `business_reports.amz_catalog_performance_data` | `asin`, `impression_count`, `click_count`, `purchase_count` | ASIN × week, Sun→Sat (verified 1 row per ASIN-week in UK) | `start_date`/`end_date` (6-day span) | `market_place=23`, `sub_source IN (6,8)` | Search-results traffic only. Latest complete week 13–19 Sep. No rows for SRM (9). |
| `listings.amazon_listings` | `id`, `asin`, `sku`, `sub_source`, `status`, `is_parent`, `wrong_sku`, `is_ended` | listing row (≈ SKU) | — | `site='UK'`, not ended; prefer child rows and `wrong_sku=0` | Some UK ASINs exist only as `is_parent=1` rows (standalone), so those rows are used as a fallback |
| `listings.amazon_listing_search_engine_keywords` | `product_id`, `keyword`, `view_order` | keyword entry per listing row | — | `product_id = amazon_listings.id` | Current snapshot only: no history, no upload log |
| `listings.amazon_listing_issues` | `asin`, `sku`, `severity`, `code`, `message` | issue | — | `marketplace_id='A1F83G8C2ARO7P'` (UK) | Amazon-reported issues only; not a manual content audit |
| `staff.ph_category_products` → `staff.ph_categories` → `staff.users` | `ref_id` (= ASIN), `source_id`, `ph_category_id`; `category_name`, `user_id`; `first_name`, `username` | ASIN × PH category (verified: no Amazon ASIN is in 2+ categories) | `assign_date` | `source_id = 1` (Amazon) | Attribution only: it does not change which ASINs are selected. See section F. |
| Lookups: `order_management.market_place` (23 = UK), `order_management.sub_source` (6 = amazon Dcvoltage, 8 = amazon Ledsone) | | | | | `listings.amazon_marketplaces.id` is a different numbering (23 = Saudi Arabia) and must not be used for this join |

Rejected / unavailable:
- `public.traffic_data` (organic, mostly eBay).
- `public.ppc_performance` (paid only).
- `amz_search_query_performance` (query-level).
- The `listing_generator.*` and `staging_ai.*` tables: permission denied.

`listing_generator.tbl_low_asins_keywords` (≈368k rows, order_management_copy):
- Structure read from pg_catalog: `sku, asin, keyword, search_volume, organic_rank, sponsored_rank, competing_products, cerebro_iq_score, keyword_sales, rank_trend, relevancy_score, competitor_asin, source_file, import_batch_id, keyword_source, marketplace, run_id, group_id, input_asins, asin_count, has_verified_asin_attribution, created_at, updated_at`.
- It is Helium 10 Cerebro keyword research. It is not the listing's backend search-term field and has no upload or change-date column, so it cannot supply Original Backend Keywords, Date Changed or upload status.
- Its rows are unreadable anyway (`permission denied for schema listing_generator`).

## B. Date logic
- Report run 2026-09-25. Weeks follow the Search Catalog Performance buckets (Sunday → Saturday), because impressions and clicks exist only at that grain.
- **Current 7D = 2026-09-13 → 2026-09-19.** This is the latest week loaded for both accounts.
- **Previous 7D = 2026-09-06 → 2026-09-12.**
- **Top-moving basis = 2026-08-23 → 2026-09-12**, the 3 complete weeks before Current 7D.
- Orders are summed over exactly the same dates, so all metrics share one window.
- Validated: both windows are 7 days, contiguous and start on a Sunday; all 14 days are present for both accounts.

## C. Source-to-report mapping

| Report field | Source | Column | Transformation | Validation |
|---|---|---|---|---|
| ASIN | amz_sales_and_traffic_by_asin | `child_asin` | report grain = ASIN (orders summed across accounts 6+8) | unique (50/50) |
| SKU | amazon_listings | `sku` | UK, not ended, child rows first, `wrong_sku=0` first; several SKUs listed one per line | each SKU is a UK listing row of that ASIN |
| Previous / Current 7D Orders | amz_sales_and_traffic_by_asin | `total_order_items` | SUM over window, both accounts | recomputed from raw + live DB spot-check |
| Impressions (prev/curr) | amz_catalog_performance_data | `impression_count` | week row; missing row → null ("—") | spot-check |
| Clicks (prev/curr) | amz_catalog_performance_data | `click_count` | as above | spot-check |
| Order / Impression / Click Change % | derived | — | (Cur − Prev) ÷ Prev × 100; Prev 0/null → null | recomputed |
| CTR | derived | — | Clicks ÷ Impressions × 100 | recomputed |
| CVR | derived | — | Orders ÷ Clicks × 100 | recomputed |
| CTR / CVR Change % | derived | — | relative change of the rate, (Cur − Prev) ÷ Prev × 100 | recomputed |
| Issue Detected | derived | — | rule D2 | 1:1 with drop flag |
| Content Status | amazon_listing_issues | `severity` | "No listing issues reported" or "N listing issue(s) reported: <severity>" | count per ASIN |
| Backend Keyword Status | keyword_finetune.py | — | "Requires Review" if duplicates or repeats; "No Duplicates Found"; "No backend keywords on record" | |
| Issue Found / Action Taken / Issue / Fine-Tuning Action | keyword_finetune.py | — | Duplicate words → Removed duplicates; Repeated keywords → Removed repeated terms; none → No change required | |
| Original Backend Keywords | amazon_listing_search_engine_keywords | `keyword` | entries joined in `view_order`, whitespace-normalised | every original unique word present in cleaned output |
| Cleaned backend keywords | keyword_finetune.py | — | first occurrence of each word kept, in place | word accounting: kept + removed = original |
| Final Status / Status | — | — | "Proposed – upload not verified" (no upload evidence exists) | no fabricated status |
| Date Changed | — | — | null ("—"): no change log is readable | |
| Pre-Change Orders / Impressions | = Current 7D values | | baseline before any upload | equality check |
| Post-Change Orders / Impressions, change %, Result | — | — | null; Result "Pending Upload" | |
| Visible Listing Content Changed | — | — | 0: this process never changes visible content | |

## D. Business rules
1. **Top-Moving ASIN — derived, pending approval.**
   - Rule: the top 50 UK ASINs by total orders over the 3 weeks before Current 7D. Tie-breaks: Previous 7D orders, then Previous 7D impressions, then ASIN.
   - Why derived: no documented Amazon top-moving rule exists, and "50" comes from the PDF example.
   - Why the 3-week basis: ranking on Previous 7D alone selects ASINs that had an unusually good week. That version flagged 39/50 as drops (regression to the mean); the 3-week basis gives 23/50.
   - Cut-off in this run: 9 orders over 3 weeks, out of 2,228 UK ASINs with orders.
2. **Performance drop:** Current 7D Orders < Previous 7D Orders. No numeric threshold was invented.
   - Sensitivity: < 0% → 23; ≤ −5% → 23; ≤ −10% (WLPAF) → 23; ≤ −30% → 17.
3. **Issue Detected — derived.** Orders = Impressions × CTR × CVR by the PDF formulas.
   - "Overall Performance Drop" when the Impression, CTR and CVR changes are all negative.
   - Otherwise it is the most negative of Impression Drop / CTR Drop / Conversion Drop.
   - "Order Drop (traffic stable)" when none of the three declined.
   - "Order Drop (no search data)" when a week has no catalog row.
4. **Keyword fine-tuning** (`scripts/keyword_finetune.py`):
   - Removes a word already used earlier in the same field. Comparison is case-insensitive and ignores surrounding punctuation.
   - Removes whole repeated entries and whitespace noise.
   - Everything else is kept in its original order. Nothing is added.
   - No stop-word, relevance or 249-byte trimming: no documented project rule exists.
   - Observation only: 19 of 26 original fields exceed Amazon's published 249-byte backend limit; 7 still do after cleaning.

## E. Data-quality findings
- **amazon Dcvoltage had 0 UK orders and 0 sessions on 2026-09-03 → 09-06.** Confirmed independently in `amz_sales_and_traffic_by_asin`, `amz_sales_and_traffic_by_date` and `order_management.orders`. This lowers Dcvoltage's Previous 7D baseline (06 Sep is inside it).
- **740 UK ASINs sell under both accounts,** but the catalog holds each ASIN once. Hence the ASIN grain, where orders are summed and traffic is taken once.

## F. Portfolio Holder (PH) attribution (added 2026-09-28)

**Authoritative source:** the ledsone `staff` schema. Before this change, nothing in the project (scripts, template, evidence) referenced a PH. The requirement PDF has no PH field either. Its sample SKUs `WCXXXX` were treated as format only, not as a scope rule.

**Paulroshan:**
- `staff.users.id = 49`: first_name `Paulroshan`, username `paulr`, Active, Jaffna.
- Do not confuse with id 123 `Roshan`, a different user.
- id 49 owns exactly one PH category: `staff.ph_categories.id = 26` **"Wire Cage"**, assigned 2026-07-01.
- Category 26 holds 1,056 products: 634 Amazon (`source_id=1`, 634 distinct and all ASIN-shaped), 322 rows with `source_id=2` (eBay item ids) and 100 rows with `source_id=16` (EANs).
- **Paulroshan's Amazon ASIN universe is 634 ASINs**, and none of them is mapped to any other PH category.
- Across the whole Amazon mapping, 0 ASINs belong to more than one PH category, so attribution is unambiguous.

**Comparison with the report (current week 13–19 Sep; basis 23 Aug–12 Sep):**

| Measure | Count |
|---|---|
| Paulroshan Amazon ASINs (category 26) | 634 |
| … with UK Business Report rows in the extract window | 569 |
| … with orders > 0 in the extract window | 197 |
| … top-moving candidates (baseline orders > 0), out of 2,228 in total | 162 |
| … inside the top 50 (ranks 2, 22, 27, 32, 39) | **5** |
| … ranked below the cut-off (the next is #61, B0D83PFMTP, with 8 baseline orders vs 9 at #50) | 157 |
| Paulroshan ASINs that qualify under the existing rule but are missing | **0** |

**PH mix of the 50 report rows:**

| PH · category | Rows |
|---|---|
| Thuwaraga · Bulbs | 11 |
| Paulroshan · Wire Cage | 5 |
| Utharsika · Lampshade | 5 |
| Illakkiya · Table Lamp | 4 |
| Theepana · Pipe lighting | 4 |
| Utharsika · Wall plug | 4 |
| Jasmini · Cable | 3 |
| 14 other categories | 1–2 each |
| Unmapped (B0H9XM38LC) | 1 |

**Change made:**
- `extract.py` pulls `ph_map` for every extracted ASIN.
- `build_dataset.py` adds `ph_name`, `ph_user_id`, `ph_category` and `ph_category_id` to each performance row. The build fails if an ASIN has more than one PH.
- The report shows `PH: <name> · <category>` under each ASIN in Section 3, and the Section 3 search matches PH name and category.
- An ASIN with no mapping shows "not mapped"; no PH is guessed.
- The selection rules (top-moving, drop, keywords, change record, monitoring) are unchanged.

**Decision (2026-09-28, business instruction): the report covers Paulroshan's ASINs only.**
- The top 50 is now selected only from ASINs mapped to PH user 49 (`PH_USER_ID` in `build_dataset.py`). Other PHs are excluded.
- The ranking basis, tie-breaks, drop rule, keyword logic, change record and monitoring are unchanged.
- Before: 50 rows across 17 PH categories plus 1 unmapped row. Only 5 of them were Paulroshan's.
- After: 50 rows, all Paulroshan · Wire Cage, drawn from 162 eligible ASINs. #1 is B0DH4KYFPD with 38 baseline orders; #50 has 2.
- Downstream sections after the change: 23 drops; 24 keyword rows, of which 22 need review; 22 change-record rows; 21 ASINs in monitoring. None of them references a non-Paulroshan ASIN.

*Superseded note:* the task asks for Paulroshan's ASINs to be "correctly included". Under the existing top-50 rule, all of his qualifying ASINs are already included. Showing more of them needs a new selection rule, for example a top-N per PH or a report limited to Paulroshan only. That decision belongs to the business and has not been made.
