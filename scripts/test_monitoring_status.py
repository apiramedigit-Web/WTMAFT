"""Tests for the Week 1 status (build_dataset.monitoring_status) and the Section 3 "Issue Detected" category.

Business rule (2026-10-06): for EVERY top-moving ASIN, its Week 1 orders (29 Sep..5 Oct, actual DB orders, to date
until all 7 days load) are compared with its Current 7D Orders (the last week before the change):
  higher -> category "No Drop"; lower -> a drop category (the original one, or - if it was No Drop - the existing
  classify() of the row's impression / CTR / CVR change); equal -> the original category.
The performance category is always the primary label; the monitoring status (accepted ASINs) is supporting detail
and never the category. A final Week 1 vs Week 2 verdict exists only once Week 2 is complete.
Results -> evidence/19_monitoring_status_test_results.json
"""
import json
import pathlib
import sys

BASE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "scripts"))
from build_dataset import SEARCH_DROPS, classify, monitoring_status  # noqa: E402

OUT = BASE / "evidence" / "19_monitoring_status_test_results.json"
results = []


def check(name, ok, detail=None):
    results.append({"test": name, "pass": bool(ok), "detail": detail})
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"  -> {detail}"))


def w(week1=None, week2=None, week1_to_date=None, week2_to_date=None):
    return {"week1": week1, "week2": week2, "week1_to_date": week1_to_date, "week2_to_date": week2_to_date}


# baseline = Current 7D Orders
# A. Current 7D 0 -> Week 1 3 to date: increased -> recovered (category No Drop), not final
a = monitoring_status(0, w(week1_to_date=3))
check("A Current 7D 0 -> Week 1 3 to date: increased, 'Recovery to date', not final",
      a["recovered"] and not a["declined"] and a["status"] == "Recovery to date" and not a["final"], a)

# B. Current 7D 3 -> Week 1 2 to date: declined (supporting info only; the category stays the existing one)
b = monitoring_status(3, w(week1_to_date=2))
check("B Current 7D 3 -> Week 1 2 to date: declined, no recovery, 'Monitoring — Week 1', not final",
      b["declined"] and not b["recovered"] and b["status"] == "Monitoring — Week 1" and not b["final"], b)
e0 = monitoring_status(1, w(week1_to_date=1))
check("B2 equal (Current 7D 1 -> Week 1 1): neither increased nor declined (category unchanged)",
      not e0["recovered"] and not e0["declined"], e0)
z = monitoring_status(0, w(week1_to_date=0))
check("B3 0 -> 0: neither increased nor declined; no data yet -> stage not_started, nothing invented",
      not z["recovered"] and not z["declined"] and monitoring_status(3, w())["stage"] == "not_started"
      and monitoring_status(3, w())["week1_orders"] is None, z)

# C. Week 1 incomplete never gives a final verdict
c = [monitoring_status(base, w(week1_to_date=td)) for base in (0, 1, 3, 10) for td in (0, 1, 3, 12)]
check("C Week 1 incomplete: never a final verdict and never a Performance Decline/Improved/Stable label",
      all(not s["final"] and not s["status"].startswith("Performance") for s in c), [s["status"] for s in c])

# D. Week 1 complete: completed Week 1 result, still not final
d1, d2, d3 = (monitoring_status(3, w(week1=x)) for x in (4, 2, 3))
check("D Week 1 complete vs Current 7D 3: 4 'Week 1 recovered' / 2 'Week 1 below baseline' / 3 'Monitoring — Week 2'",
      (d1["status"], d2["status"], d3["status"]) == ("Week 1 recovered", "Week 1 below baseline", "Monitoring — Week 2")
      and not any(x["final"] for x in (d1, d2, d3)), (d1, d2, d3))

# E. Week 2 incomplete -> pending; complete -> existing rule (Week 2 vs Week 1)
e = monitoring_status(3, w(week1=4, week2_to_date=9))
check("E Week 2 incomplete (even with more orders to date): no final verdict",
      not e["final"] and not e["status"].startswith("Performance"), e)
fin = {f"{x}->{y}": monitoring_status(3, w(week1=x, week2=y))["status"] for x, y in ((14, 7), (7, 14), (7, 7))}
check("E2 Week 2 complete: Performance Decline (14 -> 7) / Improved (7 -> 14) / Stable (7 -> 7), final",
      fin == {"14->7": "Performance Decline", "7->14": "Performance Improved", "7->7": "Performance Stable"}
      and monitoring_status(3, w(week1=14, week2=7))["final"], fin)

