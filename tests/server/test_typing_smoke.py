# -*- coding: utf-8 -*-
"""打字模块（小学）端到端冒烟测试：题库导入 → 建考试 → 学生API → 实时看板"""
import json
import os
import sys
import tempfile

# 用独立临时数据库 + 独立密码文件，不动正式环境
_tmp = tempfile.mkdtemp(prefix="exam_typing_test_")
os.environ["EXAM_DB"] = os.path.join(_tmp, "test.db")
os.environ["EXAM_AUTH_FILE"] = os.path.join(_tmp, "auth.json")
os.environ["EXAM_DATA"] = _tmp
os.environ["EXAM_ADMIN_PASSWORD"] = "Admin@123"   # 无默认密码后，测试需显式给密码

SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "src"))
ROOT = os.path.dirname(SRC_DIR)
sys.path.insert(0, SRC_DIR)
from ikeexam.server import db
db.init()          # 临时库是空的，先建表
from ikeexam.server import app as app_mod

FAIL = []


def check(name, cond, detail=""):
    print(("  PASS " if cond else "  FAIL ") + name + (f"  {detail}" if not cond else ""))
    if not cond:
        FAIL.append(name)


print("== 1. 小学键盘题库导入 ==")
bank_path = os.path.join(ROOT, "题库", "小学键盘题库.json")
items = json.load(open(bank_path, encoding="utf-8"))
check("题库 30 题", len(items) == 30, f"实际 {len(items)}")
ok_n, skip_n = db.import_questions(items)
check("全部导入成功", ok_n == 30 and skip_n == 0, f"ok={ok_n} skip={skip_n}")

print("== 2. 建一场带打字题的考试 ==")
eid = db.create_exam({
    "title": "小学打字测试", "etype": "", "duration": 40,
    "theory_duration": 40, "requirement": "", "save_as": "",
    "require_complete": 0,
})
db.set_question_types(eid, ["single", "judge", "typing"])
db.set_typing_config(eid, {"key_press": {"rounds": 5, "seconds": 4}})
# 组卷：把小学键盘题全装上
c = db.conn()
qids = [r["id"] for r in c.execute(
    "SELECT id FROM questions WHERE category='小学键盘'").fetchall()]
c.close()
db.set_exam_questions(eid, qids)
db.set_exam_status(eid, "running")
db.set_phase(eid, "theory")
db.upsert_student("TESTPC01", "127.0.0.1", "", name="小明", seat="1")
db.bind_exam(eid)

ex = db.get_exam(eid)
check("题型含 typing", "typing" in json.loads(ex["question_types"]))

print("== 3. 学生端 API ==")
api = app_mod.app.test_client()
r = api.post("/api/hello", json={"hostname": "TESTPC01", "ip": "127.0.0.1",
                                 "mac": "", "name": "小明", "seat": "1"})
d = r.get_json()
_auth = {"X-Exam-Token": d.get("token", "")}
check("hello ok", d.get("ok"))
check("hello 带 typing 配置", bool(d.get("typing", {}).get("tasks")))
check("has_typing 标记", d["exam"].get("has_typing") is True)
tasks = {t["task"]: t for t in d["typing"]["tasks"]}
check("子任务齐全（含打地鼠/打名字测试）",
      set(tasks) == {"whack_mole", "key_press", "case_press", "typing_en",
                     "typing_zh", "name_typing"})
check("打地鼠排在打字测试前面",
      [t["task"] for t in d["typing"]["tasks"]][0] == "whack_mole")
check("打地鼠带三档难度参数",
      set((tasks["whack_mole"].get("levels") or {}))
      == {"easy", "normal", "hard"})
check("自定义轮数生效", tasks["key_press"]["rounds"] == 5)
check("英文范文非空", len(tasks["typing_en"]["text"]) > 10)
check("中文范文非空", len(tasks["typing_zh"]["text"]) > 10)

r = api.get("/api/poll?host=TESTPC01", headers=_auth).get_json()
check("poll 带 typing 配置", bool(r.get("typing", {}).get("tasks")))

