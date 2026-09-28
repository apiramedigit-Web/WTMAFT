"""Validate the report dataset and standalone HTML. Writes evidence/validation_results.json.

Independent of build_dataset.py: raw metrics are re-aggregated from data/extract.json and a
sample of ASINs is re-queried live from ledsone (read-only) to prove the extract itself.
"""
import collections
import datetime as dt
import json
import math
import os
import pathlib
import random
import re

import psycopg

BASE = pathlib.Path(__file__).resolve().parent.parent
EXTRACT = json.loads((BASE / "data" / "extract.json").read_text(encoding="utf-8"))
DS = json.loads((BASE / "data" / "report_dataset.json").read_text(encoding="utf-8"))
HTML_PATH = BASE / "output" / "weekly_top_moving_asin_backend_keyword_fine_tuning_report.html"
OUT = BASE / "evidence" / "validation_results.json"

results = []


def check(name, ok, detail=""):
    results.append({"check": name, "result": "PASS" if ok else "FAIL", "detail": str(detail)[:600]})


def pct(c, p):
    return None if c is None or p in (None, 0) else round((c - p) / p * 100, 1)


def rate(n, d):
    return None if n is None or d in (None, 0) else n / d * 100


M, P, K = DS["meta"], DS["performance"], DS["keyword_analysis"]
cw, pw = M["current_7d"][0], M["previous_7d"][0]

# ---- date logic ----------------------------------------------------------------------------
c0, c1 = (dt.date.fromisoformat(x) for x in M["current_7d"])
p0, p1 = (dt.date.fromisoformat(x) for x in M["previous_7d"])
check("13. Date ranges: 7 days each, contiguous, Sunday start",
      (c1 - c0).days == 6 and (p1 - p0).days == 6 and (c0 - p1).days == 1 and c0.weekday() == 6,
      f"current {c0}->{c1}, previous {p0}->{p1}")
days = collections.defaultdict(set)
for r in M["daily_totals"]:
    days[r["sub_source"]].add(r["date"][:10])
check("13b. Daily order data present for all 14 days, both accounts",
      all(len(v) == 14 for v in days.values()) and len(days) == 2,
      {k: len(v) for k, v in days.items()})

# ---- independent re-aggregation from raw extract ---------------------------------------------
orders = collections.defaultdict(lambda: collections.defaultdict(int))
for r in EXTRACT["weekly_orders"]:
    orders[r["asin"]][r["week_start"]] += int(r["orders"] or 0)
cat = {(r["asin"], r["week_start"]): r for r in EXTRACT["catalog"]}
base_weeks = M["baseline_weeks"]
ranked = sorted((a for a in orders if sum(orders[a].get(w, 0) for w in base_weeks) > 0),
                key=lambda a: (-sum(orders[a].get(w, 0) for w in base_weeks), -orders[a].get(pw, 0),
                               -(int(cat[(a, pw)]["impressions"]) if (a, pw) in cat else 0), a))
check("Top-moving selection reproduces independently",
      [r["asin"] for r in P] == ranked[:M["top_moving_n"]], f"{len(P)} rows")

mism = []
for r in P:
    a = r["asin"]
    po, co = orders[a].get(pw, 0), orders[a].get(cw, 0)
    pc, cc = cat.get((a, pw)), cat.get((a, cw))
    pi, ci = (int(pc["impressions"]) if pc else None), (int(cc["impressions"]) if cc else None)
    pk, ck = (int(pc["clicks"]) if pc else None), (int(cc["clicks"]) if cc else None)
    exp = {"prev_orders": po, "curr_orders": co, "order_chg": pct(co, po),
           "prev_impressions": pi, "curr_impressions": ci, "impression_chg": pct(ci, pi),
           "prev_clicks": pk, "curr_clicks": ck, "click_chg": pct(ck, pk),
           "ctr_chg": pct(rate(ck, ci), rate(pk, pi)), "cvr_chg": pct(rate(co, ck), rate(po, pk))}
    for k, v in exp.items():
        if r[k] != v:
            mism.append((a, k, r[k], v))
check("17. KPI per-ASIN recomputation (orders, impressions, clicks, CTR, CVR, % changes)",
      not mism, mism[:5] or "all 50 x 11 values match")

