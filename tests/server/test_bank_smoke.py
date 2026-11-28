# -*- coding: utf-8 -*-
"""
题库增强自检：分类字段（难度/来源）、拖拽题判分、WPS 简单题与图片、
打名字测试（name_typing）配置、题库导入导出。

跑法：python test_bank_smoke.py
用独立临时数据目录，不动正式环境。
"""
import json
import os
import re
import sys
import tempfile

_tmp = tempfile.mkdtemp(prefix="exam_bank_test_")
os.environ["EXAM_DB"] = os.path.join(_tmp, "test.db")
os.environ["EXAM_AUTH_FILE"] = os.path.join(_tmp, "auth.json")
os.environ["EXAM_DATA"] = _tmp
os.environ["EXAM_ADMIN_PASSWORD"] = "Admin@123"   # 无默认密码后，测试需显式给密码

SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "src"))
sys.path.insert(0, SRC_DIR)
from ikeexam.server import db
db.init()
from ikeexam.server import app as app_mod

FAIL = []
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def check(name, cond, detail=""):
    print(("  PASS " if cond else "  FAIL ") + name + (f"  {detail}" if not cond else ""))
    if not cond:
        FAIL.append(name)


print("== 1. 难度 / 来源 字段 ==")
qid = db.add_question({
    "qtype": "drag", "stem": "把下面的按钮拖到对应的功能组里",
    "options": ["加粗", "居中", "保存"],
    "answer": "1:A|2:B|3:C", "score": 6,
    "category": "WPS文字", "difficulty": "基础", "source": "自创题",
    "payload": json.dumps({"slots": ["字体组", "段落组", "文件组"]},
                          ensure_ascii=False),
})
got = db.get_question(qid)
check("难度写入", got["difficulty"] == "基础", str(got["difficulty"]))
check("来源写入", got["source"] == "自创题", str(got["source"]))
check("槽位写入", "字体组" in (got["payload"] or ""), str(got["payload"]))
check("能按难度筛", len(db.list_questions(difficulty="基础", qtype="drag")) == 1)
check("能按来源筛", len(db.list_questions(source="自创题", qtype="drag")) == 1)
check("字段值列表", "自创题" in db.list_field_values("source"))
db.update_question(qid, {"difficulty": "挑战", "source": "2024年雅安会考",
                         "stem": got["stem"], "options": got["options"],
                         "answer": got["answer"], "score": 6,
                         "category": "WPS文字", "qtype": "drag"})
got2 = db.get_question(qid)
check("能改难度/来源", got2["difficulty"] == "挑战"
      and got2["source"] == "2024年雅安会考", f"{got2['difficulty']}/{got2['source']}")

print("== 2. 拖拽题判分（部分分） ==")
qq = dict(got2)
check("全对得满分", db.judge(qq, "1:A|2:B|3:C") == (1, 6.0), str(db.judge(qq, "1:A|2:B|3:C")))
c, s = db.judge(qq, "1:A|2:C|3:B")
check("对一个给部分分", c == 0 and s == 2.0, f"correct={c} got={s}")
c, s = db.judge(qq, "3:C|1:A|2:B")
check("顺序不同也算对", (c, s) == (1, 6.0), f"correct={c} got={s}")
check("空答案 0 分", db.judge(qq, "") == (0, 0))
check("空格/小写容错", db.judge(qq, " 1:a | 2:b | 3:c ") == (1, 6.0),
      str(db.judge(qq, " 1:a | 2:b | 3:c ")))

print("== 3. 题库整理（难度/来源补全） ==")
db.add_question({"qtype": "code", "stem": "补全代码", "options": [],
                 "answer": "print(1)", "score": 4, "category": "算法与程序"})
db.add_question({"qtype": "single", "stem": "雅安会考样题", "options": ["A. 甲", "B. 乙"],
                 "answer": "A", "score": 2, "category": "雅安会考"})
n_d, n_s = db.tidy_question_meta()
check("补了难度和来源", n_d >= 2 and n_s >= 2, f"难度 {n_d} 条 / 来源 {n_s} 条")
code_q = [q for q in db.list_questions(qtype="code")][0]
check("程序填空 → 挑战", code_q["difficulty"] == "挑战", str(code_q["difficulty"]))
yq = [q for q in db.list_questions(category="雅安会考")][0]
check("会考分类 → 会考来源", yq["source"] == "雅安会考模拟题", str(yq["source"]))
check("整理是幂等的", db.tidy_question_meta() == (0, 0))

print("== 4. WPS 超简单题（含配图） ==")
wps_path = os.path.join(ROOT, "题库", "WPS_超简单题.json")
check("题库文件存在", os.path.isfile(wps_path))
items = json.load(open(wps_path, encoding="utf-8")) if os.path.isfile(wps_path) else []
check("题目数量 >= 25", len(items) >= 25, f"实际 {len(items)}")
img_opts = [it for it in items if "img:" in json.dumps(it, ensure_ascii=False)]
check("有带图片的题", len(img_opts) >= 8, f"带图 {len(img_opts)} 题")
check("题目都带难度和来源",
      all(it.get("difficulty") and it.get("source") for it in items))
ok, skip = db.import_questions(items)
check("导入成功", ok == len(items) and skip == 0, f"ok={ok} skip={skip}")

# 图片文件真实存在（否则学生端会是空图）
missing = []
for it in items:
    for name in re.findall(r"img:(\S+)", json.dumps(it, ensure_ascii=False)):
        if not os.path.isfile(os.path.join(app_mod.QIMG_DIR, name)):
            missing.append(name)
check("引用的图片都存在", not missing, f"缺图: {sorted(set(missing))[:5]}")

print("== 5. 拖拽题题库 ==")
drag_path = os.path.join(ROOT, "题库", "拖拽题_会考风格.json")
check("拖拽题库文件存在", os.path.isfile(drag_path))
ditems = json.load(open(drag_path, encoding="utf-8")) if os.path.isfile(drag_path) else []
check("拖拽题 >= 30 道（网上会考风格又补了一批）",
      len(ditems) >= 30, f"实际 {len(ditems)}")
