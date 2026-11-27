# -*- coding: utf-8 -*-
"""成绩锁定、历史身份与跨阶段理论答案回归。"""
import os
import sys
import tempfile
import time
import json

_tmp = tempfile.mkdtemp(prefix="exam_score_lock_test_")
os.environ["EXAM_DB"] = os.path.join(_tmp, "test.db")
os.environ["EXAM_AUTH_FILE"] = os.path.join(_tmp, "auth.json")
os.environ["EXAM_DATA"] = _tmp
os.environ["EXAM_ADMIN_PASSWORD"] = "Admin@123"

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

from ikeexam.server import app as app_mod
from ikeexam.server import db

db.init()
app_mod.app.config["TESTING"] = True
client = app_mod.app.test_client()
login = client.post("/admin_api/login", json={"password": "Admin@123"}).get_json()
headers = {"X-CSRF-Token": login["csrf_token"]}

qid = db.add_question({"qtype": "single", "stem": "1+1=?", "options": ["甲", "乙"],
                      "answer": "A", "score": 5})
eid = db.create_exam({"title": "锁库测试", "etype": "", "duration": 30,
                      "theory_duration": 30})
db.set_question_types(eid, ["single"])
db.set_exam_questions(eid, [qid])
db.set_exam_status(eid, "running")
hello = client.post("/api/hello", json={"host": "LOCK-PC", "name": "小明",
                                        "seat": "01", "ip": "192.168.1.20"},
                    environ_base={"REMOTE_ADDR": "192.168.1.20"}).get_json()
auth = {"X-Exam-Token": hello["token"]}
db.set_phase(eid, "practice")
response = client.post("/api/theory/submit", headers=auth,
                       json={"host": "LOCK-PC", "exam_id": eid,
                             "answers": {str(qid): "A"}})
assert response.get_json().get("ok"), response.get_json()
assert db.theory_score(eid, "LOCK-PC", hello["participant_id"])[0] == 5

stop = client.post(f"/admin_api/exam/{eid}/stop", headers=headers,
                   json={"clear_login": True})
assert stop.status_code == 200
client.post("/api/hello", json={"host": "LOCK-PC", "name": "小红",
                                "seat": "02", "ip": "192.168.1.21"},
            environ_base={"REMOTE_ADDR": "192.168.1.21"})
rows = db.full_report(eid)
assert len(rows) == 1, rows
assert rows[0]["name"] == "小明" and rows[0]["seat"] == "01"
assert rows[0]["ip"] == "192.168.1.20" and rows[0]["theory"] == 5
locked = client.put(f"/admin_api/exam/{eid}", headers=headers,
                    json={"title": "不应修改", "duration": 31})
assert locked.status_code == 400, locked.get_json()
final = client.post("/api/theory/submit", headers=auth,
                    json={"host": "LOCK-PC", "exam_id": eid,
                          "answers": {str(qid): "A"}})
assert final.status_code == 200, final.get_json()
conn = db.conn()
conn.execute("UPDATE exams SET stop_at=? WHERE id=?",
             (time.time() - app_mod.FINAL_SUBMIT_GRACE_SECONDS - 1, eid))
conn.commit()
conn.close()
write = client.post("/api/theory/submit", headers=auth,
                    json={"host": "LOCK-PC", "exam_id": eid,
                          "answers": {str(qid): "B"}})
assert write.status_code == 403
assert db.full_report(eid)[0]["theory"] == 5
for method, path, payload in (
        ("DELETE", f"/admin_api/exam/{eid}", None),
        ("POST", f"/admin_api/exam/{eid}/start", {"klass": "一班"}),
        ("PUT", f"/admin_api/exam/{eid}/roster", {"text": "小红,02"}),
        ("POST", f"/admin_api/exam/{eid}/regrade", {}),
        ("POST", f"/admin_api/exam/{eid}/rejudge-code", {})):
    response = getattr(client, method.lower())(path, headers=headers, json=payload)
    assert response.status_code == 400, (path, response.status_code, response.get_json())
for path in (f"/exam/{eid}/delete", f"/exam/{eid}/start",
             f"/scores/{eid}/regrade", f"/scores/{eid}/rejudge-code"):
    response = client.post(path, headers=headers, data={})
    assert response.status_code == 302, (path, response.status_code)
    assert db.get_exam(eid), path

copy = client.post(f"/admin_api/exam/{eid}/copy", headers=headers, json={})
assert copy.status_code == 200 and copy.get_json()["exam"]["title"].endswith("-副本")
copy_id = copy.get_json()["exam"]["id"]
pid, _ = db.add_practice({"name": "素材去重", "etype": "word", "category": "测试",
                          "level": "基础", "materials": ["shared.docx"],
                          "requirement": "", "save_as": "答案.docx", "checks": []})
updated = client.put(f"/admin_api/exam/{copy_id}", headers=headers,
                     json={"materials": ["shared.docx", "extra.txt"],
                           "practice_ids": [pid]})
assert updated.status_code == 200, updated.get_json()
assert json.loads(db.get_exam(copy_id)["materials"] or "[]") == ["extra.txt"]
batch = client.post("/admin_api/exams/export", headers=headers,
                    json={"ids": [eid, copy_id]})
assert batch.status_code == 200
assert "spreadsheetml.sheet" in batch.content_type
from io import BytesIO
from openpyxl import load_workbook
workbook = load_workbook(BytesIO(batch.data), read_only=True)
assert len(workbook.sheetnames) == 2, workbook.sheetnames
print("[OK] 跨阶段理论答案计分，结束后身份与成绩锁定，复制与多场导出正常")
