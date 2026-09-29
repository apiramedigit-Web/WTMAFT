"""7-day monitoring-cycle tracking + Full Optimization Review alert (runs after build_dataset.py).

Reuses, without changing them: the weekly performance rows (build_dataset.py: Current 7D vs
Previous 7D, rule D2 "Current 7D Orders < Previous 7D Orders"), the change records and their
live-verification state (keyword_live_verify.py -> report_dataset.json change_record), and the
keyword analysis (keyword_finetune.py). No keyword is updated here and no listing is touched.

Rule (business instruction 2026-09-29):
    alert  <=>  Current 7D Orders < Previous 7D Orders
                AND the ASIN declined in TWO CONSECUTIVE 7-day cycles after its backend keyword
                fine-tuning was LIVE VERIFIED.
Interpretation used (documented, see evidence/10_email_alerting.md):
  * tracked ASINs  = ASINs with >= 1 change record in state "UPDATED / VERIFIED"
  * eligible cycle = Current 7D window starting AFTER the ASIN's latest live-verification date
  * consecutive    = eligible cycles whose windows are exactly 7 days apart; a non-drop cycle resets
                     the count to 0; a missing week (gap) or a week with no data for the ASIN restarts it
  * one alert per (ASIN, cycle_id): a cycle whose alert was SENT is never e-mailed again
Week 3+ (business instruction 2026-09-29):
  * the cycle that reaches 2 consecutive declines is the ALERT cycle; the streak is then CLOSED -
    no further alert from that streak, however many more weeks decline
  * the ASIN stays monitored every week ("awaiting manual Full Optimization", count 0)
  * the team records the completed Full Optimization (--record-optimization); optimization_cleanup.py
    then reads the latest Listing Management keywords, removes only duplicates/repetition, updates
    Listing Management and live-verifies. Only that LIVE VERIFICATION date becomes the new baseline
    (the week containing it is a baseline week, counting restarts at 0 from the next week), and 2 new
    consecutive declines raise a NEW alert (new cycle key). Until then the ASIN stays "awaiting".

Ledger: data/monitoring_cycles.json (cycles + alerts + optimizations; re-running a week is idempotent).
Evidence: evidence/10_monitoring_cycles.csv, evidence/10_full_optimization_alerts.json/.csv,
          evidence/10_gmail_config_validation.json (--validate).

Usage:
    python scripts/performance_alert.py              # record cycle + evaluate, DRY RUN (no e-mail)
    python scripts/performance_alert.py --validate   # read-only Gmail OAuth configuration check (sends nothing)
    python scripts/performance_alert.py --send       # live: e-mail pending alerts (only if validation passes)
    python scripts/performance_alert.py --record-optimization B0XXXXXXX --date 2026-10-24 [--by NAME] [--note TEXT]
                                                     # team: Full Optimization completed -> new baseline
"""
import argparse
import csv
import datetime as dt
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import email_alert  # noqa: E402

BASE = pathlib.Path(__file__).resolve().parent.parent
DATASET = BASE / "data" / "report_dataset.json"
LEDGER = BASE / "data" / "monitoring_cycles.json"
EVID = BASE / "evidence"
DASHBOARD_REF = ("output/weekly_top_moving_asin_backend_keyword_fine_tuning_report.html "
                 "(published: tech_team_outputs.ph_task id 1940, project WTMA)")
VERIFIED = "UPDATED / VERIFIED"


def _d(s):
    return dt.date.fromisoformat(str(s)[:10])


def monitoring_start(verified):
    """First Current 7D window (Sunday start) that begins AFTER the live-verification date."""
    if not verified:
        return None
    d = _d(verified)
    return (d + dt.timedelta(days=(6 - d.weekday()) % 7 or 7)).isoformat()


def tracked_asins(ds):
    """ASIN -> {skus, last_live_verification_date, last_fine_tuning_date} from live-verified change records."""
    out = {}
    for r in ds.get("change_record", []):
        sub = r.get("submission") or {}
        if sub.get("state") != VERIFIED or not sub.get("checked_at_utc"):
            continue
        t = out.setdefault(r["asin"], {"skus": [], "last_live_verification_date": None,
                                       "last_fine_tuning_date": None})
        t["skus"].append(r["sku"])
        ver, tuned = str(sub["checked_at_utc"])[:10], r.get("date_changed") or sub.get("post_date")
        t["last_live_verification_date"] = max(filter(None, [t["last_live_verification_date"], ver]))
        if tuned:
            t["last_fine_tuning_date"] = max(filter(None, [t["last_fine_tuning_date"], str(tuned)[:10]]))
    return out


