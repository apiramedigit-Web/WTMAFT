"""Browser tests for the Section 7 Change Record workflow and Section 8 monitoring.

Runs against:
  * REAL    - the delivered report (real data, loaded through 'available_through').
  * FIXTURE - a temporary copy whose daily data is extended with SYNTHETIC orders so the
              "full 7 days loaded" and "Completed" paths can be exercised today. The fixture
              is written to a temp folder only; it never replaces the delivered report.
Every scenario uses a fresh browser context (empty browser storage), so the user's own
browser is never touched. Results -> evidence/change_tracking_test_results.json
"""
import copy
import datetime as dt
import json
import os
import pathlib
import sys
import tempfile

import psycopg
from playwright.sync_api import sync_playwright

BASE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "scripts"))
from render import render  # noqa: E402
import build_dataset  # noqa: E402

REAL = BASE / "output" / "weekly_top_moving_asin_backend_keyword_fine_tuning_report.html"
DS = json.loads((BASE / "data" / "report_dataset.json").read_text(encoding="utf-8"))
OUT = BASE / "evidence" / "change_tracking_test_results.json"
MD = DS["monitoring_data"]
ROW0 = DS["change_record"][0]
AVAIL = set(MD["available_dates"])
results = []


def check(name, ok, detail=""):
    results.append({"test": name, "result": "PASS" if ok else "FAIL", "detail": str(detail)[:500]})
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"  -> {detail}"))


def days(d0, a, b):
    base = dt.date.fromisoformat(d0)
    return [(base + dt.timedelta(days=i)).isoformat() for i in range(a, b + 1)]


def fmt(iso):  # same format the page uses (en-GB, e.g. "22 Sept 2026")
    return None if iso is None else iso


# ---- fixture with synthetic daily data (test only) ----------------------------------------
fx = copy.deepcopy(DS)
fx["meta"]["title"] = "TEST FIXTURE - synthetic daily data - not a report"
extra = days(MD["available_through"], 1, 12)          # extend loaded days by 12 synthetic days
fx["monitoring_data"]["available_dates"] = MD["available_dates"] + extra
fx["monitoring_data"]["available_through"] = extra[-1]
series = fx["monitoring_data"]["orders"][ROW0["asin"]]
for d in days("2026-09-22", -7, -1):
    series[d] = 2                                         # pre-change 7 x 2 = 14
for d in days("2026-09-22", 1, 7):
    series[d] = 3                                         # post-change 7 x 3 = 21 -> +50.0%
tmpdir = pathlib.Path(tempfile.mkdtemp(prefix="wtma_test_"))
FIXTURE = render(fx, tmpdir / "fixture_report.html")

# ---- independent expectation for the real 22-Sep pre-period (live DB, read-only) ----------
with psycopg.connect(os.environ["WLP_SOURCE_DB_URL"]) as con:
    con.execute("SET TRANSACTION READ ONLY")
    live_pre = con.execute(
        """SELECT coalesce(sum(total_order_items),0) FROM business_reports.amz_sales_and_traffic_by_asin
           WHERE market_place=23 AND sub_source IN (6,8) AND child_asin=%s AND date BETWEEN %s AND %s""",
        (ROW0["asin"], "2026-09-15", "2026-09-21")).fetchone()[0]
real_post_days = sum(1 for d in days("2026-09-22", 1, 7) if d in AVAIL)


def open_page(browser, url):
    ctx = browser.new_context(viewport={"width": 1366, "height": 900}, accept_downloads=True)
    pg = ctx.new_page()
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
    pg.goto(url.as_uri())
    pg.click('nav.toc a[href="#s7"]')
    return ctx, pg, errs


def row(pg, key):
    return pg.locator("#t7 tbody tr").filter(has=pg.locator(f'input[data-key="{key}"]'))


def edit(pg, key, date=None, status=None):
    r = row(pg, key)
    if date is not None:
        r.locator("input.cr-date").fill(date)
    if status is not None:
        r.locator("select.cr-status").select_option(status)


def save(pg, key):
    row(pg, key).locator("button.cr-save").click()


