# 05 — Change Record Tracking & 7-Day Monitoring

Date: 2026-09-25. This extends the existing report; nothing was rebuilt from scratch.

## Files changed
| File | Change |
|---|---|
| `scripts/extract.py` | + daily ASIN orders (both accounts) and per-day load coverage, from `daily_from` (9 weeks back, or earlier to cover D−7 of any saved record) |
| `scripts/build_dataset.py` | Change Record rows get a `key` (`ASIN\|SKU`) and status `Proposed`; loads and validates `data/change_records.json`; carries forward saved records no longer proposed; embeds `monitoring_data`. The static `monitoring` rows were removed. |
| `scripts/report_template.html` | Section 7 is editable, saves and persists. Section 8 is computed from saved dates. KPI cards, Sections 1/5/6/9/11 and the footer follow the saved records. |
| `scripts/render.py` | `render(ds, path)` function, reused by the tests |
| `scripts/validate.py` | checks updated for the new model, + daily-vs-weekly reconciliation and day-continuity checks |
| `scripts/test_change_tracking.py` | **new**: 23 browser tests |

## Persistence
- **Primary:** browser `localStorage`, key `wtma.changeRecords.v1`.
  - Survives refresh and reopening the file in the same browser on the same computer.
  - Not shared with other people or computers. Lost if browser data is cleared.
  - Does not work in private windows; the page shows a warning in that case.
- **Durable / next week:** Section 7 → **Export saved records** downloads `change_records.json`.
  - Place it at `data/change_records.json` (or point `WTMA_CHANGE_RECORDS` at it) before the weekly rebuild.
  - The build validates it (a Monitoring or Completed record must have a date), embeds it, and pulls daily data far enough back for every saved date.
  - For the same record, the most recently saved version wins between browser storage and the file.
- **Import records** loads an exported file into another browser.
- **Nothing is written to any database or to Amazon.**

## Date logic (Date Changed D, as saved by the user)
- Pre-Change = **D−7 → D−1**. Post-Change = **D+1 → D+7**. D itself is excluded.
- A day counts only if Business Report rows exist for both accounts on that date (`monitoring_data.available_dates`, currently 12 Jul → 23 Sep 2026, 74 contiguous days).
- **"Monitoring — X/7 days":** X = post-change days loaded. Post-change figures stay "—" until X = 7.
- Orders = `amz_sales_and_traffic_by_asin.total_order_items`, summed over both accounts per ASIN. This is the same source and aggregation as the weekly figures; the daily series reconciles exactly to the Current 7D orders for all 21 tracked ASINs.
- **Result (derived, sign-only, no threshold):** Orders improved / declined / unchanged, comparing post-change with pre-change orders.

## Status lifecycle (never automatic)
- **Proposed:** default. Cannot carry a date, and is never monitored.
- **Monitoring:** requires a real Date Changed that is not in the future.
- **Completed:** can be selected only when all 7 post-change days are loaded and the result exists. The user chooses it; nothing is completed automatically.

## Blocker: impressions (not guessed)
- Amazon impressions exist only as Sunday → Saturday weekly totals, in both `amz_catalog_performance_data` and `amz_search_query_performance`.
- D−7 → D−1 and D+1 → D+7 are separated by D, so they can never both match those weeks.
- Pre/Post-Change Impressions and Impression Change % are therefore shown as "—" ("weekly data only"). A business decision is needed; see the handover.

## Tests (`scripts/test_change_tracking.py`): 23 PASS / 0 FAIL
- **Scenarios you listed:**
  - Proposed default.
  - Monitoring 0/7 (D = last loaded day).
  - Partial: 1/7 for 22-Sep, 5/7 for 18-Sep.
  - Full 7/7 on a synthetic-data fixture (pre 14, post 21, +50.0%, "Orders improved").
  - Completed: set by the user and persisted.
  - D = 22-Sep → 15–21 Sep / 23–29 Sep; a cross-month date (02-Oct).
- **Save behaviour:**
  - Rejected inputs: no date, Proposed with a date, future date. Completed before 7/7 days is disabled.
  - Unsaved → Saved state.
  - Refresh persistence.
  - The pre-change orders (10) match a live DB query.
- **Other checks:**
  - KPI cards and Section 5 follow the saved record.
  - Impressions shown as "—".
  - Leaving with unsaved edits asks for confirmation.
  - Export works.
  - A build-embedded record is restored without browser storage; the build rejects invalid records.
  - No horizontal scrollbar with a saved record at 1920/1366/1024/390px.
  - No JavaScript errors.

Existing suite `validate.py`: 31 PASS / 0 FAIL. Screenshots: `screenshot_change_record.png`, `screenshot_monitoring.png`, `screenshot_monitoring_mobile.png`.
