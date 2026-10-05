# 17 — Post-update monitoring from the live update date, no e-mail (business instruction 2026-10-02)

Current rules. They replace the 2-consecutive-decline Full Optimization Review **e-mail alert** (evidence 10 / 12, now historical).

## 1. E-mail alerts removed
- The production pipeline (`automation/run.py`) is: `extract → weekly_keyword_check (--apply) → build_dataset → performance_monitoring → render → validate`.
  There is **no e-mail stage** and no `--send`.
- `run.py` refuses `email_alert`, `send_test_email`, `gmail_authorize`, `publish_ph_task`, `keyword_live_*` and any `--send` stage.
  It needs no Gmail environment (only `WLP_SOURCE_DB_URL`) and imports no e-mail code; the stage-log secret redaction now lives in `run.py`.
- `performance_alert.py --send` / `--validate` are refused (exit 2, nothing sent).
- Kept as **history only**, read by no production module: `scripts/email_alert.py`, `gmail_authorize.py` and `send_test_email.py` (retired code), and `evidence/10_*` (Gmail config / test e-mail / old alert test results).
- The dashboard shows the performance status directly and carries no e-mail / alert status.

## 2. Monitoring rule (FINAL, business instruction 2026-10-05)
- **POST Accepted** = the backend keyword update was accepted/updated for the user. It starts monitoring **immediately**. No GET / live verification is required first.
- A Listing Management GET is **audit information only**: it never gates monitoring and never moves the anchor.
- An accepted payload is **never re-sent** just because a GET still shows the previous keywords.
- A new POST happens only for a genuinely new keyword change, for example a user's manual change that contains duplicates. That POST Accepted starts a new cycle on its POST date. A clean manual change starts a new cycle on the GET date, or on the date recorded with `--record-live-update`.
- **The 22 accepted original submissions** (POSTed 2026-09-28) share **one common monitoring anchor: 2026-09-29**. This is `optimization_cleanup.ORIGINAL_ANCHOR`, set by `seed()/reconcile()`. It is not taken from later GET verification dates, and B0C43L42DK also uses 2026-09-29.
- Week 1 = 29 Sep → 05 Oct 2026. Week 2 = 06 Oct → 12 Oct 2026.
- A week's orders are shown only when all 7 of its days are loaded.
- The Result shows "Monitoring — Week 1", then "Monitoring — Week 2", and only after Week 2 is complete **Performance Decline / Performance Improved / Performance Stable**.
- **Dashboard:** the previous approved layout is kept.
  - Sections 5 and 7 show "Live-verified — monitoring" for every POST Accepted submission. Section 7 status = Monitoring, Date Changed = 29 Sep.
  - Section 8 keeps the previous 8 columns: ASIN, Week 1 Orders, Week 2 Orders, Order Change %, Week 1 Impressions, Week 2 Impressions, Impression Change %, Result.
  - KPI cards: Completed = complete 14-day cycles (0 today), Live Verification Pending = 0, ASINs Under Monitoring = 22 accepted listings (21 ASINs).
- The report week (Current 7D 20–26 Sep 2026) is separate from the monitoring anchor.
- Ledger: `data/monitoring_cycles.json → keyword_rows[*].live_updates` (anchors) and `→ monitoring` (cycles; completed cycles are frozen). Evidence: `evidence/17_post_update_monitoring.csv`.

## 3. Records
All 22 accepted submissions are under monitoring from 2026-09-29.

The audit GET (evidence 07) shows the submitted keywords for B0DH4KYFPD, B0GXB7RGZK and B0C43L42DK. The other 19 still show the previous keywords. This is audit only; their monitoring is unchanged and they are not re-POSTed.

## 4. Weekly keyword check
Every due check GETs the latest live value:
- **Accepted value, or still the pre-POST value:** audit only, no POST.
- **A new value with duplicates:** cleaned with the existing `keyword_finetune` rules (relevant words kept in order, nothing added, never replaced), then POSTed once. POST Accepted becomes the new anchor, and the read-back is audit.
- **A new clean value:** a manual change. No POST, and a new anchor.

## 5. Daily execution (ready, not enabled)
`automation/run.py` is idempotent, so it is safe to run every day:
- **Keyword checks:** a listing is checked at most once per Monday-week, and only 7 or more days after its last verification or POST. An accepted payload is never retried. Re-runs on the same day, or later in the week, do nothing.
- **Monitoring:** cycles are recomputed from the stored anchors. They never restart, and completed cycles stay frozen.
- **Concurrency:** a lock file blocks overlapping runs.
- **E-mail:** none is sent.

The scheduled task `WTMAFT_Weekly_Keyword_Monitoring` stays registered and **disabled** (unchanged). To switch it to a daily 15:00 trigger, re-register it with `.\automation\scheduler.ps1 -Daily`, then enable it with `.\automation\scheduler.ps1 -Enable`. Both are user actions, to be done only after approval.

## 6. Tests (2026-10-05)
See `evidence/validation_results.json` (dashboard / dataset validation) and the test result files:
- `12_e2e_monitoring_test_results.json`
- `13_optimization_cleanup_test_results.json`
- `14_pending_verification_test_results.json`
- `11_scheduler_test_results.json`
- `12_html_validation.json`
- `change_tracking_test_results.json`
- `18_no_email_alert_test_results.json`
