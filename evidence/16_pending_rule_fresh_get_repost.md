# 16 — Pending rule changed: every due week = fresh GET → clean → POST if needed → verify

Date: 2026-09-29. This supersedes the "pending = read-only re-check, never re-POST" rule in evidence/14 and the per-ASIN model in evidence/15.

## Scope
- `data/keyword_scope.json` holds the report's **24 ASIN-SKU rows**, resolved to **26 listings**. B0CBLWLZ4W's row names 3 SKUs, each resolved by exact ASIN + SKU, UK, not ended.
- Of these, 22 were submitted and 4 were never submitted.
- The scope is frozen; the check never covers other seller listings. Identity = ASIN + SKU + account (sub_source) + UK + listing id.

## Rule (per listing, every weekly due check, whatever its status)
1. GET the latest live keywords. This is the source of truth.
2. Run the existing `finetune()`, unchanged.
3. **cleaned == live:** no POST, and the row is **Live Verified**. The detail says "proposed value is live" when the live value equals the earlier proposal.
4. **cleaned != live:**
   - Validate, then re-read just before sending. If the keywords changed, nothing is sent.
   - POST the cleaned value through the existing API. The payload is always built from this run's GET.
   - Read back. Live == cleaned gives **Live Verified**. Otherwise **POST Accepted — Live Verification Pending**, and the next due week does a fresh GET and repeats the process.
   - A rejected update is marked Update Failed. The next due week again starts from a fresh GET.
5. Every GET value, POST attempt (HTTP status, submissionId), verification result and final live value is appended to `keyword_rows[...].history`. The original submission is the first history entry.
6. **Due:** once per Monday-week, and not before 7 days after the listing's last verification or last POST. Never-submitted rows are due from the first run.
7. **Monitoring:** a listing's first live verification starts its 7-day monitoring. Routine weekly re-verifications do **not** reset the decline streak. The reset happens only after a recorded Full Optimization, when the first production run after it verifies all of the ASIN's checked listings.

## Pipeline
`extract → weekly_keyword_check (optimization_cleanup.py --apply) → build_dataset → performance_alert --send → render → validate`
- The separate read-only pending stage was removed; pending rows now get a fresh GET inside the weekly check.
- `build_dataset` overlays the weekly check's first verification onto Section 7, and `performance_alert` tracks every first-verified scope listing.

## Evidence (real `run.py --dry-run`, 15:38)
- SUCCESS, with `validate` at 47/47. Due today: the 4 never-submitted listings only. The 20 pending listings are due from 2026-10-05 and the 2 verified from 2026-10-06.
- Result of that dry run:
  - B0CBLWLZ4W `WCWDBM2PK+RPR44WH2PK`, `…_AMD` and B0DTTKL6KX: **no backend keywords** on the live listing, so there is nothing to clean.
  - B0CBLWLZ4W `…_DCVV` (Ledsone): **not in the Listing Management active feed**, so Read Failed.
  - No POST was sent. It was a dry run, so the statuses are not committed.
- Tests: `test_optimization_cleanup` **31/31** (B, C, D, AUDIT, M, E, P1–P7, R1, L, K, A, H, I, D2, F/G, SCOPE, DRY, SAFE, TC8), `test_e2e_monitoring` 51/51, `test_pending_verification` 15/15, `test_scheduler` 12/12, `test_email_alert` 32/32, `test_change_tracking` 23/23, `test_html` 7/7.
- Secret scan: 0 hits. The scheduler is Disabled.