check("12. Division by zero -> null (never 0/NaN/Inf)",
      all(r["order_chg"] is None for r in P if r["prev_orders"] == 0)
      and all(r["impression_chg"] is None for r in P if not r["prev_impressions"]),
      f"prev_orders=0 rows: {sum(1 for r in P if r['prev_orders'] == 0)}")

bad_num = [k for r in P for k, v in r.items() if isinstance(v, float) and not math.isfinite(v)]
raw = (BASE / "data" / "report_dataset.json").read_text(encoding="utf-8")
check("6/7. No NaN / Infinity in dataset", not bad_num and not re.search(r"\b(NaN|Infinity)\b", raw))

check("14. ASIN unique at report grain", len({r["asin"] for r in P}) == len(P), f"{len(P)} rows")

# ---- SKU / keyword mapping -------------------------------------------------------------------
lst = {l["product_id"]: l for l in EXTRACT["listings"]}
bad_sku = [r["asin"] for r in P if not r["skus"] or any(
    not any(l["asin"] == r["asin"] and l["sku"] == s for l in EXTRACT["listings"]) for s in r["skus"])]
check("15. SKU mapping: every SKU is a UK listing row of that ASIN", not bad_sku, bad_sku or "50/50")
bad_kw = [k["asin"] for k in K if k.get("product_id") and (
    lst[k["product_id"]]["asin"] != k["asin"] or lst[k["product_id"]]["sku"] != k["sku"])]
check("16. Keyword rows map to the correct ASIN/SKU (via product_id)", not bad_kw, bad_kw or f"{len(K)} rows")

kw_src = collections.defaultdict(list)
for x in EXTRACT["keywords"]:
    kw_src[x["product_id"]].append(x["keyword"])
principle = []
for k in K:
    if not k.get("original"):
        continue
    orig_words = [w for e in kw_src[k["product_id"]] if e for w in e.split()]
    norm = lambda w: re.sub(r"^\W+|\W+$", "", w).casefold()
    if {norm(w) for w in orig_words if norm(w)} != {norm(w) for w in k["cleaned"].split() if norm(w)}:
        principle.append((k["asin"], "unique word lost/added"))
    if len(k["cleaned"].split()) + k["duplicate_words_removed"] + sum(len(e.split()) for e in k["repeated_entries"]) != len(orig_words):
        principle.append((k["asin"], "word accounting"))
check("Keyword principle: every unique original word kept, nothing added, only duplicates removed",
      not principle, principle[:5] or f"{sum(1 for k in K if k.get('original'))} fields verified")

# ---- reconciliation --------------------------------------------------------------------------
kpi = DS["kpi"]
review = [k for k in K if k["needs_change"]]
recon = {
    "top_moving_reviewed": len(P),
    "performance_drop": sum(1 for r in P if r["curr_orders"] < r["prev_orders"]),
    "keyword_review_required": len({k["asin"] for k in review}),
    "finetune_proposed": len(review),
    "duplicate_words_removed": sum(k["duplicate_words_removed"] for k in review),
    "under_monitoring": len({s["asin"] for s in DS["saved_change_records"] if s["status"] == "Monitoring"}),
}
check("12. KPI summary reconciles to dataset rows", all(kpi[k] == v for k, v in recon.items()), recon)
CRS = DS["change_record"]
check("Change Record rows = proposed changes (+ saved records carried forward), keys unique",
      [c["key"] for c in CRS if not c["carried_forward"]] == [f'{k["asin"]}|{k["sku"]}' for k in review]
      and len({c["key"] for c in CRS}) == len(CRS), f"{len(CRS)} rows")

# ---- no fabricated status --------------------------------------------------------------------
check("18/19. Build never sets an upload / change date / monitoring status (only user saves do)",
      all(c["date_changed"] is None and c["status"] == "Proposed" for c in CRS),
      f"{len(CRS)} change records, all Proposed; {len(DS['saved_change_records'])} user-saved record(s) embedded")
MDv = DS["monitoring_data"]
av = MDv["available_dates"]
contig = all((dt.date.fromisoformat(b) - dt.date.fromisoformat(a)).days == 1 for a, b in zip(av, av[1:]))
wk = [d.isoformat() for d in (c0 + dt.timedelta(days=i) for i in range(7))]
daily_vs_weekly = [(r["asin"], sum(MDv["orders"][r["asin"]].get(d, 0) for d in wk), r["curr_orders"])
                   for r in P if r["asin"] in MDv["orders"]]
