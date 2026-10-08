"""Publish the report to tech_team_outputs.ph_task for Paulroshan (assigned_user 'paulr').

No WTMA row yet -> INSERT (V001). Exactly one WTMA row for the user -> UPDATE it in place (new
version), only while it has not been actioned (action_took_by is NULL). DSN: DATABASE_URL.
The identity sequence can lag behind max(id) (explicit-id inserts by other publishers), so it is
advanced with nextval() before an insert; ids are never set explicitly.
"""
import datetime as dt
import hashlib
import json
import os
import pathlib
import shutil

import psycopg

BASE = pathlib.Path(__file__).resolve().parent.parent
REPORT = BASE / "output" / "weekly_top_moving_asin_backend_keyword_fine_tuning_report.html"
DS = json.loads((BASE / "data" / "report_dataset.json").read_text(encoding="utf-8"))
T = "tech_team_outputs.ph_task"
USER, TEAM, CODE, VERSION = "paulr", "ph_priors", "WTMA", 8


def main():
    M = DS["meta"]
    assert M["ph_scope"]["user_id"] == 49 and len(DS["performance"]) == 50
    today = dt.date.today().isoformat()
    task_id = f"{today}_{USER}_dashboard_V{VERSION:03d}"
    release = BASE / "output" / f"{task_id}.html"
    shutil.copyfile(REPORT, release)
    html = release.read_text(encoding="utf-8")
    md5 = hashlib.md5(html.encode("utf-8")).hexdigest()
    cw = "–".join(dt.date.fromisoformat(x).strftime("%d %b %Y") for x in M["current_7d"])
    row = {
        "project_name": "Weekly Top-Moving ASIN Performance Drop & Backend Keyword Fine-Tuning Report",
        "project_code": CODE,
        "task_name": f"{USER} - Top {M['top_moving_n']} Wire Cage ASINs: {DS['kpi']['performance_drop']} "
                     f"performance drops, " + (
                         f"{DS['kpi']['posts_accepted']} backend keyword updates accepted by Amazon, under monitoring "
                         f"from {dt.date.fromisoformat(DS['monitoring_week_orders']['anchor']).strftime('%d %b %Y')} "
                         f"(Week 1 + Week 2) - {cw}"
                         if DS["kpi"].get("posts_accepted")
                         else f"{DS['kpi']['finetune_proposed']} backend keyword fine-tunes proposed - {cw}"),
        "task_id": task_id, "team": "Development", "developer": "Apirame", "assigned_user": USER,
        "html_content": html,
        "description": ("Amazon UK weekly report for PH Paulroshan (Wire Cage) only: top 50 moving ASINs, "
                        "Current 7D vs Previous 7D orders/impressions/clicks/CTR/CVR, backend keyword "
                        "duplicate clean-up, change record and Week 1 / Week 2 post-update monitoring."),
        "phase_level": 1, "version_level": VERSION, "version_status": "released",
        "assigned_user_team": TEAM,
    }

    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as con:
        known = con.execute(f"SELECT count(*) FROM {T} WHERE assigned_user=%s AND assigned_user_team=%s",
                            (USER, TEAM)).fetchone()[0]
        assert known > 0, f"{USER}/{TEAM} not found in ph_task"
        before = con.execute(f"SELECT count(*) FROM {T} WHERE project_code=%s", (CODE,)).fetchone()[0]
        assert before in (0, 1), f"{before} {CODE} rows; expected 0 (insert) or 1 (update)"
        if before == 1:  # new version of the existing card, updated in place
            with con.transaction():
                cur_id, cur_user, cur_ver, acted = con.execute(
                    f"SELECT id, assigned_user, version_level, action_took_by FROM {T} WHERE project_code=%s FOR UPDATE",
                    (CODE,)).fetchone()
                assert cur_user == USER and acted is None and cur_ver < VERSION, (cur_id, cur_user, cur_ver, acted)
                upd = {c: row[c] for c in ("task_name", "task_id", "html_content", "description",
                                           "version_level", "version_status")}
                n = con.execute(f"UPDATE {T} SET {', '.join(c + '=%s' for c in upd)}, updated_at=now() "
                                f"WHERE id=%s AND project_code=%s AND assigned_user=%s",
                                [*upd.values(), cur_id, CODE, USER]).rowcount
                got = con.execute(f"SELECT md5(html_content), version_level, task_id FROM {T} WHERE id=%s",
                                  (cur_id,)).fetchone()
                after = con.execute(f"SELECT count(*) FROM {T} WHERE project_code=%s", (CODE,)).fetchone()[0]
                if n != 1 or got != (md5, VERSION, task_id) or after != 1:
                    raise RuntimeError(f"verification failed, rolled back: {n} {got} {after}")
            print(json.dumps({"id": cur_id, "updated_from_version": cur_ver, "task_id": task_id, "md5": md5,
                              "bytes": len(html.encode()), "release_file": str(release)}, indent=1))
            return
        top = con.execute(f"SELECT max(id) FROM {T}").fetchone()[0]
        seq = con.execute(f"SELECT pg_get_serial_sequence('{T}', 'id')").fetchone()[0]
        n = 0
        while con.execute("SELECT nextval(%s)", (seq,)).fetchone()[0] < top:
            n += 1
        print(f"sequence advanced {n + 1} step(s) past max(id) {top}")

        with con.transaction():
            cols = list(row)
            new_id = con.execute(
                f"INSERT INTO {T} ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))}) RETURNING id",
                [row[c] for c in cols]).fetchone()[0]
            got = con.execute(f"SELECT md5(html_content), assigned_user, project_code FROM {T} WHERE id=%s",
                              (new_id,)).fetchone()
            after = con.execute(f"SELECT count(*) FROM {T} WHERE project_code=%s", (CODE,)).fetchone()[0]
            if got != (md5, USER, CODE) or after != before + 1:
                raise RuntimeError(f"verification failed, rolled back: {got} {after}")
    print(json.dumps({"id": new_id, "task_id": task_id, "md5": md5, "bytes": len(html.encode()),
                      "release_file": str(release)}, indent=1))


if __name__ == "__main__":
    main()