def t8_texts(pg):
    return pg.eval_on_selector_all("#t8 tbody tr", "rs => rs.map(r => [...r.children].map(td => td.innerText.trim()))")


key = ROW0["key"]
with sync_playwright() as p:
    br = p.chromium.launch()

    # T1 - Proposed record (default) -----------------------------------------------------------
    ctx, pg, errs = open_page(br, REAL)
    statuses = pg.eval_on_selector_all("#t7 select.cr-status", "e => e.map(s => s.value)")
    m = pg.evaluate("window.__wtma__.monitor({asin:'%s', status:'Proposed', date_changed:'2026-09-22'})" % ROW0["asin"])
    check("T1 Proposed: every record starts Proposed, not monitored, Section 8 empty",
          set(statuses) == {"Proposed"} and m["active"] is False
          and pg.locator("#t8 tbody td.empty").count() == 1
          and pg.evaluate("window.__REPORT_KPI__.finetune_completed_verified") == 0,
          {"statuses": set(statuses), "monitor": m})

    # T2 - validation: nothing invalid is saved ------------------------------------------------
    edit(pg, key, status="Monitoring"); save(pg, key)
    e1 = row(pg, key).locator(".cr-msg").inner_text()
    edit(pg, key, date="2026-09-22", status="Proposed"); save(pg, key)
    e2 = row(pg, key).locator(".cr-msg").inner_text()
    edit(pg, key, date="2099-01-01", status="Monitoring"); save(pg, key)
    e3 = row(pg, key).locator(".cr-msg").inner_text()
    stored = pg.evaluate("localStorage.getItem('wtma.changeRecords.v1')")
    check("T2 Validation: no date / Proposed+date / future date are rejected and not stored",
          "Enter the actual Date Changed" in e1 and "Proposed change has no Date Changed" in e2
          and "cannot be in the future" in e3 and stored is None, [e1, e2, e3, stored])
    ctx.close()

    # T3 - REAL workflow: 22-Sep + Monitoring -> Save -> refresh persists -> X/7 ---------------
    ctx, pg, errs = open_page(br, REAL)
    edit(pg, key, date="2026-09-22", status="Monitoring")
    dirty = row(pg, key).locator(".cr-state").inner_text()
    save(pg, key)
    after_save = row(pg, key).locator(".cr-state").inner_text()
    pg.reload(); pg.click('nav.toc a[href="#s7"]')
    r = row(pg, key)
    persisted = (r.locator("input.cr-date").input_value(), r.locator("select.cr-status").input_value())
    info = r.locator(".cr-state").inner_text()
    completed_disabled = r.locator('select.cr-status option[value="Completed"]').is_disabled()
    pg.click('nav.toc a[href="#s8"]')
    t8 = t8_texts(pg)
    kpi = pg.evaluate("window.__REPORT_KPI__")
    s5 = pg.evaluate("[...document.querySelectorAll('#t5 tbody tr')].map(r=>r.innerText).find(t=>t.includes('%s') && t.includes('%s'))"
                     % (ROW0["asin"], ROW0["sku"]))
    check("T3a Save shows Unsaved -> Saved state", "Unsaved" in dirty and "Saved" in after_save, [dirty, after_save])
    check("T3b Record persists after page refresh", persisted == ("2026-09-22", "Monitoring"), persisted)
    check("T3c Saved Date Changed is authoritative: Section 7 shows change date and Day X of 7",
          "Change Date: 22 Sept 2026" in info and f"Day {real_post_days} of 7" in info
          and "23 Sept 2026 → 29 Sept 2026" in info, info)
    check(f"T3d Section 8: 'Monitoring — {real_post_days}/7 days', post-change values '—' (not fabricated)",
          len(t8) == 1 and f"Monitoring — {real_post_days}/7 days" in t8[0][7]
          and t8[0][2].startswith("—") and t8[0][3].startswith("—"), t8)
    check("T3e Pre-period D-7..D-1 = 15 → 21 Sept, orders match live DB",
          "15 Sept 2026 → 21 Sept 2026" in t8[0][1] and t8[0][1].split("\n")[0] == str(live_pre)
          and "23 Sept 2026 → 29 Sept 2026" in t8[0][2], [t8[0][1], live_pre])
    check("T3f Completed cannot be chosen before 7/7 days", completed_disabled)
    check("T3g KPI + Section 5 follow the saved record",
          kpi["finetune_completed_verified"] == 1 and kpi["under_monitoring"] == 1
          and s5 and "Uploaded (user-confirmed 22 Sept 2026)" in s5, [kpi, s5])
    check("T3h Impressions shown as '—' with reason (weekly-only source), never estimated",
          all(t8[0][i].startswith("—") and "weekly data only" in t8[0][i] for i in (4, 5, 6)), t8[0][4:7])

    ov = {}
    for w in (1920, 1366, 1024, 390):
        pg.set_viewport_size({"width": w, "height": 900})
        for t in ("s7", "s8"):
            pg.click(f'nav.toc a[href="#{t}"]')
            ov[f"{w} {t}"] = pg.evaluate(
                "Math.max(document.documentElement.scrollWidth - document.documentElement.clientWidth,"
                " ...[...document.querySelectorAll('section.active .tbl')].map(f => f.scrollWidth - f.clientWidth), 0)")
    pg.set_viewport_size({"width": 1366, "height": 900})
    check("T3i Sections 7/8 with a saved record: no horizontal scrollbar at 1920/1366/1024/390px",
          all(v <= 0 for v in ov.values()), {k: v for k, v in ov.items() if v > 0} or "all 0")

    # T4 - 0/7 days and T5 partial -------------------------------------------------------------
    k2 = DS["change_record"][1]["key"]
    pg.click('nav.toc a[href="#s7"]')
    edit(pg, k2, date=MD["available_through"], status="Monitoring"); save(pg, k2)
    k3 = DS["change_record"][2]["key"]
    edit(pg, k3, date="2026-09-18", status="Monitoring"); save(pg, k3)
    pg.click('nav.toc a[href="#s8"]')
    res = {rw[0].split("\n")[0] + "|" + rw[0].split("\n")[1].split(" · ")[0]: rw[7] for rw in t8_texts(pg)}
    exp_partial = sum(1 for d in days("2026-09-18", 1, 7) if d in AVAIL)
    r2 = res.get(DS["change_record"][1]["asin"] + "|" + DS["change_record"][1]["sku"], "")
    r3 = res.get(DS["change_record"][2]["asin"] + "|" + DS["change_record"][2]["sku"], "")
    check("T4 Monitoring with 0/7 days (Date Changed = last loaded day)", "Monitoring — 0/7 days" in r2, r2)
    check(f"T5 Monitoring with partial days (18 Sept -> {exp_partial}/7)", f"Monitoring — {exp_partial}/7 days" in r3, r3)

    # T6 - dirty guard: leaving with unsaved input asks first ----------------------------------
    pg.click('nav.toc a[href="#s7"]')
    edit(pg, DS["change_record"][3]["key"], date="2026-09-20")
    dialogs = []
    pg.on("dialog", lambda d: (dialogs.append(d.type), d.dismiss()))
    pg.close(run_before_unload=True)
    pg.wait_for_timeout(300)
    check("T6 Unsaved input: leaving the page triggers a confirmation", "beforeunload" in dialogs, dialogs)
    check("No JavaScript errors (real report)", not errs, errs[:3])
    ctx.close()

    # T7 - export -------------------------------------------------------------------------------
    ctx, pg, errs = open_page(br, REAL)
    edit(pg, key, date="2026-09-22", status="Monitoring"); save(pg, key)
    with pg.expect_download() as dl:
        pg.click("#crExport")
    exported = json.loads(pathlib.Path(dl.value.path()).read_text(encoding="utf-8"))
    rec = exported["records"][0]
    check("T7 Export: saved record downloads as change_records.json",
          dl.value.suggested_filename == "change_records.json" and rec["key"] == key
          and rec["date_changed"] == "2026-09-22" and rec["status"] == "Monitoring", rec)
    ctx.close()

    # T8 - FIXTURE: full 7/7 days -> metrics + Result -> user marks Completed --------------------
    ctx, pg, errs = open_page(br, FIXTURE)
    edit(pg, key, date="2026-09-22", status="Monitoring"); save(pg, key)
    pg.click('nav.toc a[href="#s8"]')
    t8 = t8_texts(pg)[0]
    check("T8a Full 7 days: Post-Change Orders + Order Change % + Result calculated",
          t8[1].startswith("14") and t8[2].startswith("21") and t8[3].startswith("+50.0%")
          and "Orders improved" in t8[7] and "mark Completed" in t8[7], t8)
    pg.click('nav.toc a[href="#s7"]')
    r = row(pg, key)
    enabled = not r.locator('select.cr-status option[value="Completed"]').is_disabled()
    status_before = pg.evaluate("window.__wtma__.records()['%s'].status" % key)
    edit(pg, key, status="Completed"); save(pg, key)
    pg.reload(); pg.click('nav.toc a[href="#s7"]')
    info = row(pg, key).locator(".cr-state").inner_text()
    kpi = pg.evaluate("window.__REPORT_KPI__")
    pg.click('nav.toc a[href="#s8"]')
    t8 = t8_texts(pg)[0]
    check("T8b Not auto-completed: stays Monitoring until the user marks Completed", status_before == "Monitoring" and enabled)
    check("T8c Completed record: persists, shows Monitoring Period 23 → 29 Sept, KPI updated",
          "Status: Completed" in info and "Monitoring period 23 Sept 2026 → 29 Sept 2026" in info
          and kpi["completed"] == 1 and kpi["under_monitoring"] == 0 and "Completed" in t8[7], [info, kpi, t8[7]])
    check("No JavaScript errors (fixture)", not errs, errs[:3])
    ctx.close()

    # T9 - pure date logic --------------------------------------------------------------------
    ctx, pg, errs = open_page(br, REAL)
    m1 = pg.evaluate("window.__wtma__.monitor({asin:'%s', status:'Monitoring', date_changed:'2026-09-22'})" % ROW0["asin"])
    m2 = pg.evaluate("window.__wtma__.monitor({asin:'%s', status:'Monitoring', date_changed:'2026-10-02'})" % ROW0["asin"])
    check("T9 Date windows: 22-Sep -> pre 15..21, post 23..29; 02-Oct crosses month (25 Sep..01 Oct / 03..09 Oct)",
          (m1["pre_from"], m1["pre_to"], m1["post_from"], m1["post_to"]) == ("2026-09-15", "2026-09-21", "2026-09-23", "2026-09-29")
          and (m2["pre_from"], m2["pre_to"], m2["post_from"], m2["post_to"]) == ("2026-09-25", "2026-10-01", "2026-10-03", "2026-10-09"),
          [m1, m2])
    ctx.close()

    # T10 - records exported to data/change_records.json are embedded by the next build ---------
    emb = copy.deepcopy(DS)
    emb["saved_change_records"] = build_dataset.validate_saved([rec])
    EMB = render(emb, tmpdir / "embedded_report.html")
    ctx, pg, errs = open_page(br, EMB)   # fresh browser storage: only the embedded record exists
    persisted = (row(pg, key).locator("input.cr-date").input_value(), row(pg, key).locator("select.cr-status").input_value())
    check("T10 Build-embedded record (data/change_records.json) is restored without browser storage",
          persisted == ("2026-09-22", "Monitoring"), persisted)
    try:
        build_dataset.validate_saved([{"key": "x|y", "asin": "x", "status": "Monitoring", "date_changed": None}])
        bad_rejected = False
    except ValueError:
        bad_rejected = True
    check("T10b Build rejects a Monitoring record without Date Changed", bad_rejected)
    ctx.close()
    br.close()

OUT.write_text(json.dumps({"run_at": dt.datetime.now().isoformat(timespec="seconds"),
                           "passed": sum(r["result"] == "PASS" for r in results),
                           "failed": sum(r["result"] == "FAIL" for r in results), "tests": results},
                          indent=1, ensure_ascii=False), encoding="utf-8")
print(f"\n{sum(r['result'] == 'PASS' for r in results)} PASS / {sum(r['result'] == 'FAIL' for r in results)} FAIL")
