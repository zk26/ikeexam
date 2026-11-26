# -*- coding: utf-8 -*-
"""画板题型全链路冒烟测试：配置下发 → 上传 → 覆盖 → 老师端查看"""
import io
import os
import sys
import tempfile

# 用独立临时数据库，绝不动正式环境（老库会补列、会删测试数据）
_tmp = tempfile.mkdtemp(prefix="exam_drawing_test_")
os.environ["EXAM_DB"] = os.path.join(_tmp, "test.db")
os.environ["EXAM_AUTH_FILE"] = os.path.join(_tmp, "auth.json")
os.environ["EXAM_ADMIN_PASSWORD"] = "Admin@123"   # 无默认密码后，测试需显式给密码

SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "src"))
sys.path.insert(0, SRC_DIR)
from ikeexam.server import app as app_mod, db

db.init()                      # 建表 + 老库补列（drawing_config 等）

ok_n = fail_n = 0


def check(name, cond, detail=""):
    global ok_n, fail_n
    if cond:
        ok_n += 1
        print(f"  PASS {name}")
    else:
        fail_n += 1
        print(f"  FAIL {name}  {detail}")


api = app_mod.app.test_client()

print("== 1. 画板配置与下发 ==")
eid = db.create_exam({"title": "画板测试", "etype": "", "duration": 40,
                      "theory_duration": 40, "requirement": "",
                      "save_as": "", "require_complete": 0})
db.set_question_types(eid, ["drawing"])
db.set_drawing_config(eid, {"brief": "画一个电阻和灯泡串联的电路图"})
db.set_exam_status(eid, "running")
db.set_phase(eid, "theory")
db.upsert_student("DRAWPC", "127.0.0.1", "", name="小明", seat="1")
db.bind_exam(eid)

d = api.post("/api/hello", json={"hostname": "DRAWPC", "ip": "127.0.0.1",
                                 "mac": "", "name": "小明", "seat": "1"})
h = d.get_json()
_auth = {"X-Exam-Token": h.get("token", "")}
check("hello 下发 has_drawing", h["exam"].get("has_drawing") is True)
check("hello 下发画题要求",
      (h.get("drawing") or {}).get("brief") == "画一个电阻和灯泡串联的电路图",
      str(h.get("drawing")))
r = api.get("/api/poll?host=DRAWPC", headers=_auth).get_json()
check("poll 下发画板配置", (r.get("drawing") or {}).get("brief", "") != "")

print("== 2. 上传压缩图片 ==")
from PIL import Image                     # noqa: E402
buf = io.BytesIO()
Image.new("RGB", (200, 120), "white").save(buf, format="PNG", optimize=True)
png = buf.getvalue()
r = api.post("/api/drawing/upload", headers=_auth, data={
    "host": "DRAWPC", "exam_id": str(eid),
    "file": (io.BytesIO(png), "画板.png"),
}, content_type="multipart/form-data").get_json()
check("上传成功", bool(r.get("ok")), str(r))
check("服务端记录了文件", db.drawing_of(eid, "DRAWPC") is not None)
row = db.drawing_of(eid, "DRAWPC")
check("文件存在磁盘", row and os.path.isfile(row["saved_path"]),
      str(row))

print("== 3. 重复上传覆盖 ==")
buf2 = io.BytesIO()
Image.new("RGB", (300, 200), "yellow").save(buf2, format="PNG", optimize=True)
png2 = buf2.getvalue()
api.post("/api/drawing/upload", headers=_auth, data={
    "host": "DRAWPC", "exam_id": str(eid),
    "file": (io.BytesIO(png2), "画板.png"),
}, content_type="multipart/form-data")
row2 = db.drawing_of(eid, "DRAWPC")
check("以最新一份为准", row2["size"] == len(png2), str(row2))
check("drawings_for 有记录",
      any(d["hostname"] == "DRAWPC" for d in db.drawings_for(eid)))

print("== 3b. 画板 / 拼豆两道小题各交各的 ==")
db.set_drawing_config(eid, {"brief": "画电路图 / 拼一只小兔子",
                            "styles": ["draw", "beads"], "grid": 20})
d = api.post("/api/hello", headers=_auth, json={"hostname": "DRAWPC", "ip": "127.0.0.1",
                                               "mac": "", "name": "小明", "seat": "1"})
h = d.get_json()
check("下发 styles 两道小题",
      (h.get("drawing") or {}).get("styles") == ["draw", "beads"],
      str(h.get("drawing")))