r = api.get("/api/paper?host=TESTPC01", headers=_auth).get_json()
check("试卷 30 题", len(r.get("questions", [])) == 30,
      f"实际 {len(r.get('questions', []))}")

print("== 4. 打字进度上报 ==")
reports = [
    {"task": "key_press", "progress": 3, "total": 5, "hits": 2,
     "elapsed_ms": 12000, "accuracy": 66.7, "reaction_ms": 1500, "finished": 0},
    {"task": "typing_zh", "typed": 20, "correct_chars": 18, "total": 45,
     "elapsed_ms": 30000, "wpm": 36.0, "accuracy": 90.0, "finished": 0},
]
r = api.post("/api/typing/report", headers=_auth,
             json={"host": "TESTPC01", "exam_id": eid, "reports": reports})
check("上报成功", r.get_json().get("ok"))
r = api.post("/api/typing/report", headers=_auth,
             json={"host": "TESTPC01", "exam_id": eid, "reports": [
                 dict(reports[1], typed=45, correct_chars=43, wpm=52.0,
                      accuracy=95.6, finished=1)]})
check("重复上报(upsert)成功", r.get_json().get("ok"))

print("== 5. 老师端实时看板 ==")
# 登录（先拿 CSRF token）
import re as _re
html = api.get("/login").get_data(as_text=True)
csrf = (_re.search(r'name="_csrf" value="([^"]+)"', html) or [None, ""])[1]
r = api.post("/login", data={"password": "Admin@123", "next": "/",
                             "_csrf": csrf}, follow_redirects=False)
check("老师登录", r.status_code in (301, 302), f"HTTP {r.status_code}")
d = api.get(f"/exam/{eid}/typing.json").get_json()
check("看板 ok", d.get("ok"))
rows = d.get("rows", [])
check("看板有学生行", len(rows) == 1)
if rows:
    tk = rows[0]["tasks"]
    kp = tk.get("key_press") or {}
    zh = tk.get("typing_zh") or {}
    check("按键题数据", kp.get("progress") == 3 and kp.get("hits") == 2,
          str(kp))
    check("中文打字被覆盖更新", zh.get("typed") == 45 and zh.get("finished") == 1,
          str(zh))
    check("正确率字段", abs((zh.get("accuracy") or 0) - 95.6) < 0.01)
r = api.get(f"/exam/{eid}/typing")
check("看板页面 200", r.status_code == 200, f"HTTP {r.status_code}")
# 任务列现在是按启用的子任务动态生成的（表头 id=boardHead、任务名走 JSON）
page2 = r.get_data(as_text=True)
check("看板页动态任务列", "boardHead" in page2 and "taskLabel" in page2)

print("== 6. 选项图片 ==")
png = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
       b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
       b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82")
open(os.path.join(app_mod.QIMG_DIR, "test_cap.png"), "wb").write(png)
r = api.get("/qimg/test_cap.png")
check("图片可匿名下载", r.status_code == 200 and r.data == png,
      f"HTTP {r.status_code}")
r = api.get("/qimg/../app.py")
check("路径穿越被拦", r.status_code in (400, 404), f"HTTP {r.status_code}")

print("== 7. 阶段切换校验（打字考试可切实操） ==")
ex2 = db.get_exam(eid)
sel = app_mod._selected_types(ex2)
check("打字考试允许切实操", bool({"practice", "typing"} & set(sel)))
check("开考校验通过", app_mod._exam_ready(eid)[0])
# 旧考试（question_types 为空）不该被当成有打字题
legacy = {"question_types": "[]"}
check("旧考试默认不含 typing", "typing" not in app_mod._selected_types(legacy))
check("旧考试不下发打字配置", app_mod._typing_payload(legacy) is None)