check("拖拽题都有槽位", all(it.get("slots") or it.get("payload") for it in ditems))
check("拖拽题都带难度/来源",
      all(it.get("difficulty") and it.get("source") for it in ditems))
ok2, skip2 = db.import_questions(ditems)
check("拖拽题导入成功", ok2 == len(ditems) and skip2 == 0, f"ok={ok2} skip={skip2}")
d0 = db.list_questions(qtype="drag")[0]
check("判分可用", db.judge(d0, d0["answer"])[0] == 1)

print("== 5b. 实操题库的入门（一级）题 ==")
_pq_path = os.path.join(ROOT, "题库", "实操题库.json")
check("实操题库文件存在", os.path.isfile(_pq_path))
_pq = json.load(open(_pq_path, encoding="utf-8")) if os.path.isfile(_pq_path) else []
_one = [it for it in _pq if str(it.get("category", "")).startswith("一级")]
check("有一级（入门）实操题", len(_one) >= 3, f"实际 {len(_one)}")
check("一级题难度标成「入门」",
      all(it.get("level") == "入门" for it in _one),
      str(sorted({it.get("level") for it in _one})))
check("一级题每套都有评分点",
      all(it.get("checks") for it in _one))
check("一级题素材文件都在",
      all(os.path.isfile(os.path.join(app_mod.MATERIAL_DIR, m))
          for it in _one for m in it["materials"]),
      str([m for it in _one for m in it["materials"]
           if not os.path.isfile(os.path.join(app_mod.MATERIAL_DIR, m))]))
# 标准答案必须满分、原始素材必须不得分（评分规则自己完善过，要能自证）
sys.path.insert(0, ROOT)
from ikeexam.graders import grader_for                     # noqa: E402
_ANS_DIR = os.path.join(ROOT, "题库", "标准答案")
for _it in _one:
    _full = sum(float(c["score"]) for c in _it["checks"])
    _ans = os.path.join(_ANS_DIR, _it["materials"][0]
                        .replace("_素材", "_答案"))
    _mat = os.path.join(app_mod.MATERIAL_DIR, _it["materials"][0])
    _ga, _ = grader_for(_ans).run(_it["checks"]) if os.path.isfile(_ans) else (0, [])
    _gm, _ = grader_for(_mat).run(_it["checks"]) if os.path.isfile(_mat) else (0, [])
    check(f"{_it['name']}：标准答案满分", abs(_ga - _full) < 0.01,
          f"{_ga}/{_full}")
    check(f"{_it['name']}：原始素材基本不得分", _gm <= _full * 0.2,
          f"{_gm}/{_full}")

print("== 6. 打名字测试（name_typing） ==")
eid = db.create_exam({"title": "打名字测试", "etype": "", "duration": 30,
                      "theory_duration": 30, "requirement": "",
                      "save_as": "", "require_complete": 0})
db.set_question_types(eid, ["typing"])
db.set_exam_status(eid, "running")
db.set_phase(eid, "theory")
db.upsert_student("NAMEPC", "127.0.0.1", "", name="小明", seat="1")
db.bind_exam(eid)
api = app_mod.app.test_client()
h = api.post("/api/hello", json={"hostname": "NAMEPC", "ip": "127.0.0.1",
                                 "mac": "", "name": "小明", "seat": "1"}).get_json()
_token_name = h.get("token", "")
_auth_name = {"X-Exam-Token": _token_name}
tasks = {t["task"]: t for t in (h.get("typing") or {}).get("tasks", [])}
check("下发里有 name_typing", "name_typing" in tasks, str(sorted(tasks)))
check("无实操考试不返回教师文件名", h.get("exam", {}).get("save_as", "") == "",
      str(h.get("exam", {}).get("save_as")))
nt = tasks.get("name_typing") or {}
check("默认打 5 次", nt.get("rounds") == 5, str(nt.get("rounds")))
check("认出了学生 → 只打自己的名字（不下发老师词表）",
      nt.get("words") == [], str(nt.get("words"))[:80])
check("三阶段（拼音/汉字/无提示）标记", nt.get("stages") == 3, str(nt.get("stages")))

print("== 7. 图片服务 ==")
pngs = [f for f in os.listdir(app_mod.QIMG_DIR) if f.endswith(".png")]
check("选项图片已生成", len(pngs) >= 10, f"实际 {len(pngs)}")
if pngs:
    r = api.get("/qimg/" + pngs[0])
    check("图片可匿名下载", r.status_code == 200 and len(r.data) > 200,
          f"HTTP {r.status_code} {len(r.data)}B")
r = api.get("/qimg/nope.png")
check("不存在的图片 404", r.status_code == 404, f"HTTP {r.status_code}")

print("== 8. 全链路：题库页 → 组卷 → 作答 → 判分 ==")
api2 = app_mod.app.test_client()
html = api2.get("/login").get_data(as_text=True)
csrf = (re.search(r'name="_csrf" value="([^"]+)"', html) or [None, ""])[1]
api2.post("/login", data={"password": "Admin@123", "next": "/", "_csrf": csrf})

# 题库页：新题型、难度筛选、配图
pg = api2.get("/questions").get_data(as_text=True)
check("题库页有拖拽题选项", 'value="drag"' in pg)
check("题库页有难度筛选", "难度" in pg and "进阶" in pg)
check("题库页有来源筛选", "来源" in pg and "自创题" in pg)
check("题库页显示难度标签", "基础" in pg)
check("题库长列表支持 hover/focus 滚轮",
      'class="scroll exam-scroll question-list-scroll" tabindex="0"' in pg
      and "enableQuestionScrollWheel" in pg)
