# Manual Postman reproduction — WTMAFT live backend-keyword read (READ-ONLY)

Date: 2026-09-29. This is discovery and validation only. Only GET requests were made; there was no POST, PUT, PATCH or DELETE, and no code or production data was changed.

## 1. Existing WTMAFT code path
| Step | Code |
|---|---|
| Live keyword GET (the **only** implementation) | `scripts/keyword_live_verify.py` → `lm_listing(sub_source, pid)` (lines 54–76), constant `LM_API` |
| Used by the weekly check | `scripts/optimization_cleanup.py` → `ListingManagement.read()` imports `keyword_live_verify.lm_listing` |
| Keyword extraction | `optimization_cleanup._entries()`: `amz_platinum_keywords` sorted by `view_order`, then `id` → the list of `keyword` values |
| Identity check | `optimization_cleanup._identity()` |
| Cleaning | `keyword_finetune.finetune()`, unchanged |
| POST (not used here) | `keyword_live_update.post()` → `/api/edit-amazon-listing` |

**Source:** the value comes from the **Listing Management API**, not the PostgreSQL mirror. The ledsone `listings.*` mirror is only an audit value in `keyword_live_verify.py`, because it lags the tool.

## 2. Exact request
```
GET https://listings.vintageinterior.co.uk/api/get-all-active-listings-data?channel=amazon&account={sub_source}&per_page=1&next_page_id={listing_id - 1}
```
- **Method:** GET, via `urllib.request.Request(url, method="GET")`, timeout 60 s.
- **Required parameters** (per the API's own `instructions` block):
  - `channel=amazon`
  - `account`: 8 = amazon Ledsone, 6 = amazon Dcvoltage (per the `valid_accounts` block).
- **Optional parameters:**
  - `site` (UK, Germany, …)
  - `fulfilment`
  - `per_page`: 1–1000, default 50; `limitations.max_per_page` = 500.
  - `next_page_id`
- **Marketplace:** WTMAFT does **not** send `site`. UK is enforced after the response, by requiring `data[0].site == "UK"`, together with the exact listing id, ASIN, SKU and account.
  - Verified 2026-09-29: adding `&site=UK` returned an **identical** `data[0]` for both test listings.
  - The existing n8n workflows do send `site=UK`.
- **Pagination:** a cursor. The response has `pagination.next_page_id`, `has_more`, `total_count` and `current_page_id`, and returns ids **greater than** `next_page_id`.
- **Targeting one listing:** WTMAFT uses `per_page=1&next_page_id=<listing id − 1>`, which returns exactly that listing. It accepts the result only if `data[0].id == listing id`; otherwise it treats the listing as "not in the active feed".
- **Rate limit:** the API documents 100 requests/min and 5 concurrent requests. In practice WTMAFT saw HTTP 429 after about 7 fast calls, so it paces 6 s between calls and retries a 429 after 20/40/60/90 s.
- **Authentication:** **none.** WTMAFT sends no header, cookie, token or API key. All 6 existing n8n nodes calling this endpoint also use `authentication: none`, no headers and no credentials. There is no secret involved.

## 3. Response field
- `data[0].amz_platinum_keywords` is a list of `{id, product_id, keyword, view_order, which_channel}`.
- Sort by `view_order`, then `id`, and join the `keyword` values. That is the live backend-keyword value WTMAFT uses.
- The identity fields are in the same object: `id` (listing id), `item_id` (ASIN), `sku`, `site`, and `channel` (for example `"amazon Ledsone"`).

## 4. Identity match (`optimization_cleanup._identity`)
The row is used only if all of these hold:
- `item_id` = ASIN
- `sku` = SKU
- `site` = "UK"
- `channel` = account name
- `id` = listing id

Otherwise the result is Read Failed and there is no POST.

## 5. Postman (read-only)

**Ledsone (account 8), exact WTMAFT form:**
- Method `GET`.
- URL: `https://listings.vintageinterior.co.uk/api/get-all-active-listings-data`
- Params: `channel=amazon`, `account=8`, `per_page=1`, `next_page_id=836393` (B0DH4KYFPD's listing id 836394 minus 1).
- Optional: `site=UK` (verified to give the same row).
- Auth tab: **No Auth**. Headers: none needed.

**Dcvoltage (account 6):** same as above with `account=6`, `next_page_id=604365` (B0D59MHSXN, listing id 604366).

**Browsing all UK listings for one account:** `channel=amazon&account=8&site=UK&per_page=500`. Then repeat with `&next_page_id=<pagination.next_page_id>` while `pagination.has_more` is `true`.

**Where to look:** in Body → Pretty, look at `data` → `[0]` → `amz_platinum_keywords` → each `keyword`. For identity, check `data[0].item_id`, `sku`, `site`, `channel` and `id`.

**Finding one ASIN/SKU in a page:** paste this into the Postman *Scripts → Post-response* tab. It has no secrets and only reads the response.
```javascript
const want = { asin: "B0DH4KYFPD", sku: "WCDTBM2PK+RPR44WH2PK" };
const row = (pm.response.json().data || []).find(r => r.item_id === want.asin && r.sku === want.sku);
if (row) {
  const kw = (row.amz_platinum_keywords || []).sort((a, b) => (+a.view_order - +b.view_order) || (a.id - b.id))
                                                .map(k => k.keyword).join(" ");
  console.log({ id: row.id, asin: row.item_id, sku: row.sku, site: row.site, channel: row.channel, backend_keywords: kw });
} else {
  console.log("not on this page - request the next page with next_page_id =", pm.response.json().pagination.next_page_id);
}
```
Send one request at a time, a few seconds apart, to avoid HTTP 429.

## 6. WTMAFT vs Postman
They are **functionally equivalent**: same endpoint, same method, same parameters, and no authentication in either.
- `lm_listing()` returned **exactly** the same `data[0]` as a direct GET of the same URL, for Ledsone B0DH4KYFPD and Dcvoltage B0D59MHSXN.
- Adding `site=UK` changed nothing for these UK listings.
- The API's own examples use the alias path `/api/getAllActiveListingsData`, but WTMAFT and n8n use `/api/get-all-active-listings-data`, which returned HTTP 200. The alias path was not tested.

## 7. Conclusion: **PASS**
The existing GET implementation, endpoint, parameters, pagination, response field, identity rules and (absence of) authentication are identified from repository code, existing n8n workflows and the API's own response metadata. Postman reproduces the same request. Only GETs were sent (about 7 in total), with no mutation.
