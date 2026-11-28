# -*- coding: utf-8 -*-
"""桌面老师端管理员 API 全流程回归。"""
import io
import json
import os
import re
import sys
import tempfile
import zipfile

_tmp = tempfile.mkdtemp(prefix="exam_admin_test_")
os.environ["EXAM_DB"] = os.path.join(_tmp, "test.db")
os.environ["EXAM_AUTH_FILE"] = os.path.join(_tmp, "auth.json")
os.environ["EXAM_DATA"] = _tmp
os.environ["EXAM_ADMIN_PASSWORD"] = "Admin@123"

SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "src"))
sys.path.insert(0, SRC_DIR)
from ikeexam.server import app as app_mod, db

db.init()
app_mod.app.config["TESTING"] = True
api = app_mod.app.test_client()
failures = []


def check(name, condition, detail=""):
    print(("  PASS " if condition else "  FAIL ") + name)
    if not condition:
        failures.append(name)
        if detail:
            print("       " + str(detail)[:400])


def call(method, path, data=None, headers=None):
    kwargs = {"headers": headers or {}}
    if data is not None:
        kwargs["json"] = data
    return getattr(api, method.lower())(path, **kwargs)


print("== 1. 登录与总览 ==")
r = api.post("/admin_api/login", json={"password": "Admin@123"})
csrf = (r.get_json() or {}).get("csrf_token", "")
H = {"X-CSRF-Token": csrf}
check("管理员登录", r.status_code == 200 and bool(csrf), r.get_json())
r = call("GET", "/admin_api/dashboard")
check("总览含考试/题库/实操/学生", r.status_code == 200
      and all(k in r.get_json() for k in ("exams", "questions", "practice", "students")), r.get_json())

print("== 2. 理论题 CRUD 与导入 ==")
question = {"qtype": "single", "stem": "管理员测试题",
            "options": ["甲", "乙"], "answer": "A", "score": 3,
            "category": "测试", "difficulty": "基础", "source": "自创题"}
r = call("POST", "/admin_api/questions", question, H)
qid = ((r.get_json() or {}).get("question") or {}).get("id")
check("新增理论题", r.status_code == 200 and qid, r.get_json())
r = call("PUT", f"/admin_api/question/{qid}", dict(question, stem="修改后的题", score=4), H)
check("修改理论题", r.status_code == 200
      and r.get_json()["question"]["stem"] == "修改后的题", r.get_json())
r = call("POST", "/admin_api/questions/import", {"items": [dict(question, stem="导入题")]}, H)
check("JSON 导入理论题", r.status_code == 200 and r.get_json().get("added") == 1, r.get_json())
r = call("POST", "/admin_api/questions/import", {"items": [{"stem": "x"}] * 10001}, H)
check("JSON 导入拒绝超条目预算", r.status_code == 413, r.get_json())
r = call("POST", "/admin_api/questions/import", {"items": [dict(question, stem="x" * (app_mod.QUESTION_IMPORT_MAX_FIELD_BYTES + 1))]}, H)
check("JSON 导入拒绝超长题干", r.status_code == 413, r.get_json())
r = call("POST", "/admin_api/questions/tidy", {}, H)
check("整理分类", r.status_code == 200 and r.get_json().get("ok"), r.get_json())

print("== 3. 实操题 CRUD ==")
task = {"name": "管理员实操题", "etype": "word", "category": "测试",
        "level": "基础", "materials": [], "requirement": "完成排版",
        "save_as": "作业.docx", "checks": [{"id": "标题", "type": "title", "score": 5}]}
r = call("POST", "/admin_api/practice", task, H)
pid = ((r.get_json() or {}).get("practice") or {}).get("id")
check("新增实操题", r.status_code == 200 and pid, r.get_json())
r = call("PUT", f"/admin_api/practice/{pid}", dict(task, name="改名后的实操题"), H)
check("改名仍更新原记录", r.status_code == 200
      and r.get_json()["practice"]["id"] == pid
      and r.get_json()["practice"]["name"] == "改名后的实操题", r.get_json())

print("== 4. 创建考试、组卷、名单与素材 ==")
exam = {"title": "管理员流程考试", "etype": ["word"], "duration": 40,
        "theory_duration": 20, "klass": "七年级1班", "save_as": "座位号_姓名.docx",
        "requirement": "完成文档", "finish_note": "考试结束", "require_complete": False,
        "question_types": ["single", "practice"], "question_ids": [qid],
        "practice_ids": [pid], "roster": "小明,01\n小红,02", "materials": [],
        "checks": [], "typing_config": {}, "drawing_config": {"brief": "", "styles": ["draw"], "grid": 28}}
r = call("POST", "/admin_api/exams", exam, H)
eid = ((r.get_json() or {}).get("exam") or {}).get("id")
check("创建完整考试", r.status_code == 200 and eid, r.get_json())
_created_exam = (r.get_json() or {}).get("exam") or {}
check("创建时忽略分开的理论时长",
      _created_exam.get("duration") == 40 and _created_exam.get("theory_duration") == 40,
      _created_exam)
