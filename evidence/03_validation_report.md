# 03 — Validation Report

Script: `scripts/validate.py` → `evidence/validation_results.json` (machine-readable).
Last run: 2026-09-25 11:33. **Result: 30 PASS / 0 FAIL.**

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
