# -*- coding: utf-8 -*-
"""组卷快照与考试包私有原子导入回归。"""
import json
import os
import sys
import tempfile

_tmp = tempfile.mkdtemp(prefix="exam_bundle_test_")
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

# 全局题库变化不得改写已经组卷的题目。
qid = db.add_question({"qtype": "code", "stem": "原题干", "options": [],
                       "answer": "1", "score": 5, "language": "python",
                       "code": "print(____)", "expected_output": "1\n",
                       "difficulty": "挑战", "source": "单元测试",
                       "payload": {"slots": ["x"]}})
pid, _ = db.add_practice({"name": "原实操题", "etype": "word", "category": "测试",
                          "level": "基础", "materials": [], "requirement": "原要求",
                          "save_as": "答案.docx", "checks": [{"id": "标题", "type": "title", "score": 5}]})
eid = db.create_exam({"title": "快照考试", "etype": "word", "duration": 40,
                      "theory_duration": 40})
db.set_question_types(eid, ["code", "practice"])
db.set_exam_questions(eid, [qid])
db.add_task_to_exam(eid, pid)
db.update_question(qid, {"stem": "改后的题干", "answer": "2", "expected_output": "2\n"})
db.update_practice(pid, {"name": "改后的实操题", "requirement": "改后的要求"})
assert db.get_exam_questions(eid)[0]["stem"] == "原题干"
assert db.get_exam_questions(eid)[0]["answer"] == "1"
assert db.get_exam_questions(eid)[0]["expected_output"] == "1\n"
db.update_question(qid, {"payload": {"slots": ["y"]}})
assert db.get_question(qid)["payload"] == '{"slots": ["y"]}'
assert db.get_exam_questions(eid)[0]["payload"] == '{"slots": ["x"]}'
assert db.exam_tasks(eid)[0]["name"] == "原实操题"
assert db.exam_tasks(eid)[0]["requirement"] == "原要求"
db.delete_question(qid)
db.delete_practice(pid)
assert db.get_exam_questions(eid)[0]["stem"] == "原题干"
assert db.exam_tasks(eid)[0]["name"] == "原实操题"

# 导出/导入保留策略、名单、配置和题目语义，且不写入全局题库。
db.update_exam(eid, {"require_complete": 1, "passwordless": 1,
                     "finish_note": "交卷结束"})
db.set_exam_klass(eid, "七年级1班")
db.set_typing_config(eid, {"rounds": 3, "source": "abc"})
db.set_drawing_config(eid, {"brief": "画一幅画", "styles": ["draw"], "grid": 28})
db.set_roster(eid, [("01", "小明", "192.168.1.10")])
before_q = db.question_meta_counts()["total"]
before_p = len(db.list_practice())
bundle = db.export_bundle(eid)
assert bundle["version"] == 2
new_eid, info = db.import_bundle(bundle)
assert new_eid and info["ok"], info
assert db.question_meta_counts()["total"] == before_q
assert len(db.list_practice()) == before_p
new_exam = db.get_exam(new_eid)
assert new_exam["require_complete"] == 1 and new_exam["passwordless"] == 1
assert new_exam["finish_note"] == "交卷结束" and new_exam["klass"] == "七年级1班", (bundle["exam"], new_exam)
assert json.loads(new_exam["typing_config"])["rounds"] == 3
assert json.loads(new_exam["drawing_config"])["brief"] == "画一幅画"
assert db.roster_of(new_eid)[0]["name"] == "小明"
assert db.get_exam_questions(new_eid)[0]["expected_output"] == "1\n"
assert db.get_exam_questions(new_eid)[0]["payload"] == '{"slots": ["x"]}'
assert db.get_exam_questions(new_eid)[0]["difficulty"] == "挑战"
assert db.exam_tasks(new_eid)[0]["requirement"] == "原要求"
expected_checks = [{"id": "标题", "type": "title", "score": 5}]
private_q = db.get_exam_questions(new_eid)[0]
private_p = db.exam_tasks(new_eid)[0]
assert db.checks_for_submission(new_exam, {"task_id": private_p["id"]}) == expected_checks
assert private_q["owner_exam_id"] == new_eid
assert private_p["owner_exam_id"] == new_eid
for fn, args in ((db.update_question, (private_q["id"], {"stem": "非法修改"})),
                 (db.delete_question, (private_q["id"],)),
                 (db.update_practice, (private_p["id"], dict(private_p, name="非法修改"))),
                 (db.delete_practice, (private_p["id"],))):
    try:
        fn(*args)
        raise AssertionError(f"{fn.__name__} 不应允许修改私有行")
    except ValueError:
        pass

# 复制导入考试后即使删除源考试，副本题目、实操规则和私有所有权仍完整。
copy_id = app_mod._copy_exam_config(new_eid)
db.delete_exam(new_eid)
assert db.get_exam_questions(copy_id)[0]["stem"] == "原题干"
copied_task = db.exam_tasks(copy_id)[0]
assert copied_task["requirement"] == "原要求"
assert copied_task["owner_exam_id"] == copy_id
assert db.checks_for_submission(db.get_exam(copy_id), {"task_id": copied_task["id"]}) == expected_checks
copied_exam = db.get_exam(copy_id)
assert copied_exam["theory_duration"] == 40 and copied_exam["passwordless"] == 1
assert db.roster_of(copy_id)[0]["name"] == "小明"

# 导入中途失败必须完全回滚，不留下考试或私有题目。
before_exams = len(db.list_exams())
before_private_q = db.conn().execute(
    "SELECT COUNT(*) FROM questions WHERE owner_exam_id<>0").fetchone()[0]
before_private_p = db.conn().execute(
    "SELECT COUNT(*) FROM practice_questions WHERE owner_exam_id<>0").fetchone()[0]
original_insert = db._insert_practice


def fail_insert(*args, **kwargs):
    raise ValueError("注入实操题写入失败")


db._insert_practice = fail_insert
try:
    failed_eid, failed_info = db.import_bundle(bundle)
finally:
    db._insert_practice = original_insert
assert failed_eid is None and not failed_info["ok"]
assert len(db.list_exams()) == before_exams
assert db.conn().execute(
    "SELECT COUNT(*) FROM questions WHERE owner_exam_id<>0").fetchone()[0] == before_private_q
assert db.conn().execute(
    "SELECT COUNT(*) FROM practice_questions WHERE owner_exam_id<>0").fetchone()[0] == before_private_p

# 旧版 v1 包仍可导入，缺失字段使用兼容默认值。
legacy = {"version": 1, "exam": {"title": "旧包", "duration": 30},
          "questions": [{"qtype": "single", "stem": "旧题", "options": ["甲", "乙"], "answer": "A"}]}
legacy_eid, legacy_info = db.import_bundle(legacy)
assert legacy_eid and legacy_info["ok"], legacy_info
assert db.get_exam(legacy_eid)["theory_duration"] == 30
assert db.get_exam_questions(legacy_eid)[0]["stem"] == "旧题"

print("[OK] 组卷快照、考试包私有导入、语义保留与原子回滚回归通过")
