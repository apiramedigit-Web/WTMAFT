# 08 — Listing Management → PostgreSQL refresh: discovery (read-only)

Run date: 2026-09-28. Read-only: SELECTs on ledsone, local file searches and `schtasks /query`. No POST, PUT, PATCH or DELETE was sent, no data was changed, and nothing was triggered.

## Findings

| Question | Finding | Evidence |
|---|---|---|
| Sync mechanism | Server-side, inside the Listing Management Tool (`listings.vintageinterior.co.uk`, Apache/PHP 8.2 on 207.148.78.148). Its source code is not on this machine. | See the searches below |
| Source → destination | Amazon (Seller Central listings for 7 accounts) → Listing Management Tool → ledsone `listings.amazon_listings` and `listings.amazon_listing_search_engine_keywords` | 7 distinct `sub_source` values refreshed on 2026-09-28 |
| Schedule | **Daily at about 03:00 UTC (08:30 Sri Lanka).** Every day from 9 Sep to 28 Sep has updates between 03:00 and 03:03 UTC (02:30 on 16 Sep). | `amazon_listings.updated_at` by day, below |
| Last run | **2026-09-28, 03:03:07 → 03:15:14 UTC. It touched 127,084 of 135,349 rows across 7 accounts** (a full reload). Earlier days touched 1–206 rows, because `updated_at` only moves when a row's data changes. | as above |
| The 22 submitted listings | Last refreshed 2026-09-28 03:03:07–03:03:21 UTC, which is **before** the POSTs (run 1 and run 2, later the same day) | `evidence/07_live_keyword_verification.json` (`listing_updated_at`) |
| Expected next run | **2026-09-29 around 03:00 UTC (08:30 Sri Lanka)**, inferred from the pattern; no schedule definition could be read | inferred |
| Existing trigger command or workflow | **None found.** The documented API has no sync or refresh endpoint. There is no n8n workflow or node that refreshes Amazon listings, no local Windows scheduled task, and no job, queue, sync or log table in ledsone. | See the searches below |
| Safe to trigger manually? | **No approved trigger exists, so nothing can be triggered.** Only the tool's owner or the server can run it. | — |

`amazon_listings.updated_at`, last 60 days (UTC): 09-09 03:01 (7,792 rows) · 09-10…09-27 daily at 03:00–03:02 (1–206 rows, except 09-16 at 02:30) · **09-28 03:03–03:15 (127,084 rows, 7 accounts)**.

Keyword table: 190,242 rows. Ids run from 77,078 to 287,900 with gaps, so rows are deleted and re-inserted on refresh. There is no timestamp column. The keyword row for B0DH4KYFPD (id 217,138) was not recreated on 28 Sep, which is consistent with its keywords not having changed before the POST.

## Searches performed

- **API endpoints** documented in `Downloads\*.md/.json/.csv/.tsv`: 17 `listings.vintageinterior.co.uk/api/*` endpoints. They are all `get-*` data feeds, plus `edit-amazon-listing` and `update-shopify-listing-auto`. None is a sync or refresh endpoint.
- **n8n** (`N8N_Registry_Final_-_5-Node_Registry_updated.tsv` and the workflow registry): no node writes `amazon_listings` or search-engine keywords, and none refreshes Amazon listings. The KOIB/KOIBM/UAAT workflows only POST edits.
- **Windows Task Scheduler** (`schtasks /query`): no task matching amazon, listing, sync or keyword.
- **Local code** (every project folder under the user's home; py, php, js, sql, ps1, json, md): the only references to these tables are readers (this project, SKU_Negative_Feedback_Monitor, WLPAF discovery, the PDTM explorer). There are no writers.
- **ledsone catalog**: no table named like sync, job, cron, queue, feed, submission or import.

## Open risk for verification

The PDTM Business Database Table Explorer describes `amazon_listing_search_engine_keywords` as "search engine keywords (**platinum keywords**) per listing". The edit API sets `backend_keywords`. If the table holds a different Amazon attribute (platinum keywords rather than search terms, the `generic_keyword` field), then the refresh would never show our change.
- A re-check after the next refresh settles it.
- If B0DH4KYFPD still shows the original keywords after the 2026-09-29 03:00 UTC run, verification needs another read source: the tool's own listing page, or Amazon's listing view. That question should go to the tool's owner.
- A listing that stays unchanged must not be treated as "update failed" until that is resolved.

## Recommended next action

1. Do not trigger anything, because there is no approved trigger. Do not re-send any POST.
2. After 2026-09-29 around 03:20 UTC (08:50 Sri Lanka), run the read-only check `python scripts/keyword_live_verify.py`.
3. If the listings show the proposed keywords, mark them UPDATED / VERIFIED and write the change records (Status Monitoring, Date Changed = the actual POST timestamp).
4. If the listings still show the original keywords after that refresh, confirm with the tool's owner which Amazon attribute the keyword table stores, and how to read `backend_keywords` / `generic_keyword` back.
