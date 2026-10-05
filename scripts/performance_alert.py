"""Post-update performance monitoring (weekly pipeline stage; runs after build_dataset.py). NO e-mail of any kind.

Business rule (2026-10-05, replaces the 2-consecutive-decline Full Optimization Review e-mail alert and the
2026-10-02 GET-gated start):
  * POST Accepted = the backend keyword update is done for the user: monitoring starts from its anchor without a
    GET verification (GET read-backs are audit only). The 22 original submissions share the anchor 2026-09-29;
    later updates are anchored on their POST Accepted date (or the detected / recorded manual-change date).
    Anchors come from the weekly keyword check ledger (optimization_cleanup.py -> keyword_rows[*].live_updates).
  * Week 1 = anchor .. anchor+6, Week 2 = anchor+7 .. anchor+13 (7 days each, the anchor day included).
  * Metric = ORDERS: ASIN-level daily Amazon Business Report order items, both UK accounts summed
    (build_dataset.py -> monitoring_data, the same source as the weekly figures). A window is complete only
    when all 7 days are loaded for both accounts; its orders are shown only then (orders so far = partial).
  * Week 2 is never evaluated before it is complete: Week 2 orders < Week 1 -> Performance Decline,
    > -> Performance Improved, = -> Performance Stable.
  * A newer update of the same ASIN (e.g. the user's manual change) starts a NEW cycle; a window
    of the older cycle that reaches the new anchor would mix two keyword versions, so it is not used and the
    older cycle is closed as superseded. Completed cycles are frozen in the ledger (later daily-data windows
    no longer contain their dates).
Statuses: Backend Keyword Update Pending · Live-verified — monitoring not yet started · Monitoring — Week 1 ·
          Monitoring — Week 2 · Monitoring Complete / Performance Comparison Available, with the performance
          status Performance Decline / Performance Improved / Performance Stable (or "Monitoring in progress").
Ledger: data/monitoring_cycles.json -> "monitoring" (the older "cycles"/"alerts"/"optimizations" keys are kept
        unchanged as history). Evidence: evidence/17_post_update_monitoring.csv.

Usage:
    python scripts/performance_alert.py     # reconcile keyword rows with the evidence + update the monitoring cycles
"""
import argparse
import csv
import datetime as dt
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import optimization_cleanup as oc  # noqa: E402

BASE = pathlib.Path(__file__).resolve().parent.parent
DATASET = BASE / "data" / "report_dataset.json"
LEDGER = BASE / "data" / "monitoring_cycles.json"
EVID = BASE / "evidence"
EVID_CSV = "17_post_update_monitoring.csv"

S_PENDING = "Backend Keyword Update Pending"
S_NOT_STARTED = "Live-verified — monitoring not yet started"
S_WEEK1 = "Monitoring — Week 1"
S_WEEK2 = "Monitoring — Week 2"
S_COMPLETE = "Monitoring Complete / Performance Comparison Available"
S_SUPERSEDED = "Superseded by a newer backend keyword update"
P_DECLINE = "Performance Decline"
P_IMPROVED = "Performance Improved"
P_STABLE = "Performance Stable"
P_WAIT = "Monitoring in progress"
P_NOT_STARTED = "Monitoring not yet started"
P_NOT_EVALUATED = "Not evaluated (superseded before Week 2 completed)"
P_PENDING = "Not monitored: no accepted backend keyword update yet"
# keyword-row states in which an update was attempted but is not confirmed live
# keyword-row states of an update that was NOT accepted (POST Accepted is monitored: GET read-back is audit only)
UNCONFIRMED = (oc.S_UPDATE_FAILED, oc.S_VERIFY_FAILED, oc.S_VALIDATION, oc.S_READ_FAILED, oc.S_DRY)


def _d(s):
    return dt.date.fromisoformat(str(s)[:10])


def windows(anchor):
    """(Week 1, Week 2) as ((start, end), (start, end)) ISO dates; the anchor day is day 1 of Week 1."""
    a = _d(anchor)
    return ((a.isoformat(), (a + dt.timedelta(6)).isoformat()),
            ((a + dt.timedelta(7)).isoformat(), (a + dt.timedelta(13)).isoformat()))


def window(series, available, start, end, valid=True):
    """Orders in one 7-day window. orders is set only when all 7 days are loaded (never a partial total)."""
    days = [(_d(start) + dt.timedelta(i)).isoformat() for i in range(7)]
    loaded = [x for x in days if x in available] if valid else []
    complete = valid and len(loaded) == 7
    return {"start": start, "end": end, "valid": valid, "days_loaded": len(loaded), "complete": complete,
            "orders": sum(series.get(x, 0) for x in days) if complete else None,
            "orders_so_far": sum(series.get(x, 0) for x in loaded) if loaded and not complete else None}