check("17b. Daily monitoring series reconciles to the weekly Current 7D orders (same source)",
      all(a == b for _, a, b in daily_vs_weekly) and len(daily_vs_weekly) == len(MDv["orders"]),
      [x for x in daily_vs_weekly if x[1] != x[2]][:3] or f"{len(daily_vs_weekly)} ASINs match")
check("17c. Loaded days are contiguous and cover both comparison weeks",
      contig and av[0] <= p0.isoformat() and av[-1] >= c1.isoformat(), f"{av[0]} -> {av[-1]} ({len(av)} days)")
check("20. No fabricated thresholds: drop rule is orders < previous (no numeric cut-off)",
      all((r["issue_detected"] != "No Drop") == (r["curr_orders"] < r["prev_orders"]) for r in P))

# ---- live DB spot-check (read-only) ----------------------------------------------------------
sample = random.Random(20260925).sample([r["asin"] for r in P], 8)
with psycopg.connect(os.environ["WLP_SOURCE_DB_URL"]) as con:
    con.execute("SET TRANSACTION READ ONLY")
    live = dict(((a, w), (int(o), )) for a, w, o in con.execute(
        """SELECT child_asin, CASE WHEN date >= %s THEN 'c' ELSE 'p' END, sum(total_order_items)
           FROM business_reports.amz_sales_and_traffic_by_asin
           WHERE market_place=23 AND sub_source IN (6,8) AND child_asin = ANY(%s)
             AND date BETWEEN %s AND %s GROUP BY 1,2""", (c0, sample, p0, c1)).fetchall())
    livec = {(a, str(s)): (int(i), int(k)) for a, s, i, k in con.execute(
        """SELECT asin, start_date, impression_count, click_count FROM business_reports.amz_catalog_performance_data
           WHERE market_place=23 AND asin = ANY(%s) AND start_date IN (%s,%s)""", (sample, p0, c0)).fetchall()}
spot = []
for a in sample:
    r = next(x for x in P if x["asin"] == a)
    if live.get((a, "p"), (0,))[0] != r["prev_orders"] or live.get((a, "c"), (0,))[0] != r["curr_orders"]:
        spot.append((a, "orders"))
    if livec.get((a, str(p0)), (None, None))[0] != r["prev_impressions"] or livec.get((a, str(c0)), (None, None))[1] != r["curr_clicks"]:
        spot.append((a, "catalog"))
check("Live DB spot-check (8 random ASINs: orders, impressions, clicks)", not spot, spot or sample)

# ---- HTML static checks ----------------------------------------------------------------------
html = HTML_PATH.read_text(encoding="utf-8")
check("1. HTML file exists", HTML_PATH.exists(), f"{HTML_PATH.stat().st_size} bytes")
ext = re.findall(r"<(?:script|link|img|iframe)[^>]+(?:src|href)\s*=\s*[\"']?(?:https?:)?//", html, re.I)
check("9/10/11. No external CSS/JS/network references", not ext and "@import" not in html and "fetch(" not in html, ext)
m = re.search(r'<script id="report-data" type="application/json">(.*?)</script>', html, re.S)
try:
    embedded = json.loads(m.group(1))
    ok = embedded == DS
except Exception as e:  # noqa: BLE001
    ok, embedded = False, e
check("8. Embedded JSON parses and equals dataset", ok)

REQUIRED = {
    "sections": ["Report Purpose", "Weekly Performance Summary", "ASIN Performance Comparison",
                 "Backend Keyword Analysis", "Keyword Change Details", "Optimization Classification",
                 "Change Record", "7-Day Monitoring Report", "Weekly Workflow", "KPI Formulas", "Management Summary"],
    "t3": ["ASIN", "SKU", "Previous 7D Orders", "Current 7D Orders", "Order Change %", "Impression Change %",
           "Click Change %", "CTR Change %", "CVR Change %", "Issue Detected"],
    "t4": ["ASIN", "Content Status", "Backend Keyword Status", "Issue Found", "Action Taken"],
    "t5": ["ASIN", "Original Backend Keywords", "Issue", "Fine-Tuning Action", "Final Status"],
    "t6": ["Change Type", "Applied?", "Description"],
    "t7": ["ASIN", "SKU", "Issue Detected", "Optimization Action", "Change Type", "Date Changed", "Status"],
    "t8": ["ASIN", "Pre-Change Orders", "Post-Change Orders", "Order Change %", "Pre-Change Impressions",
           "Post-Change Impressions", "Impression Change %", "Result"],
    "kpis": ["Top-Moving ASINs Reviewed", "ASINs Showing Performance Drop", "ASINs Requiring Backend Keyword Review",
             "Backend Keyword Fine-Tuning Completed", "Visible Listing Content Changed", "Duplicate Keywords Removed",
             "ASINs Under Monitoring", "Monitoring Period", "Next Review"],
    "workflow": ["Detect", "Compare", "Check Content", "Review Keywords", "Fine-Tune", "Upload", "Monitor", "Measure"],
}