pg2 = api2.get("/questions?qtype=drag").get_data(as_text=True)
check("按拖拽题筛选", "拖拽项：" in pg2)
pg3 = api2.get("/questions?difficulty=挑战").get_data(as_text=True)
check("按难度筛选", "挑战" in pg3)

# 建一场「单选 + 拖拽」的考试并开考
eid2 = db.create_exam({"title": "拖拽题联调", "etype": "", "duration": 30,
                       "theory_duration": 30, "requirement": "",
                       "save_as": "", "require_complete": 0})
db.set_question_types(eid2, ["single", "drag"])
drag_qs = db.list_questions(qtype="drag")[:2]
single_qs = db.list_questions(qtype="single")[:1]
db.set_exam_questions(eid2, [q["id"] for q in single_qs + drag_qs])
db.set_exam_status(eid2, "running")
db.set_phase(eid2, "theory")
db.upsert_student("DRAGPC", "127.0.0.1", "", name="小红", seat="2")
db.bind_exam(eid2)

_h_drag = api2.post("/api/hello", json={"hostname": "DRAGPC", "ip": "127.0.0.1",
                                        "mac": "", "name": "小红", "seat": "2"}).get_json()
_auth_drag = {"X-Exam-Token": _h_drag.get("token", "")}
paper = api2.get("/api/paper?host=DRAGPC", headers=_auth_drag).get_json()
drag_items = [q for q in paper["questions"] if q["qtype"] == "drag"]
check("试卷下发拖拽题", len(drag_items) == 2, str(len(drag_items)))
check("拖拽题带槽位", bool(drag_items[0].get("slots")), str(drag_items[0])[:120])
check("拖拽题不下发答案", "answer" not in drag_items[0])

