"""Browser tests for the Section 7 Change Record workflow and the Section 8 monitoring (previous 8-column layout).

Business rule (2026-10-05): POST Accepted = the backend keyword update was done for the user, so the submission is
monitored straight away (status Monitoring) from the update date - the 22 original submissions share 2026-09-29:
Week 1 = 29 Sep..5 Oct, Week 2 = 6..12 Oct. A Listing Management GET is audit only. The Result shows
"Monitoring — Week 1" / "Monitoring — Week 2" and a verdict (Performance Decline / Improved / Stable) only after
Week 2 is complete.

Runs against:
  * REAL     - the delivered report (real data and the real monitoring ledger).
  * FIXTURES - temporary copies whose daily data is extended with SYNTHETIC orders and whose ledger is recomputed
               with performance_alert.update(), so the Week 1 / Week 2 / complete / superseded paths can be shown
               today. Written to a temp folder only; the delivered report and ledger are never replaced.
Every scenario uses a fresh browser context (empty browser storage), so the user's own browser is never touched.
Results -> evidence/change_tracking_test_results.json
"""
import copy
import datetime as dt
import hashlib
import json
import pathlib
import sys
import tempfile

from playwright.sync_api import sync_playwright

BASE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "scripts"))
from render import render  # noqa: E402
import build_dataset  # noqa: E402
import optimization_cleanup as oc  # noqa: E402
import performance_alert as pa  # noqa: E402

REAL = BASE / "output" / "weekly_top_moving_asin_backend_keyword_fine_tuning_report.html"
DS = json.loads((BASE / "data" / "report_dataset.json").read_text(encoding="utf-8"))
LEDGER_PATH = BASE / "data" / "monitoring_cycles.json"
LEDGER = json.loads(LEDGER_PATH.read_text(encoding="utf-8"))
OUT = BASE / "evidence" / "change_tracking_test_results.json"
MD = DS["monitoring_data"]
ANCHOR = "2026-09-29"
MONITORED = [c for c in DS["change_record"] if c["status"] == "Monitoring"]
PROPOSED_ROWS = [c for c in DS["change_record"] if c["status"] == "Proposed"]
A1, A2, A3 = "B0DH4KYFPD", "B0GXB7RGZK", "B0C43L42DK"
KEY = {c["asin"]: c["key"] for c in MONITORED}
results = []


def check(name, ok, detail=""):
    results.append({"test": name, "result": "PASS" if ok else "FAIL", "detail": str(detail)[:500]})
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"  -> {detail}"))


def days(start, n):
    s = dt.date.fromisoformat(start)
    return [(s + dt.timedelta(i)).isoformat() for i in range(n)]


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def fixture(through, orders, extra_updates=None):
    """Dataset whose loaded days are extended to `through` with synthetic orders {asin: {date: n}}, and a copy of
    the real ledger recomputed by performance_alert.update (optionally with extra manual live updates)."""
    fx, led = copy.deepcopy(DS), copy.deepcopy(LEDGER)
    fx["meta"]["title"] = "TEST FIXTURE - synthetic daily data - not a report"
    md = fx["monitoring_data"]
    last = dt.date.fromisoformat(MD["available_through"])
    ext = days((last + dt.timedelta(1)).isoformat(), max(0, (dt.date.fromisoformat(through) - last).days))
    md["available_dates"] = [d for d in MD["available_dates"] if d <= through] + ext
    md["available_through"] = through
    for asin, s in orders.items():
        md["orders"].setdefault(asin, {}).update(s)
    for (asin, date) in (extra_updates or []):
        row = next(r for r in led["keyword_rows"].values() if r["asin"] == asin and r.get("live_updates"))
        oc.add_live_update(row, date + "T09:00:00", oc.SRC_MANUAL, "user changed keywords manually")
    pa.update(led, fx, now=dt.datetime.fromisoformat(through + "T12:00:00"))
    return fx, led


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


def t8(pg):
    """Section 8 rows by change-record key: data attributes + {column label: cell text}."""
    rows = pg.eval_on_selector_all("#t8 tbody tr[data-key]", """rs => rs.map(r => [r.dataset.key, r.dataset.start, r.dataset.result,
        Object.fromEntries([...r.children].map(td => [td.dataset.label, td.innerText.trim()]))])""")
    return {k: {"start": s, "result": res, **cells} for k, s, res, cells in rows}


