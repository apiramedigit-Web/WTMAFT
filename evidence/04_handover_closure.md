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
python scripts\performance_alert.py  # 7D cycle ledger + Full Optimization alert (dry run; --send = live e-mail, see 10_email_alerting.md)
python scripts\render.py         # -> output\...report.html
python scripts\validate.py       # -> evidence\validation_results.json (+ screenshots)
python scripts\test_change_tracking.py  # Section 7/8 workflow tests
```
Before the rebuild, put last week's exported Section 7 records at `data\change_records.json`, so confirmed changes and their monitoring carry over. See `05_change_tracking.md`.
The windows move automatically to the latest complete Search Catalog week.

## Weekly automation (2026-09-29)
- Task **`WTMAFT_Weekly_Keyword_Monitoring`**: Friday 15:00, runs `python automation\run.py`. It is registered **DISABLED**; enable it with `.\automation\scheduler.ps1 -Enable` after approval.
- Manual run: `python automation\run.py` (production) or `python automation\run.py --dry-run` (no e-mail).
- Logs are under `logs\` and run records in `evidence\11_scheduler_runs.json`.
- Tests: `test_e2e_monitoring.py`, `test_scheduler.py`, `test_email_alert.py`, `test_change_tracking.py`, `test_html.py`.
- Details: `evidence\11_scheduler_dashboard_e2e.md`.

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

## ph_task publication — V001 (2026-09-28)

| Field | Value |
|---|---|
| id | **1940** (`tech_team_outputs.ph_task`, DATABASE_URL) |
| project_code / task_id | `WTMA` / `2026-09-28_paulr_dashboard_V001` |
| assigned_user / assigned_user_team | `paulr` / `ph_priors` (existing spelling: 24 prior rows) |
| team / developer / phase / version / status | Development / Apirame / 1 / 1 / released |
| html_content | `output/2026-09-28_paulr_dashboard_V001.html`: 135,526 bytes, md5 `9479e3e42a500fbba08c98fc20d97305` (LF text), matching the DB md5 |
| Scope | Paulroshan (Wire Cage) only: top 50 ASINs, current week 13–19 Sep 2026 |

- Publisher: `scripts/publish_ph_task.py`. It is insert-only, aborts if a WTMA row already exists, checks the md5 and the WTMA row count inside the transaction, and rolls back on any mismatch.
- The identity sequence was at 1915 while max(id) was 1939. It was advanced with `nextval` (24 steps) before the insert; no explicit id was used.
- A V002 will need an update path, because the V001 publisher refuses to run once a WTMA row exists.

## ph_task publication — V002 (2026-09-28)

Row **1940** was updated in place, so no new row was added and there is still one WTMA row. Before the update, the row was V001, released, with `action_took_by` NULL (not actioned).

| Field | Value |
|---|---|
| task_id | `2026-09-28_paulr_dashboard_V002` |
| version_level / status | 2 / released |
| task_name | "paulr - Top 50 Wire Cage ASINs: 23 performance drops, 22 backend keyword updates accepted by Amazon (0 live-verified) - 13 Sep 2026–19 Sep 2026" |
| html_content | `output/2026-09-28_paulr_dashboard_V002.html`: 142,176 bytes, md5 `4931b6a38fadab2fcd8d2bd0ce2abcb5`, matching the DB |
| assigned_user / team | `paulr` / `ph_priors` (unchanged) |

`scripts/publish_ph_task.py` now inserts when there are 0 WTMA rows and updates when there is 1. The update requires the row to be unactioned and a lower version, and it rolls back on an md5, version or row-count mismatch.

## ph_task publication — V003 (2026-09-29)

Row **1940** was updated in place, so there is still one WTMA row. Before the update it was V002, released, with `action_took_by` NULL (not actioned).

| Field | Value |
|---|---|
| task_id | `2026-09-29_paulr_dashboard_V003` |
| version_level / status | 3 / released |
| task_name | "paulr - Top 50 Wire Cage ASINs: 23 performance drops, 22 backend keyword updates accepted by Amazon (2 live-verified, 20 pending) - 13 Sep 2026–19 Sep 2026" |
| html_content | `output/2026-09-29_paulr_dashboard_V003.html`: 149,797 bytes, md5 `f6516678d0258c11605084c7c2324b43`, matching the DB |
| assigned_user / team | `paulr` / `ph_priors` (unchanged) |

State: 2 live-verified (B0DH4KYFPD, B0GXB7RGZK), 20 pending live verification, monitoring not started. The only change to `scripts/publish_ph_task.py` is VERSION = 3, plus the pending count in task_name.
