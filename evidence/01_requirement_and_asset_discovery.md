# 01 — Requirement Understanding & Existing-Asset Discovery

Run date: 2026-09-25 · Requirement: `Downloads\Weekly_Top_Moving_ASIN_Backend_Keyword_Fine_Tuning_Report.pdf` (3 pages, 11 sections)
Skill references read: `skills 3 (1) (3) (1)\skills\TABLE_traffic_data.md`, `TABLE_ppc.md`, `TABLE_thresholds.md`, `SKILL_threshold_validator.md` (same files in `skills_minimal_pack 2 (2) 2`).

## A. Requirement map

| # | Section | Required content | Implemented as |
|---|---|---|---|
| 1 | Report Purpose | Detect sudden declines in top-moving ASINs, compare 7D vs previous 7D, review/clean backend search terms, monitor 7 days | Section 1 + upload-status callout |
| 2 | Weekly Performance Summary | 9 KPIs: Top-Moving ASINs Reviewed … Next Review | 9 KPI cards, computed in-page from rows |
| 3 | ASIN Performance Comparison | ASIN, SKU, Previous 7D Orders, Current 7D Orders, Order/Impression/Click/CTR/CVR Change %, Issue Detected | Sortable/searchable table (+ supporting raw columns after the required ones) |
| 4 | Backend Keyword Analysis | ASIN, Content Status, Backend Keyword Status, Issue Found, Action Taken | Table + Keyword Optimization Principle |
| 5 | Keyword Change Details | ASIN, Original Backend Keywords, Issue, Fine-Tuning Action, Final Status | Original kept, removed words struck through, cleaned terms shown separately |
| 6 | Optimization Classification | Change Type, Applied?, Description (9 change types) | Same 9 rows; Backend = "Proposed (not yet uploaded)" |
| 7 | Change Record | ASIN, SKU, Issue Detected, Optimization Action, Change Type, Date Changed, Status | Date Changed "—", Status "Proposed – upload not verified" |
| 8 | 7-Day Monitoring Report | ASIN, Pre/Post-Change Orders, Order Change %, Pre/Post-Change Impressions, Impression Change %, Result | Pre = Current 7D baseline; Post = "—"; Result "Pending Upload" |
| 9 | Weekly Workflow | Detect, Compare, Check Content, Review Keywords, Fine-Tune, Upload, Monitor, Measure | 8 step cards with real status |
| 10 | KPI Formulas | 5 formulas | Verbatim + CTR/CVR change and div-by-zero notes |
| 11 | Management Summary | 8 areas + Management Note | Same areas, figures from data, + Current Status |

Core principle (sections 4/5): keep existing relevant keywords; remove only duplicate / repeated / unnecessary words; never replace the keyword strategy; no visible-content change.
PDF sample values (50 / 12 / 10, B0XXXXXXX, WCXXXX, 22-Sep-2026) were treated as format only — none are used as data.

## B. Existing-asset discovery (repository, local folders, PostgreSQL)

| Asset | What it is | Decision |
|---|---|---|
| `ANPIA_Amazon_No_Moving_Automation\` (`run_anpia.py`, `anpia/extract.py`) | Amazon no-moving report; daily per-ASIN arrays; uses `public.amz_sales_and_traffic_by_asin` via DATABASE_URL | **Not reused**: no drop rule, no keywords, and it reads the OMC copy, whose `amz_traffic_by_asin` was renamed. The ledsone `business_reports` originals are used here instead. Pattern (window sums, null handling) followed. |
| `Weekly_Listing_Performance_Action_Framework\05_SQL_Development\WLPAF.sql:243-246` | eBay week-vs-week rule: `<= -10%` = Dropping | **Not adopted** (eBay, units); reported as sensitivity: all 23 drops also meet ≤ −10% |
| `SKILL_threshold_validator.md` BL-03 | 30-day organic impression decline trigger (30%) | **Not adopted** (30-day, impressions); sensitivity: 17 of 23 drops are ≤ −30% |
| Before/After analyses (`Downloads\Kishanth\...`) | ±5% improved/declined convention | **Not adopted**; sensitivity: 23 |
| `public.thresholds` (OMC) | 183 rules; Amazon rows are per-category minimum weekly organic impressions | **No** top-moving or week-over-week drop rule exists |
| `SKU-Opportunity-Task-Phases.md:11716` | eBay FAST_MOVING = ≥10 units/30d | Not Amazon; not adopted |
| `Amazon_UK_Image_Gap_Analysis\` | 3-month window, `traffic_data` | Not reusable for 7D |
| `Keyword_Check_Kobiga\10_automation\detect_changes.py` | eBay snapshot diff (NEW/CHANGED/REMOVED) | Recommended next step for real "Date Changed" evidence |
| `listings.amazon_listing_search_engine_keywords` (ledsone) | Live backend search terms per listing row | **Reused — the backend keyword source** |
| `listing_generator.generated_listings`, `listing_generator.tbl_low_asins_keywords`, `staging_ai.ph_action_evidence_snapshots_prototype_20260615` (OMC) | AI-generated listings with `pushed_at`; Helium 10 Cerebro keyword research; before/after snapshots | **Permission denied** for temp_user (schema USAGE / table SELECT). Structure of `tbl_low_asins_keywords` read from pg_catalog only (see 02). |

Duplicate risk: none found — no existing Amazon report performs 7D-vs-7D drop detection or backend keyword cleaning.
