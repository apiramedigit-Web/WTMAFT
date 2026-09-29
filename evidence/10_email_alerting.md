# 10 — Full Optimization Review alert: Gmail API (OAuth 2.0)

- Date: 2026-09-29.
- The transport changed from Resend to the **Gmail API**, by business instruction. There is no SMTP, n8n, Resend or Gmail password.
- The alert rule, the 2-consecutive-decline logic, the recipients, the duplicate protection, the cycle ledger and the evidence files are unchanged. The Amazon keyword-update logic is untouched.

## Files
| File | Role |
|---|---|
| `scripts/email_alert.py` | Transport replaced; `build_alert` (subject, body, warning) is unchanged. Covers the OAuth token, identity and scope validation, a non-sending Gmail API probe, the MIME message, `users.messages.send` and secret redaction. |
| `scripts/gmail_authorize.py` | **new.** A one-time browser consent. The token is saved **only** if the Google account is exactly `apiramedigit@gmail.com` and `gmail.send` was granted. |
| `scripts/performance_alert.py` | Transport-only edits: `validate_gmail`, the `message_id` field, and `10_gmail_config_validation.json`. The alert logic is unchanged. |
| `scripts/test_email_alert.py` | 32 safe tests. They use a fake Gmail/Google transport, fake tokens and temporary credential files, with no network and no real e-mail. |

## Credentials (outside the repository; paths only, never contents)
- `WTMA_GMAIL_CLIENT_FILE` (User env) = `C:\Users\LED 222\Downloads\client_secret_260911389240-….json`. This is the Desktop ("installed") OAuth client.
- `WTMA_GMAIL_TOKEN_FILE` (User env) = `C:\Users\LED 222\AppData\Roaming\WTMA\gmail_token.json`, written by `gmail_authorize.py`.
- The code refuses relative paths and any path inside the repository. `.gitignore` also covers `client_secret*.json`, `gmail_token*.json`, `.env` and `credentials.json`.
- Scopes:
  - `https://www.googleapis.com/auth/gmail.send` is the only Gmail scope.
  - `openid` + `email` are requested only so the code can prove the authorizing account is `apiramedigit@gmail.com`, because `gmail.send` alone cannot reveal the account.

## Message
- One Gmail message, From `apiramedigit@gmail.com`, To `apiramedigit@gmail.com, roshandigitweb@gmail.com`, with no Cc or Bcc.
- Subject: `⚠️ Full Optimization Review Required – ASIN {ASIN} – 2 Consecutive 7D Performance Drops`.
- The body is plain text plus HTML and carries the unchanged alert fields and Full Optimization warning.
- Header `X-WTMA-Alert-Key: wtma-foa-<ASIN>-<cycle_id>`.

## Duplicate protection
- `data/monitoring_cycles.json → alerts["ASIN|cycle_id"]` records the status, `alert_sent_at`, the recipients and the Gmail `message_id`.
- A SENT alert is never re-sent. A FAILED one is retried on the next run.
- Gmail has no idempotency key, so the local ledger is the guard.
- **Week 3+ (business instruction 2026-09-29, replaces the earlier per-cycle repeat):**
  - The cycle where a streak reaches 2 is the only alert cycle. The streak then **closes**, so there are no weekly repeat alerts while the old count would stay ≥ 2.
  - The ASIN stays monitored every week as *awaiting manual Full Optimization*, with count 0.
  - When the team finishes the optimization, they record it: `python scripts/performance_alert.py --record-optimization ASIN --date YYYY-MM-DD [--by NAME] [--note TEXT]`.
  - The recording is validated. The ASIN must have an open alert. The date must not be in the future and must not be before the alert cycle's end. Recording the same date again is a no-op.
  - The recorded date is the **new baseline**, exactly like live verification. The week containing it is a baseline week, the count restarts at 0 from the next week, and 2 new consecutive declines raise a **new** alert with a new cycle key.
  - A FAILED alert is retried on the next run under its original cycle key.

## Validation (`performance_alert.py --validate`, read-only, sends nothing)
The check is ready only when all of these hold:
- Google accepts the token.
- tokeninfo reports the account as `apiramedigit@gmail.com`, email verified.
- `gmail.send` has been granted.
- The token file is outside the repo.
- The Gmail API accepts the token: reading the profile returns HTTP 403 for *insufficient scope* rather than 401 or SERVICE_DISABLED.

The result is written to `10_gmail_config_validation.json`.

## Weekly run order
`extract.py → build_dataset.py → performance_alert.py [--send] → render.py → validate.py`

Live sending (`--send`) stays unused until the user approves the real test e-mail.

## Execution evidence (2026-09-29)
| Step | Result |
|---|---|
| `gmail_authorize.py`, attempt 1 | Google granted only openid/email because the gmail.send checkbox was left unticked. **The token was refused and not saved.** |
| `gmail_authorize.py`, attempt 2 | Saved. The account is `apiramedigit@gmail.com` and the scopes include `gmail.send`. The token is at `%APPDATA%\WTMA\gmail_token.json`, outside the repo. |
| `performance_alert.py --validate` | **ready = true**. Authorized account = `apiramedigit@gmail.com`, email verified, `gmail.send` granted, token outside the repo. The Gmail API probe returned HTTP 403 for insufficient scope, meaning the API is enabled and the token accepted. See `10_gmail_config_validation.json`. |
| `test_email_alert.py` | **32 PASS / 0 FAIL**. See `10_email_alert_test_results.json`. |
| Real-data dry run | Cycle 2026-09-13_2026-09-19: 2 tracked ASINs, **0 alerts due**, no e-mail. |
| Secret scan | The real access token, refresh token, client secret and Resend key were each found 0 times in the repo files, the git history and the run logs. There are 0 token-shaped strings apart from the FAKE test values. |
| Real e-mail | **Not sent.** It waits for the user's explicit approval. |
| TEST e-mail (user-approved, apiramedigit@gmail.com ONLY) | Sent 2026-09-29 11:47 via `scripts/send_test_email.py`. HTTP 200, Gmail message id `1a0ebcf45b96f251`, labels SENT/INBOX. The subject is prefixed [TEST] and carries a TEST banner. It is recorded in `10_gmail_test_email.json` only; the production alert log (`data/monitoring_cycles.json`) is unchanged with 0 entries. roshandigitweb@gmail.com was **not** e-mailed. |

## Re-authorization after moving the consent screen to In production (2026-09-29 14:12)
- `gmail_authorize.py`, attempt 1: the consent never reached the local listener, so no token was saved. The stale listener was stopped.
- Attempt 2 saved a token for `apiramedigit@gmail.com`: email verified, scopes include `gmail.send` (plus openid/email), and a **fresh refresh token** was issued.
- Google's token response had **no `refresh_token_expires_in`**, so this is not a limited-lifetime refresh token. That matches In production; there is no 7-day Testing expiry.
- The token is at `%APPDATA%\WTMA\gmail_token.json`, outside the repo.
- A forced access-token refresh using the new refresh token succeeded.
- `performance_alert.py --validate`: ready = true. The Gmail API probe returned 403 for insufficient scope, meaning the API is enabled and the token accepted.
- `test_email_alert.py`: 32 PASS / 0 FAIL.
- Secret scan: the access token, refresh token and client secret each appear 0 times in the repo, git history and task logs.
- No e-mail was sent, and the scheduler is still Disabled.