def overflow(pg, tabs=("s7", "s8", "s13")):
    ov = {}
    for w in (1920, 1366, 1024, 390):
        pg.set_viewport_size({"width": w, "height": 900})
        for t in tabs:
            pg.evaluate(f"window.__showTab__('{t}', false)")
            ov[f"{w} {t}"] = pg.evaluate(
                "Math.max(document.documentElement.scrollWidth - document.documentElement.clientWidth,"
                " ...[...document.querySelectorAll('section.active .tbl')].map(f => f.scrollWidth - f.clientWidth), 0)")
    pg.set_viewport_size({"width": 1366, "height": 900})
    return {k: v for k, v in ov.items() if v > 0}


def kpis(pg):
    return {l: pg.inner_text(f'#kpis .v[data-kpi="{l}"]') for l in (
        "Backend Keyword Fine-Tuning Completed", "Live Verification Pending", "ASINs Under Monitoring", "Backend Keyword POSTs Accepted")}


before = {p.name: sha(p) for p in (LEDGER_PATH, BASE / "data" / "report_dataset.json", REAL)}
tmpdir = pathlib.Path(tempfile.mkdtemp(prefix="wtma_test_"))

# FIXTURES. Week 1 = 29 Sep..5 Oct, Week 2 = 6..12 Oct for every accepted submission.
ORDERS = {A1: {**{d: 2 for d in days("2026-09-29", 7)}, **{d: 1 for d in days("2026-10-06", 7)}},   # 14 -> 7  Decline
          A2: {**{d: 1 for d in days("2026-09-29", 7)}, **{d: 2 for d in days("2026-10-06", 7)}},   # 7 -> 14  Improved
          A3: {**{d: 1 for d in days("2026-09-29", 7)}, **{d: 1 for d in days("2026-10-06", 7)}}}   # 7 -> 7   Stable
fx1, led1 = fixture("2026-10-12", ORDERS)          # both weeks complete
F1 = render(fx1, tmpdir / "fixture_complete.html", led1)
fx2, led2 = fixture("2026-10-03", ORDERS)          # Week 1 in progress (5/7)
F2 = render(fx2, tmpdir / "fixture_week1.html", led2)
fx3, led3 = fixture("2026-10-10", ORDERS)          # Week 1 complete, Week 2 in progress (5/7)
F3 = render(fx3, tmpdir / "fixture_week2.html", led3)
fx4, led4 = fixture("2026-10-12", ORDERS, extra_updates=[(A1, "2026-10-08")])   # user's manual change live 8 Oct
F4 = render(fx4, tmpdir / "fixture_manual_change.html", led4)

