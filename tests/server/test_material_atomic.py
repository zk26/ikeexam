# -*- coding: utf-8 -*-
"""素材引用删除的原子性、并发与损坏数据回归。"""
import json
import os
import sys
import tempfile
import threading
import time

_tmp = tempfile.mkdtemp(prefix="exam_material_test_")
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


def make_exam(title, materials):
    eid = db.create_exam({"title": title, "etype": "", "duration": 30,
                          "theory_duration": 30})
    db.update_exam(eid, {"materials": materials})
    db.set_question_types(eid, ["single"])
    return eid


def write_material(name, data=b"material"):
    with open(os.path.join(app_mod.MATERIAL_DIR, name), "wb") as f:
        f.write(data)


# 最后一个引用删除时，数据库提交前异常必须恢复文件和引用。
eid = make_exam("回滚", ["rollback.txt"])
write_material("rollback.txt")
original_scan = db.material_references_on


def fail_scan(*args, **kwargs):
    raise ValueError("注入引用扫描失败")


db.material_references_on = fail_scan
try:
    try:
        app_mod._delete_exam_material(eid, "rollback.txt")
        raise AssertionError("应当抛出注入异常")
    except ValueError as exc:
        assert "注入" in str(exc)
finally:
    db.material_references_on = original_scan
assert json.loads(db.get_exam(eid)["materials"]) == ["rollback.txt"]
assert os.path.isfile(os.path.join(app_mod.MATERIAL_DIR, "rollback.txt"))

# 其他考试仍引用时只移除当前考试引用，不删除共享文件。
other = make_exam("共享", ["shared.txt"])
write_material("shared.txt")
files, used, removed = app_mod._delete_exam_material(eid, "rollback.txt")
assert files == [] and not used and removed, (files, used, removed, db.get_exam(eid), db.get_exam(other))
assert not os.path.exists(os.path.join(app_mod.MATERIAL_DIR, "rollback.txt"))
files, used, removed = app_mod._delete_exam_material(other, "shared.txt")
assert files == [] and used == [] and removed

# 实操题仍引用时保留文件。
practice_eid = make_exam("实操引用", ["practice.txt"])
write_material("practice.txt")
pid, _ = db.add_practice({"name": "引用素材", "etype": "word", "category": "测试",
                          "level": "基础", "materials": ["practice.txt"],
                          "requirement": "", "save_as": "答案.docx", "checks": []})
files, used, removed = app_mod._delete_exam_material(practice_eid, "practice.txt")
assert files == [] and used == ["实操题「引用素材」"] and not removed
assert os.path.isfile(os.path.join(app_mod.MATERIAL_DIR, "practice.txt"))

# 损坏的引用 JSON 必须失败关闭，不能误删最后一份文件。
bad = make_exam("损坏", [])
conn = db.conn()
conn.execute("UPDATE exams SET materials=? WHERE id=?", ("{bad", bad))
conn.commit()
conn.close()
try:
    app_mod._delete_exam_material(practice_eid, "practice.txt")
    raise AssertionError("损坏引用数据应当失败关闭")
except ValueError as exc:
    assert "损坏" in str(exc)
assert json.loads(db.get_exam(practice_eid)["materials"]) == [], db.get_exam(practice_eid)
assert os.path.isfile(os.path.join(app_mod.MATERIAL_DIR, "practice.txt"))
conn = db.conn()
conn.execute("UPDATE exams SET materials='[]' WHERE id=?", (bad,))
conn.commit()
conn.close()

# 删除与新增引用并发时，新增方必须等待锁，并因文件已删除而失败。
race_delete = make_exam("并发删除", ["race.txt"])
race_add = make_exam("并发新增", [])
write_material("race.txt")
entered = threading.Event()
release = threading.Event()
delete_result = []
add_result = []
original_scan = db.material_references_on


def paused_scan(*args, **kwargs):
    entered.set()
    assert release.wait(5)
    return original_scan(*args, **kwargs)


def delete_worker():
    try:
        delete_result.append(app_mod._delete_exam_material(race_delete, "race.txt"))
    except Exception as exc:
        delete_result.append(exc)


def add_worker():
    response = client.put(f"/admin_api/exam/{race_add}", headers=headers, json={
        "title": "并发新增", "duration": 30, "question_types": ["single"],
        "materials": ["race.txt"],
    })
    add_result.append((response.status_code, response.get_json()))


db.material_references_on = paused_scan
t1 = threading.Thread(target=delete_worker)
t2 = threading.Thread(target=add_worker)
try:
    t1.start()
    assert entered.wait(5)
    t2.start()
    time.sleep(0.1)
    assert t2.is_alive(), "新增引用未等待素材锁"
finally:
    release.set()
    t1.join(5)
    t2.join(5)
    db.material_references_on = original_scan
assert not t1.is_alive() and not t2.is_alive()
assert delete_result and not isinstance(delete_result[0], Exception), delete_result
assert add_result and add_result[0][0] == 400, add_result
assert "不存在" in (add_result[0][1].get("error", "") + add_result[0][1].get("msg", "")), add_result
assert json.loads(db.get_exam(race_add)["materials"]) == []
assert not os.path.exists(os.path.join(app_mod.MATERIAL_DIR, "race.txt"))

print("[OK] 素材删除原子性、共享引用、损坏数据与并发回归通过")
