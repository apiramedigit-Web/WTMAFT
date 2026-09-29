# 13 — Post-optimization backend keyword clean-up → live verification → new monitoring cycle

Date: 2026-09-29. This is integrated into the existing WTMAFT pipeline as one extra stage; no standalone workflow was created.

## Integration points (existing code reused)
| Step | Reused | Where it is integrated |
|---|---|---|
| Latest keywords after the user's optimization | `keyword_live_verify.lm_listing()`: the Listing Management GET (`amz_platinum_keywords`, 429 back-off) | `optimization_cleanup._step()` READ, plus an identity check (ASIN, SKU, UK, account, listing id) |
| Duplicate detection / removal | `keyword_finetune.finetune()`, unchanged | same; payload validation checks that every unique word is kept, no word is added, and the result is non-empty; over 249 bytes gives a **warning only**, with no truncation (evidence/02 D4) |
| Listing Management update | `keyword_live_update.post()`, with the same endpoint and acceptance criteria | `--apply` only; one POST per listing per optimization; live keywords are re-read just before the POST |
| Live verification | the same live-vs-cleaned comparison as `keyword_live_verify.py` | runs every weekly run until the listing is verified (pending) or the live value is neither (failed) |
| Monitoring-cycle reset | `performance_alert.evaluate()` | the baseline is now `monitoring_baseline_date`, the **live-verification date** of the clean-up, not the date the user reported the optimization |
| Dashboard | `render.py` → Section 12 | new "Post-optimization keyword clean-up" table and states |
| Scheduler | `automation/run.py` | new stage 3, `optimization_cleanup.py --apply` (without `--apply` under `--dry-run`) |

## Flow
1. Alert on 2 consecutive declines. The ASIN shows **Awaiting User Optimization**.
2. The team optimizes manually, then runs `performance_alert.py --record-optimization ASIN --date …`. The ASIN shows **User Optimization Completed**.
3. The weekly run reads the latest live keywords and detects duplicates.
   - **No duplicates:** no update is sent, and the result is *No cleanup required*. The live read is the verification.
   - **Duplicates:** the keywords are cleaned and the payload validated, giving **Listing Update Pending**. In a production run the update is POSTed, giving **Live Verification Pending**. Once the live keywords match the cleaned payload, the status is **Live Verified**.
4. When every listing is done, the status is **Monitoring Active**. The live-verification date becomes the new baseline, and the next 7-day cycle starts the following Sunday.
5. Monitoring then continues as before:
   - 1 decline: no alert.
   - 2 consecutive declines: a new alert, and the streak closes again.
   - The same ASIN can go through any number of optimization cycles. All cycles, alerts and optimization records are kept.

### Failure handling
- *Update Failed*: the listing is not readable, an identity check fails, validation fails, or the update is rejected.
- *Verification Failed*: the live value is neither the cleaned nor the original keywords.
- In either case the baseline is not set, and the ASIN stays out of a new cycle with no alerts.
- Nothing is re-sent automatically. The team retries with `optimization_cleanup.py --retry-keyword-cleanup ASIN`; the failed attempt is archived in `history`.
- A clean-up that stays pending just keeps being re-checked each week, with no re-POST.

## Tests
| Suite | Result |
|---|---|
| `test_optimization_cleanup.py` (TC1–TC8 + 6 safety cases) | **37 PASS / 0 FAIL** (`13_optimization_cleanup_test_results.json`) |
| `test_e2e_monitoring.py` | 51 / 0 |
| `test_email_alert.py` | 32 / 0 |
| `test_scheduler.py` (6-stage order) | 11 / 0 |
| `test_change_tracking.py` | 23 / 0 |
| `validate.py` (in the real run) | all PASS |
| `test_html.py` | 7 / 0 |
| Real 6-stage `automation/run.py --dry-run` | SUCCESS. The clean-up stage found 0 recorded optimizations, so there was no read and no POST. No e-mail was sent. |

The production ledger, dataset, extract and TEST-mail record were verified unchanged by the tests (hash comparison).
