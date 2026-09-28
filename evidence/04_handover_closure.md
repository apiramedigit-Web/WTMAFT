# 04 — Handover / Closure Note

**Deliverable:** `output\weekly_top_moving_asin_backend_keyword_fine_tuning_report.html`
- Standalone file with CSS, JS and data inline. No server, database or network needed.
- Scope: Amazon UK, amazon Dcvoltage + amazon Ledsone. Current 7D 13–19 Sep 2026 vs Previous 7D 06–12 Sep 2026.

## Results

| KPI | Value |
|---|---|
| Top-Moving ASINs Reviewed | 50 |
| ASINs Showing Performance Drop | 23 |
| ASINs Requiring Backend Keyword Review | 21 |
| Backend Keyword Fine-Tuning Completed | 0 verified (24 SKU listings proposed) |
| Visible Listing Content Changed | 0 |
| Duplicate Keywords Removed | 662 words, in the proposed cleaned terms |
| ASINs Under Monitoring | 0 (21 pending upload) |

- **Issue mix of the 23 drops:** Conversion Drop 9, CTR Drop 9, Impression Drop 2, Overall Performance Drop 2, Order Drop (no search data) 1.
- **Drops not needing review:** of the 23, 1 has no backend keywords on record and 1 already has none repeated.

## Rebuild (read-only, about 1 minute)
```
cd "%USERPROFILE%\Weekly_Top_Moving_ASIN_Keyword_Report"
python scripts\extract.py        # ledsone via WLP_SOURCE_DB_URL -> data\extract.json
python scripts\build_dataset.py  # rules -> data\report_dataset.json
python scripts\render.py         # -> output\...report.html
python scripts\validate.py       # -> evidence\validation_results.json (+ screenshots)
python scripts\test_change_tracking.py  # Section 7/8 workflow tests
```
Before the rebuild, put last week's exported Section 7 records at `data\change_records.json`, so confirmed changes and their monitoring carry over. See `05_change_tracking.md`.
The windows move automatically to the latest complete Search Catalog week.

## Open items needing a decision or access
1. **Approve the derived rules:**
   - Top-moving = top 50 by orders over the 3 prior weeks.
   - Issue Detected classification.
   - Both are parameters in `scripts/build_dataset.py`.
2. **Upload evidence:**
   - Grant SELECT on `listing_generator.generated_listings`, or confirm another upload log. Until then, Final Status stays "Proposed – upload not verified" and Date Changed stays "—".
   - Alternatively, snapshot `listings.amazon_listing_search_engine_keywords` weekly and diff it, to evidence the real change date.
3. **Access to `listing_generator.tbl_low_asins_keywords`** (Helium 10 search volume and relevancy) is needed only if the business wants a documented "unnecessary word" rule beyond duplicates.

## Files
- **Scripts:** `scripts\` — `extract.py`, `keyword_finetune.py` (self-test included), `build_dataset.py`, `report_template.html`, `render.py`, `validate.py`.
- **Data:** `data\extract.json` (raw) and `data\report_dataset.json` (report dataset).
- **Evidence:** `evidence\01_requirement_and_asset_discovery.md`, `02_data_mapping_and_rules.md`, `03_validation_report.md`, `validation_results.json`, screenshots.
