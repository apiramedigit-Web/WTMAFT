# 11 — Weekly scheduler, Section 12 dashboard, final E2E validation

Date: 2026-09-29. This extends the existing WTMAFT pipeline; no parallel workflow was created.

**Unchanged:**
- the keyword fine-tuning rules (`keyword_finetune.py`)
- the Listing Management POST logic (`keyword_live_update.py`)
- the live verification rules (`keyword_live_verify.py`)
- the 2-consecutive-7D alert rule
- the Gmail OAuth transport, the recipients and the duplicate-alert protection

## 1. Scheduler
| Item | Value |
|---|---|
| Task | `WTMAFT_Weekly_Keyword_Monitoring` (Windows Task Scheduler). It is the only WTMAFT task, so there is no duplicate. |
| State | **Registered DISABLED**, awaiting production approval. Enable with `.\automation\scheduler.ps1 -Enable`. |
| Schedule | Weekly, **Friday 15:00** local (Asia/Colombo). Catalog weeks (Sun–Sat) land Mon–Thu of the following week; see "Catalog load timing" below. |
| Action | `C:\Program Files\Python313\python.exe "…\automation\run.py"`, working dir = project. There is no project venv; this is the same interpreter the other automation tasks use, and the Google libraries are in its user site-packages. |
| Settings | StartWhenAvailable (a missed Friday runs when the PC is next on). IgnoreNew (no overlap). 2 h limit. Interactive user, with no stored password, like the other tasks. |
| Order | `extract.py → build_dataset.py → performance_alert.py --send → render.py → validate.py` |
| Failure handling | A non-zero exit, timeout or crash in any stage sets the run to FAILED. The remaining stages are marked NOT RUN, and the runner exits 1. SUCCESS only if all 5 stages exit 0. `validate.py` now exits 1 on any FAIL, so a bad dashboard cannot "succeed". |
| Logs / evidence | `logs/<run_id>/NN_<stage>.log` holds stdout, stderr and the exit code, with secrets redacted; `logs/` is git-ignored. `logs/run_history.log` has one line per run. `evidence/11_scheduler_runs.json` keeps the last 100 run records. |
| Safety | The runner refuses any `keyword_live_*`, `send_test_email`, `gmail_authorize` or `publish_ph_task` stage. A lock file prevents overlapping runs. `--dry-run` runs the alert stage without `--send`. |

### Catalog load timing (read-only query, `amz_catalog_performance_data.created_at`)
- Weeks since 5 Jul were loaded Mon (8×), Tue (3×), Wed (2×), Thu (2×) and Sat (1×) after the week ended.
- If a Friday run finds no new week, it re-records the same cycle. This is idempotent, so nothing is double counted, and the run record notes it.

## 2. Continuity fixes found during implementation
These were required for Test Case 7 and for the weekly run to work at all.
- **`build_dataset.py` would have crashed the first weekly run.** It asserted that every submitted update is in this week's review list, but a successfully cleaned listing leaves that list.
  - The 29 Sep fresh extract proves it: B0DH4KYFPD and B0GXB7RGZK now show *No Duplicates Found* in the DB mirror.
  - Fix: submitted rows are carried forward (`carried_forward: true`), like saved records.
- **`monitoring_performance`:** the same `metrics()` code as Section 3, applied to every change-record and ledger ASIN, so an ASIN keeps being measured after it leaves the top 50. For the top-50 rows, `performance`, `kpi` and `keyword_analysis` are byte-identical to before (verified).
- **`performance_alert.py`:** reads `monitoring_performance`, and keeps ASINs already in the ledger tracked. It adds display fields only: `monitoring_start_date` (the first Sunday-start window after live verification), `monitoring_cycle_number` and `in_top_moving`. `evaluate()`'s rule is unchanged.
- **`validate.py`:** the hard-coded snapshot numbers (2 verified / 20 pending / 506 words / 12 tabs / two fixed keys) are now derived from the evidence. It has new checks for continuity, the recomputed monitored metrics, ledger integrity and Section 12, and it exits non-zero on FAIL.
- **`test_change_tracking.py`:** two date/data snapshot assumptions (T5, T3g) are now derived from the data.
  - T5 was already failing before this work (baseline 22/1 at 09:23).