with sync_playwright() as p:
    br = p.chromium.launch()

    # T1 - REAL: every POST Accepted submission is Monitoring from 29 Sep ----------------------------------------
    ctx, pg, errs = open_page(br, REAL)
    statuses = dict(zip(pg.eval_on_selector_all("#t7 select.cr-status", "e => e.map(s => s.dataset.key)"),
                        pg.eval_on_selector_all("#t7 select.cr-status", "e => e.map(s => s.value)")))
    dates = dict(zip(pg.eval_on_selector_all("#t7 input.cr-date", "e => e.map(s => s.dataset.key)"),
                     pg.eval_on_selector_all("#t7 input.cr-date", "e => e.map(s => s.value)")))
    pills = pg.eval_on_selector_all('#t7 td[data-label="Amazon Submission"] .pill', "e => e.map(x => x.textContent)")
    s5 = pg.eval_on_selector_all('#t5 td[data-label="Final Status"]', "e => e.map(x => x.textContent)")
    check("T1a Sections 5 / 7 show exactly the 22 submitted SKUs (no new 'Proposed – upload not confirmed' rows): all Monitoring "
          "with Date Changed 29 Sep 2026 and 'Live-verified — monitoring'; no 'Live Verification Pending'",
          len(MONITORED) == 22 and not PROPOSED_ROWS and len(statuses) == 22
          and all(statuses[c["key"]] == "Monitoring" and dates[c["key"]] == ANCHOR for c in MONITORED)
          and pills.count("Live-verified — monitoring") == 22 and not [x for x in pills if "Pending" in x]
          and s5 == ["Live-verified — monitoring"] * 22,
          {"monitoring": sum(v == "Monitoring" for v in statuses.values()), "pills": sorted(set(pills))})
    pg.click('nav.toc a[href="#s8"]')
    s8 = t8(pg)
    heads = pg.eval_on_selector_all("#t8 thead th", "e => e.map(x => x.textContent)")
    check("T1b Section 8 keeps the previous 8-column layout; one row per monitoring change record (22), all from 29 Sep: "
          "Week 1 29 Sep → 5 Oct, Week 2 6 Oct → 12 Oct",
          heads == ["ASIN", "Week 1 Orders", "Week 2 Orders", "Order Change %", "Week 1 Impressions", "Week 2 Impressions",
                    "Impression Change %", "Result"]
          and set(s8) == {c["key"] for c in MONITORED} and all(v["start"] == ANCHOR for v in s8.values())
          and all("29 Sept 2026 → 05 Oct 2026" in v["Week 1 Orders"] and "06 Oct 2026 → 12 Oct 2026" in v["Week 2 Orders"]
                  and "changed 29 Sept 2026" in v["ASIN"] for v in s8.values()), (heads, len(s8)))
    loaded_w1 = sum(1 for d in days(ANCHOR, 7) if d in set(MD["available_dates"]))
    exp_res = "Monitoring — Week 1" if loaded_w1 < 7 else "Monitoring — Week 2"
    check(f"T1c Real data ({loaded_w1}/7 Week 1 days loaded): Result '{exp_res}', Week 1 orders '—' + 'Week 1 monitoring' until 7/7, no verdict",
          all(v["result"] == exp_res for v in s8.values())
          and all(v["Week 1 Orders"].startswith("—") and "Week 1 monitoring" in v["Week 1 Orders"] and "days loaded" not in v["Week 1 Orders"]
                  and v["Result"] == "Monitoring — Week 1" + chr(10) + "Week 1 monitoring" for v in s8.values() if loaded_w1 < 7)
          and not [v for v in s8.values() if v["result"].startswith("Performance")], next(iter(s8.values())))
    k0 = kpis(pg)
    check("T1d KPI cards: Completed 0, Live Verification Pending 0, Under Monitoring 22, POSTs Accepted 22",
          k0 == {"Backend Keyword Fine-Tuning Completed": "0", "Live Verification Pending": "0", "ASINs Under Monitoring": "22",
                 "Backend Keyword POSTs Accepted": "22"}, k0)

    # T2 - validation: nothing invalid is saved -------------------------------------------------------------------
    pg.click('nav.toc a[href="#s7"]')
    key = KEY[A1]
    edit(pg, key, date="", status="Monitoring"); save(pg, key)
    e1 = row(pg, key).locator(".cr-msg").inner_text()
    edit(pg, key, date="2026-09-22", status="Proposed"); save(pg, key)
    e2 = row(pg, key).locator(".cr-msg").inner_text()
    edit(pg, key, date="2099-01-01", status="Monitoring"); save(pg, key)
    e3 = row(pg, key).locator(".cr-msg").inner_text()
    completed_disabled = row(pg, key).locator('select.cr-status option[value="Completed"]').is_disabled()
    stored = pg.evaluate("localStorage.getItem('wtma.changeRecords.v1')")
    check("T2 Validation: no date / Proposed+date / future date rejected and not stored; Completed disabled before Week 2 is complete",
          "Enter the actual Date Changed" in e1 and "Proposed change has no Date Changed" in e2
          and "cannot be in the future" in e3 and completed_disabled and stored is None,
          [e1, e2, e3, completed_disabled, stored])
    ctx.close()

    # T3 - REAL: save + refresh persists; info shows the Week 1 / Week 2 windows ---------------------------------
    ctx, pg, errs = open_page(br, REAL)
    edit(pg, key, date=ANCHOR, status="Live-verified")
    dirty = row(pg, key).locator(".cr-state").inner_text()
    save(pg, key)
    after_save = row(pg, key).locator(".cr-state").inner_text()
    pg.reload(); pg.click('nav.toc a[href="#s7"]')
    r = row(pg, key)
    persisted = (r.locator("input.cr-date").input_value(), r.locator("select.cr-status").input_value())
    edit(pg, key, status="Monitoring"); save(pg, key)
    pg.reload(); pg.click('nav.toc a[href="#s7"]')
    info = row(pg, key).locator(".cr-state").inner_text()
    check("T3a Save shows Unsaved -> Saved state", "Unsaved" in dirty and "Saved" in after_save, [dirty, after_save])
    check("T3b Record persists after page refresh", persisted == (ANCHOR, "Live-verified"), persisted)
    check("T3c Section 7 info: Change Date 29 Sep, current monitoring week and both windows",
          "Change Date: 29 Sept 2026" in info and "Week 1 29 Sept 2026 → 05 Oct 2026" in info
          and "Week 2 06 Oct 2026 → 12 Oct 2026" in info and "Monitoring — Week" in info, info)
    ov = overflow(pg)
    check("T3d Sections 7/8/12: no horizontal scrollbar at 1920/1366/1024/390px", not ov, ov or "all 0")

    # T6 - dirty guard: leaving with unsaved input asks first ----------------------------------------------------
    pg.click('nav.toc a[href="#s7"]')
    edit(pg, KEY[A2], date="2026-09-30")
    dialogs = []
    pg.on("dialog", lambda d: (dialogs.append(d.type), d.dismiss()))
    pg.close(run_before_unload=True)
    pg.wait_for_timeout(300)
    check("T6 Unsaved input: leaving the page triggers a confirmation", "beforeunload" in dialogs, dialogs)
    check("No JavaScript errors (real report)", not errs, errs[:3])
    ctx.close()

    # T7 - export -------------------------------------------------------------------------------------------------
    ctx, pg, errs = open_page(br, REAL)
    edit(pg, key, date="2026-09-30", status="Monitoring"); save(pg, key)
    with pg.expect_download() as dl:
        pg.click("#crExport")
    exported = json.loads(pathlib.Path(dl.value.path()).read_text(encoding="utf-8"))
    rec = exported["records"][0]
    check("T7 Export: saved record downloads as change_records.json",
          dl.value.suggested_filename == "change_records.json" and rec["key"] == key
          and rec["date_changed"] == "2026-09-30" and rec["status"] == "Monitoring", rec)
    ctx.close()

    # T8 - FIXTURE F1: both weeks complete -> results; Completed only now ----------------------------------------
    ctx, pg, errs = open_page(br, F1)
    pg.click('nav.toc a[href="#s8"]')
    f1 = t8(pg)
    check("T8a Week 2 complete, 14 -> 7: Week 1 Orders 14, Week 2 Orders 7, Order Change -50.0%, Result Performance Decline",
          f1[KEY[A1]]["Week 1 Orders"].startswith("14") and f1[KEY[A1]]["Week 2 Orders"].startswith("7")
          and f1[KEY[A1]]["Order Change %"] == "-50.0%" and f1[KEY[A1]]["result"] == "Performance Decline", f1[KEY[A1]])
    check("T8b 7 -> 14: Performance Improved; 7 -> 7: Performance Stable",
          f1[KEY[A2]]["result"] == "Performance Improved" and f1[KEY[A3]]["result"] == "Performance Stable",
          (f1[KEY[A2]]["result"], f1[KEY[A3]]["result"]))
    kf1 = kpis(pg)
    check("T8c KPI after the full 14 days: Completed 22, Under Monitoring 0, Pending 0",
          kf1["Backend Keyword Fine-Tuning Completed"] == "22" and kf1["ASINs Under Monitoring"] == "0"
          and kf1["Live Verification Pending"] == "0", kf1)
    pg.click('nav.toc a[href="#s7"]')
    enabled = not row(pg, KEY[A1]).locator('select.cr-status option[value="Completed"]').is_disabled()
    edit(pg, KEY[A1], status="Completed"); save(pg, KEY[A1])
    pg.reload(); pg.click('nav.toc a[href="#s7"]')
    info = row(pg, KEY[A1]).locator(".cr-state").inner_text()
    check("T8d Completed can be saved once Week 2 is complete; it persists with the result",
          enabled and "Status: Completed" in info and "Performance Decline" in info, info)
    check("No JavaScript errors (fixture F1)", not errs, errs[:3])
    ctx.close()

    # T9 - FIXTURES F2 / F3: Week 1 then Week 2 in progress -> no verdict ------------------------------------------
    ctx, pg, errs = open_page(br, F2)
    pg.click('nav.toc a[href="#s8"]')
    f2 = t8(pg)
    k2 = kpis(pg)
    ctx.close()
    ctx, pg, errs3 = open_page(br, F3)
    pg.click('nav.toc a[href="#s8"]')
    f3 = t8(pg)
    k3 = kpis(pg)
    pg.click('nav.toc a[href="#s7"]')
    still_disabled = row(pg, KEY[A1]).locator('select.cr-status option[value="Completed"]').is_disabled()
    ctx.close()
    check("T9a Week 1 in progress (5/7 internally): Result 'Monitoring — Week 1', Week 1 orders '—' with 'Week 1 monitoring' (no 7/7, no total)",
          all(v["result"] == "Monitoring — Week 1" for v in f2.values())
          and f2[KEY[A1]]["Week 1 Orders"].startswith("—") and "Week 1 monitoring" in f2[KEY[A1]]["Week 1 Orders"]
          and led2["monitoring"][f"{A1}|{ANCHOR}"]["week1_days_loaded"] == 5, f2[KEY[A1]])
    check("T9b Week 2 in progress (5/7): Result 'Monitoring — Week 2', Week 1 = 14 shown, Week 2 '—', NO verdict, Completed disabled",
          all(v["result"] == "Monitoring — Week 2" for v in f3.values()) and f3[KEY[A1]]["Week 1 Orders"].startswith("14")
          and f3[KEY[A1]]["Week 2 Orders"].startswith("—") and "5/7 days loaded" in f3[KEY[A1]]["Week 2 Orders"]
          and f3[KEY[A1]]["Order Change %"] == "—" and still_disabled, f3[KEY[A1]])
    check("T9c KPI while monitoring: Completed 0, Under Monitoring 22", k2["Backend Keyword Fine-Tuning Completed"] == "0"
          and k2["ASINs Under Monitoring"] == "22" and k3["Backend Keyword Fine-Tuning Completed"] == "0"
          and k3["ASINs Under Monitoring"] == "22", (k2, k3))
    check("No JavaScript errors (fixtures F2 / F3)", not errs and not errs3, (errs + errs3)[:3])

    # T10 - FIXTURE F4: manual change live 8 Oct -> new cycle; the 29 Sep cycle is superseded, not mixed ----------
    ctx, pg, errs = open_page(br, F4)
    pg.click('nav.toc a[href="#s8"]')
    f4 = t8(pg)
    hist = sorted(tuple(x) for x in pg.eval_on_selector_all(
        "#t13h tbody tr[data-asin]", "e => e.map(r => [r.dataset.asin, r.dataset.live, r.dataset.status])"))
    old = led4["monitoring"][f"{A1}|{ANCHOR}"]
    check("T10 Manual change on 8 Oct: Section 8 marks the 29 Sep record superseded (no mixed Week 2 verdict); "
          "Section 12 history shows the superseded 29 Sep cycle and the new 8 Oct cycle",
          f4[KEY[A1]]["result"].startswith("Superseded by the 08 Oct 2026 update") and old["week2_valid"] is False
          and (A1, ANCHOR, pa.S_SUPERSEDED) in hist and (A1, "2026-10-08", pa.S_WEEK1) in hist,
          (f4[KEY[A1]]["result"], [h for h in hist if h[0] == A1]))
    check("No JavaScript errors (fixture F4)", not errs, errs[:3])
    ctx.close()

    # T11 - records exported to data/change_records.json are embedded by the next build ----------------------------
    emb = copy.deepcopy(DS)
    emb["saved_change_records"] = build_dataset.validate_saved([rec])
    EMB = render(emb, tmpdir / "embedded_report.html", LEDGER)
    ctx, pg, errs = open_page(br, EMB)   # fresh browser storage: only the embedded record exists
    persisted = (row(pg, key).locator("input.cr-date").input_value(), row(pg, key).locator("select.cr-status").input_value())
    check("T11 Build-embedded record (data/change_records.json) is restored without browser storage",
          persisted == ("2026-09-30", "Monitoring"), persisted)
    try:
        build_dataset.validate_saved([{"key": "x|y", "asin": "x", "status": "Monitoring", "date_changed": None}])
        bad_rejected = False
    except ValueError:
        bad_rejected = True
    check("T11b Build rejects a Monitoring record without Date Changed", bad_rejected)
    ctx.close()
    br.close()

# T12 - pure date logic (performance_alert.windows)
check("T12 Week windows: 29 Sep -> W1 29 Sep..5 Oct, W2 6..12 Oct; common anchor constant = 2026-09-29",
      pa.windows(ANCHOR) == (("2026-09-29", "2026-10-05"), ("2026-10-06", "2026-10-12")) and oc.ORIGINAL_ANCHOR == ANCHOR)
after = {p.name: sha(p) for p in (LEDGER_PATH, BASE / "data" / "report_dataset.json", REAL)}
check("SAFETY production ledger, dataset and report unchanged by the test", before == after)

OUT.write_text(json.dumps({"run_at": dt.datetime.now().isoformat(timespec="seconds"),
                           "passed": sum(r["result"] == "PASS" for r in results),
                           "failed": sum(r["result"] == "FAIL" for r in results), "tests": results},
                          indent=1, ensure_ascii=False), encoding="utf-8")
print(f"\n{sum(r['result'] == 'PASS' for r in results)} PASS / {sum(r['result'] == 'FAIL' for r in results)} FAIL")
raise SystemExit(1 if any(r["result"] == "FAIL" for r in results) else 0)