def record_cycle(ds, ledger):
    """Adds this dataset's Current 7D cycle for every tracked ASIN (idempotent per ASIN/cycle)."""
    m = ds["meta"]
    (cs, ce), (ps, pe) = m["current_7d"], m["previous_7d"]
    cycle_id = f"{cs}_{ce}"
    perf = {p["asin"]: p for p in ds["performance"]}
    perf.update({p["asin"]: p for p in ds.get("monitoring_performance", [])})  # incl. ASINs outside the top 50
    kw = {}
    for k in ds.get("keyword_analysis", []):
        kw.setdefault(k["asin"], []).append(k)
    tracked = tracked_asins(ds)
    for r in ledger.get("keyword_rows", {}).values():   # weekly keyword check: first live verification starts monitoring
        if r.get("first_verified_at"):
            t = tracked.setdefault(r["asin"], {"skus": [], "last_live_verification_date": None, "last_fine_tuning_date": None})
            if r["sku"] not in t["skus"]:
                t["skus"].append(r["sku"])
            t["last_live_verification_date"] = max(filter(None, [t["last_live_verification_date"], r["first_verified_at"][:10]]))
    for c in ledger["cycles"].values():  # an ASIN already being monitored is never dropped silently
        if c["asin"] not in tracked and c.get("last_live_verification_date"):
            tracked[c["asin"]] = {"skus": c["sku"].split(", "), "last_live_verification_date":
                                  c["last_live_verification_date"], "last_fine_tuning_date": c.get("last_fine_tuning_date")}
    for asin, t in tracked.items():
        p = perf.get(asin)
        kws = kw.get(asin, [])
        ledger["cycles"][f"{asin}|{cycle_id}"] = {
            "cycle_id": cycle_id, "asin": asin, "sku": ", ".join(t["skus"]),
            "account": (p or {}).get("account") or ", ".join(sorted({k.get("account", "") for k in kws})) or None,
            "marketplace": m.get("marketplace"),
            "current_7d_start": cs, "current_7d_end": ce, "previous_7d_start": ps, "previous_7d_end": pe,
            "previous_orders": (p or {}).get("prev_orders"), "current_orders": (p or {}).get("curr_orders"),
            "order_change_pct": (p or {}).get("order_chg"),
            "previous_impressions": (p or {}).get("prev_impressions"),
            "current_impressions": (p or {}).get("curr_impressions"),
            "impression_change_pct": (p or {}).get("impression_chg"),
            "previous_clicks": (p or {}).get("prev_clicks"), "current_clicks": (p or {}).get("curr_clicks"),
            "ctr_change_pct": (p or {}).get("ctr_chg"), "cvr_change_pct": (p or {}).get("cvr_chg"),
            "performance_drop": (None if p is None or p.get("prev_orders") is None or p.get("curr_orders") is None
                                 else p["curr_orders"] < p["prev_orders"]),
            "data_status": "ok" if p else "ASIN not in this week's report dataset (no performance row)",
            "in_top_moving": (p or {}).get("in_top_moving", asin in {r["asin"] for r in ds["performance"]}),
            "backend_keyword_status": ((p or {}).get("keyword_status") or
                                       "; ".join(sorted({k.get("backend_keyword_status") or "" for k in kws})) or None),
            "duplicate_words_removed": ((p or {}).get("duplicate_words_removed_at_fine_tuning") if
                                        (p or {}).get("duplicate_words_removed_at_fine_tuning") is not None else
                                        sum(int(k.get("duplicate_words_removed") or 0) for k in kws)),
            "monitoring_start_date": monitoring_start(t["last_live_verification_date"]),
            "last_fine_tuning_date": t["last_fine_tuning_date"],
            "last_live_verification_date": t["last_live_verification_date"],
            "recorded_at": dt.datetime.now().isoformat(timespec="seconds"),
        }
        # verification dates can move on (re-cleaning in a later cycle): refresh them on older rows too
        for c in ledger["cycles"].values():
            if c["asin"] == asin:
                c["last_live_verification_date"] = t["last_live_verification_date"]
                c["last_fine_tuning_date"] = t["last_fine_tuning_date"]
                c["monitoring_start_date"] = monitoring_start(t["last_live_verification_date"])
    return cycle_id


