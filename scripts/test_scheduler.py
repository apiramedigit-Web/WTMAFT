"""Scheduler runner tests (automation/run.py). Uses FAKE stage scripts in a temp folder, so no
real extract, e-mail or keyword update runs. Output: evidence/11_scheduler_test_results.json
"""
import datetime as dt
import json
import pathlib
import subprocess
import sys
import tempfile

BASE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "automation"))
import run as runner  # noqa: E402

RESULTS = []


def check(tid, name, cond, detail=""):
    RESULTS.append({"id": tid, "test": name, "result": "PASS" if cond else "FAIL", "detail": str(detail)[:400]})
    print(f'{"PASS" if cond else "FAIL"}  {tid}  {name}  {"" if cond else detail}')


def fake(tmp, name, body):
    p = tmp / f"{name}.py"
    p.write_text(body, encoding="utf-8")
    return str(p)


def run(tmp, stages, *extra):
    logs, ev = tmp / "logs", tmp / "runs.json"
    p = subprocess.run([sys.executable, str(BASE / "automation" / "run.py"), "--stages-json", json.dumps(stages),
                        "--log-dir", str(logs), "--evidence", str(ev), *extra],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    runs = json.loads(ev.read_text(encoding="utf-8"))["runs"] if ev.exists() else []
    return p, (runs[-1] if runs else None), logs


def main():
    tmp = pathlib.Path(tempfile.mkdtemp())
    ok = fake(tmp, "ok", "print('stage ok')")
    bad = fake(tmp, "bad", "import sys; print('about to fail'); print('boom', file=sys.stderr); sys.exit(3)")
    leak = fake(tmp, "leak", "print('token ya29.SECRETtokenVALUE_123456 and GOCSPX-SECRETvalue99')")

    # S1 production stage list: exact order; NO e-mail stage and no --send anywhere (e-mail alerts removed 2026-10-02)
    prod, dry = runner.pipeline(False), runner.pipeline(True)
    check("S1", "production order = extract -> weekly_keyword_check --apply -> build_dataset -> performance_monitoring (no --send) -> render -> validate",
          [n for n, _ in prod] == ["extract", "weekly_keyword_check", "build_dataset", "performance_monitoring", "render", "validate"]
          and prod[1][1] == ["scripts/optimization_cleanup.py", "--apply"]
          and prod[3][1] == ["scripts/performance_alert.py"]
          and not [c for _, c in prod if "--send" in c or any("email" in x or "gmail" in x for x in c)], prod)
    check("S2", "--dry-run: keyword check without --apply (no POST); monitoring stage identical (it never e-mails)",
          dry[1][1] == ["scripts/optimization_cleanup.py"] and dry[3][1] == ["scripts/performance_alert.py"]
          and not [c for _, c in dry if "--send" in c or "--apply" in c], dry)
    p_send, _, _ = run(tmp, [["alert", "scripts/performance_alert.py", "--send"]])
    p_mail, _, _ = run(tmp, [["mail", "scripts/email_alert.py"]])
    check("S2b", "an e-mail stage (performance_alert --send / email_alert) is REFUSED by the runner before anything runs",
          p_send.returncode == 2 and "REFUSED" in p_send.stdout and p_mail.returncode == 2 and "REFUSED" in p_mail.stdout,
          (p_send.stdout, p_mail.stdout))
    check("S3", "no Amazon keyword-update / test-mail / publish script in the pipeline",
          not any(f in " ".join(c) for _, c in prod + dry for f in runner.FORBIDDEN))

    # S4 all stages succeed
    p, rec, logs = run(tmp, [[f"st{i}", ok] for i in range(5)])
    check("S4", "all stages exit 0 -> SUCCESS, process exit 0, 5 logs", p.returncode == 0 and rec["status"] == "SUCCESS"
          and [s["status"] for s in rec["stages"]] == ["OK"] * 5
          and len(list((logs / rec["run_id"]).glob("*.log"))) == 5, (p.returncode, rec and rec["status"]))

    # S5 stage 3 fails -> FAILED, 4 and 5 not run, exit 1, stderr + exit code captured
    p, rec, logs = run(tmp, [["a", ok], ["b", ok], ["c", bad], ["d", ok], ["e", ok]])
    st = {s["stage"]: s for s in rec["stages"]}
    log_c = (logs / rec["run_id"] / "03_c.log").read_text(encoding="utf-8")
    check("S5", "stage 3 fails -> run FAILED at 'c', exit 1, later stages NOT RUN",
          p.returncode == 1 and rec["status"] == "FAILED" and rec["failed_stage"] == "c"
          and st["c"]["exit_code"] == 3 and st["d"]["status"] == st["e"]["status"] == "NOT RUN"
          and "RUN FAILED" in p.stdout and "RUN SUCCESS" not in p.stdout, (p.returncode, rec["stages"]))
    check("S6", "stdout, stderr and exit code are in the stage log + error tail in the run record",
          "about to fail" in log_c and "boom" in log_c and "exit code: 3" in log_c and "boom" in st["c"]["error_tail"])

    # S7 first stage fails -> nothing else runs
    p, rec, _ = run(tmp, [["a", bad], ["b", ok]])
    check("S7", "first stage fails -> nothing else runs, FAILED", p.returncode == 1 and rec["stages"][1]["status"] == "NOT RUN")

    # S8 secrets printed by a stage are redacted in logs and the run record
    p, rec, logs = run(tmp, [["leak", leak]])
    txt = (logs / rec["run_id"] / "01_leak.log").read_text(encoding="utf-8") + json.dumps(rec)
    check("S8", "token / client-secret shaped output is redacted in logs and run record",
          "SECRETtokenVALUE" not in txt and "GOCSPX-SECRET" not in txt and "[REDACTED]" in txt)

    # S9 forbidden script refused
    p, rec, _ = run(tmp, [["x", str(BASE / "scripts" / "keyword_live_update.py")]])
    check("S9", "an Amazon keyword-update script is refused (exit 2, not run)", p.returncode == 2 and "REFUSED" in p.stdout)

    p, rec, _ = run(tmp, [["x", str(BASE / "scripts" / "keyword_live_verify.py")]])
    check("S9b", "keyword_live_verify.py is only allowed in --pending-only (read-only weekly) mode", p.returncode == 2 and "REFUSED" in p.stdout)

    # S10 concurrent run blocked by the lock
    (tmp / "logs" / "run.lock").write_text("123", encoding="utf-8")
    p, _, _ = run(tmp, [["a", ok]])
    check("S10", "a second run while one is in progress is refused (lock)", p.returncode == 3 and "in progress" in p.stdout)
    (tmp / "logs" / "run.lock").unlink()

    # S11 run history
    hist = (tmp / "logs" / "run_history.log").read_text(encoding="utf-8").splitlines()
    check("S11", "run_history.log has one line per completed run with status", len(hist) == 4
          and sum("FAILED" in h for h in hist) == 2 and sum("SUCCESS" in h for h in hist) == 2, hist)

    p_, f_ = sum(r["result"] == "PASS" for r in RESULTS), sum(r["result"] == "FAIL" for r in RESULTS)
    (BASE / "evidence" / "11_scheduler_test_results.json").write_text(json.dumps(
        {"run_at": dt.datetime.now().isoformat(timespec="seconds"), "mode": "fake stage scripts in a temp folder",
         "passed": p_, "failed": f_, "results": RESULTS}, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"{p_} PASS / {f_} FAIL")
    return 0 if f_ == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