check("创建时考试级文件名不采用教师输入", _created_exam.get("save_as") == "",
      _created_exam.get("save_as"))
r = call("PUT", f"/admin_api/exam/{eid}",
         dict(exam, duration=55, theory_duration=7, save_as="教师填写.docx"), H)
_updated_exam = (r.get_json() or {}).get("exam") or {}
check("更新时总时长同步且忽略教师文件名",
      r.status_code == 200 and _updated_exam.get("duration") == 55
      and _updated_exam.get("theory_duration") == 55
      and _updated_exam.get("save_as") == "", _updated_exam)
check("多任务实操保留任务自身文件名",
      app_mod._exam_save_as(db.get_exam(eid), db.exam_tasks(eid)) == "作业.docx",
      app_mod._exam_save_as(db.get_exam(eid), db.exam_tasks(eid)))
r = call("GET", f"/admin_api/exam/{eid}")
detail = r.get_json() or {}
check("考试详情包含选题/名单/配置", detail.get("question_ids") == [qid]
      and detail.get("practice_ids") == [pid] and "小明" in detail.get("roster", ""), detail)
r = call("PUT", f"/admin_api/exam/{eid}/roster", {"text": "小明,01\n小红,02\n小刚,03", "mode": "append"}, H)
check("追加名单去重", r.status_code == 200 and r.get_json().get("count") == 3, r.get_json())

buf = io.BytesIO()
with zipfile.ZipFile(buf, "w") as z:
    z.writestr("[Content_Types].xml", "<Types/>")
    z.writestr("word/document.xml", "<document/>")
r = api.post(f"/admin_api/exam/{eid}/materials", headers=H,
             data={"file": (io.BytesIO(buf.getvalue()), "素材.docx")},
             content_type="multipart/form-data")
check("上传合法 Office 素材", r.status_code == 200
      and "素材.docx" in r.get_json().get("materials", []), r.get_json())
r = api.post(f"/admin_api/exam/{eid}/materials", headers=H,
             data={"file": (io.BytesIO(b"fake"), "伪造.docx")},
             content_type="multipart/form-data")
check("拒绝伪装 Office 素材", r.status_code == 400, r.get_json())
r = call("DELETE", f"/admin_api/exam/{eid}/materials/素材.docx", headers=H)
check("删除素材", r.status_code == 200 and not r.get_json().get("materials"), r.get_json())

print("== 4b. 学生端自动实操文件名 ==")
for _index, (_kind, _ext) in enumerate((("word", ".docx"), ("excel", ".xlsx"),
                                        ("ppt", ".pptx")), 1):
    _legacy_eid = db.create_exam({"title": f"自动文件名-{_kind}", "etype": _kind,
                                  "duration": 30, "theory_duration": 30,
                                  "save_as": ""})
    db.set_question_types(_legacy_eid, ["practice"])
    db.set_exam_status(_legacy_eid, "running")
    db.set_phase(_legacy_eid, "theory")
    _hello = api.post("/api/hello", json={
        "hostname": f"AUTO-NAME-{_index}", "name": "小明", "seat": f"0{_index}"
    }).get_json() or {}
    check(f"{_kind} 无任务时 API 返回系统文件名",
          _hello.get("exam", {}).get("save_as") == f"座位号_姓名{_ext}"
          and _hello.get("practice", {}).get("save_as") == f"座位号_姓名{_ext}",
          _hello.get("practice"))
    db.set_exam_status(_legacy_eid, "stopped")
    db.set_phase(_legacy_eid, "ended")
    db.delete_exam(_legacy_eid)

print("== 5. 考试控制与成绩导出 ==")
r = call("POST", f"/admin_api/exam/{eid}/start", {"klass": "七年级1班"}, H)
check("开始考试", r.status_code == 200
      and r.get_json()["exam"]["status"] == "running", r.get_json())
home_html = api.get("/").get_data(as_text=True)
check("网页考试状态显示醒目图标", "exam-list-table .tag .ic" in home_html
      and "M7 4.5l12 7.5-12 7.5z" in home_html)