def evaluate(ledger):
    """Sets the streak fields on every cycle; returns the ALERT cycles (the cycle where a streak reaches 2).

    Per ASIN, chronologically. The baseline is the latest of the live-verification date and any
    recorded Full Optimization completed on or before the cycle's end. A cycle counts only if it
    starts after the baseline. When a streak reaches 2 that cycle is the alert cycle and the streak
    closes; later cycles are monitored but not counted until a Full Optimization is recorded."""
    by_asin = {}
    for c in ledger["cycles"].values():
        by_asin.setdefault(c["asin"], []).append(c)
    opts = ledger.get("optimizations", {})
    due = []
    for asin, cycles in by_asin.items():
        cycles.sort(key=lambda c: c["current_7d_start"])
        # baseline = live verification of the post-optimization keyword clean-up (optimization_cleanup.py),
        # never the date the user merely reported the optimization
        done = sorted(o["monitoring_baseline_date"] for o in opts.get(asin, []) if o.get("monitoring_baseline_date"))
        streak, prev_start, n, awaiting, anchor, alert_cycle = 0, None, 0, False, None, None
        for c in cycles:
            ver = c.get("last_live_verification_date")
            last_opt = max((d for d in done if d <= c["current_7d_end"]), default=None)
            new_anchor = max(filter(None, [ver, last_opt]), default=None)
            if new_anchor != anchor:                       # new baseline: live verification or Full Optimization
                streak, prev_start, n, awaiting, anchor = 0, None, 0, False, new_anchor
            c["baseline_date"] = anchor
            c["baseline_type"] = ("Full Optimization" if last_opt and (not ver or last_opt >= ver)
                                  else "live verification" if ver else None)
            c["last_full_optimization_date"] = last_opt
            c["eligible_after_live_verification"] = bool(anchor) and bool(ver) and _d(c["current_7d_start"]) > _d(anchor)
            c["alert_cycle_id"] = alert_cycle if awaiting else None
            if not c["eligible_after_live_verification"]:
                c.update(consecutive_decline_count=0, monitoring_cycle_number=None, streak_status="baseline")
                continue
            n += 1
            c["monitoring_cycle_number"] = n
            if awaiting:                                   # streak closed by the alert; wait for the optimization
                c.update(consecutive_decline_count=0, streak_status="awaiting_full_optimization")
                prev_start = _d(c["current_7d_start"])
                continue
            contiguous = prev_start is not None and (_d(c["current_7d_start"]) - prev_start).days == 7
            if c["performance_drop"] is True:
                streak = streak + 1 if contiguous or prev_start is None else 1
            else:
                streak = 0
            prev_start = _d(c["current_7d_start"])
            c["consecutive_decline_count"] = streak
            c["streak_status"] = "open"
            if streak >= 2:
                c["streak_status"] = "alert"
                due.append(c)
                awaiting, alert_cycle, streak = True, c["cycle_id"], 0
    return due


def open_alert(ledger, asin):
    """The ASIN's latest alert cycle not yet resolved by a recorded Full Optimization (or None)."""
    evaluate(ledger)
    cyc = sorted((c for c in ledger["cycles"].values() if c["asin"] == asin), key=lambda c: c["current_7d_start"])
    alerts = [c for c in cyc if c.get("streak_status") == "alert"]
    if not alerts:
        return None
    last = alerts[-1]
    done = [o["completed_on"] for o in ledger.get("optimizations", {}).get(asin, [])]
    return None if any(d >= last["current_7d_end"] for d in done) else last


