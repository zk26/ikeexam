# -*- coding: utf-8 -*-
"""参与者身份、打字重练与组合题型交卷状态回归。"""
import os
import sys
import tempfile

_tmp = tempfile.TemporaryDirectory(prefix="exam_lifecycle_test_", ignore_cleanup_errors=True)
os.environ["EXAM_DB"] = os.path.join(_tmp.name, "test.db")
os.environ["EXAM_AUTH_FILE"] = os.path.join(_tmp.name, "auth.json")
os.environ["EXAM_DATA"] = _tmp.name
os.environ["EXAM_ADMIN_PASSWORD"] = "Admin@123"

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))
from ikeexam.server import app as app_mod, db

db.init()
app_mod.app.config["TESTING"] = True
api = app_mod.app.test_client()


def make_exam(title, types):
    eid = db.create_exam({"title": title, "etype": "", "duration": 30,
                          "theory_duration": 30})
    db.set_question_types(eid, types)
    db.set_exam_status(eid, "running")
    db.set_phase(eid, "theory")
    return eid


def main():
    waiting = api.post("/api/hello", json={"hostname": "WAITING-PC", "name": "待考学生",
                                          "seat": "01", "mac": "00-00-00-00-00-09"})
    waiting_data = waiting.get_json() or {}
    assert waiting.status_code == 200 and waiting_data.get("ok"), waiting_data
    assert waiting_data.get("exam") is None and waiting_data.get("phase") == "idle", waiting_data
    assert waiting_data.get("token") and db.get_student("WAITING-PC"), waiting_data
    replay = api.post("/api/hello", json={"hostname": "WAITING-PC", "name": "待考学生",
                                          "seat": "01", "mac": "00-00-00-00-00-09",
                                          "token": waiting_data["token"]})
    assert replay.status_code == 200 and replay.get_json().get("phase") == "idle", replay.get_json()
    no_token = api.post("/api/hello", json={"hostname": "WAITING-PC", "name": "待考学生",
                                            "seat": "01", "mac": "00-00-00-00-00-09"})
    assert no_token.status_code == 403 and "机器凭证" in no_token.get_json().get("msg", ""), no_token.get_json()
    missing_profile = api.post("/api/hello", json={"hostname": "EMPTY-PROFILE-PC"})
    assert missing_profile.status_code == 403 and "姓名和座位号" in missing_profile.get_json().get("msg", ""), missing_profile.get_json()
    assert not db.get_student("EMPTY-PROFILE-PC")
    db.delete_student(db.get_student("WAITING-PC")["id"])

    eid = make_exam("打字重练", ["typing"])
    pid = db.add_participant(eid, "TYPE-PC", "小明", "01", "127.0.0.1")
    db.set_current_participant("TYPE-PC", eid, pid)
    base = {"task": "typing_zh", "progress": 5, "total": 10, "hits": 5,
            "typed": 20, "correct_chars": 20, "elapsed_ms": 5000,
            "wpm": 10, "accuracy": 100, "reaction_ms": 0, "finished": 1}
    db.save_typing_report(eid, "TYPE-PC", [dict(base, attempt=1)], pid,
                          allowed_tasks={"typing_zh"})
    db.save_typing_report(eid, "TYPE-PC", [dict(base, attempt=0, typed=0)], pid,
                          allowed_tasks={"typing_zh"})
    c = db.conn()
    row = c.execute("SELECT * FROM typing_results WHERE exam_id=? AND participant_id=?",
                    (eid, pid)).fetchone()
    c.close()
    assert row["attempt"] == 1 and row["typed"] == 20, dict(row)
    db.save_typing_report(eid, "TYPE-PC", [dict(base, attempt=2, progress=0,
                                               hits=0, typed=0, correct_chars=0,
                                               elapsed_ms=0, finished=0)], pid,
                          allowed_tasks={"typing_zh"})
    c = db.conn()
    row = c.execute("SELECT * FROM typing_results WHERE exam_id=? AND participant_id=?",
                    (eid, pid)).fetchone()
    c.close()
    assert row["attempt"] == 2 and row["typed"] == 0 and row["finished"] == 0, dict(row)
    db.save_typing_report(eid, "TYPE-PC", [dict(base, attempt=2, typed=0)], pid,
                          allowed_tasks={"typing_zh"})
    c = db.conn()
    assert c.execute("SELECT typed FROM typing_results WHERE exam_id=? AND participant_id=?",
                     (eid, pid)).fetchone()[0] == 0
    c.close()

    conflict_eid = make_exam("资料冲突", ["single"])
    first_pid = db.add_participant(conflict_eid, "EDIT-PC", "小明", "01", "127.0.0.1")
    second_pid = db.add_participant(conflict_eid, "EDIT-PC", "小红", "02", "127.0.0.1")
    db.upsert_student("EDIT-PC", "127.0.0.1", "", name="小红", seat="02")
    db.set_current_participant("EDIT-PC", conflict_eid, second_pid)
    try:
        db.update_student_profile_atomic("EDIT-PC", "小明", "01")
    except ValueError:
        pass
    else:
        raise AssertionError("duplicate participant profile accepted")
    assert db.get_student("EDIT-PC")["name"] == "小红"
    assert db.participant_snapshot(conflict_eid, first_pid)["name"] == "小明"
    db.set_exam_status(eid, "stopped")
    db.set_exam_status(conflict_eid, "stopped")

    grace_eid = make_exam("收卷宽限期", ["single"])
    qid = db.add_question({"qtype": "single", "stem": "宽限测试", "options": ["A", "B"],
                           "answer": "A", "score": 2})
    db.set_exam_questions(grace_eid, [qid])
    db.set_roster(grace_eid, [("01", "小明")])
    first = api.post("/api/hello", json={"hostname": "GRACE-PC", "name": "小明",
                                         "seat": "01", "mac": ""}).get_json()
    assert first.get("ok"), first
    assert db.update_student_profile_atomic("GRACE-PC", "修正姓名", "09")
    db.set_exam_status(grace_eid, "stopped")
    reconnected = api.post("/api/hello", json={
        "hostname": "GRACE-PC", "name": "小明", "seat": "01", "mac": "",
        "token": first["token"]})
    data = reconnected.get_json() or {}
    assert reconnected.status_code == 200 and data.get("student", {}).get("name") == "修正姓名", data
    assert data.get("participant_id") == first["participant_id"], data
    newcomer = api.post("/api/hello", json={"hostname": "NEW-PC", "name": "小明",
                                            "seat": "01", "mac": ""})
    newcomer_data = newcomer.get_json() or {}
    assert newcomer.status_code == 200 and newcomer_data.get("exam") is None, newcomer_data
    assert newcomer_data.get("phase") == "idle", newcomer_data

    combo_eid = make_exam("理论加打字", ["single", "typing"])
    combo_qid = db.add_question({"qtype": "single", "stem": "组合测试", "options": ["A", "B"],
                                 "answer": "A", "score": 2})
    db.set_exam_questions(combo_eid, [combo_qid])
    combo_pid = db.add_participant(combo_eid, "COMBO-PC", "小明", "01", "127.0.0.1")
    db.upsert_student("COMBO-PC", "127.0.0.1", "", name="小明", seat="01")
    db.set_current_participant("COMBO-PC", combo_eid, combo_pid)
    combo_tasks = app_mod._enabled_typing_tasks(db.get_exam(combo_eid))
    db.save_typing_report(combo_eid, "COMBO-PC",
                          [dict(base, task=task, finished=1) for task in combo_tasks],
                          combo_pid, allowed_tasks=combo_tasks)
    assert not app_mod._refresh_submission_status(combo_eid, "COMBO-PC", combo_pid)
    db.save_theory_answers(combo_eid, "COMBO-PC", {str(combo_qid): "A"}, combo_pid)
    assert app_mod._refresh_submission_status(combo_eid, "COMBO-PC", combo_pid)

    report_cases = []
    typing_eid = make_exam("纯打字报告", ["typing"])
    typing_pid = db.add_participant(typing_eid, "REPORT-TYPE", "小红", "02", "127.0.0.1")
    typing_tasks = app_mod._enabled_typing_tasks(db.get_exam(typing_eid))
    db.save_typing_report(typing_eid, "REPORT-TYPE",
                          [dict(base, task=task, finished=1) for task in typing_tasks],
                          typing_pid, allowed_tasks=typing_tasks)
    assert app_mod._refresh_submission_status(typing_eid, "REPORT-TYPE", typing_pid)
    report_cases.append((typing_eid, typing_pid))

    theory_eid = make_exam("纯理论报告", ["single"])
    report_qid = db.add_question({"qtype": "single", "stem": "报告测试",
                                  "options": ["A", "B"], "answer": "A", "score": 2})
    db.set_exam_questions(theory_eid, [report_qid])
    theory_pid = db.add_participant(theory_eid, "REPORT-THEORY", "小刚", "03", "127.0.0.1")
    db.save_theory_answers(theory_eid, "REPORT-THEORY", {str(report_qid): "A"}, theory_pid)
    assert app_mod._refresh_submission_status(theory_eid, "REPORT-THEORY", theory_pid)
    report_cases.append((theory_eid, theory_pid))

    drawing_eid = make_exam("纯画图报告", ["drawing"])
    drawing_pid = db.add_participant(drawing_eid, "REPORT-DRAW", "小美", "04", "127.0.0.1")
    db.save_drawing(drawing_eid, "REPORT-DRAW", "draw.png", "draw.png", 10,
                    participant_id=drawing_pid)
    assert app_mod._refresh_submission_status(drawing_eid, "REPORT-DRAW", drawing_pid)
    report_cases.append((drawing_eid, drawing_pid))

    for report_eid, report_pid in report_cases:
        row = next(r for r in db.full_report(report_eid)
                   if r["participant_id"] == report_pid)
        assert row["submitted"] is True, row
    print("[OK] 打字重练版本、资料冲突、宽限期身份、组合题型与非实操报告通过")


if __name__ == "__main__":
    try:
        main()
    finally:
        _tmp.cleanup()
