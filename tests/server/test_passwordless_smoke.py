# -*- coding: utf-8 -*-
"""管理员密码登录、免密码考试与 IP 名单回归。"""
import json
import os
import sys
import tempfile
import urllib.parse

_tmp = tempfile.mkdtemp(prefix="exam_passwordless_test_")
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


def hello(client, host, remote, name="", seat=""):
    response = client.post(
        "/api/hello",
        json={"hostname": host, "client_id": host, "mac": "",
              "name": name, "seat": seat},
        environ_base={"REMOTE_ADDR": remote})
    return response, response.get_json() or {}


print("== 1. 管理员必须使用密码登录 ==")
local = app_mod.app.test_client()
remote = app_mod.app.test_client()
r = local.post("/admin_api/setup", json={"password": "Admin@123"})
check("本机可以设置初始密码", r.status_code == 200 and r.get_json().get("ok"))
r = local.post("/admin_api/login", json={"password": ""})
check("空密码不能登录", r.status_code == 401, r.get_json())
r = remote.post("/admin_api/login", json={"password": ""},
                environ_base={"REMOTE_ADDR": "10.0.0.8"})
check("远程空密码登录被拒绝", r.status_code == 401, r.get_json())
r = local.post("/admin_api/login", json={"password": "Admin@123"})
check("正确密码登录成功", r.status_code == 200 and r.get_json().get("ok"))
csrf = (r.get_json() or {}).get("csrf_token", "")
r = local.post("/admin_api/passwordless", json={"enabled": True},
               headers={"X-ikeexam-Local": "1", "X-CSRF-Token": csrf})
check("管理员免密码接口已移除", r.status_code == 404, r.get_data(as_text=True))

print("== 2. IP 名单解析 ==")
rows = app_mod._roster_rows(
    "小明,01,10.0.0.10\n10.0.0.11\n小红,10.0.0.12\nIP,03,10.0.0.13")
check("姓名座位 IP 格式", ("01", "小明", "10.0.0.10") in rows, rows)
check("纯 IP 格式", ("", "IP学生", "10.0.0.11") in rows, rows)
check("姓名 IP 格式", ("", "小红", "10.0.0.12") in rows, rows)
check("IP 座位格式", ("03", "IP", "10.0.0.13") in rows, rows)

print("== 3. 老师开考时控制免密码考试 ==")
eid = db.create_exam({"title": "免密码考试", "etype": "", "duration": 30,
                      "theory_duration": 30})
db.set_question_types(eid, ["single"])
qid = db.add_question({"qtype": "single", "stem": "1+1=?", "options": ["1", "2"],
                      "answer": "B", "score": 2})
db.set_exam_questions(eid, [qid])
db.set_roster(eid, [("01", "小明", "10.0.0.10"),
                    ("", "IP学生", "10.0.0.11")])
r = local.get(f"/exam/{eid}")
check("老师编辑页不再出现免密码开关", r.status_code == 200
      and 'name="passwordless"' not in r.get_data(as_text=True))
r = local.get(f"/exam/{eid}/start")
check("网页开考弹窗包含免密码开关", r.status_code == 200
      and 'name="passwordless"' in r.get_data(as_text=True))
r = local.put(f"/admin_api/exam/{eid}", headers={"X-CSRF-Token": csrf},
              json={"passwordless": True})
check("编辑接口不能偷偷开启免密码", r.status_code == 200
      and not db.get_exam(eid).get("passwordless"), r.get_json())
r = local.post(f"/admin_api/exam/{eid}/start",
               headers={"X-CSRF-Token": csrf},
               json={"klass": "免密码验收班", "passwordless": True})
check("开考接口按老师选择开启免密码", r.status_code == 200
      and db.get_exam(eid).get("passwordless") == 1, r.get_json())
known = app_mod.app.test_client()
r, data = hello(known, "PC-10", "10.0.0.10")
check("名单 IP 自动匹配身份", r.status_code == 200
      and data.get("student", {}).get("name") == "小明"
      and data.get("student", {}).get("seat") == "01"
      and data.get("exam", {}).get("passwordless") is True, data)
progress = {"section": "single", "current_question": 3,
            "question_total": 20, "theory_answered": 5,
            "current_task": "打地鼠", "status": "working"}
query = urllib.parse.urlencode({"host": "PC-10", "client_id": "PC-10",
                                "token": data.get("token", ""),
                                "progress": json.dumps(progress, ensure_ascii=False)})
r = known.get("/api/poll?" + query,
              environ_base={"REMOTE_ADDR": "10.0.0.10"})
check("学生心跳上报当前题目", r.status_code == 200
      and r.get_json().get("passwordless") is True, r.get_json())
stored = next((student for student in db.list_students()
               if student.get("hostname") == "PC-10"), {})
check("服务端保存实时进度", stored.get("progress", {}).get("current_question") == 3
      and stored.get("progress", {}).get("current_task") == "打地鼠",
      stored)
r = local.get("/admin_api/dashboard/students")
rows = (r.get_json() or {}).get("students") or []
check("老师端学生接口返回进度", any(row.get("hostname") == "PC-10"
      and row.get("progress", {}).get("current_question") == 3
      and row.get("progress", {}).get("current_task") == "打地鼠" for row in rows), rows)
unknown = app_mod.app.test_client()
r, data = hello(unknown, "PC-99", "10.0.0.99")
check("未知 IP 被名单拒绝", r.status_code == 403
      and "不在" in data.get("msg", ""), data)
report = db.full_report(eid)
check("汇总包含座位号和 IP", bool(report)
      and report[0].get("seat") == "01"
      and report[0].get("ip") == "10.0.0.10", report)

print("== 4. 未配 IP 名单时自动生成 IP 身份 ==")
db.set_exam_status(eid, "stopped")
eid2 = db.create_exam({"title": "开放免密码考试", "etype": "", "duration": 30,
                       "theory_duration": 30})
db.set_question_types(eid2, ["single"])
db.set_exam_questions(eid2, [qid])
r = local.post(f"/admin_api/exam/{eid2}/start",
               headers={"X-CSRF-Token": csrf},
               json={"klass": "开放免密码班", "passwordless": True})
check("空名单免密码考试可开考", r.status_code == 200
      and db.get_exam(eid2).get("passwordless") == 1, r.get_json())
open_client = app_mod.app.test_client()
r, data = hello(open_client, "PC-12", "10.0.0.12")
check("空名单按 IP 自动入考", r.status_code == 200
      and data.get("student", {}).get("name") == "IP-10.0.0.12", data)

db.set_exam_status(eid2, "stopped")
eid3 = db.create_exam({"title": "普通考试", "etype": "", "duration": 30,
                       "theory_duration": 30, "passwordless": 1})
db.set_question_types(eid3, ["single"])
db.set_exam_questions(eid3, [qid])
r = local.post(f"/admin_api/exam/{eid3}/start",
               headers={"X-CSRF-Token": csrf},
               json={"klass": "普通班", "passwordless": False})
check("老师不勾选时普通考试不自动入考", r.status_code == 200
      and db.get_exam(eid3).get("passwordless") == 0, r.get_json())

root = getattr(app_mod.app, "root_path", "")
if failures:
    print(f"[FAIL] {len(failures)} 项失败: {failures}")
    raise SystemExit(1)
print("[OK] 免密码与 IP 名单测试全部通过")
