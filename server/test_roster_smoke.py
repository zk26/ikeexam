# -*- coding: utf-8 -*-
"""
考试参与名单（快照）回归测试：
成绩页名单必须「谁参与过谁就在」——
清掉学生记录、同一台机器换人登录，名单都不能丢人。

⚠️ 必须跑在**临时库**上（复制一份正式库当沙盒）：
名单测试要故意删学生、造数据，绝不能碰正式 exam.db。
"""
import json
import os
import shutil
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC_DB = os.path.join(_HERE, "..", "exam.db")
_TMPDIR = tempfile.mkdtemp(prefix="exam_roster_test_")
_DBSANDBOX = os.path.join(_TMPDIR, "exam.db")
if os.path.isfile(_SRC_DB):
    shutil.copy2(_SRC_DB, _DBSANDBOX)     # 有正式库就复制一份当沙盒
os.environ["EXAM_DB"] = _DBSANDBOX       # 必须在 import app/db 之前设好
os.environ["EXAM_AUTH_FILE"] = os.path.join(_TMPDIR, "auth.json")
os.environ["EXAM_ADMIN_PASSWORD"] = "Admin@123"   # 无默认密码后，测试需显式给密码

sys.path.insert(0, _HERE)
import app as app_mod          # noqa: E402
import db                      # noqa: E402

db.init()

ok_n = fail_n = 0


def check(name, cond, detail=""):
    global ok_n, fail_n
    if cond:
        ok_n += 1
        print(f"  PASS {name}")
    else:
        fail_n += 1
        print(f"  FAIL {name}  {detail}")