# ---- browser checks --------------------------------------------------------------------------
from playwright.sync_api import sync_playwright  # noqa: E402

with sync_playwright() as pw_:
    br = pw_.chromium.launch()
    pg = br.new_page(viewport={"width": 1366, "height": 900})
    errors, requests = [], []
    pg.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.on("request", lambda r: requests.append(r.url) if not r.url.startswith(("file:", "data:", "about:")) else None)
    pg.goto(HTML_PATH.as_uri())
    pg.wait_for_load_state("load")
    check("2. Page loads with no JavaScript console errors", not errors, errors[:3])
    check("11b. No network requests at runtime", not requests, requests[:3])

    h2 = pg.eval_on_selector_all("section h2", "e => e.map(x => x.textContent)")
    check("3. Required sections present", all(any(s in h for h in h2) for s in REQUIRED["sections"]), h2)
    missing = {}
    for t in ["t3", "t4", "t5", "t6", "t7", "t8"]:
        heads = pg.eval_on_selector_all(f"#{t} thead th", "e => e.map(x => x.textContent.trim())")
        miss = [c for c in REQUIRED[t] if c not in heads]
        if miss:
            missing[t] = miss
    labels = pg.eval_on_selector_all("#kpis .kpi .l", "e => e.map(x => x.textContent)")
    if [c for c in REQUIRED["kpis"] if c not in labels]:
        missing["kpis"] = [c for c in REQUIRED["kpis"] if c not in labels]
    steps = pg.eval_on_selector_all("#flow .step .t", "e => e.map(x => x.textContent)")
    if steps != REQUIRED["workflow"]:
        missing["workflow"] = steps
    check("4/5. Required columns, KPI names and workflow steps present (exact terminology)", not missing, missing)

    js_kpi = pg.evaluate("window.__REPORT_KPI__")
    shown = {l: pg.inner_text(f'#kpis .v[data-kpi="{l}"]') for l in REQUIRED["kpis"]}
    check("12b. KPI cards (computed in page) = dataset KPI",
          all(js_kpi[k] == kpi[k] for k in js_kpi if k in kpi) and shown["ASINs Showing Performance Drop"] == str(kpi["performance_drop"])
          and shown["Top-Moving ASINs Reviewed"] == str(kpi["top_moving_reviewed"]), shown)

    n = lambda sel: pg.locator(sel).count()
    drops = kpi["performance_drop"]
    counts = {"t3 (drop filter)": (n("#t3 tbody tr"), drops), "t4": (n("#t4 tbody tr"), len(K)),
              "t5": (n("#t5 tbody tr"), len(K)), "t6": (n("#t6 tbody tr"), 9),
              "t7": (n("#t7 tbody tr"), len(DS["change_record"])),
              "t8": (n("#t8 tbody tr:not(:has(td.empty))"),
                     sum(1 for s in DS["saved_change_records"] if s["status"] != "Proposed"))}
    check("Displayed row counts = dataset row counts", all(a == b for a, b in counts.values()), counts)

    # textContent of page elements (includes hidden tab panels, excludes <script> source)
    body = pg.evaluate("[...document.querySelectorAll('header, nav, section, footer')].map(e => e.textContent).join(' ')")
    check("6b. No NaN / Infinity / undefined rendered", not re.search(r"\b(NaN|Infinity|undefined|null)\b", body))

    struck = pg.eval_on_selector_all("#t5 tbody tr", "rows => rows.map(r => r.querySelectorAll('del.rm').length)")
    t5_rows = pg.eval_on_selector_all("#t5 tbody tr td:first-child", "e => e.map(x => x.textContent)")
    exp_struck = [k["duplicate_words_removed"] for k in K]
    check("Struck-through words in Keyword Change Details = removed-word counts", struck == exp_struck,
          list(zip(t5_rows, struck, exp_struck))[:4])

    # Tabs: each tab click shows exactly its own section and does not scroll the page.
    tab_ids = pg.eval_on_selector_all("nav.toc a", "e => e.map(a => a.getAttribute('href').slice(1))")
    tab_fail = []
    for tid in tab_ids:
        pg.evaluate("window.scrollTo(0,0)")
        pg.click(f'nav.toc a[href="#{tid}"]')
        vis = pg.eval_on_selector_all("section", "e => e.filter(s => s.offsetParent !== null).map(s => s.id)")
        sel = pg.eval_on_selector_all('nav.toc a[aria-selected="true"]', "e => e.map(a => a.getAttribute('href'))")
        if vis != [tid] or sel != ["#" + tid] or pg.evaluate("window.scrollY") != 0:
            tab_fail.append((tid, vis, sel, pg.evaluate("window.scrollY")))
    check("Tabs: clicking each tab shows only that section, no page scroll", not tab_fail and len(tab_ids) == 12,
          tab_fail[:3] or f"{len(tab_ids)} tabs")

    pg.click('nav.toc a[href="#s3"]')
    pg.select_option("#f3", "all")
    all_rows = n("#t3 tbody tr")
    pg.fill("#q3", P[0]["asin"])
    one = n("#t3 tbody tr")
    pg.fill("#q3", "")
    pg.click("#t3 thead th:nth-child(5)")
    vals = pg.eval_on_selector_all("#t3 tbody tr td:nth-child(5)", "e => e.map(x => x.textContent)")
    nums = [float(v.replace("%", "").replace("+", "")) for v in vals if v.strip() not in ("—", "")]
    check("Controls: filter, search and sort work",
          all_rows == len(P) and one == 1 and (nums == sorted(nums) or nums == sorted(nums, reverse=True)),
          f"all={all_rows}, search=1->{one}, sorted={len(nums)} values")

    overflow = {}
    pg.select_option("#f3", "all")
    widths = (1920, 1366, 1024, 800, 390)
    for w in widths:
        pg.set_viewport_size({"width": w, "height": 900})
        for tid in tab_ids:
            pg.evaluate(f"window.__showTab__('{tid}', false)")
            overflow[f"{w}px {tid}"] = pg.evaluate(
                "Math.max(document.documentElement.scrollWidth - document.documentElement.clientWidth,"
                " ...[...document.querySelectorAll('section.active .tbl')].map(f => f.scrollWidth - f.clientWidth), 0)")
    check("No horizontal scrollbar (page or table) on any tab at 1920/1366/1024/800/390px",
          all(v <= 0 for v in overflow.values()),
          {k: v for k, v in overflow.items() if v > 0} or f"all {len(overflow)} tab x width combinations = 0")
    pg.set_viewport_size({"width": 1366, "height": 900})
    pg.goto(HTML_PATH.as_uri())
    ev = BASE / "evidence"
    for tid, name in (("s2", "screenshot_summary.png"), ("s3", "screenshot_desktop.png"), ("s5", "screenshot_keywords.png")):
        pg.click(f'nav.toc a[href="#{tid}"]')
        pg.screenshot(path=str(ev / name), full_page=False)
    pg.emulate_media(color_scheme="dark")
    pg.click('nav.toc a[href="#s3"]')
    pg.screenshot(path=str(ev / "screenshot_dark.png"), full_page=False)
    br.close()

OUT.write_text(json.dumps({"validated_at": dt.datetime.now().isoformat(timespec="seconds"),
                           "passed": sum(r["result"] == "PASS" for r in results),
                           "failed": sum(r["result"] == "FAIL" for r in results), "checks": results},
                          indent=1, ensure_ascii=False), encoding="utf-8")
for r in results:
    print(f"{r['result']}  {r['check']}" + ("" if r["result"] == "PASS" else f"  -> {r['detail']}"))
print(f"\n{sum(r['result'] == 'PASS' for r in results)} PASS / {sum(r['result'] == 'FAIL' for r in results)} FAIL")