# 学生作答：一道全对、一道只对一半
d0, d1 = drag_items[0], drag_items[1]
std0 = db.get_question(d0["id"])
std1 = db.get_question(d1["id"])
right0 = std0["answer"]
right1 = std1["answer"]
pairs1 = right1.split("|")
half1 = "|".join(pairs1[:max(1, len(pairs1) // 2)])
r = api2.post("/api/theory/submit", headers=_auth_drag, json={
    "host": "DRAGPC", "exam_id": eid2,
    "answers": {str(d0["id"]): right0, str(d1["id"]): half1}}).get_json()
check("提交拖拽题答案", r.get("ok"), str(r))
got0 = db.get_question(d0["id"]) and None
score, n = db.theory_score(eid2, "DRAGPC")
expect_full = float(std0["score"]) + round(float(std1["score"]) *
                                           len(half1.split("|")) /
                                           len(pairs1), 2)
check("拖拽题按槽位给部分分", abs(score - expect_full) < 0.01,
      f"得分 {score} / 预期 {expect_full}")

# 明细页能看到拖拽题的逐题结果
det = api2.get(f"/detail/{eid2}/DRAGPC").get_data(as_text=True)
check("明细页显示拖拽题", "拖拽题" in det)

# 答案不唯一的拖拽题：同一格写多个答案，填哪个都算对
multi_q = {"id": -99, "qtype": "drag", "score": 10.0,
           "answer": "1:A|1:B|2:C",
           "payload": '{"slots": ["一", "二"]}'}
check("一槽多答案：填第一个算对", db.judge(multi_q, "1:A|2:C") == (1, 10.0),
      str(db.judge(multi_q, "1:A|2:C")))
check("一槽多答案：填第二个也算对", db.judge(multi_q, "1:B|2:C") == (1, 10.0),
      str(db.judge(multi_q, "1:B|2:C")))
check("一槽多答案：不对的选项还是不给分",
      db.judge(multi_q, "1:Z|2:C") == (0, 5.0),
      str(db.judge(multi_q, "1:Z|2:C")))

# 编辑页：打名字测试设置
eid3 = db.create_exam({"title": "打字考试", "etype": "", "duration": 30,
                       "theory_duration": 30, "requirement": "",
                       "save_as": "", "require_complete": 0})
db.set_question_types(eid3, ["typing"])
db.set_exam_questions(eid3, [single_qs[0]["id"]])
ed = api2.get(f"/exam/{eid3}").get_data(as_text=True)
check("编辑页有打名字测试设置", "typing_name_rounds" in ed)
check("打名字测试不再要老师手工录词表",
      "typing_name_words" not in ed and "不用老师录词表" in ed)
check("编辑页有拖拽题勾选", "'drag'" in ed or "拖拽题" in ed)
# 题型设置全都在标签栏里（基本设置不再设题）
for _k in ("basic", "single", "multi", "judge", "blank", "drag", "code",
           "practice", "typing", "drawing"):
    check(f"标签页有 {_k}", f'data-tab="{_k}"' in ed)
check("每个题型页有开关", ed.count('class="qtype-cb"') >= 9,
      str(ed.count('class="qtype-cb"')))
check("打字子任务也能勾选", 'name="typing_enable_name_typing"' in ed)
check("编辑页不保留考试级文件名输入",
      'name="save_as"' not in ed
      and re.search(r'<label[^>]*>\s*保存文件名\s*</label>', ed) is None)
check("基本设置只有一个总时长",
      ed.count('name="duration"') == 1 and 'name="theory_duration"' not in ed
      and "考试总时长（分钟）" in ed and "理论题、实操题共用" in ed)
_initial_row = re.search(r'<tr[^>]*data-qid="%s"[^>]*>.*?</tr>' % single_qs[0]["id"],
                         ed, re.S)
check("题库复选框初始反映本场已选题",
      bool(_initial_row) and 'checked' in _initial_row.group(0),
      _initial_row.group(0)[:180] if _initial_row else "未找到题目行")
check("选题页自动保存且底部按钮已移除",
      'id="saveQuestionsBtn"' not in ed and "返回基本设置" not in ed
      and "queueQuestionSave" in ed and "saveQuestionType" in ed
      and "选题会自动保存" in ed)
check("选题滚动容器支持 hover/focus 滚轮",
      'class="scroll exam-scroll" tabindex="0"' in ed
      and "enableExamScrollWheel" in ed and "overscroll-behavior:auto" in ed)
new_pg = api2.get("/exam/new").get_data(as_text=True)
check("新建页移除保存文件名输入",
      'name="save_as"' not in new_pg
      and re.search(r'<label[^>]*>\s*保存文件名\s*</label>', new_pg) is None)
check("新建页只有一个总时长",
      new_pg.count('name="duration"') == 1
      and 'name="theory_duration"' not in new_pg)

print("== 9. 交卷结束语 ==")
# 用当前真正在进行中的那场考试来验证下发（前面建了不止一场）
act = db.active_exam()
check("有进行中的考试", bool(act))
db.update_exam(act["id"], {"finish_note": "考试结束，请安静等待老师收卷！"})
check("结束语存库",
      db.get_exam(act["id"])["finish_note"] == "考试结束，请安静等待老师收卷！")
h3 = api2.post("/api/hello", json={"hostname": "NAMEPC2", "ip": "127.0.0.1",
                                   "mac": "", "name": "小明", "seat": "1"}).get_json()
_auth_name2 = {"X-Exam-Token": h3.get("token", "")}
check("hello 下发结束语", h3.get("exam", {}).get("finish_note", "").startswith("考试结束"),
      str(h3.get("exam", {}).get("finish_note")))
p3 = api2.get("/api/poll?host=NAMEPC2", headers=_auth_name2).get_json()
check("心跳下发结束语", p3.get("finish_note", "").startswith("考试结束"),
      str(p3.get("finish_note")))
ed3 = api2.get(f"/exam/{eid3}").get_data(as_text=True)
check("编辑页能改结束语", 'name="finish_note"' in ed3)

print("== 10. 打名字测试用学生自己的名字 ==")
cfg_nt = app_mod._typing_config_of(db.get_exam(eid3))["name_typing"]
check("默认词表有内容", len(cfg_nt["words"]) >= 6)
pay_nt = app_mod._typing_payload(db.get_exam(eid3), "NAMEPC2")
nt = {t["task"]: t for t in pay_nt["tasks"]}.get("name_typing") or {}
stu = nt.get("student") or {}
check("下发学生本人姓名", stu.get("name") == "小明", str(stu))
check("自动生成拼音", stu.get("pinyin") == "xiǎo míng", str(stu.get("pinyin")))
check("标注字段齐全",
      "missing" in stu and "duoyin" in stu and "duoyin_chars" in stu, str(stu))
# 多音字：乐/单 这类要能标出来
py, missing, has_duo = app_mod.word_pinyin("乐乐")
check("多音字能标注", has_duo and py, f"{py} {has_duo}")
py2, miss2, _d2 = app_mod.word_pinyin("㐀㐁")
check("查不到的字用 ? 占位", "?" in py2 and len(miss2) == 2, f"{py2} {miss2}")
# 名单自动识别（老师不用手工录词表）
ros = app_mod.exam_roster(eid)
check("名单能自动认人", ros.get("count", 0) >= 1 and ros["people"][0]["name"],
      str(ros)[:120])
check("名单带自动拼音", all(p.get("pinyin") for p in ros["people"]),
      str(ros["people"])[:120])
ed_roster = api2.get(f"/exam/{eid}").get_data(as_text=True)
check("编辑页显示自动识别的学生名单",
      "本场已自动识别" in ed_roster and "小明" in ed_roster)
# 老师端一键生成姓名词表（老接口保留）
r10 = api2.get(f"/exam/{eid3}/typing/name-words").get_json()
check("姓名词表接口 ok", r10.get("ok"), str(r10)[:120])
check("姓名词表含拼音", "小明|xiǎo míng" in (r10.get("text") or ""),
      str(r10.get("text"))[:120])

print("== 11. 实操题库筛选（按钮组 + 难度排序） ==")
# 测试环境里没跑开机装载，先自己放两道实操题
for _n, _lv in (("排序测试A（基础）", "基础"), ("排序测试B（进阶）", "进阶")):
    db.add_practice({"name": _n, "etype": "ppt", "category": "二级·演示",
                     "level": _lv, "materials": [], "requirement": "要求",
                     "save_as": "座位号_姓名.pptx",
                     "checks": [{"name": "检查点", "score": 10}]})
pgp = api2.get("/practice").get_data(as_text=True)
check("类型是按钮组", 'class="btn' in pgp and "WPS演示" in pgp)
check("难度不再是下拉筛选", 'name="level"' not in pgp)
check("有难度排序入口", "难度 简单 → 难" in pgp)
check("排序是下拉框（不再是按钮组）",
      'onchange="if(this.value) location.href=this.value"' in pgp)
pgp2 = api2.get("/practice?sort=level").get_data(as_text=True)
check("难度升序可用", pgp2.count("qitem") >= 1)
# 只比较列表条目（名字里带「（基础）/（进阶）」）的先后顺序
body_up = pgp2[pgp2.find("qitem"):]
body_dn = api2.get("/practice?etype=ppt&sort=-level")\
    .get_data(as_text=True)
body_dn = body_dn[body_dn.find("qitem"):]
u_basic, u_adv = body_up.find("（基础）"), body_up.find("（进阶）")
d_basic, d_adv = body_dn.find("（基础）"), body_dn.find("（进阶）")
check("升序时基础在前", u_basic != -1 and (u_adv == -1 or u_basic < u_adv),
      f"基础@{u_basic} 进阶@{u_adv}")
check("降序时进阶在前", d_adv != -1 and (d_basic == -1 or d_adv < d_basic),
      f"基础@{d_basic} 进阶@{d_adv}")

print("== 12. 随机抽题按分类 + 分类下拉只列有题的 ==")
_allq = db.list_questions()
cats_by = app_mod._cats_by_type(_allq)
check("分类按题型过滤：列出的分类都有该题型",
      all(db.list_questions(qtype=k, category=c)
          for k, cs in cats_by.items() for c in cs), str(cats_by)[:160])
_empty_cats = [c for c in db.list_categories()
               if not db.list_questions(qtype="drag", category=c)]
check("该题型没题的分类不出现在下拉里",
      all(c not in cats_by.get("drag", []) for c in _empty_cats),
      str(_empty_cats)[:80])
_r_cat = (cats_by.get("single") or [None])[0]
if _r_cat:
    eid4 = db.create_exam({"title": "随机抽题", "etype": "", "duration": 30,
                           "theory_duration": 30, "requirement": "",
                           "save_as": "", "require_complete": 0})
    db.set_question_types(eid4, ["single"])
    # 后台的 POST 都要带 CSRF token（和浏览器里一样）
    _tok = (re.search(r'name="_csrf" value="([^"]+)"',
                      api2.get(f"/exam/{eid4}").get_data(as_text=True))
            or [None, ""])[1]
    api2.post(f"/exam/{eid4}/random",
              data={"qtype": "single", "category": _r_cat, "n": 3,
                    "tab": "single", "_csrf": _tok})
    qs4 = db.get_exam_questions(eid4)
    check("随机抽题只在当前分类里抽",
          bool(qs4) and all(q["category"] == _r_cat for q in qs4),
          str(sorted({q["category"] for q in qs4})))

print("== 12b. 网页按题型全量保存选题 ==")
_new_tok = (re.search(r'name="_csrf" value="([^"]+)"', new_pg) or [None, ""])[1]
_r_new = api2.post("/exam/new", data={"_csrf": _new_tok, "title": "网页新建同步",
                                      "duration": 33, "theory_duration": 2,
                                      "qtypes": ["single"],
                                      "save_as": "教师填写.docx"})
_created_web = max(db.list_exams(), key=lambda item: item["id"])
check("网页创建总时长同步且考试级文件名为空",
      _r_new.status_code == 302 and _created_web["title"] == "网页新建同步"
      and _created_web["duration"] == 33
      and _created_web["theory_duration"] == 33 and _created_web["save_as"] == "",
      str({k: _created_web[k] for k in ("duration", "theory_duration", "save_as")}))
_single_all = db.list_questions(qtype="single")
for _i in range(3):
    _single_all.append(db.get_question(db.add_question({
        "qtype": "single", "stem": f"网页选题单测-{_i}",
        "options": ["甲", "乙"], "answer": "A", "score": 2})))
_multi_all = db.list_questions(qtype="multi")
for _i in range(2):
    _multi_all.append(db.get_question(db.add_question({
        "qtype": "multi", "stem": f"网页选题多测-{_i}",
        "options": ["甲", "乙", "丙"], "answer": "AB", "score": 3})))
_drag_all = db.list_questions(qtype="drag")
web_eid = db.create_exam({"title": "网页选题同步", "etype": "", "duration": 45,
                          "theory_duration": 45, "requirement": "",
                          "save_as": "", "require_complete": 0})
db.set_question_types(web_eid, ["single", "multi", "judge", "blank", "drag", "code"])
_web_original = [_single_all[0]["id"], _drag_all[0]["id"],
                 _multi_all[0]["id"], _single_all[1]["id"]]
db.set_exam_questions(web_eid, _web_original)
_web_html = api2.get(f"/exam/{web_eid}").get_data(as_text=True)
_web_tok = (re.search(r'name="_csrf" value="([^"]+)"', _web_html) or [None, ""])[1]
_r_web = api2.post(f"/exam/{web_eid}/questions/single",
                   data={"_csrf": _web_tok, "tab": "single",
                         # 模拟主表单同时携带其他页签的复选框，服务端必须忽略。
                         "qids": [_single_all[2]["id"], _multi_all[0]["id"],
                                  _single_all[0]["id"]]})
_web_after_single = db.exam_question_ids(web_eid)
check("按题型保存路由成功", _r_web.status_code == 302, _r_web.status_code)
check("当前题型全量替换且保留其他题型顺序",
      _web_after_single == [_drag_all[0]["id"], _multi_all[0]["id"],
                            _single_all[2]["id"], _single_all[0]["id"]],
      str(_web_after_single))
_r_web = api2.post(f"/exam/{web_eid}/questions/multi",
                   data={"_csrf": _web_tok, "qids": [_multi_all[1]["id"]]})
check("另一题型保存不改变前面题型",
      db.exam_question_ids(web_eid) == [_drag_all[0]["id"], _single_all[2]["id"],
                                        _single_all[0]["id"], _multi_all[1]["id"]],
      str(db.exam_question_ids(web_eid)))
_r_web = api2.post(f"/exam/{web_eid}/questions/multi",
                   data={"_csrf": _web_tok, "qids": []})
check("取消全部勾选可保存为空",
      _r_web.status_code == 302
      and db.exam_question_ids(web_eid) == [_drag_all[0]["id"], _single_all[2]["id"],
                                            _single_all[0]["id"]],
      str(db.exam_question_ids(web_eid)))
_before_locked = db.exam_question_ids(web_eid)
db.set_exam_status(web_eid, "running")
_r_web = api2.post(f"/exam/{web_eid}/questions/single",
                   data={"_csrf": _web_tok, "qids": [_single_all[1]["id"]]})
check("锁定考试拒绝保存选题",
      _r_web.status_code == 302 and db.exam_question_ids(web_eid) == _before_locked,
      str(db.exam_question_ids(web_eid)))
# 已开始的考试不能回退为草稿；后续编辑流程用另一场草稿验证。
web_eid = db.create_exam({"title": "网页选题同步-编辑", "etype": "", "duration": 45,
                          "theory_duration": 45, "requirement": "",
                          "save_as": "", "require_complete": 0})
db.set_question_types(web_eid, ["single", "multi", "judge", "blank", "drag", "code"])
db.set_exam_questions(web_eid, _web_original)
_web_html = api2.get(f"/exam/{web_eid}").get_data(as_text=True)
_web_tok = (re.search(r'name="_csrf" value="([^"]+)"', _web_html) or [None, ""])[1]
_before_other = [qid for qid in db.exam_question_ids(web_eid)
                 if db.get_question(qid)["qtype"] != "single"]
_r_web = api2.post(f"/exam/{web_eid}/random",
                   data={"_csrf": _web_tok, "qtype": "single", "tab": "single",
                         "selection_present": "1", "n": 1,
                         "qids": [_single_all[1]["id"]]})
_after_random = db.get_exam_questions(web_eid)
check("随机抽题先保存待保存勾选且不丢其他题型",
      _r_web.status_code == 302
      and _single_all[1]["id"] in db.exam_question_ids(web_eid)
      and [q["id"] for q in _after_random if q["qtype"] != "single"] == _before_other,
      str(db.exam_question_ids(web_eid)))
_before_other = [qid for qid in db.exam_question_ids(web_eid)
                 if db.get_question(qid)["qtype"] != "single"]
_r_web = api2.post(f"/exam/{web_eid}/addall",
                   data={"_csrf": _web_tok, "qtype": "single", "tab": "single",
                         "selection_present": "1", "qids": [_single_all[0]["id"]]})
_after_addall = db.get_exam_questions(web_eid)
check("全选先保存待保存勾选且不丢其他题型",
      _r_web.status_code == 302
      and _single_all[0]["id"] in db.exam_question_ids(web_eid)
      and [q["id"] for q in _after_addall if q["qtype"] != "single"] == _before_other,
      str(db.exam_question_ids(web_eid)))
_r_web = api2.post(f"/exam/{web_eid}",
                   data={"_csrf": _web_tok, "title": "网页选题同步", "duration": 50,
                         "theory_duration": 3, "save_as": "教师填写.docx",
                         "qtypes": ["single", "multi", "drag"], "tab": "basic"})
_web_exam = db.get_exam(web_eid)
check("网页更新总时长同步并忽略教师文件名",
      _r_web.status_code == 302 and _web_exam["duration"] == 50
      and _web_exam["theory_duration"] == 50 and _web_exam["save_as"] == "",
      str({k: _web_exam[k] for k in ("duration", "theory_duration", "save_as")}))

print("== 13. 勾选即存（quickset）==")
db.update_exam(eid4, {"theory_duration": 2})
r12 = api2.post(f"/exam/{eid4}/quickset",
                headers={"X-CSRF-Token": _tok},
                json={"types": ["single", "drag"]}).get_json()
check("勾选即存：接口成功", r12.get("ok"), str(r12)[:120])
check("勾选即存：题型立刻写进库",
      set(app_mod._selected_types(db.get_exam(eid4))) == {"single", "drag"},
      str(app_mod._selected_types(db.get_exam(eid4))))
check("勾选即存：总时长保持同步",
      db.get_exam(eid4)["theory_duration"] == db.get_exam(eid4)["duration"],
      str(db.get_exam(eid4)["theory_duration"]))
r12b = api2.post(f"/exam/{eid4}/quickset",
                 headers={"X-CSRF-Token": _tok},
                 json={"types": ["single"],
                       "typing_enable": {"typing_zh": False,
                                         "name_typing": True}}).get_json()
check("勾选即存：打字子任务开关也能存",
      r12b.get("ok") and app_mod._typing_config_of(
          db.get_exam(eid4))["typing_zh"]["enabled"] is False,
      str(app_mod._typing_config_of(db.get_exam(eid4))["typing_zh"]))
db.set_exam_status(eid4, "running")
r13 = api2.post(f"/exam/{eid4}/quickset", headers={"X-CSRF-Token": _tok},
                json={"types": ["single"]}).get_json()
check("考试进行中拒绝即时保存", not r13.get("ok"), str(r13)[:120])
check("进行中考试不能回退为草稿", db.get_exam(eid4)["status"] == "running")

print("== 14. 姓名拼音标注（打名字测试）==")
r14 = api2.post(f"/exam/{eid}/pinyin", headers={"X-CSRF-Token": _tok},
                json={"text": "小明|xiǎo míng\n㐀|qī"}).get_json()
check("拼音标注保存成功", r14.get("ok") and r14.get("count") == 2, str(r14)[:120])
check("整词标注生效", app_mod.word_pinyin("小明")[0] == "xiǎo míng",
      str(app_mod.word_pinyin("小明")))
check("单字标注生效", app_mod.word_pinyin("㐀")[0] == "qī",
      str(app_mod.word_pinyin("㐀")))
check("标注过的字不再算「未收录」", app_mod.word_pinyin("㐀㐁")[1] == ["㐁"],
      str(app_mod.word_pinyin("㐀㐁")))
check("标注后名单里拼音跟着变",
      all(p["pinyin"] == "xiǎo míng"
          for p in r14["roster"]["people"] if p["name"] == "小明"),
      str(r14["roster"])[:120])
ed_py = api2.get(f"/exam/{eid}").get_data(as_text=True)
check("编辑页有拼音标注框", 'id="pinyinOverride"' in ed_py)
check("编辑页回填了已标注的内容", "㐀|qī" in ed_py)

print("== 15. 缺卷提交（没做的操作题直接按 0 分）==")
_task_id = None
if app_mod.db.exam_tasks(eid):
    _task_id = app_mod.db.exam_tasks(eid)[0]["id"]
_r_empty = api.post("/api/upload_empty", headers=_auth_name,
                    json={"host": "NAMEPC", "exam_id": eid,
                          "task_id": _task_id or 0}).get_json()
check("缺卷提交接口可用", _r_empty.get("ok"), str(_r_empty)[:120])
if _r_empty.get("ok"):
    _sub = [s for s in app_mod.db.list_submissions(eid)
            if s["hostname"] == "NAMEPC"]
    check("缺卷记录已写入（Path 显示缺卷）",
          any((s.get("filename") or "").startswith("（缺卷") for s in _sub),
          str([s.get("filename") for s in _sub]))
    check("缺卷成绩是 0 分", any(s.get("score") == 0 for s in _sub),
          str([s.get("score") for s in _sub]))

print("== 16. 试卷评讲页 ==")
rv = api2.get(f"/exam/{eid2}/review")       # eid2 是第 8 节那套卷（有理论题）
check("评讲页能打开", rv.status_code == 200, str(rv.status_code))
if rv.status_code == 200:
    rp = rv.get_data(as_text=True)
    check("评讲页有理论题", "第 1 题" in rp)
    check("答案默认藏着（要点才显示）", 'class="ans-box"' in rp)
    check("有对错人数 chip", "人做对" in rp and "人做错" in rp)
    check("点人数弹名单（页面里藏着名单）", 'id="pop-' in rp)
    check("正确率排序 / 只看错题 / 投影放大都在",
          'id="qSort"' in rp and "只看有错的题" in rp and "投影放大字体" in rp)
    check("答案块初始是收起的", 'ans-box show' not in rp)
    check("正确答案已经写在页面里（点一下就能看到）", "正确答案" in rp)
check("控制台有评讲入口",
      "/review" in api2.get("/", follow_redirects=True).get_data(as_text=True))

print("== 17. 学生身份唯一（同名机器不再互相覆盖）==")
# 复现老师遇到的情况：38 台机器，Windows 机器名全都一样（同 Ghost）
for i in range(1, 39):
    _r = api.post("/api/hello", json={
        "hostname": "SAME-PC",
        "client_id": f"PC-1122334455{i:02d}",
        "mac": f"11:22:33:44:55:{i:02d}",
        "name": f"同学{i:02d}", "seat": f"{i:02d}"}).get_json()
    if not _r.get("ok"):
        check(f"第 {i} 台机器登录", False, str(_r))
check("38 台同名机器全部登录成功", True)
_s1 = db.get_student("PC-112233445501")
_s38 = db.get_student("PC-112233445538")
check("同名机器在服务端各自是独立学生",
      bool(_s1) and bool(_s38)
      and _s1["name"] == "同学01" and _s38["name"] == "同学38",
      f"{_s1 and _s1['name']} / {_s38 and _s38['name']}")
check("按 hostname 反查不到（身份已换成 client_id）",
      db.get_student("SAME-PC") is None)
# 老数据迁移：以前用机器名记账，按 MAC 迁到新的 client_id
db.upsert_student("OLD-PC", "10.0.0.9", "99:88:77:66:55:44",
                  name="老机器", seat="09")
api.post("/api/hello", json={
    "hostname": "OLD-PC", "client_id": "PC-998877665544",
    "mac": "99:88:77:66:55:44", "name": "老机器", "seat": "09"},
    environ_base={"REMOTE_ADDR": "10.0.0.9"})
check("老记录按 MAC 迁到新身份",
      db.get_student("PC-998877665544") is not None
      and db.get_student("OLD-PC") is None)
# 统计只数本场考试的学生（不再把历史机器全算上）
db.bind_exam(eid4)
_st = db.stats(eid4)
check("38 台同名机器一个都没被合并",
      all(db.get_student(f"PC-1122334455{i:02d}") for i in range(1, 39)))
check("本场学生数 >= 39（38 台同名 + 迁移来的老机器 + 早先几台）",
      _st["students"] >= 39, str(_st))

print("== 18. 结束考试：强制交卷 + 关闭学生端 ==")
db.set_exam_status(eid4, "running")
db.set_phase(eid4, "theory")
_rv18 = api2.post(f"/exam/{eid4}/stop", data={"_csrf": _tok, "clear_login": "1"})
check("结束考试成功", _rv18.status_code in (200, 302), str(_rv18.status_code))
_c = db.conn()
_row = _c.execute("""SELECT target, ctype, payload FROM commands
                     WHERE ctype='force_exit' ORDER BY id DESC LIMIT 1""").fetchone()
_c.close()
check("下发了 force_exit 指令", _row is not None)
if _row:
    _pl = json.loads(_row["payload"] or "{}")
    check("指令带 clear_login 参数", _pl.get("clear_login") is True, str(_pl))
    check("指令发给全体学生", _row["target"] == "all", str(_row["target"]))
check("考试状态已置为结束", db.get_exam(eid4)["status"] == "stopped")
check("阶段已置为 ended", db.get_phase(eid4) == "ended")
# 结束过的考试不能重新开考；用一场新考试验证另一种收卷参数。
eid4b = db.create_exam({"title": "保留登录收卷", "etype": "", "duration": 30,
                        "theory_duration": 30})
db.set_question_types(eid4b, ["single"])
db.set_exam_questions(eid4b, [db.list_questions(qtype="single")[0]["id"]])
db.set_exam_status(eid4b, "running")
db.set_phase(eid4b, "theory")
api2.post(f"/exam/{eid4b}/stop", data={"_csrf": _tok, "clear_login": "0"})
_c = db.conn()
_row = _c.execute("""SELECT payload FROM commands
                     WHERE ctype='force_exit' ORDER BY id DESC LIMIT 1""").fetchone()
_c.close()
check("不清理登录信息时 clear_login=False",
      json.loads(_row["payload"] or "{}").get("clear_login") is False,
      str(_row["payload"]))

print("== 19. 本场学生名单（老师端配，学生端按名单进）==")
# 先建一场有名单的考试
eid5 = db.create_exam({"title": "名单测试", "etype": "", "duration": 30,
                       "theory_duration": 30, "requirement": "",
                       "save_as": "", "require_complete": 0})
db.set_question_types(eid5, ["single"])
db.set_exam_questions(eid5, [db.list_questions(qtype="single")[0]["id"]])
db.set_exam_status(eid5, "running")
db.set_phase(eid5, "theory")
_rows19 = app_mod._parse_roster_text("小明,01\n小红 02\n小王|03\n只写姓名的小刚")
check("名单文本解析（4 种写法）", len(_rows19) == 4, str(_rows19))
check("座位号/姓名分开存", _rows19[0] == ("01", "小明", "")
      and _rows19[3] == ("", "只写姓名的小刚", ""), str(_rows19))
_r19 = api2.post(f"/exam/{eid5}/roster", headers={"X-CSRF-Token": _tok},
                 json={"text": "小明,01\n小红 02\n小王|03", "mode": "replace"}).get_json()
check("保存名单成功", _r19.get("ok") and _r19.get("count") == 3, str(_r19))
# 名单里的学生 → 放行
_r19b = api.post("/api/hello", json={
    "hostname": "ROSTER-PC", "client_id": "PC-R0STER000001",
    "mac": "R0:ST:ER:00:00:01", "name": "小红", "seat": "02"}).get_json()
check("名单里的学生能进", _r19b.get("ok"), str(_r19b)[:120])
# 不在名单里 → 拒（并且服务端记录里也不该留下这个名字）
_r19c = api.post("/api/hello", json={
    "hostname": "ROSTER-PC2", "client_id": "PC-R0STER000002",
    "mac": "R0:ST:ER:00:00:02", "name": "路人甲", "seat": "99"})
check("不在名单里的学生被拒", _r19c.status_code == 403, str(_r19c.status_code))
check("拒绝信息说清了原因",
      "不在本场考试的学生名单里" in (_r19c.get_json() or {}).get("msg", ""),
      str(_r19c.get_json())[:120])
# 姓名对但座位号不对 → 拒
_r19d = api.post("/api/hello", json={
    "hostname": "ROSTER-PC3", "client_id": "PC-R0STER000003",
    "mac": "R0:ST:ER:00:00:03", "name": "小明", "seat": "99"})
check("座位号和名单不一致也被拒", _r19d.status_code == 403,
      str(_r19d.get_json())[:120])
# 清空名单 → 不限制
api2.post(f"/exam/{eid5}/roster/clear", headers={"X-CSRF-Token": _tok})
_r19e = api.post("/api/hello", json={
    "hostname": "ROSTER-PC4", "client_id": "PC-R0STER000004",
    "mac": "R0:ST:ER:00:00:04", "name": "谁都能进", "seat": "77"}).get_json()
check("清空名单后不限制姓名", _r19e.get("ok"), str(_r19e)[:120])
# 追加模式
_r19f = api2.post(f"/exam/{eid5}/roster", headers={"X-CSRF-Token": _tok},
                  json={"text": "小明,01\n小明,01", "mode": "append"}).get_json()
check("追加会去重", _r19f.get("ok") and _r19f.get("added") == 1
      and _r19f.get("count") == 1, str(_r19f))
# 学生端不再绑定机器：换人登录就是新身份（名单里放两个学生）
api2.post(f"/exam/{eid5}/roster", headers={"X-CSRF-Token": _tok},
          json={"text": "先来的,01\n后来的,02", "mode": "replace"})
_r19g = api.post("/api/hello", json={
    "hostname": "SWAP-PC", "client_id": "PC-SWAP00000001",
    "mac": "SW:AP:00:00:00:01", "name": "先来的", "seat": "01"}).get_json()
check("第一个学生登录", _r19g.get("ok") and
      (_r19g.get("student") or {}).get("name") == "先来的", str(_r19g)[:120])
_r19h = api.post("/api/hello", headers={"X-Exam-Token": _r19g.get("token", "")},
                 json={"hostname": "SWAP-PC", "client_id": "PC-SWAP00000001",
                       "mac": "SW:AP:00:00:00:01", "name": "后来的", "seat": "02"}).get_json()
check("换学生登录 → 用新身份（不做机器绑定）",
      (_r19h.get("student") or {}).get("name") == "后来的",
      str(_r19h)[:120])
check("服务端记录的姓名也跟着更新",
      db.get_student("PC-SWAP00000001")["name"] == "后来的")

print("== 20. 评讲页答案带内容 + 明细导出 ==")
_q20 = [q for q in db.get_exam_questions(eid2) if q["qtype"] == "single"]
if _q20:
    _txt = app_mod._answer_text(_q20[0])
    check("评讲页答案带选项内容（A. xxx）",
          _txt.startswith("A") and "." in _txt and len(_txt) > 3, _txt[:80])
_drag20 = [q for q in db.get_exam_questions(eid2) if q["qtype"] == "drag"]
if _drag20:
    _dt = app_mod._answer_text(_drag20[0])
    check("拖拽题答案写成「第N格 → A. 内容」", "第1格 →" in _dt, _dt[:100])
_rv20 = api2.get(f"/exam/{eid2}/review").get_data(as_text=True)
check("评讲页渲染出带内容的答案", "正确答案：" in _rv20 and "。" in _rv20)
check("评讲页渲染出选项（带正确项高亮）", 'class="opt-row' in _rv20,
      str(_rv20.count('opt-row')))
_rx = api2.get(f"/scores/{eid2}/xlsx")
check("明细 Excel 能导出", _rx.status_code == 200, str(_rx.status_code))
if _rx.status_code == 200:
    _body = _rx.get_data()
    check("导出的是 xlsx（zip 包）", _body[:2] == b"PK", str(_body[:4]))
    check("文件有内容（>3KB）", len(_body) > 3000, str(len(_body)))
_ed20 = api2.get(f"/exam/{eid2}").get_data(as_text=True)
check("配置页有「本场学生名单」设置",
      'id="rosterText"' in _ed20 and "本场学生名单" in _ed20)
check("名单区说明了「姓名必须在名单里」", "姓名必须在这个名单里" in _ed20)

print()
if FAIL:
    print(f"[FAIL] {len(FAIL)} 项失败: {FAIL}")
    sys.exit(1)
print("[OK] 题库增强自检全部通过")