try:
    api = app_mod.app.test_client()

    print("== 1. 开考，学生登录就进参与名单 ==")
    eid = db.create_exam({"title": "名单测试", "etype": "", "duration": 40,
                          "theory_duration": 40, "requirement": "",
                          "save_as": "", "require_complete": 0})
    db.set_question_types(eid, ["single", "typing", "drawing"])
    db.set_exam_status(eid, "running")
    db.set_phase(eid, "theory")
    r = api.post("/api/hello", json={"hostname": "PC01", "ip": "127.0.0.1",
                                     "mac": "", "name": "小明",
                                     "seat": "1"}).get_json()
    check("登录成功", bool(r.get("ok")), str(r)[:120])
    parts = db.participants_for(eid)
    check("登录即入参与名单", any(p["name"] == "小明" for p in parts), str(parts))

    print("== 2. 作答证据也记参与 ==")
    api.post("/api/hello", json={"hostname": "PC02", "ip": "127.0.0.1",
                                 "mac": "", "name": "小红", "seat": "2"})
    api.post("/api/theory/submit",
             json={"host": "PC02", "exam_id": eid, "answers": {}})
    db.save_drawing(eid, "PC03", "画板.png", "", 10)     # 只交了画板
    db.save_typing_report(eid, "PC04",                   # 只打了字
                          [{"task": "key_press", "progress": 1, "total": 10}])
    names = {p["name"] for p in db.participants_for(eid)}
    check("登录/理论/画板/打字都算参与",
          {"小明", "小红"} <= names, str(names))
    rows = db.full_report(eid)
    hosts = {r["hostname"] for r in rows}
    check("full_report 覆盖四类参与者",
          {"PC01", "PC02", "PC03", "PC04"} <= hosts, str(hosts))

    print("== 3. 清掉学生记录名单也不丢人 ==")
    c = db.conn()
    c.execute("DELETE FROM students")
    c.commit()
    c.close()
    rows = db.full_report(eid)
    names = {r["name"] for r in rows}
    check("清学生后名单仍在（快照兜底）",
          {"小明", "小红"} <= names, str(names))
    check("清学生后证据机器仍在",
          {"PC03", "PC04"} <= {r["hostname"] for r in rows})

    print("== 4. 同一台机器换人登录：两个人都在 ==")
    api.post("/api/hello", json={"hostname": "PC01", "ip": "127.0.0.1",
                                 "mac": "", "name": "王小刚", "seat": "3"})
    rows = db.full_report(eid)
    names = {r["name"] for r in rows}
    check("换人后两人都在名单",
          {"小明", "王小刚"} <= names, str(names))
    check("人数不重复不丢",
          len(rows) >= 5,
          str([(r["hostname"], r["name"]) for r in rows]))

    print("== 5. 成绩页/汇总与名单一致 ==")
    cs = db.class_summary(eid)
    check("class_summary 人数 = 名单人数", cs["count"] == len(rows),
          f"{cs['count']} vs {len(rows)}")

    print("== 6. 成绩页页面渲染出名字（不覆盖、不丢人） ==")
    import re                                  # noqa: E402
    html = api.get("/login").get_data(as_text=True)
    csrf = (re.search(r'name="_csrf" value="([^"]+)"', html) or [None, ""])[1]
    api.post("/login", data={"password": "Admin@123", "next": "/",
                             "_csrf": csrf})
    before = {(p["hostname"], p["name"]) for p in db.participants_for(eid)}
    page = api.get(f"/scores/{eid}").get_data(as_text=True)
    check("成绩页显示参与学生", "小明" in page and "王小刚" in page
          and "小红" in page)
    check("成绩页不再空名单", "还没有学生记录" not in page)
    # 渲染不能改数据：页面打开前后名单完全一致
    after = {(p["hostname"], p["name"]) for p in db.participants_for(eid)}
    check("渲染不改动名单", after == before and len(after) >= 3, str(after))

    print("== 6.5 评讲页：添加的题型都要讲出来 ==")
    rv = api.get(f"/exam/{eid}/review")
    check("评讲页可访问", rv.status_code == 200, str(rv.status_code))
    rv_html = rv.get_data(as_text=True)
    check("评讲有打字练习区块", "打字练习" in rv_html and "sec-typing" in rv_html)
    check("评讲有画板创作区块", "画板创作" in rv_html and "sec-drawing" in rv_html)
    check("评讲参考人数不为 0", "参考 0 人" not in rv_html)
    check("返回控制台链接正确", 'href="/"' in rv_html and "/index" not in rv_html)

    print("== 6.6 评讲：实操做题情况 / 打字详情 / 画板灯箱 ==")
    db.add_practice({"name": "评讲测试题", "etype": "word",
                     "category": "测试", "level": "基础",
                     "requirement": "随便做做", "save_as": "作业.docx",
                     "checks": [{"id": "1", "score": 5},
                                {"id": "2", "score": 5}]})
    c = db.conn()
    pid = c.execute("SELECT id FROM practice_questions WHERE name=?",
                    ("评讲测试题",)).fetchone()[0]
    c.close()
    # 老师勾了三个操作题类型：装题不能把后面的勾清掉
    db.update_exam(eid, {"etype": json.dumps(["word", "excel", "ppt"],
                                             ensure_ascii=False)})
    db.add_task_to_exam(eid, pid)
    ex2 = db.get_exam(eid)
    check("装题不清操作题类型勾选",
          json.loads(ex2["etype"] or "[]") == ["word", "excel", "ppt"],
          str(ex2.get("etype")))
    fpath = os.path.join(_TMPDIR, "作业.docx")
    with open(fpath, "wb") as f:
        f.write(b"dummy-docx-content")
    sid = db.save_submission("PC01", eid, "作业.docx", fpath, 18, task_id=pid)
    db.update_score(sid, 7.0, 10.0,
                    [{"id": "1", "got": 3, "full": 5, "msg": "格式扣了2分"},
                     {"id": "2", "got": 4, "full": 5, "msg": "内容还行"}])
    rv = api.get(f"/exam/{eid}/review")
    rv_html = rv.get_data(as_text=True)
    check("实操做题情况表渲染", "评分点" in rv_html and "格式扣了2分" in rv_html,
          "task card missing")
    check("实操答卷下载入口", f"/submission/{sid}/file" in rv_html)
    dl = api.get(f"/exam/{eid}/submission/{sid}/file")
    check("答卷原件可下载", dl.status_code == 200 and dl.data.startswith(b"dummy"),
          str(dl.status_code))
    check("打字全排名进评讲", "第 1 名" in rv_html and "第 2 名" in rv_html)
    check("打字各人详情可展开", "看键" in rv_html or "打地鼠" in rv_html
          or "英文打字" in rv_html)
    check("画板灯箱脚本就绪", "openDraw" in rv_html and "drawNav" in rv_html)

    print("== 7. 老考试（没记快照）靠证据进名单，不猜名字不回填 ==")
    eid2 = db.create_exam({"title": "历史考试", "etype": "", "duration": 40,
                           "theory_duration": 40, "requirement": "",
                           "save_as": "", "require_complete": 0})
    db.set_exam_status(eid2, "running")
    c = db.conn()
    c.execute("INSERT INTO theory_answers(exam_id, hostname, question_id, "
              "answer, correct, got, answered_at) VALUES(?,?,?,?,?,?,?)",
              (eid2, "OLDPC", 1, "A", 0, 0.0, 0.0))
    c.commit()
    c.close()
    rows2 = db.full_report(eid2)
    check("证据机器进名单（无需回填）",
          any(r["hostname"] == "OLDPC" for r in rows2), str(rows2))
    check("不落库猜名字（名单快照不被污染）",
          not any(p["hostname"] == "OLDPC"
                  for p in db.participants_for(eid2)),
          str(db.participants_for(eid2)))

    print("== 8. 删除考试连参与名单一起清 ==")
    db.delete_exam(eid)
    db.delete_exam(eid2)
    check("delete_exam 清掉参与名单",
          not db.participants_for(eid) and not db.participants_for(eid2))

finally:
    shutil.rmtree(_TMPDIR, ignore_errors=True)

print(f"\n{'[OK] 参与名单测试全部通过' if not fail_n else f'[FAIL] {fail_n} 条未过'}")
sys.exit(1 if fail_n else 0)