def anchors(rows):
    """ASIN -> [{date, skus, sources, confirmed_at}] (sorted): confirmed live backend keyword updates of any of
    the ASIN's listings; updates of several listings on the same date are one anchor."""
    out = {}
    for r in rows.values():
        for u in r.get("live_updates") or []:
            a = out.setdefault(r["asin"], {}).setdefault(u["date"], {"date": u["date"], "skus": [], "sources": [],
                                                                    "confirmed_at": u["confirmed_at"]})
            if r["sku"] not in a["skus"]:
                a["skus"].append(r["sku"])
            if u["source"] not in a["sources"]:
                a["sources"].append(u["source"])
            a["confirmed_at"] = min(a["confirmed_at"], u["confirmed_at"])
    return {k: [v[d] for d in sorted(v)] for k, v in out.items()}


def cycle(asin, anc, next_anchor, series, available, as_of=None):
    """One monitoring cycle from a confirmed live update. Pure."""
    (s1, e1), (s2, e2) = windows(anc["date"])
    superseded = next_anchor is not None and next_anchor <= e2
    w1 = window(series, available, s1, e1, valid=not (superseded and next_anchor <= e1))
    w2 = window(series, available, s2, e2, valid=not superseded)
    if superseded:
        status = S_SUPERSEDED
    elif w1["days_loaded"] == 0:
        status = S_NOT_STARTED
    elif not w1["complete"]:
        status = S_WEEK1
    elif not w2["complete"]:
        status = S_WEEK2
    else:
        status = S_COMPLETE
    c = {"key": f"{asin}|{anc['date']}", "kind": "cycle", "asin": asin, "skus": anc["skus"], "sku": ", ".join(anc["skus"]),
         "backend_keyword_status": "Live-verified", "live_update_date": anc["date"], "live_confirmed_at": anc["confirmed_at"],
         "live_update_source": "; ".join(anc["sources"]), "monitoring_status": status,
         "week1_start": s1, "week1_end": e1, "week1_valid": w1["valid"], "week1_days_loaded": w1["days_loaded"],
         "week1_complete": w1["complete"], "week1_orders": w1["orders"], "week1_orders_so_far": w1["orders_so_far"],
         "week2_start": s2, "week2_end": e2, "week2_valid": w2["valid"], "week2_days_loaded": w2["days_loaded"],
         "week2_complete": w2["complete"], "week2_orders": w2["orders"], "week2_orders_so_far": w2["orders_so_far"],
         "order_change": None, "order_change_pct": None, "superseded_by": next_anchor if superseded else None,
         "data_through": as_of}
    if w1["complete"] and w2["complete"]:
        c["order_change"] = w2["orders"] - w1["orders"]
        c["order_change_pct"] = round(c["order_change"] / w1["orders"] * 100, 1) if w1["orders"] else None
        c["performance_status"] = (P_DECLINE if w2["orders"] < w1["orders"] else
                                   P_IMPROVED if w2["orders"] > w1["orders"] else P_STABLE)
    else:
        c["performance_status"] = (P_NOT_EVALUATED if superseded else
                                   P_NOT_STARTED if status == S_NOT_STARTED else P_WAIT)
    c["monitoring_detail"] = (
        f"Week 1 {w1['days_loaded']}/7 days loaded" if status == S_WEEK1 else
        f"Week 1 complete; Week 2 {w2['days_loaded']}/7 days loaded" if status == S_WEEK2 else
        f"waiting for the first day of Week 1 to load (data through {as_of})" if status == S_NOT_STARTED else
        f"new backend keyword update live on {next_anchor}: a new cycle started" if superseded else
        "Week 1 vs Week 2 comparison available")
    return c


def frozen(c):
    """A finished cycle is kept as recorded (its days drop out of later daily-data windows)."""
    return c.get("kind") == "cycle" and (c["monitoring_status"] == S_COMPLETE or (
        c["monitoring_status"] == S_SUPERSEDED and all(c[f"week{i}_complete"] for i in (1, 2) if c[f"week{i}_valid"])))