# F. Real data (dataset + rendered page)
ds = json.loads((BASE / "data" / "report_dataset.json").read_text(encoding="utf-8"))
MW = ds["monitoring_week_orders"]["orders"]
MON = {"Recovery to date", "Week 1 recovered", "Week 1 below baseline", "Monitoring — Week 1", "Monitoring — Week 2",
       "Performance Decline", "Performance Improved", "Performance Stable"}


def week1(asin):
    x = MW[asin]
    return x["week1"] if x["week1"] is not None else x["week1_to_date"]


def expected(r):
    o = week1(r["asin"])   # 2026-10-08 final rule: Week 1 recovery -> No Drop; completed Week 1 decline on No Drop ->
    if o is not None and o > r["curr_orders"]:                     # Overall Performance Drop; else existing category
        return "No Drop"
    if MW[r["asin"]]["week1"] is not None and MW[r["asin"]]["week1"] < r["curr_orders"] and r["issue_detected"] == "No Drop":
        return "Overall Performance Drop"
    return r["issue_detected"]


rows = {r["asin"]: r for r in ds["performance"]}
check("F every top-moving ASIN: category = existing classification, Week 1 > Current 7D -> No Drop, completed Week 1 "
      "< Current 7D on No Drop -> Overall Performance Drop",
      all(r["issue_current"] == expected(r) for r in rows.values()),
      [(a, r["curr_orders"], week1(a), r["issue_current"], expected(r)) for a, r in rows.items() if r["issue_current"] != expected(r)][:5])
check("F1 no category in the dataset is a monitoring status; original classification kept in issue_detected",
      not [a for a, r in rows.items() if r["issue_current"] in MON or r["issue_detected"] in MON])
check("F1b a category differs from the existing classification only as 'No Drop' (Week 1 recovery) or 'Overall "
      "Performance Drop' (completed Week 1 decline)",
      not [a for a, r in rows.items() if r["issue_current"] != r["issue_detected"]
           and r["issue_current"] not in ("No Drop", "Overall Performance Drop")],
      sorted({r["issue_current"] for r in rows.values() if r["issue_current"] != r["issue_detected"]}))

from playwright.sync_api import sync_playwright  # noqa: E402
html = BASE / "output" / "weekly_top_moving_asin_backend_keyword_fine_tuning_report.html"
with sync_playwright() as p:
    br = p.chromium.launch()
    pg = br.new_page(viewport={"width": 1366, "height": 900})
    pg.goto(html.as_uri())
    pg.click('nav.toc a[href="#s3"]')
    READ = """() => { const t = document.querySelector('#t3');
        const i = [...t.querySelectorAll('thead th')].findIndex(th => th.textContent.trim().startsWith('Issue Detected'));
        const out = {}; t.querySelectorAll('tbody tr').forEach(tr => { if (tr.children.length > 1)
            out[tr.children[0].innerText.split('\\n')[0]] = tr.children[i].innerText; }); return out; }"""
    views = {}
    for v in ("drop", "nodrop", "all"):   # the three Section 3 filter views
        pg.select_option("#f3", v)
        views[v] = pg.evaluate(READ)
    br.close()
cells = views["all"]
first = {a: t.splitlines()[0] for a, t in cells.items()}

check("F2 all 50 rendered; every primary label = the shown category, never a monitoring status; a changed category "
      "shows 'Original category' underneath",
      len(cells) == len(rows) and all(first[a] == r["issue_current"] for a, r in rows.items())
      and not [a for a in first if first[a] in MON]
      and all("Original category: " + r["issue_detected"] in cells[a] for a, r in rows.items()
              if r["issue_current"] != r["issue_detected"]),
      {"rows": len(cells), "primary": sorted(set(first.values()))})

# 2026-10-08 final rule: completed Week 1 below Current 7D on an original No Drop -> Overall Performance Drop
fell = {a: (rows[a]["curr_orders"], week1(a)) for a in ("B084R7QN2X", "B0C43LY8DB", "B08XWSBPHW") if a in rows}
check("F3 screenshot rows (Current 7D 3 -> Week 1 2 / 0 / 2, 7/7): 'Overall Performance Drop', original No Drop underneath",
      fell and all(first[a] == "Overall Performance Drop" and "Original category: No Drop" in cells[a] for a in fell
                   if MW[a]["week1"] is not None and rows[a]["issue_detected"] == "No Drop"),
      {a: (fell[a], first[a]) for a in fell})
