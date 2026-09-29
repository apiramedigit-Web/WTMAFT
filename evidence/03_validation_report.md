# 03 — Validation Report

Script: `scripts/validate.py` → `evidence/validation_results.json` (machine-readable).
Last run: 2026-09-28, after the PH attribution change. **Result: 32 PASS / 0 FAIL.** The 2026-09-25 run was 31/0. The new check is "PH attribution matches live staff.ph_category_products". Details are in the PH section at the end of this file.

The validator does not share code with the builder:
- It re-aggregates every metric from the raw `data/extract.json`.
- It re-queries 8 random ASINs live from ledsone (read-only).
- It drives the HTML in headless Chromium (Playwright).

| # | Check | Result | Evidence |
|---|---|---|---|
| 13 | Date ranges: 7 days each, contiguous, Sunday start | PASS | 13–19 Sep vs 06–12 Sep 2026 |
| 13b | Daily order data present for all 14 days, both accounts | PASS | 14/14 days for accounts 6 and 8 |
| — | Top-moving selection reproduces independently | PASS | 50 ASINs, same order |
| 17 | Per-ASIN recomputation: orders, impressions, clicks, CTR, CVR, all % changes | PASS | 50 × 11 values match |
| 12 | Division by zero → null (never 0 / NaN / Inf) | PASS | 2 rows with Previous 7D Orders = 0 show "—" |
| 6/7 | No NaN / Infinity in dataset | PASS | |
| 14 | ASIN unique at report grain | PASS | 50/50 |
| 15 | SKU mapping: every SKU is a UK listing row of that ASIN | PASS | 50/50 |
| 16 | Keyword rows map to the correct ASIN/SKU via product_id | PASS | 26 rows |
| — | Keyword principle: every unique original word kept, nothing added; kept + removed = original | PASS | 25 fields |
| 12 | KPI summary reconciles to dataset rows | PASS | 50 / 23 / 21 / 24 / 662 / 0 |
| — | Monitoring rows = ASINs with proposed changes | PASS | 21 |
| 18/19 | No fabricated upload, change date or monitoring result | PASS | 24 change records, all Proposed, Date Changed null |
| 17b | Monitoring Pre-Change values = Current 7D baseline; Post-Change null | PASS | |
| 20 | No fabricated threshold: Issue ≠ "No Drop" ⇔ orders fell | PASS | |
| — | PH attribution matches live `staff.ph_category_products` (one PH per ASIN, none guessed) | PASS | 49 mapped / 1 unmapped (B0H9XM38LC) |
| — | Live DB spot-check (8 random ASINs: orders, impressions, clicks) | PASS | |
| 1 | HTML file exists | PASS | 112,946 bytes |
| 9/10/11 | No external CSS/JS/network references (src/href/@import/fetch) | PASS | |
| 8 | Embedded JSON parses and equals the dataset exactly | PASS | |
| 2 | Page loads with no JavaScript console errors | PASS | |
| 11b | No network requests at runtime | PASS | |
| 3 | All 11 required sections present | PASS | |
| 4/5 | Required columns, 9 KPI names and 8 workflow steps present in exact PDF terminology | PASS | |
| 12b | KPI cards (computed in the page from rows) = dataset KPI | PASS | |
| — | Displayed row counts = dataset rows | PASS | s3 23 (drop filter) / s4 26 / s5 26 / s6 9 / s7 24 / s8 21 |
| 6b | No NaN / Infinity / undefined / null text rendered | PASS | |
| — | Struck-through words per row = removed-word count | PASS | |
| — | Filter, search and sort controls work | PASS | |
| — | Tabs: clicking each of the 12 tabs shows only that section, without scrolling the page | PASS | |
| — | No horizontal scrollbar (page or table) on any tab at 1920 / 1366 / 1024 / 800 / 390px | PASS | table view at ≥1200px, labelled cards below (2 per row, 1 on phones) |

Screenshots (visual check done):
- `screenshot_summary.png`: KPI cards.
- `screenshot_desktop.png`: Section 3, with all 10 required columns visible at 1366px.
- `screenshot_keywords.png`: Section 5, with removed duplicates struck through.
- `screenshot_dark.png`: dark theme.

