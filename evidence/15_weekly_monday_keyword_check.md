# 15 — Weekly Monday cycle: per-ASIN keyword check + 7-day performance check

Date: 2026-09-29. Business rule:
- Every Monday, for each ASIN due for its weekly cycle, run the 7-day performance check and a read-only check of the live backend keywords.
- Remove only duplicates and repetition. Update Listing Management once, and only if the cleaned value differs from the live one, then live-verify. If the keywords are already clean, do not POST.
- This happens once per Monday cycle, regardless of performance; a decline is used only by the alert logic.
- Never re-POST the 20 pending submissions.

## Changes
| File | Change |
|---|---|
| `automation/scheduler.ps1` | Trigger **Monday 15:00** (was Friday). The task was re-registered **DISABLED**. |
| `scripts/extract.py` | Current 7D = the Sun–Sat week ending on the latest Saturday ≤ run date − 8 days, so on a Monday it is the week that ended 9 days earlier. If that week is not loaded, it falls back to the latest loaded week and records a warning in `extract.json → week_rule`. |
| `scripts/optimization_cleanup.py` | Redesigned as a per-ASIN **weekly keyword check**; see the rules below. |
| `scripts/performance_alert.py` | `record_optimization(now=)` for test ordering. The alert rule is unchanged. |
| `scripts/render.py`, `report_template.html`, `validate.py` | The Section 12 "Weekly backend keyword check" table: monitored listings, their own next due date, latest check, optimization status, keywords, duplicates, update, verification and monitoring. |
| Tests | `test_optimization_cleanup.py` rewritten for the weekly model (32 checks). The `test_e2e_monitoring.py` helpers were updated. |

Why the lag: on the real catalog load history (Jul–Sep 2026), Monday runs with the old "latest loaded week" rule gave **2 skipped and 2 repeated weeks in 11 Mondays**, and a skipped week breaks a decline streak. With the fixed lag, all 10 simulated Mondays got a loaded, contiguous week. On 2026-09-29 it resolves to the same week as before (13–19 Sep).

## Weekly keyword check rules
- **Monitored listings:** those whose original submission is UPDATED / VERIFIED. **Pending submissions are never read, cleaned or re-sent.**
- **Due:** at most one production check per ASIN per Monday-week, and only when run date ≥ live-verification date + 7 days. After a check, the next check is due the following Monday. Recording an optimization never makes an ASIN due early.
- **Per listing:**
  - Read the live keywords (existing GET) and check identity.
  - Detect with `finetune()` (unchanged).
  - Validate: keep every word, add nothing; over 249 bytes gives a warning only.
  - POST once, and only if the cleaned value differs. It is not sent if the live keywords changed since the read, nor if the identical payload failed before (the team retries with `--retry-keyword-cleanup ASIN`).
  - Verify. A pending verification is re-verified the next Monday and never re-sent.
  - Clean on read: no POST.
- **Optimization:** the first production check that runs after the recorded Full Optimization and completes (clean or live-verified) sets the new monitoring baseline. A failed or pending check sets no baseline.
- **Dry run:** read and validate only, no POST. It does not use up the week's production check.

## Evidence
- Real 7-stage `automation/run.py --dry-run` (15:10): SUCCESS.
  - Pending re-check: 20 still pending, 2 verified, nothing re-sent.
  - Weekly keyword check: 2 monitored ASINs, **0 due** (next due 2026-10-06, so the first Monday run is 12 Oct). No read, no POST.
  - `validate.py` passed.
- Tests:
  - `test_optimization_cleanup` **32/32** (A, A2/M, B, C, D, D2, E, F, G, H, I, J, K, L, M, M2, P20, DRY, SAFE, TC8)
  - `test_e2e_monitoring` 51/51, `test_pending_verification` 15/15, `test_scheduler` 12/12, `test_email_alert` 32/32, `test_change_tracking` 23/23, `test_html` 7/7