check("下发拼豆格子数", (h.get("drawing") or {}).get("grid") == 20)
buf3 = io.BytesIO()
Image.new("RGB", (120, 120), "red").save(buf3, format="PNG", optimize=True)
png3 = buf3.getvalue()
r = api.post("/api/drawing/upload", headers=_auth, data={
    "host": "DRAWPC", "exam_id": str(eid), "kind": "beads",
    "file": (io.BytesIO(png3), "拼豆.png"),
}, content_type="multipart/form-data").get_json()
check("拼豆那份上传成功", bool(r.get("ok")), str(r))
row_b = db.drawing_of(eid, "DRAWPC", "beads")
check("拼豆单独记录", row_b is not None and row_b["size"] == len(png3),
      str(row_b))
row_d = db.drawing_of(eid, "DRAWPC", "draw")
check("画板那份没被覆盖", row_d is not None and row_d["size"] == len(png2),
      str(row_d))
check("drawings_for 两份都在",
      len([x for x in db.drawings_for(eid) if x["hostname"] == "DRAWPC"]) == 2,
      str(db.drawings_for(eid)))

print("== 3c. 只勾画板时拒收拼豆 ==")
db.set_drawing_config(eid, {"brief": "", "styles": ["draw"], "grid": 28})
r = api.post("/api/drawing/upload", headers=_auth, data={
    "host": "DRAWPC", "exam_id": str(eid), "kind": "beads",
    "file": (io.BytesIO(png3), "拼豆.png"),
}, content_type="multipart/form-data").get_json()
check("没勾的小题拒收", not r.get("ok"), str(r))
db.set_drawing_config(eid, {"brief": "画一个电阻和灯泡串联的电路图",
                            "styles": ["draw", "beads"], "grid": 20})

print("== 4. 老师端查看 ==")
# 老师端页面要先登录（拿 CSRF 再登）
import re                                  # noqa: E402
html = api.get("/login").get_data(as_text=True)
csrf = (re.search(r'name="_csrf" value="([^"]+)"', html) or [None, ""])[1]
api.post("/login", data={"password": "Admin@123", "next": "/", "_csrf": csrf})
_pid_draw = h.get("participant_id", 0)
html = api.get(f"/detail/{eid}/DRAWPC?participant_id={_pid_draw}").get_data(as_text=True)
check("详情页展示画板", "画板作品" in html)
st = api.get(f"/exam/{eid}/drawing/DRAWPC?participant_id={_pid_draw}")
check("可查看图片", st.status_code == 200 and st.data[:4] == b"\x89PNG",
      f"HTTP {st.status_code}")
dl = api.get(f"/exam/{eid}/drawing/DRAWPC/download?participant_id={_pid_draw}")
check("可下载图片", dl.status_code == 200)

print("== 5. 校验拦截 ==")
r = api.post("/api/drawing/upload", headers=_auth, data={
    "host": "DRAWPC", "exam_id": str(eid),
    "file": (io.BytesIO(b"MZ not an image"), "bad.exe"),
}, content_type="multipart/form-data").get_json()
check("拒绝非图片扩展名", not r.get("ok"), str(r))
eid2 = db.create_exam({"title": "无画板", "etype": "", "duration": 40,
                       "theory_duration": 40, "requirement": "",
                       "save_as": "", "require_complete": 0})
db.set_question_types(eid2, ["single"])
r = api.post("/api/drawing/upload", headers=_auth, data={
    "host": "DRAWPC", "exam_id": str(eid2),
    "file": (io.BytesIO(png), "画板.png"),
}, content_type="multipart/form-data").get_json()
check("没勾画板的考试拒收", not r.get("ok"), str(r))
check("没勾画板时 payload 为 None",
      app_mod._drawing_payload(db.get_exam(eid2)) is None)

print("== 6. 编辑页画板标签 ==")
import re as _re                         # noqa: E402
html = api.get(f"/exam/{eid}").get_data(as_text=True)
check("编辑页有画板标签", "画板创作" in html and "drawing_brief" in html)

# 清理测试数据，不往用户库里留垃圾
for _eid in (eid, eid2):
    try:
        db.delete_exam(_eid)
    except Exception:
        pass

print(f"\n{'[OK] 画板冒烟全部通过' if not fail_n else f'[FAIL] {fail_n} 条未过'}")
sys.exit(1 if fail_n else 0)
