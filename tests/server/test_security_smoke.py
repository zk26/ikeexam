# -*- coding: utf-8 -*-
"""安全边界回归：机器令牌、CSRF、试卷范围、命令 ACK、参与者成绩归属。"""
import io
import json
import os
import re
import sys
import tempfile
import zipfile

_tmp = tempfile.mkdtemp(prefix="exam_security_test_")
os.environ["EXAM_DB"] = os.path.join(_tmp, "test.db")
os.environ["EXAM_AUTH_FILE"] = os.path.join(_tmp, "auth.json")
os.environ["EXAM_DATA"] = _tmp
os.environ.pop("EXAM_ADMIN_PASSWORD", None)

SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "src"))
sys.path.insert(0, SRC_DIR)
from ikeexam.server import app as app_mod, db

db.init()
app_mod.app.config["TESTING"] = True
failures = []


def check(name, condition, detail=""):
    print(("  PASS " if condition else "  FAIL ") + name)
    if not condition:
        failures.append(name)
        if detail:
            print("       " + str(detail)[:300])


def login(client, password="Admin@123"):
    html = client.get("/login").get_data(as_text=True)
    token = (re.search(r'name="_csrf" value="([^"]+)"', html) or [None, ""])[1]
    response = client.post("/login", data={"password": password, "_csrf": token})
    return token, response


def hello(client, host, name, seat="1", token=None, remote="127.0.0.1"):
    payload = {"hostname": host, "client_id": host, "mac": "",
               "name": name, "seat": seat}
    if token:
        payload["token"] = token
    response = client.post("/api/hello", json=payload,
                           environ_base={"REMOTE_ADDR": remote})
    return response, response.get_json() or {}


print("== 1. 首次设密来源与管理员 CSRF ==")
remote = app_mod.app.test_client()
r = remote.post("/admin_api/setup", json={"password": "Admin@123"},
                environ_base={"REMOTE_ADDR": "10.0.0.8"})
check("远程不能抢先设置初始密码", r.status_code == 403, r.get_data(as_text=True))
admin = app_mod.app.test_client()
r = admin.post("/admin_api/setup", json={"password": "Admin@123"})
check("本机可以设置初始密码", r.status_code == 200 and r.get_json().get("ok"))
r = admin.post("/admin_api/login", json={"password": "Admin@123"})
csrf = (r.get_json() or {}).get("csrf_token", "")
check("登录返回 CSRF token", r.status_code == 200 and bool(csrf))
r = admin.post("/admin_api/questions/tidy")
check("管理写请求缺少 CSRF 被拒绝", r.status_code == 400)
r = admin.post("/admin_api/questions/tidy", headers={"X-CSRF-Token": csrf})
check("管理写请求携带 CSRF 成功", r.status_code == 200 and r.get_json().get("ok"))

print("== 2. 机器令牌不能冒用他人身份 ==")
qid = db.add_question({"qtype": "single", "stem": "安全测试题",
                       "options": ["A. 错", "B. 对"], "answer": "B", "score": 2})
other_qid = db.add_question({"qtype": "single", "stem": "不在试卷里的题",
                             "options": ["A. 对", "B. 错"], "answer": "A", "score": 10})
eid = db.create_exam({"title": "安全测试考试", "etype": "", "duration": 30,
                      "theory_duration": 30, "requirement": "", "save_as": "",
                      "require_complete": 0})
db.set_question_types(eid, ["single"])
db.set_exam_questions(eid, [qid])
db.set_exam_status(eid, "running")
db.set_phase(eid, "theory")
db.set_roster(eid, [("1", "受害者"), ("2", "攻击者")])

victim = app_mod.app.test_client()
_, v = hello(victim, "PC-VICTIM", "受害者", "1")
attacker = app_mod.app.test_client()
_, a = hello(attacker, "PC-ATTACKER", "攻击者", "2")
check("两台机器分别取得令牌", bool(v.get("token")) and bool(a.get("token"))
      and v["token"] != a["token"])
r = attacker.post("/api/theory/submit", headers={"X-Exam-Token": a["token"]},
                  json={"host": "PC-VICTIM", "exam_id": eid,
                        "answers": {str(qid): "B"}})