check("F4 B0DTHZ1JX5 (Current 7D 0 -> Week 1 3): 'No Drop', original 'Conversion Drop' underneath",
      first["B0DTHZ1JX5"] == ("No Drop" if week1("B0DTHZ1JX5") > rows["B0DTHZ1JX5"]["curr_orders"] else rows["B0DTHZ1JX5"]["issue_detected"])
      and (first["B0DTHZ1JX5"] != "No Drop" or "Original category: Conversion Drop" in cells["B0DTHZ1JX5"]), cells["B0DTHZ1JX5"])
check("F5 B0D59MHSXN (Current 7D 1 -> Week 1 1, equal): keeps its original category",
      first["B0D59MHSXN"] == rows["B0D59MHSXN"]["issue_detected"] or week1("B0D59MHSXN") != rows["B0D59MHSXN"]["curr_orders"],
      (rows["B0D59MHSXN"]["curr_orders"], week1("B0D59MHSXN"), first["B0D59MHSXN"]))

cur = {a: r["issue_current"] for a, r in rows.items()}
check("F6 filter views: 'Performance drop only' = exactly the rows shown with a drop category, 'No Drop only' = exactly "
      "the No Drop rows, together all 50, no overlap",
      set(views["drop"]) == {a for a, c in cur.items() if c != "No Drop"}
      and set(views["nodrop"]) == {a for a, c in cur.items() if c == "No Drop"}
      and not set(views["drop"]) & set(views["nodrop"]) and len(views["drop"]) + len(views["nodrop"]) == len(cur),
      {"drop": len(views["drop"]), "nodrop": len(views["nodrop"]), "all": len(views["all"])})

# ---- regression tests (2026-10-06): monitoring never creates or replaces the primary category ----------------------
NEVER = MON | {"Order Drop", "Order Drop (traffic stable)", "Order Drop (no search data)", "Stable"}


def reg(n, asin, want, never):
    r = rows.get(asin)
    got = first.get(asin)
    check(f"R{n} {asin} (Previous 7D {r and r['prev_orders']}, Current 7D {r and r['curr_orders']}, Week 1 "
          f"{week1(asin) if r else None}): primary '{want}', never '{never}'",
          r is not None and got == want and r["issue_current"] == want and got != never and got not in NEVER,
          {"shown": got, "issue_detected": r and r["issue_detected"], "monitoring": r and r.get("monitoring")})


# 2026-10-08 final monitoring rule: completed Week 1 below Current 7D on No Drop -> Overall Performance Drop
reg(1, "B0C8NJDTJS", "Overall Performance Drop", "No Drop")
reg("1b", "B0D59PP6PC", "Overall Performance Drop", "Order Drop (traffic stable)")
reg("1c", "B0C43LY8DB", "Overall Performance Drop", "No Drop")
reg(2, "B0DTHZ1JX5", "No Drop", "Recovery to date")
reg(3, "B0D7944QDY", "Conversion Drop", "Monitoring — Week 1")
reg(4, "B0D59MHSXN", "Overall Performance Drop", "Monitoring — Week 1")
check("R5 no monitoring status or generic 'Order Drop' value is ever a primary category (dataset + all 50 rendered)",
      not [a for a, r in rows.items() if r["issue_current"] in NEVER] and not [a for a, t in first.items() if t in NEVER],
      sorted(set(first.values())))
check("R6 'Performance drop only' lists only rows whose current category is a drop (none shows 'No Drop'): "
      "includes B0D7944QDY / B0D59PP6PC / B0C43LY8DB, excludes B0DTHZ1JX5",
      all(t.splitlines()[0] != "No Drop" and t.splitlines()[0] not in NEVER for t in views["drop"].values())
      and {"B0D7944QDY", "B0D59PP6PC", "B0C43LY8DB"} <= set(views["drop"]) and "B0DTHZ1JX5" not in views["drop"]
      and all(rows[a]["issue_current"] in SEARCH_DROPS for a in views["drop"]), {"drop rows": len(views["drop"])})
check("R7 'All top-moving ASINs' lists all 50 rows", len(views["all"]) == len(rows) == 50, len(views["all"]))

passed = sum(r["pass"] for r in results)
OUT.write_text(json.dumps({"results": results, "passed": passed, "failed": len(results) - passed}, indent=1,
                          ensure_ascii=False, default=str), encoding="utf-8")
print(f"{passed} PASS / {len(results) - passed} FAIL")
sys.exit(0 if passed == len(results) else 1)