Issues found and fixed during validation:
1. **ASIN × account grain split one ASIN's orders from its traffic.** 740 UK ASINs sell under both accounts, so the report was rebuilt at ASIN grain.
2. **The Previous-7D-only ranking produced regression-to-the-mean drops** (39 of 50). It was replaced by the 3-week basis.
3. **Two ASINs had no SKU**, because only `is_parent=1` rows exist for them. A fallback to those rows was added.
4. **Mobile overflow from long table names** was fixed.
5. **Required columns were cut off at 1366px** by the wide SKU column. SKU is now capped with wrapping.

## PH attribution change — validation (2026-09-28)

| Check | Result |
|---|---|
| Paulroshan identifier verified | `staff.users.id 49` (`paulr`) → `staff.ph_categories.id 26` "Wire Cage" |
| Paulroshan Amazon ASINs | 634 (5 in the report; 0 qualifying ASINs missing; 157 candidates rank below #50) |
| Report rows before / after | 50 / 50 (the extract was re-run and returned the same weeks: 13–19 Sep vs 06–12 Sep) |
| Duplicate ASINs | 0 (50 distinct in 50 rows); 0 Amazon ASINs mapped to 2+ PH categories |
| Unrelated logic unchanged | The 25 Sep extract plus the PH map was rebuilt, and everything except the four new `ph_*` fields is identical to the 25 Sep dataset: performance rows, meta, KPI, keyword analysis, change record, saved records and monitoring data |
| `validate.py` | 32 PASS / 0 FAIL, including no console errors, no horizontal overflow at 1920/1366/1024/800/390px, and displayed rows = dataset rows |
| Rendered HTML | Searching "Paulroshan" in Section 3 shows 5 of 50 rows. Console errors: none. Screenshot: `screenshot_ph_paulroshan.png` |
| `test_change_tracking.py` | 22 PASS / 1 FAIL — see the note below |

**About T5:** T5 ("Monitoring with partial days, 18 Sept") depends on the run date, and the PH change did not cause the failure.
- The test hardcodes a Date Changed of 2026-09-18 and expects "Monitoring — N/7 days".
- Business Report data now runs through 2026-09-26 (it ran through 09-23 on 25 Sep), so all 7 post-change days are loaded.
- The page therefore correctly shows the completed result ("Orders declined · 7/7 days loaded").
- The test has not been changed.

Layout fix during this run: the first PH label version overflowed Section 3 by 98 px at 1366 px. The PH line now wraps (`.sub2.ph`), and the re-run passed.

## Paulroshan-only scope — validation (2026-09-28)

| Check | Result |
|---|---|
| Scope | PH user 49 (Paulroshan, `paulr`) → category 26 "Wire Cage"; 569 of the 634 mapped ASINs are in the extract, and 162 are eligible to rank |
| Report rows before / after | 50 (5 Paulroshan, 44 other PHs, 1 unmapped) / 50 (50 Paulroshan, 0 other PHs) |
| Duplicates | 50 distinct ASINs in 50 rows |
| Other PHs in any section | 0 in performance, keyword analysis, change record or monitoring |
| `validate.py` | 33 PASS / 0 FAIL. New check: "PH scope: every row is a Paulroshan ASIN". The independent top-50 recomputation also applies the same scope and passes |
| Rendered HTML | Subtitle reads "PH Paulroshan (Wire Cage)"; Section 3 shows 50 of 50 rows, each labelled `PH: Paulroshan`; no console errors. Screenshot: `screenshot_ph_paulroshan.png` |
| `test_change_tracking.py` | 22 PASS / 1 FAIL. This is the same T5 date-drift failure described above, unrelated to the scope change |

## V002 — Amazon submission status (2026-09-28)

This is a report-only update: no listing, database or n8n change, and no API call. The dashboard now separates five states:
1. Proposed
2. POST accepted by Amazon
3. Listing Management sync pending
4. Live verification pending
5. Monitoring not started

Submission data comes from `evidence/06_live_keyword_update.json` (submission ids and POST times) and `evidence/07_live_keyword_verification.json` (the latest read-only state). It is display-only and never sets Status or Date Changed.

| Check | Result |
|---|---|
| 22 submitted records present in Section 7, each with its unique submissionId | PASS (22/22) |
| B0CBLWLZ4W and B0DTTKL6KX still skipped: not submitted, no change proposed (no backend keywords on record) | PASS |
| No post-change metrics: no Date Changed, all Status = Proposed, 0 under monitoring, 0 monitoring rows in Section 8 | PASS |
| KPI reconciliation: POSTs accepted 22 = rows with a submission; live-verified 0 = the evidence/07 classification; the page's KPI cards match the dataset | PASS |
| `validate.py` | 38 PASS / 0 FAIL, with no console errors and no overflow at 5 widths |
| `test_change_tracking.py` | 22 PASS / 1 FAIL (the known T5 date drift) |

KPIs:
- 50 reviewed, 23 drops, 21 ASINs requiring review (22 listing rows).
- 0 completed/verified, 22 POSTs accepted, 0 visible content changed.
- **506** duplicate words removed. The 662 in the V002 request is the old all-PH figure; 506 is the Paulroshan-only value computed from the rows.
- 0 under monitoring; monitoring period is 7 days after a verified change.

Output: `output/2026-09-28_paulr_dashboard_V002.html` (md5 4931b6a38fadab2fcd8d2bd0ce2abcb5). Screenshots: `screenshot_v002_summary.png`, `screenshot_v002_change_record.png`. V002 has **not** been published to ph_task; row 1940 still holds V001.

## V003 — live-verification status (2026-09-29)

This is a dashboard and evidence update only. No POST was sent, no sync was triggered, and no database, listing or n8n change was made.

Verification now reads the Listing Management Tool itself: `GET /api/get-all-active-listings-data`, field `amz_platinum_keywords`, one listing per GET paced about 6 s apart. The ledsone `listings.*` mirror is used only for the identity/guard checks and as an audit value, because it lags the tool.

**Status rule (business instruction):** `BUYABLE` → `Active` with matching identity and `is_ended = 0` is an observation, not a failure. It was seen on B0DH4KYFPD, B0GXB7RGZK and B0C43N2F3D after the 29 Sep refresh.

**Evidence:**
- `07_live_keyword_verification.json/.csv`: all 22 submissions, read 03:45–03:48 UTC.
- `09_remaining_20_live_verification.json/.csv`: the 20 pending listings, read 03:41–03:43 UTC.
- Earlier runs preserved unchanged: `07_..._2026-09-28T1735_db`, `07_..._2026-09-29T0331Z`, `09_..._2026-09-29T0338Z`.

**Build rule change:** a row whose Listing Management read shows the proposed keywords becomes **Live-verified**, with Date Changed = the POST date (2026-09-28). Every other row stays Proposed with no date. Monitoring still starts only from a user save, and a pending POST cannot be saved as Monitoring.

| Check | Result |
|---|---|
| 22 submitted = 2 live-verified (B0DH4KYFPD, B0GXB7RGZK) + 20 pending; 0 mismatch; 0 not found / failed | PASS |
| Only the 2 verified rows are Live-verified; the 20 pending rows are Proposed with no Date Changed | PASS |
| 0 monitoring rows, 0 under monitoring, no post-change metrics; Section 8 shows no results | PASS |
| B0CBLWLZ4W and B0DTTKL6KX still skipped | PASS |
| Performance, keyword analysis and monitoring data identical to V002 (only the status/KPI fields changed) | PASS |
| No "22 completed / updated / live" wording on the page | PASS |
| `validate.py` | 41 PASS / 0 FAIL |
| `test_change_tracking.py` | 22 PASS / 1 FAIL (the known T5 date drift). T1, T2 and T4 were updated for the Live-verified state; T2 now also proves a pending POST cannot enter monitoring |

**KPIs:**
- 50 reviewed, 23 drops, 21 requiring review.
- Fine-Tuning Completed 2, POSTs Accepted 22, Live Verification Pending 20.
- 0 visible content changed, 506 duplicate words removed, 0 under monitoring.

**Output:** `output/2026-09-29_paulr_dashboard_V003.html` (md5 f6516678d0258c11605084c7c2324b43), with screenshots `screenshot_v003_{summary,change_record,monitoring,workflow}.png`. Published to ph_task row 1940 on 2026-09-29 (see 04_handover_closure.md).