check("攻击者令牌不能提交受害者身份", r.status_code == 403, r.get_json())
r = attacker.get("/api/paper?host=PC-VICTIM")
check("未带令牌不能读取试卷", r.status_code == 403)
r = victim.post("/api/theory/submit", headers={"X-Exam-Token": v["token"]},
                json={"host": "PC-VICTIM", "exam_id": eid,
                      "answers": {str(qid): "B", str(other_qid): "A"}})
check("受害者正常提交", r.status_code == 200 and r.get_json().get("score") == 2,
      r.get_json())
check("试卷外题目不计分", db.theory_score(eid, "PC-VICTIM", v["participant_id"]) == (2.0, 1))
r = attacker.post("/api/theory/finish", headers={"X-Exam-Token": a["token"]},
                  json={"host": "PC-VICTIM", "exam_id": eid})
check("不能冒用受害者交卷", r.status_code == 403)

print("== 3. 填空不能堆叠错误答案骗分 ==")
blank = {"id": -1, "qtype": "blank", "score": 5, "answer": "正确答案"}
check("错误|正确 不得分", db.judge(blank, "错误答案|正确答案") == (0, 0),
      db.judge(blank, "错误答案|正确答案"))
check("多空按空给部分分", db.judge(dict(blank, answer="甲||乙"), "甲||错") == (0, 2.5))

print("== 4. 名单在写接口再次校验 ==")
db.set_roster(eid, [("2", "攻击者")])
r = victim.post("/api/theory/submit", headers={"X-Exam-Token": v["token"]},
                json={"host": "PC-VICTIM", "exam_id": eid,
                      "answers": {str(qid): "B"}})
check("名单移除后不能继续提交", r.status_code == 403, r.get_json())
db.set_roster(eid, [("1", "受害者"), ("2", "攻击者")])

print("== 5. 命令逐机器 ACK ==")
c1 = db.push_command("all", "pause", {"exam_id": eid})
check("两台机器都收到命令", bool(db.fetch_commands("PC-VICTIM", eid, "theory"))
      and bool(db.fetch_commands("PC-ATTACKER", eid, "theory")))
check("重复拉取在 ACK 前仍下发", any(x["id"] == c1 for x in db.fetch_commands("PC-VICTIM", eid, "theory")))
db.ack_commands("PC-VICTIM", [c1])
check("第一台确认后第二台仍待确认", any(x["id"] == c1 for x in db.fetch_commands("PC-ATTACKER", eid, "theory")))
check("第一台确认后不再收到", not any(x["id"] == c1 for x in db.fetch_commands("PC-VICTIM", eid, "theory")))
db.ack_commands("PC-ATTACKER", [c1])
c = db.conn()
status = c.execute("SELECT status FROM commands WHERE id=?", (c1,)).fetchone()[0]
c.close()
check("全部确认后命令完成", status == "acked")

print("== 6. 同一机器换学生成绩分开 ==")
db.set_roster(eid, [("1", "受害者"), ("2", "攻击者"),
                    ("1", "先来的"), ("2", "后来的")])
_, first = hello(victim, "PC-SWAP", "先来的", "1")
victim.post("/api/theory/submit", headers={"X-Exam-Token": first["token"]},
            json={"host": "PC-SWAP", "exam_id": eid,
                  "answers": {str(qid): "B"}})
_, second = hello(victim, "PC-SWAP", "后来的", "2", token=first["token"])
check("换学生后参与者 ID 更新", second.get("participant_id") != first.get("participant_id"),
      second)
victim.post("/api/theory/submit", headers={"X-Exam-Token": second["token"]},
            json={"host": "PC-SWAP", "exam_id": eid,
                  "answers": {str(qid): "A"}})
rows = {r["name"]: r for r in db.full_report(eid) if r["hostname"] == "PC-SWAP"}
check("两名学生分别显示", "先来的" in rows and "后来的" in rows, rows)
if "先来的" in rows and "后来的" in rows:
    check("先来的 2 分，后来的 0 分",
          rows["先来的"]["theory"] == 2 and rows["后来的"]["theory"] == 0, rows)
    check("参与者 ID 不同", rows["先来的"]["participant_id"] != rows["后来的"]["participant_id"])