r = call("PUT", f"/admin_api/exam/{eid}", dict(exam, title="不应保存"), H)
check("进行中锁定配置", r.status_code == 400, r.get_json())
r = call("POST", f"/admin_api/exam/{eid}/pause", {}, H)
check("暂停考试", r.status_code == 200 and r.get_json()["exam"]["status"] == "paused", r.get_json())
r = call("POST", f"/admin_api/exam/{eid}/resume", {}, H)
check("恢复考试", r.status_code == 200 and r.get_json()["exam"]["status"] == "running", r.get_json())
r = call("POST", f"/admin_api/exam/{eid}/phase", {"phase": "practice"}, H)
check("切换实操阶段", r.status_code == 200 and r.get_json()["exam"]["phase"] == "practice", r.get_json())
r = call("GET", f"/admin_api/exam/{eid}/scores")
check("成绩接口", r.status_code == 200 and "rows" in r.get_json(), r.get_json())
scores_html = api.get(f"/scores/{eid}").get_data(as_text=True)
check("网页成绩表独立横向滚动", 'class="table-scroll"' in scores_html)
check("网页表头同步导航高度", '--nav-h' in scores_html)
r = call("GET", f"/admin_api/exam/{eid}/review")
check("评讲接口", r.status_code == 200 and r.get_json().get("ok")
      and "cards" in r.get_json() and "task_cards" in r.get_json(), r.get_json())
r = call("GET", f"/admin_api/exam/{eid}/stats")
check("图表接口", r.status_code == 200 and "dist" in r.get_json()
      and "questions" in r.get_json() and "rows" in r.get_json(), r.get_json())
for fmt, magic in (("json", b"{"), ("csv", b"\xef\xbb\xbf"),
                   ("xlsx", b"PK"), ("zip", b"PK")):
    r = api.get(f"/admin_api/exam/{eid}/export?fmt={fmt}")
    check(f"导出 {fmt}", r.status_code == 200 and r.data.startswith(magic),
          (r.status_code, r.data[:20]))
r = call("POST", f"/admin_api/exam/{eid}/regrade", {}, H)
check("重新评分接口", r.status_code == 200 and r.get_json().get("ok"), r.get_json())
r = call("POST", f"/admin_api/exam/{eid}/rejudge-code", {}, H)
check("重判程序填空接口", r.status_code == 200 and r.get_json().get("ok"), r.get_json())
r = call("POST", f"/admin_api/exam/{eid}/stop", {"clear_login": True}, H)
check("结束考试", r.status_code == 200 and r.get_json()["exam"]["status"] == "stopped", r.get_json())

print("== 6. 学生凭证与删除 ==")
profile_eid = db.create_exam({"title": "资料修正考试", "etype": "", "duration": 30,
                              "theory_duration": 30})
db.set_question_types(profile_eid, ["single"])
db.set_exam_status(profile_eid, "running")
db.set_phase(profile_eid, "theory")
db.bind_exam(profile_eid)
db.upsert_student("PC-ADMIN", "127.0.0.1", "", name="小明", seat="01")
r = call("POST", "/admin_api/students/reset-token", {"hostname": "PC-ADMIN"}, H)
check("重置学生机器凭证", r.status_code == 200 and r.get_json().get("ok"), r.get_json())
before_seen = db.get_student("PC-ADMIN").get("last_seen")
r = call("POST", "/admin_api/students/update",
         {"hostname": "PC-ADMIN", "name": "修正姓名", "seat": "09"}, H)
check("老师修改学生姓名和座位", r.status_code == 200
      and db.get_student("PC-ADMIN").get("name") == "修正姓名"
      and db.get_student("PC-ADMIN").get("seat") == "09", r.get_json())
check("修改资料不刷新离线时间", db.get_student("PC-ADMIN").get("last_seen") == before_seen)
r = api.post("/api/hello", json={"host": "PC-ADMIN", "name": "小明", "seat": "01",
                                 "ip": "127.0.0.1", "mac": ""})
hello_data = r.get_json() or {}
check("修改命令待确认时旧学生端不能覆盖资料", r.status_code == 200
      and hello_data.get("student", {}).get("name") == "修正姓名"
      and any(cmd.get("type") == "update_profile" for cmd in hello_data.get("commands", [])),
      hello_data)
r = api.get("/students")
students_html = r.get_data(as_text=True)
check("网页学生机页包含修改信息入口", r.status_code == 200
      and 'data-student-action="edit"' in students_html
      and 'id="studentEditForm"' in students_html)
r = api.post(f"/students/{db.get_student('PC-ADMIN')['id']}/update",
             data={"_csrf": csrf, "name": "网页修正", "seat": "10"})
check("网页表单也能修改学生信息", r.status_code == 302
      and db.get_student("PC-ADMIN").get("name") == "网页修正")
db.delete_exam(profile_eid)
r = call("DELETE", f"/admin_api/exam/{eid}", headers=H)
check("已结束考试禁止删除", r.status_code == 400 and bool(db.get_exam(eid)), r.get_json())
r = call("DELETE", f"/admin_api/question/{qid}", headers=H)
check("删除理论题", r.status_code == 200 and not db.get_question(qid), r.get_json())
r = call("DELETE", f"/admin_api/practice/{pid}", headers=H)
check("删除实操题", r.status_code == 200 and not db.get_practice(pid), r.get_json())

print("\n" + ("[OK] 管理员 API 全流程通过" if not failures
              else f"[FAIL] {len(failures)} 条未过: {failures}"))
sys.exit(1 if failures else 0)