## 3. Dashboard: Section 12 "Full Optimization Review Monitoring" (tab "12. Full Optimization")
- **Source:** `render.py` embeds `data/monitoring_cycles.json` (cycles + alerts) at render time. The separate TEST-mail record is shown only as a note.
- **Latest-cycle table fields:** ASIN / SKU, account and marketplace, Current and Previous 7D orders with date ranges, Order Change % with performance status, the "outside top 50 · still monitored" marker, last fine-tuning, live verification, monitoring start and current cycle #, consecutive declines, keyword status with duplicate words removed, Full Optimization Review state, and alert status with sent time and Gmail message id.
- There is also a cycle-history table, state counters, a red banner listing ASINs that need review, and the **"Full Optimization is MANUAL"** notice (no title, bullets, images, description or A+ changes).
- **States:**
  - Normal / improving (green)
  - One consecutive decline, no alert (amber)
  - ⚠️ Full Optimization Review Required (red, only at count ≥ 2)
  - ⚠️ … · Alert already sent (blue)
  - Awaiting first eligible cycle
  - No data this cycle
- Screenshots:
  - `screenshot_s12_real.png`: real data. Both monitored ASINs are *Awaiting first eligible cycle*, because 13–19 Sep precedes their 29 Sep verification.
  - `screenshot_s12_states_fixture.png`: synthetic `B0FIXTURE*` rows, one per state.

## 4. Validation results
| Suite | Result | Evidence |
|---|---|---|
| `test_e2e_monitoring.py` (TC1–TC8 + safety) | **33 PASS / 0 FAIL** | `12_e2e_monitoring_test_results.json` |
| `test_scheduler.py` | **11 PASS / 0 FAIL** | `11_scheduler_test_results.json` |
| `test_email_alert.py` (Gmail transport, fake) | **32 PASS / 0 FAIL** | `10_email_alert_test_results.json` |
| `test_change_tracking.py` (Sections 7/8) | **23 PASS / 0 FAIL** | `change_tracking_test_results.json` |
| `validate.py` (dataset + dashboard, browser) | **46 PASS / 0 FAIL** | `validation_results.json` |
| `test_html.py` (HTML structure) | **7 PASS / 0 FAIL** | `12_html_validation.json` |
| `keyword_finetune.py` self-test | OK | — |
| Real pipeline via `automation/run.py --dry-run` | 1st run **FAILED at validate**, correctly reported. It exposed a snapshot expectation, now derived. 2nd run **SUCCESS**: 5/5 stages exit 0, no e-mail, production alert log unchanged (0 records). | `11_scheduler_runs.json`, `logs/run_history.log` |
| Gmail `--validate` (read-only) | ready = true | `10_gmail_config_validation.json` |
| Secret scan | The Gmail access and refresh tokens, OAuth client secret, Resend key and DB password each appear 0 times in repo files, `logs/` and git history. There are 0 token-shaped strings apart from FAKE fixtures, and 0 credential files in the repo. | — |

E2E safety:
- The production ledger, dataset, extract and TEST-mail record were hashed before and after the E2E run: unchanged.
- There was no real e-mail and no network: the dry run used a transport that raises if called, and the send path used a fake Gmail transport.
- No Amazon keyword update was imported or run.

## 5. Week 3+ alert policy update (2026-09-29, business instruction)
- **Expected sequence, verified in E2E TC9:**
  - Week 1 down: count 1, no alert.
  - Week 2 down: count 2, **alert**.
  - Manual Full Optimization is recorded.
  - Week 3: new monitoring cycle (baseline).
  - Week 4 down: count 1, no alert.
  - Week 5 down: count 2, **new alert**.
- **Changed:**
  - `performance_alert.py`: `evaluate()` now closes the streak at the alert cycle; new `record_optimization()` and `--record-optimization` CLI; ledger key `optimizations`; per-cycle fields `streak_status` (`open` / `alert` / `awaiting_full_optimization` / `baseline`), `baseline_type`, `baseline_date`, `last_full_optimization_date` and `alert_cycle_id`.
  - Section 12: new states *Awaiting manual Full Optimization* and *New monitoring cycle after Full Optimization*. The banner shows the record-optimization command.
  - `validate.py`: its state and integrity checks now match (alerts only on alert cycles, with count 2).
- **Unchanged:** the alert condition (2 consecutive eligible declines after the baseline), the Gmail transport, the recipients, duplicate protection per ASIN and cycle, the keyword and API logic, and the scheduler.
- **Tests:**
  - E2E **51 PASS / 0 FAIL**. TC5 now checks for no weekly repeats. New cases: TC9 (the business example), TC10 (a late optimization), TC11 (recording safeguards, CLI) and TC12 (a failed alert retried while awaiting).
  - `test_email_alert` 32/32, `test_scheduler` 11/11, `test_change_tracking` 23/23, `test_html` 7/7.
  - Real pipeline dry run SUCCESS, with `validate` passing.
- **Screenshot:** `screenshot_s12_states_fixture.png` shows all 8 states on synthetic `B0FIXTURE*` rows.
