# -*- coding: utf-8 -*-
"""管理员会话 24 小时失效与密码登录回归。"""
import os
import re
import sys
import tempfile
import time

_tmp = tempfile.mkdtemp(prefix="exam_session_test_")
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


client = app_mod.app.test_client()
r = client.post("/admin_api/setup", json={"password": "Admin@123"})
check("设置初始密码", r.status_code == 200 and r.get_json().get("ok"), r.get_json())
r = client.post("/admin_api/login", json={"password": "Admin@123"})
data = r.get_json() or {}
check("密码登录成功", r.status_code == 200 and data.get("ok"), data)
check("会话有效期返回 24 小时", data.get("expires_in") == 24 * 3600, data)
check("Flask 会话有效期也是 24 小时",
      int(app_mod.app.permanent_session_lifetime.total_seconds()) == 24 * 3600)
csrf = data.get("csrf_token", "")
r = client.get("/admin_api/exams")
check("有效会话可以访问管理接口", r.status_code == 200 and r.get_json().get("ok"), r.get_json())

with client.session_transaction() as sess:
    sess["at"] = int(time.time()) - 24 * 3600 - 1
r = client.get("/admin_api/exams")
check("超过 24 小时管理接口返回 401", r.status_code == 401, r.get_json())
r = client.post("/admin_api/questions/tidy", headers={"X-CSRF-Token": csrf})
check("过期会话写接口返回 401 而非 CSRF 400", r.status_code == 401, r.get_json())
r = client.get("/")
check("过期会话网页跳回登录页", r.status_code == 302 and "/login" in r.headers.get("Location", ""),
      (r.status_code, r.headers.get("Location")))

r = client.post("/admin_api/login", json={"password": "Admin@123"})
check("过期后可重新登录", r.status_code == 200 and r.get_json().get("ok"), r.get_json())

web = app_mod.app.test_client()
r = web.get("/login")
check("网页登录页可访问", r.status_code == 200, r.status_code)
with web.session_transaction() as sess:
    web_csrf = sess.get("_csrf", "")
r = web.post("/login", data={"_csrf": web_csrf, "password": "Admin@123"})
check("网页密码登录成功", r.status_code == 302 and r.headers.get("Location", "").startswith("/"),
      (r.status_code, r.headers.get("Location")))
r = web.get("/")
remaining = re.search(r"var seconds = (\d+);", r.get_data(as_text=True))
check("网页注入不足 24 小时的会话倒计时",
      r.status_code == 200 and remaining and 86390 <= int(remaining.group(1)) <= 86400,
      remaining.group(1) if remaining else None)
with web.session_transaction() as sess:
    sess["at"] = int(time.time()) - 24 * 3600 - 1
r = web.get("/")
check("网页登录超过 24 小时同样跳回登录页",
      r.status_code == 302 and "/login" in r.headers.get("Location", ""),
      (r.status_code, r.headers.get("Location")))

if failures:
    print(f"[FAIL] {len(failures)} 项失败: {failures}")
    raise SystemExit(1)
print("[OK] 管理员会话与密码登录测试全部通过")
