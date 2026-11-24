# -*- coding: utf-8 -*-
"""老师端桌面软件 —— 管理员 HTTP 客户端

用标准库 urllib（不额外依赖 requests），与嵌入式 Python 打包时保持一致。
通过 session cookie 与服务端的 /admin_api/* 接口通信。
"""
import http.cookiejar
import json
import urllib.error
import urllib.request


class AdminAPI:
    def __init__(self, base):
        self.base = (base or "").strip().rstrip("/")
        self.cj = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cj))

    def _req(self, method, path, data=None, timeout=10):
        url = self.base + path
        body = None
        headers = {"Accept": "application/json"}
        if data is not None:
            body = json.dumps(data).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=body, method=method,
                                     headers=headers)
        try:
            with self.opener.open(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                return json.loads(e.read().decode("utf-8"))
            except Exception:
                return {"ok": False, "msg": f"HTTP {e.code}"}
        except urllib.error.URLError as e:
            return {"ok": False, "msg": f"无法连接服务器：{e.reason}"}
        except Exception as e:
            return {"ok": False, "msg": str(e)}

    # ---- 基础 ----
    def ping(self):
        return self._req("GET", "/admin_api/ping")

    def about(self):
        return self._req("GET", "/admin_api/about")

    def setup(self, password):
        return self._req("POST", "/admin_api/setup", {"password": password})

    def login(self, password):
        return self._req("POST", "/admin_api/login", {"password": password})

    # ---- 考试 ----
    def exams(self):
        return self._req("GET", "/admin_api/exams")

    def exam(self, eid):
        return self._req("GET", f"/admin_api/exam/{eid}")