print("== 8. 小学语文题库与范文库 ==")
bank2_path = os.path.join(ROOT, "题库", "小学语文题库.json")
items2 = json.load(open(bank2_path, encoding="utf-8"))
check("语文题库 45 题", len(items2) == 45, f"实际 {len(items2)}")
ok2, skip2 = db.import_questions(items2)
check("语文题库导入成功", ok2 == 45 and skip2 == 0, f"ok={ok2} skip={skip2}")
# 古诗填空判分：标点/空格差异也算对
q = {"qtype": "blank", "answer": "疑是地上霜", "score": 2}
check("古诗填空(带句号)判对", db.judge(q, "疑是地上霜。")[0] == 1)
check("古诗填空(错字)判错", db.judge(q, "疑是地上雪")[0] == 0)
# 打字范文库
passages = app_mod._load_typing_passages()
check("范文库 66 篇", len(passages) == 66, f"实际 {len(passages)}")
kinds = {p["kind"] for p in passages}
check("范文库含生词/古诗/课文/段落",
      {"生词", "古诗", "课文", "段落"} <= kinds, str(kinds))
check("范文库有雅安课文", any("雅安" in p["text"] for p in passages))
# 编辑考试页：范文下拉渲染
r = api.get(f"/exam/{eid}")
page = r.get_data(as_text=True)
check("编辑页 200", r.status_code == 200)
check("编辑页有范文下拉", "zhPreset" in page and "雨城雅安" in page)
check("编辑页有启用勾选", "typing_enable_key_press" in page)

print("== 9. 子任务可勾选 / 时长可不限 ==")
from werkzeug.datastructures import ImmutableMultiDict  # noqa: E402
form = ImmutableMultiDict([
    ("typing_enable_key_press", "1"), ("typing_enable_typing_zh", "1"),
    ("typing_key_rounds", "8"), ("typing_key_seconds", "0"),
    ("typing_zh_seconds", "0"),
])
cfg9 = app_mod._typing_form_config(form)
on = [t for t in app_mod.TYPING_TASK_ORDER if cfg9[t]["enabled"]]
check("只勾两个就只考两个", on == ["key_press", "typing_zh"], str(on))
check("秒数 0 = 不限时", cfg9["key_press"]["seconds"] == 0
      and cfg9["typing_zh"]["seconds"] == 0, str(cfg9))
check("勾选的任务参数照常生效", cfg9["key_press"]["rounds"] == 8)
db.set_typing_config(eid, cfg9)
payload9 = app_mod._typing_payload(db.get_exam(eid))
got9 = [t["task"] for t in payload9["tasks"]]
check("学生端只下发启用的任务", got9 == ["key_press", "typing_zh"], str(got9))
check("下发配置不带 enabled 字段", "enabled" not in payload9["tasks"][0])
board9 = api.get(f"/exam/{eid}/typing.json").get_json()
check("看板只出启用的列", board9["task_order"] == ["key_press", "typing_zh"],
      str(board9["task_order"]))
# 打地鼠：难度 / 每关只数可配置，非法值回退默认
form_w = ImmutableMultiDict([
    ("typing_enable_whack_mole", "1"),
    ("typing_whack_level", "hard"), ("typing_whack_rounds", "6"),
    ("typing_whack_grid", "4x5"),
])
cfg_w = app_mod._typing_form_config(form_w)
check("打地鼠难度档可选", cfg_w["whack_mole"]["level"] == "hard"
      and cfg_w["whack_mole"]["rounds"] == 6, str(cfg_w["whack_mole"]))
check("打地鼠洞口布局可配（不只 3×3）", cfg_w["whack_mole"]["grid"] == "4x5",
      str(cfg_w["whack_mole"]))
db.set_typing_config(eid, cfg_w)          # 存下来才测得到下发
pw = {t["task"]: t for t in app_mod._typing_payload(db.get_exam(eid))["tasks"]}
check("洞口布局随配置下发", pw["whack_mole"]["grid"] == "4x5",
      str(pw["whack_mole"].get("grid")))
check("非法洞口布局回退 3×3",
      app_mod._typing_form_config(
          ImmutableMultiDict([("typing_whack_grid", "9x9")])
      )["whack_mole"]["grid"] == "3x3")
form_w2 = ImmutableMultiDict([("typing_whack_level", "nope"),
                              ("typing_whack_rounds", "999")])
cfg_w2 = app_mod._typing_form_config(form_w2)
check("打地鼠非法难度回退三关连打", cfg_w2["whack_mole"]["level"] == "all")
check("打地鼠只数限制在 1~50", cfg_w2["whack_mole"]["rounds"] == 50)
check("打地鼠可单独关掉",
      app_mod._typing_form_config(
          ImmutableMultiDict([]))["whack_mole"]["enabled"] is False)
