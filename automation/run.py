"""WTMAFT weekly pipeline runner (Windows Task Scheduler entry point).

Runs the EXISTING production scripts in order; no business logic lives here:
    extract.py -> optimization_cleanup.py --apply (weekly keyword check of the 24 report rows)
              -> build_dataset.py -> performance_alert.py --send -> render.py -> validate.py

* One stage failing (non-zero exit, timeout, crash) marks the run FAILED, the remaining stages
  are NOT RUN, and this process exits 1. Success is reported only when every stage exits 0.
* optimization_cleanup.py updates backend keywords ONLY for ASINs whose manual Full Optimization the
  team recorded, and only after its payload validation; performance_alert.py e-mails only when its own
  rule says so. --dry-run runs both without --apply / --send (no POST, no e-mail).
* No Amazon keyword update runs here (keyword_live_* scripts are refused), and nothing touches
  title, bullets, images, description, A+ content or any visible listing content.
* stdout/stderr of every stage -> logs/<run_id>/NN_<stage>.log (secrets redacted);
  run record -> evidence/11_scheduler_runs.json (last 100 runs) + logs/run_history.log.

Usage:
    python automation/run.py            # production (what the scheduled task runs)
    python automation/run.py --dry-run  # same pipeline, no POST (no --apply) and no e-mail (no --send)
"""
import argparse
import datetime as dt
import json
import os
import pathlib
import subprocess
import sys
import time

BASE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "scripts"))
from email_alert import redact  # noqa: E402  (same secret redaction as the alert module)

STAGE_TIMEOUT = 1800
FORBIDDEN = ("keyword_live_update", "keyword_live_dryrun", "send_test_email", "gmail_authorize", "publish_ph_task")
# Environment the stages need; read from the Windows user environment if the calling shell is stale.
USER_ENV = ("WLP_SOURCE_DB_URL", "WTMA_GMAIL_CLIENT_FILE", "WTMA_GMAIL_TOKEN_FILE")


def pipeline(dry_run):
    return [("extract", ["scripts/extract.py"]),
            # weekly keyword check of the report's ASIN-SKU rows (fresh GET, clean, POST only if needed, verify)
            ("weekly_keyword_check", ["scripts/optimization_cleanup.py"] + ([] if dry_run else ["--apply"])),
            ("build_dataset", ["scripts/build_dataset.py"]),
            ("performance_alert", ["scripts/performance_alert.py"] + ([] if dry_run else ["--send"])),
            ("render", ["scripts/render.py"]),
            ("validate", ["scripts/validate.py"])]


def load_user_env(env):
    if os.name != "nt":
        return env
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
        for name in USER_ENV:
            if not env.get(name):
                try:
                    env[name] = winreg.QueryValueEx(k, name)[0]
                except OSError:
                    pass
    return env


def current_cycle():
    try:
        m = json.loads((BASE / "data" / "report_dataset.json").read_text(encoding="utf-8"))["meta"]
        return "_".join(m["current_7d"])
    except (OSError, ValueError, KeyError):
        return None


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="alert stage without --send (no e-mail)")
    ap.add_argument("--stages-json", help=argparse.SUPPRESS)  # tests only: [[name, script, args...], ...]
    ap.add_argument("--log-dir", default=str(BASE / "logs"), help=argparse.SUPPRESS)
    ap.add_argument("--evidence", default=str(BASE / "evidence" / "11_scheduler_runs.json"), help=argparse.SUPPRESS)
    a = ap.parse_args(argv)

    stages = ([(s[0], s[1:]) for s in json.loads(a.stages_json)] if a.stages_json else pipeline(a.dry_run))
    for _, cmd in stages:
        if any(f in c for c in cmd for f in FORBIDDEN) or                 (any("keyword_live_verify" in c for c in cmd) and "--pending-only" not in cmd):
            print(f"REFUSED: {cmd} is not part of the weekly pipeline")
            return 2

    log_root = pathlib.Path(a.log_dir)
    log_root.mkdir(parents=True, exist_ok=True)
    lock = log_root / "run.lock"
    if lock.exists() and time.time() - lock.stat().st_mtime < 6 * 3600:
        print(f"REFUSED: another run is in progress ({lock}); stale locks expire after 6 h")
        return 3
    lock.write_text(str(os.getpid()), encoding="utf-8")
    failed = None
    try:
        run_id = dt.datetime.now().strftime("%Y%m%dT%H%M%S_%f")  # unique even for runs in the same second
        run_dir = log_root / run_id
        run_dir.mkdir()
        env = load_user_env(dict(os.environ))
        env["PYTHONIOENCODING"] = "utf-8"
        before = current_cycle()
        rec = {"run_id": run_id, "mode": "dry-run (no e-mail)" if a.dry_run else "production",
               "started_at": dt.datetime.now().isoformat(timespec="seconds"), "python": sys.executable,
               "cwd": str(BASE), "status": None, "stages": []}
        for n, (name, cmd) in enumerate(stages, 1):
            entry = {"stage": name, "command": " ".join(["python"] + cmd), "log": str(run_dir / f"{n:02d}_{name}.log")}
            if failed:
                entry.update(status="NOT RUN", exit_code=None)
                rec["stages"].append(entry)
                continue
            t0 = time.time()
            try:
                p = subprocess.run([sys.executable] + cmd, cwd=BASE, env=env, capture_output=True,
                                   timeout=STAGE_TIMEOUT, encoding="utf-8", errors="replace")
                code, out, err = p.returncode, p.stdout, p.stderr
            except subprocess.TimeoutExpired as e:
                code, out, err = None, e.stdout or "", f"TIMEOUT after {STAGE_TIMEOUT}s"
            except OSError as e:
                code, out, err = None, "", f"could not start: {e}"
            pathlib.Path(entry["log"]).write_text(
                redact(f"$ {entry['command']}\nexit code: {code}\n\n--- stdout ---\n{out}\n--- stderr ---\n{err}\n"),
                encoding="utf-8")
            entry.update(exit_code=code, seconds=round(time.time() - t0, 1),
                         status="OK" if code == 0 else "FAILED",
                         tail=redact("\n".join((out or "").strip().splitlines()[-3:]))[:600])
            if code != 0:
                entry["error_tail"] = redact("\n".join(((err or "") + "\n" + (out or "")).strip().splitlines()[-6:]))[:1200]
                failed = name
            rec["stages"].append(entry)
            print(f"[{n}/{len(stages)}] {name}: {entry['status']} (exit {code}, {entry['seconds']}s)", flush=True)
    finally:
        lock.unlink(missing_ok=True)

    after = current_cycle()
    rec.update(finished_at=dt.datetime.now().isoformat(timespec="seconds"),
               status="FAILED" if failed else "SUCCESS", failed_stage=failed, cycle_before=before, cycle_after=after,
               note=("no new catalog week loaded yet: the same cycle was re-recorded (idempotent, not double counted)"
                     if not failed and before and before == after else None))
    ev = pathlib.Path(a.evidence)
    runs = json.loads(ev.read_text(encoding="utf-8"))["runs"] if ev.exists() else []
    ev.write_text(json.dumps({"runs": (runs + [rec])[-100:]}, indent=1, ensure_ascii=False), encoding="utf-8")
    with (log_root / "run_history.log").open("a", encoding="utf-8") as f:
        f.write(f"{rec['started_at']}  {rec['mode']:22} {rec['status']:8} failed_stage={failed}  cycle={after}  {run_id}\n")
    print(f"RUN {rec['status']}" + (f" at stage '{failed}'" if failed else "") + f" - logs: {run_dir}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