print("== 6a. 公开看板范围 ==")
screen = admin.get("/screen.json").get_json()
check("公开看板不暴露机器标识", all("hostname" not in row for row in screen.get("rows", [])))
check("公开看板只含本场参与者", {row["name"] for row in screen.get("rows", [])}
      >= {"受害者", "攻击者", "先来的", "后来的"}, screen.get("rows"))

print("== 6b. 已结束考试换考生不再回填旧身份 ==")
db.set_exam_status(eid, "stopped")
swap_eid = db.create_exam({"title": "换考生回归", "etype": "", "duration": 30,
                           "theory_duration": 30})
db.set_question_types(swap_eid, ["single"])
db.set_exam_questions(swap_eid, [qid])
db.set_roster(swap_eid, [("1", "旧考生"), ("2", "新考生")])
db.set_exam_status(swap_eid, "running")
db.set_phase(swap_eid, "theory")
swap_client = app_mod.app.test_client()
_, old_student = hello(swap_client, "PC-END-SWAP", "旧考生", "1")
swap_client.post("/api/theory/submit", headers={"X-Exam-Token": old_student["token"]},
                 json={"host": "PC-END-SWAP", "exam_id": swap_eid,
                       "answers": {str(qid): "B"}})
db.set_exam_status(swap_eid, "stopped")
r, same_student = hello(swap_client, "PC-END-SWAP", "旧考生", "1",
                        token=old_student["token"])
check("原考生在宽限期仍恢复原身份", r.status_code == 200
      and same_student.get("student", {}).get("name") == "旧考生"
      and same_student.get("phase") == "ended", same_student)
r, new_student = hello(swap_client, "PC-END-SWAP", "新考生", "2",
                       token=old_student["token"])
check("换考生使用新姓名座位并回到待考", r.status_code == 200
      and new_student.get("exam") is None and new_student.get("phase") == "idle"
      and new_student.get("student", {}).get("name") == "新考生"
      and new_student.get("student", {}).get("seat") == "2", new_student)
check("换考生保留机器令牌", new_student.get("token") == old_student.get("token"),
      new_student)
check("换考生清除旧当前参与者绑定",
      db.current_participant("PC-END-SWAP", swap_eid) == 0)
swap_rows = {row["name"]: row for row in db.full_report(swap_eid)
             if row["hostname"] == "PC-END-SWAP"}
check("换考生不删除或改写旧成绩", "旧考生" in swap_rows
      and "新考生" not in swap_rows and swap_rows["旧考生"]["theory"] == 2,
      swap_rows)

print("== 7. 多题考试交卷人数按人统计 ==")
pid, _is_new = db.add_practice({"name": "统计题", "etype": "word", "category": "测试",
                       "level": "基础", "materials": [], "requirement": "要求",
                       "save_as": "作业.docx", "checks": [{"id": "1", "type": "title", "score": 5}]})
db.add_task_to_exam(eid, pid)
path = os.path.join(_tmp, "作业.docx")
with open(path, "wb") as f:
    f.write(b"dummy")
swap_pid = rows["先来的"]["participant_id"]
db.save_submission("PC-SWAP", eid, "作业.docx", path, 5, task_id=pid,
                   participant_id=swap_pid)
db.save_submission("PC-SWAP", eid, "缺卷.docx", "", 0, task_id=0,
                   participant_id=swap_pid)
check("同一学生两条提交只算一人", db.stats(eid)["submitted"] == 1, db.stats(eid))

print("== 8. GET 状态变更 ==")
r = admin.get(f"/exam/{eid}/stop")
check("GET 结束考试返回 405", r.status_code == 405, r.status_code)

print("== 9. Office 上传内容校验 ==")
buf = io.BytesIO()
with zipfile.ZipFile(buf, "w") as z:
    z.writestr("[Content_Types].xml", "<Types/>")
    z.writestr("word/document.xml", "<document/>")
valid = app_mod._valid_office_upload(type("U", (), {"filename": "a.docx", "stream": io.BytesIO(buf.getvalue())})())
bad = app_mod._valid_office_upload(type("U", (), {"filename": "a.docx", "stream": io.BytesIO(b"not zip")})())
check("合法 Office 结构通过", valid)
check("伪装 Office 文件被拒绝", not bad)

print("\n" + ("[OK] 安全边界测试全部通过" if not failures
              else f"[FAIL] {len(failures)} 条未过: {failures}"))
sys.exit(1 if failures else 0)