# 恢复默认，后面的用例不受影响
db.set_typing_config(eid, app_mod._typing_defaults())
dft = app_mod._typing_payload(db.get_exam(eid))["tasks"]
check("默认六项全启用", len(dft) == 6, str([t["task"] for t in dft]))
check("默认打地鼠排最前", dft[0]["task"] == "whack_mole")

print("== 9.5 姓名拼音解析 ==")
py, miss, duo = app_mod.word_pinyin("曾小明")
check("多音姓氏按姓氏读音", py.startswith("zēng"), py)
py2, miss2, _ = app_mod.word_pinyin("尉迟恭")
check("复姓整体解析", py2.startswith("yù chí"), py2)
py3, miss3, _ = app_mod.word_pinyin("王一龘")
check("生僻字也能解析", not miss3 and "dá" in py3, f"{py3} {miss3}")
check("全量字表已加载", len(app_mod.pinyin_table()[0]) > 20000,
      str(len(app_mod.pinyin_table()[0])))
check("多音提醒已收窄", len(app_mod.pinyin_table()[1]) < 400)

print("== 10. 老师点选要考的键 / 英文范文库 ==")
# 按键池：只留合法 id、去重；字母题只认字母
raw = '["a","A","z","1","space","enter","garbage","a","up"]'
pool = app_mod._clean_key_pool(raw)
check("按键池自动清洗去重", pool == ["a", "z", "1", "space", "enter", "up"],
      str(pool))
check("字母池只留字母", app_mod._clean_key_pool(raw, letters_only=True) == ["a", "z"])
check("空按键池 = 全部随机", app_mod._clean_key_pool("") == [])
form10 = ImmutableMultiDict([
    ("typing_enable_key_press", "1"), ("typing_enable_case_press", "1"),
    ("typing_key_rounds", "6"), ("typing_key_seconds", "3"),
    ("typing_case_rounds", "6"), ("typing_case_seconds", "3"),
    ("typing_key_pool", '["a","s","d","f","space"]'),
    ("typing_case_pool", '["j","k","l"]'),
])
cfg10 = app_mod._typing_form_config(form10)
check("按键池存进配置", cfg10["key_press"]["keys"] == ["a", "s", "d", "f", "space"],
      str(cfg10["key_press"]["keys"]))
check("字母池存进配置", cfg10["case_press"]["keys"] == ["j", "k", "l"])
db.set_typing_config(eid, cfg10)
p10 = {t["task"]: t for t in app_mod._typing_payload(db.get_exam(eid))["tasks"]}
check("按键池随配置下发", p10["key_press"]["keys"] == ["a", "s", "d", "f", "space"],
      str(p10["key_press"].get("keys")))
db.set_typing_config(eid, app_mod._typing_defaults())

en = app_mod._load_typing_passages("小学英语打字范文.json")
check("英文范文库 14 篇", len(en) == 14, f"实际 {len(en)}")
check("英文范文有长有短", max(len(p["text"]) for p in en) > 250
      and min(len(p["text"]) for p in en) < 80)
zh = app_mod._load_typing_passages()
check("中文范文有长段落", max(len(p["text"]) for p in zh) > 150,
      f"最长 {max(len(p['text']) for p in zh)}")

page_x = api.get(f"/exam/{eid}").get_data(as_text=True)
check("编辑页有虚拟键盘", "vk-key_press" in page_x and "typing_key_pool" in page_x)
check("编辑页有字母键盘", "vk-case_press" in page_x and "typing_case_pool" in page_x)
check("编辑页英文可选课文", "enPreset" in page_x)
check("编辑页有截断控件", page_x.count("crop-bar") >= 2 and "cropText" in page_x)

# 清理测试写到真实 qimg 目录的图片
try:
    os.remove(os.path.join(app_mod.QIMG_DIR, "test_cap.png"))
except OSError:
    pass

print()
if FAIL:
    print(f"[FAIL] {len(FAIL)} 项失败: {FAIL}")
    sys.exit(1)
print("[OK] 全部通过")