def record_optimization(ledger, asin, completed_on, by=None, note=None, today=None, now=None):
    """Team input: Full Optimization completed for ASIN on completed_on (YYYY-MM-DD). Validated; returns the record."""
    today = today or dt.date.today()
    try:
        d = dt.date.fromisoformat(completed_on)
    except ValueError:
        raise ValueError(f"--date must be YYYY-MM-DD, got {completed_on!r}") from None
    if d > today:
        raise ValueError(f"Full Optimization date {d} is in the future")
    if not any(c["asin"] == asin for c in ledger["cycles"].values()):
        raise ValueError(f"{asin} is not in the monitoring ledger")
    existing = [o for o in ledger.get("optimizations", {}).get(asin, []) if o["completed_on"] == d.isoformat()]
    if existing:
        return existing[0]                                # idempotent
    a = open_alert(ledger, asin)
    if not a:
        raise ValueError(f"{asin} has no open Full Optimization Review alert to resolve")
    if d.isoformat() < a["current_7d_end"]:
        raise ValueError(f"Full Optimization date {d} is before the end of the alert cycle {a['cycle_id']}")
    rec = {"asin": asin, "completed_on": d.isoformat(), "alert_cycle_id": a["cycle_id"], "recorded_by": by,
           "note": note, "recorded_at": (now or dt.datetime.now()).isoformat(timespec="seconds")}
    ledger.setdefault("optimizations", {}).setdefault(asin, []).append(rec)
    evaluate(ledger)
    return rec


def alert_context(c):
    return {**c, "dashboard_ref": DASHBOARD_REF}


def process_alerts(ledger, due, mode, cfg=None, transport=None):
    """mode: 'dry_run' (never sends) or 'send'. A (ASIN, cycle) alert already SENT is blocked."""
    results = []
    for c in due:
        key = f'{c["asin"]}|{c["cycle_id"]}'
        prev = ledger["alerts"].get(key)
        if prev and prev.get("alert_status") == "SENT":
            results.append({**prev, "this_run": "BLOCKED_DUPLICATE (already sent)"})
            continue
        alert = email_alert.build_alert(alert_context(c))
        rec = {"asin": c["asin"], "cycle_id": c["cycle_id"], "sku": c["sku"],
               "consecutive_decline_count": c["consecutive_decline_count"],
               "subject": alert["subject"], "recipients": list(email_alert.RECIPIENTS),
               "alert_status": None, "alert_sent_at": None, "message_id": None, "error": None,
               "attempted_at": dt.datetime.now().isoformat(timespec="seconds")}
        if mode != "send":
            rec["alert_status"] = "DRY_RUN (not sent)"
        else:
            res = email_alert.send_alert(cfg, alert, idempotency_key=f"wtma-foa-{c['asin']}-{c['cycle_id']}",
                                         transport=transport)
            rec["http_status"] = res["http_status"]
            if res["ok"]:
                rec.update(alert_status="SENT", message_id=res["message_id"],
                           alert_sent_at=dt.datetime.now().isoformat(timespec="seconds"))
            else:
                rec.update(alert_status="FAILED", error=res["error"])
        ledger["alerts"][key] = rec
        results.append({**rec, "this_run": rec["alert_status"]})
    for c in ledger["cycles"].values():
        a = ledger["alerts"].get(f'{c["asin"]}|{c["cycle_id"]}')
        c["full_optimization_alert_status"] = (a or {}).get("alert_status") or (
            "Pending" if c.get("streak_status") == "alert" else
            f'Awaiting manual Full Optimization (alert cycle {c.get("alert_cycle_id")})'
            if c.get("streak_status") == "awaiting_full_optimization" else "Not required")
        c["alert_sent_at"] = (a or {}).get("alert_sent_at")
        c["alert_recipients"] = ", ".join(email_alert.RECIPIENTS) if a else None
    return results


def load_ledger(path):
    if path.exists():
        led = json.loads(path.read_text(encoding="utf-8"))
        led.setdefault("optimizations", {})
        return led
    return {"cycles": {}, "alerts": {}, "optimizations": {}}