def update(ledger, ds, now=None):
    """Recomputes ledger["monitoring"] from the keyword rows' live updates and the dataset's daily orders."""
    md = ds["monitoring_data"]
    available, as_of = set(md["available_dates"]), md.get("available_through")
    rows = ledger.get("keyword_rows", {})
    old = ledger.get("monitoring", {})
    new = {}
    stamp = (now or dt.datetime.now()).isoformat(timespec="seconds")
    anc = anchors(rows)
    for asin, lst in anc.items():
        series = md["orders"].get(asin, {})
        for i, a in enumerate(lst):
            c = cycle(asin, a, lst[i + 1]["date"] if i + 1 < len(lst) else None, series, available, as_of)
            if c["key"] in old and frozen(old[c["key"]]) and old[c["key"]].get("superseded_by") == c["superseded_by"]:
                c = old[c["key"]]
            else:
                c["computed_at"] = stamp
            c["cycle_number"], c["latest"] = i + 1, i + 1 == len(lst)
            pend = sorted(r["sku"] for r in rows.values() if r["asin"] == asin and r["status"] in UNCONFIRMED)
            c["other_listings_pending"] = pend if c["latest"] else []
            new[c["key"]] = c
    for asin in sorted({r["asin"] for r in rows.values() if r["status"] in UNCONFIRMED} - set(anc)):
        skus = sorted(r["sku"] for r in rows.values() if r["asin"] == asin and r["status"] in UNCONFIRMED)
        new[f"{asin}|pending"] = {
            "key": f"{asin}|pending", "kind": "pending", "asin": asin, "skus": skus, "sku": ", ".join(skus),
            "backend_keyword_status": "; ".join(sorted({r["status"] for r in rows.values()
                                                        if r["asin"] == asin and r["status"] in UNCONFIRMED})),
            "live_update_date": None, "monitoring_status": S_PENDING, "performance_status": P_PENDING,
            "monitoring_detail": "no confirmed live update yet; the weekly keyword check re-checks it from a fresh GET",
            "cycle_number": None, "latest": True, "data_through": as_of, "computed_at": stamp}
    ledger["monitoring"] = new
    return new


COLS = ["asin", "sku", "kind", "cycle_number", "latest", "backend_keyword_status", "live_update_date", "live_confirmed_at",
        "live_update_source", "monitoring_status", "monitoring_detail", "week1_start", "week1_end", "week1_days_loaded",
        "week1_orders", "week2_start", "week2_end", "week2_days_loaded", "week2_orders", "order_change",
        "order_change_pct", "performance_status", "superseded_by", "other_listings_pending", "data_through", "computed_at"]


def write_evidence(ledger, evid_dir):
    with (evid_dir / EVID_CSV).open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=COLS, extrasaction="ignore")
        w.writeheader()
        w.writerows(sorted(ledger["monitoring"].values(), key=lambda c: (c["asin"], c.get("live_update_date") or "")))


def load_ledger(path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"cycles": {}, "alerts": {}}


def run(dataset=None, ledger_path=None, evid_dir=None, reconcile=False, now=None):
    dataset, ledger_path, evid_dir = dataset or DATASET, ledger_path or LEDGER, evid_dir or EVID
    ds = json.loads(pathlib.Path(dataset).read_text(encoding="utf-8"))
    ledger = load_ledger(pathlib.Path(ledger_path))
    if reconcile:   # production: rows reflect the latest verification evidence (07/09) before monitoring
        oc.seed(ledger, oc.load_scope(), oc.prior_states_from_evidence())
    mon = update(ledger, ds, now)
    pathlib.Path(ledger_path).write_text(json.dumps(ledger, indent=1, ensure_ascii=False), encoding="utf-8")
    write_evidence(ledger, pathlib.Path(evid_dir))
    return mon


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--send", action="store_true", help=argparse.SUPPRESS)       # retired: refused below
    ap.add_argument("--validate", action="store_true", help=argparse.SUPPRESS)   # retired: refused below
    a = ap.parse_args(argv)
    if a.send or a.validate:
        print("REFUSED: e-mail alerting was removed from this workflow (business instruction 2026-10-02); "
              "the dashboard shows the performance status directly. Nothing was sent.")
        return 2
    mon = run(reconcile=True)
    from collections import Counter
    print(f"monitoring entries: {len(mon)}; status: {dict(Counter(c['monitoring_status'] for c in mon.values()))}")
    for c in sorted(mon.values(), key=lambda c: (c["asin"], c.get("live_update_date") or "")):
        if c["kind"] == "cycle":
            print(f'  {c["asin"]} live {c["live_update_date"]}: W1 {c["week1_start"]}..{c["week1_end"]} '
                  f'{c["week1_orders"] if c["week1_complete"] else str(c["week1_days_loaded"]) + "/7 days"} | '
                  f'W2 {c["week2_start"]}..{c["week2_end"]} '
                  f'{c["week2_orders"] if c["week2_complete"] else str(c["week2_days_loaded"]) + "/7 days"} | '
                  f'{c["monitoring_status"]} | {c["performance_status"]}')
    return 0


if __name__ == "__main__":
    sys.exit(main())
