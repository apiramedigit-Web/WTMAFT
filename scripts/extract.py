"""Read-only extract from ledsone for the Weekly Top-Moving ASIN report.

Scope: Amazon UK (business_reports.market_place = 23), accounts
6 = amazon Dcvoltage, 8 = amazon Ledsone (the only accounts present in
amz_catalog_performance_data, the sole source of ASIN impressions/clicks).

Weeks follow the Search Catalog Performance buckets (Sun -> Sat), so orders,
impressions and clicks cover exactly the same 7 days.

DSN: WLP_SOURCE_DB_URL (ledsone, tech_user). SELECT only.
"""
import datetime as dt
import json
import os
import pathlib

import psycopg

BASE = pathlib.Path(__file__).resolve().parent.parent
OUT = BASE / "data" / "extract.json"
MARKET_PLACE = 23
ACCOUNTS = {6: "amazon Dcvoltage", 8: "amazon Ledsone"}
HISTORY_WEEKS = 4  # weekly orders kept for context / evidence


CHANGE_RECORDS = pathlib.Path(os.environ.get("WTMA_CHANGE_RECORDS", BASE / "data" / "change_records.json"))


def load_saved_records():
    """User-confirmed change records exported from the report (Section 7 -> Export)."""
    if not CHANGE_RECORDS.exists():
        return []
    data = json.loads(CHANGE_RECORDS.read_text(encoding="utf-8"))
    return data.get("records", data) if isinstance(data, dict) else data


def q(cur, sql, params=()):
    cur.execute(sql, params)
    cols = [d.name for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def main():
    with psycopg.connect(os.environ["WLP_SOURCE_DB_URL"]) as con:
        con.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        cur = con.cursor()

        # Latest complete catalog week present for BOTH accounts.
        cur_start = cur.execute(
            """SELECT max(start_date) FROM (
                 SELECT start_date FROM business_reports.amz_catalog_performance_data
                 WHERE market_place=%s AND sub_source = ANY(%s)
                   AND end_date - start_date = 6
                 GROUP BY start_date HAVING count(DISTINCT sub_source) = %s) w""",
            (MARKET_PLACE, list(ACCOUNTS), len(ACCOUNTS)),
        ).fetchone()[0]
        prev_start = cur_start - dt.timedelta(days=7)
        hist_start = cur_start - dt.timedelta(days=7 * (HISTORY_WEEKS - 1))
        cur_end = cur_start + dt.timedelta(days=6)

        weekly_orders = q(cur, """
            SELECT child_asin AS asin, sub_source,
                   (%s::date + 7 * floor((date - %s::date) / 7.0)::int) AS week_start,
                   sum(total_order_items) AS orders, sum(units_ordered) AS units,
                   sum(sessions) AS sessions, count(*) AS day_rows
            FROM business_reports.amz_sales_and_traffic_by_asin
            WHERE market_place=%s AND sub_source = ANY(%s)
              AND date BETWEEN %s AND %s
            GROUP BY 1,2,3""",
            (hist_start, hist_start, MARKET_PLACE, list(ACCOUNTS), hist_start, cur_end))

        catalog = q(cur, """
            SELECT asin, sub_source, start_date AS week_start, count(*) AS n_rows,
                   sum(impression_count) AS impressions, sum(click_count) AS clicks,
                   sum(purchase_count) AS search_purchases
            FROM business_reports.amz_catalog_performance_data
            WHERE market_place=%s AND sub_source = ANY(%s)
              AND start_date IN (%s, %s) AND end_date - start_date = 6
            GROUP BY 1,2,3""",
            (MARKET_PLACE, list(ACCOUNTS), prev_start, cur_start))

        daily_totals = q(cur, """
            SELECT date, sub_source, sum(total_order_items) AS orders, sum(sessions) AS sessions
            FROM business_reports.amz_sales_and_traffic_by_asin
            WHERE market_place=%s AND sub_source = ANY(%s) AND date BETWEEN %s AND %s
            GROUP BY 1,2 ORDER BY 1,2""",
            (MARKET_PLACE, list(ACCOUNTS), prev_start, cur_end))

        # --- Daily order series for the 7-Day Monitoring Report (Section 8) ----------------
        # Window: 9 weeks back from the current week, extended further back if a saved
        # change record (data/change_records.json) has an older Date Changed (needs D-7).
        saved = load_saved_records()
        daily_from = cur_start - dt.timedelta(days=63)
        for r in saved:
            if r.get("date_changed"):
                daily_from = min(daily_from, dt.date.fromisoformat(r["date_changed"]) - dt.timedelta(days=7))
        daily_orders = q(cur, """
            SELECT child_asin AS asin, date, sum(total_order_items) AS orders
            FROM business_reports.amz_sales_and_traffic_by_asin
            WHERE market_place=%s AND sub_source = ANY(%s) AND date >= %s
            GROUP BY 1,2 HAVING sum(total_order_items) > 0""",
            (MARKET_PLACE, list(ACCOUNTS), daily_from))
        # A day counts as loaded only when BOTH accounts have Business Report rows for it.
        day_coverage = q(cur, """
            SELECT date, count(DISTINCT sub_source) AS accounts
            FROM business_reports.amz_sales_and_traffic_by_asin
            WHERE market_place=%s AND sub_source = ANY(%s) AND date >= %s
            GROUP BY 1 ORDER BY 1""",
            (MARKET_PLACE, list(ACCOUNTS), daily_from))

        asins = sorted({r["asin"] for r in weekly_orders if r["asin"]})

        listings = q(cur, """
            SELECT l.id AS product_id, l.asin, l.sku, l.sub_source, l.status, l.is_parent,
                   l.wrong_sku, l.is_ended, l.title, l.updated_at
            FROM listings.amazon_listings l
            WHERE l.site='UK' AND l.sub_source = ANY(%s) AND l.asin = ANY(%s)""",
            (list(ACCOUNTS), asins))

        pids = [r["product_id"] for r in listings]
        keywords = q(cur, """
            SELECT product_id, view_order, keyword
            FROM listings.amazon_listing_search_engine_keywords
            WHERE product_id = ANY(%s) ORDER BY product_id, view_order, id""", (pids,))

        issues = q(cur, """
            SELECT asin, sku, sub_source_id AS sub_source, severity, code,
                   left(message, 200) AS message, attribute_names
            FROM listings.amazon_listing_issues
            WHERE sub_source_id = ANY(%s) AND asin = ANY(%s)
              AND marketplace_id = 'A1F83G8C2ARO7P'""",
            (list(ACCOUNTS), asins))

    payload = {
        "extracted_at": dt.datetime.now().isoformat(timespec="seconds"),
        "market_place": MARKET_PLACE,
        "accounts": ACCOUNTS,
        "current_week": [cur_start.isoformat(), cur_end.isoformat()],
        "previous_week": [prev_start.isoformat(), (prev_start + dt.timedelta(days=6)).isoformat()],
        "history_start": hist_start.isoformat(),
        "weekly_orders": weekly_orders,
        "catalog": catalog,
        "daily_totals": daily_totals,
        "listings": listings,
        "keywords": keywords,
        "listing_issues": issues,
        "daily_from": daily_from.isoformat(),
        "daily_orders": daily_orders,
        "day_coverage": day_coverage,
    }
    OUT.write_text(json.dumps(payload, default=str, indent=1), encoding="utf-8")
    print({k: len(v) for k, v in payload.items() if isinstance(v, list)})
    print("current", payload["current_week"], "previous", payload["previous_week"])


if __name__ == "__main__":
    main()