def write_evidence(ledger, results, evid_dir, mode):
    cols = ["cycle_id", "asin", "sku", "account", "marketplace", "current_7d_start", "current_7d_end",
            "previous_7d_start", "previous_7d_end", "previous_orders", "current_orders", "order_change_pct",
            "previous_impressions", "current_impressions", "impression_change_pct", "previous_clicks",
            "current_clicks", "ctr_change_pct", "cvr_change_pct", "performance_drop", "data_status",
            "backend_keyword_status", "duplicate_words_removed", "last_fine_tuning_date",
            "last_live_verification_date", "monitoring_start_date", "in_top_moving",
            "eligible_after_live_verification", "baseline_type", "baseline_date", "last_full_optimization_date",
            "monitoring_cycle_number", "consecutive_decline_count", "streak_status", "alert_cycle_id",
            "full_optimization_alert_status", "alert_sent_at", "alert_recipients"]
    with (evid_dir / "10_monitoring_cycles.csv").open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(sorted(ledger["cycles"].values(), key=lambda c: (c["asin"], c["cycle_id"])))
    (evid_dir / "10_full_optimization_alerts.json").write_text(json.dumps(
        {"run_at": dt.datetime.now().isoformat(timespec="seconds"), "mode": mode,
         "recipients": list(email_alert.RECIPIENTS), "this_run": results,
         "alert_log": list(ledger["alerts"].values()),
         "full_optimizations": ledger.get("optimizations", {})}, indent=1, ensure_ascii=False), encoding="utf-8")


def run(mode="dry_run", dataset=None, ledger_path=None, evid_dir=None, cfg=None, transport=None):
    dataset, ledger_path, evid_dir = dataset or DATASET, ledger_path or LEDGER, evid_dir or EVID
    ds = json.loads(pathlib.Path(dataset).read_text(encoding="utf-8"))
    ledger = load_ledger(ledger_path)
    cycle_id = record_cycle(ds, ledger)
    due = evaluate(ledger)
    results = process_alerts(ledger, due, mode, cfg, transport)
    ledger_path.write_text(json.dumps(ledger, indent=1, ensure_ascii=False), encoding="utf-8")
    write_evidence(ledger, results, evid_dir, mode)
    return {"cycle_id": cycle_id, "tracked": len({c["asin"] for c in ledger["cycles"].values()}),
            "due": len(due), "results": results}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--validate", action="store_true", help="read-only Gmail OAuth configuration check")
    ap.add_argument("--send", action="store_true", help="send pending alerts (live)")
    ap.add_argument("--record-optimization", metavar="ASIN", help="team: Full Optimization completed for ASIN")
    ap.add_argument("--date", help="Full Optimization completion date YYYY-MM-DD (with --record-optimization)")
    ap.add_argument("--by", help="who completed it (optional)")
    ap.add_argument("--note", help="short note (optional)")
    a = ap.parse_args()
    if a.record_optimization:
        if not a.date:
            print("ERROR: --date YYYY-MM-DD is required with --record-optimization")
            return 2
        ledger = load_ledger(LEDGER)
        try:
            rec = record_optimization(ledger, a.record_optimization.strip().upper(), a.date, a.by, a.note)
        except ValueError as e:
            print("NOT RECORDED:", e)
            return 2
        LEDGER.write_text(json.dumps(ledger, indent=1, ensure_ascii=False), encoding="utf-8")
        write_evidence(ledger, [], EVID, "record-optimization")
        print("RECORDED:", json.dumps(rec, ensure_ascii=False))
        print("Next: the ASIN's next due Monday keyword check reads the latest backend keywords, removes only "
              "duplicates, updates Listing Management if needed and live-verifies; the new 7-day monitoring "
              "starts from that verification. Nothing is sent now.")
        return 0
    try:
        if a.validate or a.send:
            cfg = email_alert.load_config()
            rep = email_alert.validate_gmail(cfg)
            rep["checked_at"] = dt.datetime.now().isoformat(timespec="seconds")
            (EVID / "10_gmail_config_validation.json").write_text(
                json.dumps(rep, indent=1, ensure_ascii=False), encoding="utf-8")
            print(email_alert.redact(json.dumps(rep, ensure_ascii=False)))
            if a.validate:
                return 0 if rep["ready"] else 2
            if not rep["ready"]:
                print("NOT SENDING - configuration not ready:", rep["blocker"])
                return 2
            out = run("send", cfg=cfg)
        else:
            out = run("dry_run")
    except email_alert.AlertConfigError as e:
        print("CONFIG ERROR:", email_alert.redact(e))
        return 2
    print(email_alert.redact(f'cycle {out["cycle_id"]}: tracked ASINs {out["tracked"]}, alerts due {out["due"]}'))
    for r in out["results"]:
        print(email_alert.redact(f'  {r["asin"]} {r["cycle_id"]} count={r["consecutive_decline_count"]} '
                                 f'-> {r["this_run"]} {r.get("message_id") or ""}'))
    return 0


if __name__ == "__main__":
    sys.exit(main())
