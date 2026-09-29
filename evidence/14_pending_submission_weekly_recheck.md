# 14 — Weekly READ-ONLY re-check of the 20 pending keyword submissions

Date: 2026-09-29. Business clarification: this covers only the 22 submissions already sent (2 verified + 20 pending). It is not a new update of any other ASIN, and it is never a re-submission.

## Implementation (existing verification reused)
- **New pipeline stage 2**, `keyword_live_verify.py --pending-only`, runs after `extract`. It performs read-only GETs (Listing Management `amz_platinum_keywords`) and read-only DB queries. The file contains no POST code. The runner only allows this script in `--pending-only` mode.
- It re-checks only submissions that are **not yet UPDATED / VERIFIED**, using the unchanged classification against the **originally proposed payload** from evidence/06.
  - Live = proposed and identity OK (ASIN, SKU, account, site): **UPDATED / VERIFIED**, and `verified_at_utc` is recorded.
  - Live = original: stays **Pending**. Live is neither: **MISMATCH**. Either way it is not verified, nothing is re-submitted, and it is re-checked next week.
  - Identity mismatch: **VERIFICATION FAILED**, not verified.
  - A guarded field changed since the POST (for example a title changed by the user's optimization) is recorded as an observation in this mode and does not block verification. Identity still blocks.
- Records that are already verified are kept unchanged and never re-read, so the first verification date is preserved.
- All 22 records stay in `07`, and each previous `07` is archived as `07_live_keyword_verification_<checked_at>_archived.json`.
- The guard baseline is frozen in `evidence/06_guard_baseline.json` (the 22 listing rows from the 2026-09-28 11:44 extract, before any POST), because `data/extract.json` is now re-extracted weekly.
- **`build_dataset.py`:** the per-submission state comes from `keyword_live_verify.latest_states([09, 07])`.
  - A verification is final, dated by its first verification.
  - Otherwise the weekly `07` wins over the legacy one-off `09`. `09` stores local times labelled "Z", so its timestamps are not comparable.
  - A newly verified submission becomes Live-verified. Its ASIN is then tracked by `performance_alert.py`, and its 7-day monitoring starts from the verification date (the next Sunday-start week). From there it follows the normal monitoring and post-optimization clean-up workflow.
- **`validate.py`:** uses the same resolution, and accepts any honest mix of verified / pending / mismatch / failed as long as all 22 are accounted for.

## Evidence (real run, `automation/run.py --dry-run`, 2026-09-29 14:40 local)
- Stage 2 re-read the 20 pending listings (146.7 s, paced GETs). All 20 are **still POST_ACCEPTED_PENDING_SYNC**: Listing Management still shows the original keywords about 21 h after the POST.
- The 2 verified records were kept with their original verification times.
- Nothing was re-submitted. KPIs: 22 accepted, 2 live-verified, 20 pending. The monitoring ledger holds 2 ASINs and 0 alerts.
- The previous `07` was archived as `07_live_keyword_verification_20260929T041710Z_archived.json`.
- Tests:
  - `test_pending_verification.py` **15/15**.
  - `test_scheduler.py` 12/12, including the new 7-stage order and a refusal of full-mode verify.
  - `test_e2e_monitoring` 51/51, `test_optimization_cleanup` 37/37, `test_email_alert` 32/32, `test_change_tracking` 23/23, `test_html` 7/7.
  - `validate.py` passed inside the run.
