# -*- coding: utf-8 -*-
"""老师端桌面软件 —— 管理员 HTTP 客户端。

只使用标准库 urllib，兼容安装包内的嵌入式 Python。所有请求共享
CookieJar；登录后保存 CSRF token，并自动添加到所有写请求头。
JSON 接口和二进制下载都返回字典，失败统一为 ``{"ok": False, "msg": ...}``。
"""
import email.message
import http.cookiejar
import json
import mimetypes
import os
import threading
import urllib.error
import urllib.parse
import urllib.request
import uuid


class AdminAPI:
    def __init__(self, base):
        self._base = ""
        self.csrf_token = ""
        self._session_lock = threading.RLock()
        self.cj = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cj))
        self.base = base

    @property
    def base(self):
        return self._base

    @base.setter
    def base(self, value):
        value = (value or "").strip().rstrip("/")
        with self._session_lock:
            if value != self._base:
                self._base = value
                self.csrf_token = ""
                self.cj = http.cookiejar.CookieJar()
                self.opener = urllib.request.build_opener(
                    urllib.request.HTTPCookieProcessor(self.cj))

    def set_base(self, base):
        self.base = base

    def clear_session(self):
        """退出登录并尽量让服务端会话同时失效。"""
        try:
            if self.csrf_token:
                self._req("POST", self._path("logout"), {})
        except Exception:
            pass
        with self._session_lock:
            self.csrf_token = ""
            self.cj.clear()
            for cookie in list(self.cj):
                self.cj.clear(cookie.domain, cookie.path, cookie.name)

    @staticmethod
    def _path(*parts):
        return "/admin_api/" + "/".join(
            urllib.parse.quote(str(part), safe="") for part in parts)

    @staticmethod
    def _error_message(raw, status=None, fallback="请求失败"):
        if isinstance(raw, dict):
            msg = raw.get("msg") or raw.get("error") or fallback
            return {"ok": False, "msg": str(msg), "status": status}
        text = ""
        if raw:
            try:
                text = raw.decode("utf-8", "replace").strip()
            except Exception:
                text = str(raw)
        if text:
            try:
                parsed = json.loads(text)
                if isinstance(parsed, dict):
                    msg = parsed.get("msg") or parsed.get("error") or text
                    return {"ok": False, "msg": str(msg), "status": status}
            except (TypeError, ValueError):
                pass
            # 不把一整段 HTML 错误页塞进状态栏。
            if len(text) > 240:
                text = text[:240] + "…"
            return {"ok": False, "msg": text, "status": status}
        return {"ok": False,
                "msg": f"HTTP {status}" if status else fallback,
                "status": status}

    @staticmethod
    def _download_name(disposition):
        if not disposition:
            return ""
        try:
            msg = email.message.Message()
            msg["Content-Disposition"] = disposition
            name = msg.get_filename()
            if name:
                return os.path.basename(name)
        except Exception:
            pass
        try:
            for part in disposition.split(";"):
                key, _, value = part.strip().partition("=")
                if key.lower() == "filename":
                    return os.path.basename(value.strip().strip('"'))
        except Exception:
            pass
        return ""

    def _remember_csrf(self, payload):
        if isinstance(payload, dict) and payload.get("csrf_token"):
            with self._session_lock:
                self.csrf_token = str(payload["csrf_token"])

    def _perform(self, method, path, body=None, headers=None, timeout=10,
                 binary=False):
        """实际执行请求；网络和解析均在此完成，可由后台线程调用。"""
        url = self.base + path
        request_headers = {"Accept": "application/json, */*"}
        if headers:
            request_headers.update(headers)
        # setup/login 是建立会话的接口，其余写请求必须带 token。
        if method.upper() in ("POST", "PUT", "PATCH", "DELETE"):
            with self._session_lock:
                token = self.csrf_token
            if token:
                request_headers["X-CSRF-Token"] = token
        req = urllib.request.Request(url, data=body, method=method.upper(),
                                     headers=request_headers)
        try:
            with self.opener.open(req, timeout=timeout) as response:
                raw = response.read()
                content_type = response.headers.get("Content-Type", "")
                disposition = response.headers.get("Content-Disposition", "")
                status = getattr(response, "status", 200)
            if binary:
                return {
                    "ok": True,
                    "data": raw,
                    "content": raw,       # 兼容调用方对字段名的不同习惯
                    "filename": self._download_name(disposition),
                    "content_type": content_type,
                    "size": len(raw),
                    "status": status,
                }
            if not raw:
                return {"ok": True, "status": status}
            try:
                payload = json.loads(raw.decode("utf-8-sig"))
            except (UnicodeDecodeError, ValueError):
                snippet = raw[:160].decode("utf-8", "replace")
                return {"ok": False, "status": status,
                        "msg": f"服务器返回了无效 JSON：{snippet}"}
            self._remember_csrf(payload)
            if isinstance(payload, dict):
                payload.setdefault("status", status)
            return payload
        except urllib.error.HTTPError as exc:
            try:
                raw = exc.read()
            except Exception:
                raw = b""
            finally:
                exc.close()
            result = self._error_message(raw, exc.code,
                                         f"HTTP {exc.code}")
            # 极少数服务端会在错误响应里刷新会话 token。
            try:
                parsed = json.loads(raw.decode("utf-8-sig"))
                self._remember_csrf(parsed)
            except Exception:
                pass
            return result
        except urllib.error.URLError as exc:
            return {"ok": False,
                    "msg": f"无法连接服务器：{exc.reason}"}
        except (TimeoutError, OSError) as exc:
            return {"ok": False, "msg": f"网络请求失败：{exc}"}
        except Exception as exc:
            return {"ok": False, "msg": str(exc) or "请求失败"}

    def _req(self, method, path, data=None, timeout=10, headers=None):
        body = None
        request_headers = dict(headers or {})
        if data is not None:
            try:
                body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            except (TypeError, ValueError) as exc:
                return {"ok": False, "msg": f"JSON 数据格式错误：{exc}"}
            request_headers["Content-Type"] = "application/json; charset=utf-8"
        return self._perform(method, path, body, request_headers, timeout=timeout)

    def _download(self, path, timeout=60):
        return self._perform("GET", path, timeout=timeout, binary=True)

    # ---- 基础 ----
    def ping(self, timeout=3):
        return self._req("GET", self._path("ping"), timeout=timeout)

    def about(self):
        return self._req("GET", self._path("about"))

    def setup(self, password):
        return self._req("POST", self._path("setup"), {"password": password})

    def login(self, password):
        return self._req("POST", self._path("login"), {"password": password})

    def set_passwordless(self, enabled):
        return self._req("POST", self._path("passwordless"),
                         {"enabled": bool(enabled)},
                         headers={"X-ikeexam-Local": "1"})

    # ---- 总览 / 考试 ----
    def dashboard(self):
        return self._req("GET", self._path("dashboard"), timeout=20)

    def exams(self):
        return self._req("GET", self._path("exams"))

    def exam(self, eid):
        return self._req("GET", self._path("exam", int(eid)))

    def create_exam(self, data):
        return self._req("POST", self._path("exams"), data)

    def update_exam(self, eid, data):
        return self._req("PUT", self._path("exam", int(eid)), data)

    def delete_exam(self, eid):
        return self._req("DELETE", self._path("exam", int(eid)))

    def set_exam_questions(self, eid, question_ids):
        return self._req("POST", self._path("exam", int(eid), "questions"),
                         {"question_ids": list(question_ids)})

    def set_exam_practice(self, eid, practice_ids):
        return self._req("POST", self._path("exam", int(eid), "practice"),
                         {"practice_ids": list(practice_ids)})

    def set_roster(self, eid, text, mode="replace"):
        return self._req("PUT", self._path("exam", int(eid), "roster"),
                         {"text": text, "mode": mode})

    def upload_material_file(self, eid, fileobj, filename):
        """上传一个已打开的二进制文件，表单字段名为 file。"""
        boundary = "----ikee-" + uuid.uuid4().hex
        filename = os.path.basename(filename or "material")
        mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        quoted = urllib.parse.quote(filename, safe="")
        fallback = filename.encode("ascii", "replace").decode("ascii")
        fallback = fallback.replace('"', "_").replace("\\", "_")
        prefix = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; '
            f'filename="{fallback}"; filename*=UTF-8\'\'{quoted}\r\n'
            f"Content-Type: {mime}\r\n\r\n"
        ).encode("ascii")
        try:
            content = fileobj.read()
            if isinstance(content, str):
                content = content.encode("utf-8")
        except Exception as exc:
            return {"ok": False, "msg": f"读取素材失败：{exc}"}
        body = prefix + content + f"\r\n--{boundary}--\r\n".encode("ascii")
        return self._perform(
            "POST", self._path("exam", int(eid), "materials"),
            body=body,
            headers={"Content-Type":
                     f"multipart/form-data; boundary={boundary}"},
            timeout=60)

    def upload_material(self, eid, path):
        try:
            with open(path, "rb") as fileobj:
                return self.upload_material_file(
                    eid, fileobj, os.path.basename(path))
        except OSError as exc:
            return {"ok": False, "msg": f"无法打开素材文件：{exc}"}

    def delete_material(self, eid, name):
        return self._req("DELETE", self._path(
            "exam", int(eid), "materials", os.path.basename(name)))

    def start_exam(self, eid, klass):
        return self._req("POST", self._path("exam", int(eid), "start"),
                         {"klass": klass})

    def pause_exam(self, eid):
        return self._req("POST", self._path("exam", int(eid), "pause"), {})

    def resume_exam(self, eid):
        return self._req("POST", self._path("exam", int(eid), "resume"), {})

    def set_phase(self, eid, phase):
        return self._req("POST", self._path("exam", int(eid), "phase"),
                         {"phase": phase})

    def stop_exam(self, eid, clear_login=False):
        return self._req("POST", self._path("exam", int(eid), "stop"),
                         {"clear_login": bool(clear_login)})

    def scores(self, eid):
        return self._req("GET", self._path("exam", int(eid), "scores"),
                         timeout=30)

    def review(self, eid):
        return self._req("GET", self._path("exam", int(eid), "review"),
                         timeout=30)

    def stats(self, eid):
        return self._req("GET", self._path("exam", int(eid), "stats"),
                         timeout=30)

    def export_exam(self, eid, fmt="json", timeout=120):
        fmt = str(fmt or "json").lower()
        if fmt not in ("json", "csv", "xlsx", "zip"):
            return {"ok": False, "msg": "不支持的导出格式"}
        query = urllib.parse.urlencode({"fmt": fmt})
        return self._download(self._path("exam", int(eid), "export") +
                              "?" + query, timeout=timeout)

    def regrade(self, eid):
        return self._req("POST", self._path("exam", int(eid), "regrade"), {},
                         timeout=30)

    def rejudge_code(self, eid):
        return self._req("POST", self._path("exam", int(eid), "rejudge-code"),
                         {}, timeout=60)

    # ---- 理论题库 ----
    def questions(self, keyword="", qtype="", category=""):
        query = urllib.parse.urlencode({
            "kw": keyword or "", "qtype": qtype or "",
            "category": category or ""})
        return self._req("GET", self._path("questions") + "?" + query,
                         timeout=20)

    def create_question(self, data):
        return self._req("POST", self._path("questions"), data)

    def question(self, qid):
        return self._req("GET", self._path("question", int(qid)))

    def update_question(self, qid, data):
        return self._req("PUT", self._path("question", int(qid)), data)

    def delete_question(self, qid):
        return self._req("DELETE", self._path("question", int(qid)))

    def import_questions(self, items):
        return self._req("POST", self._path("questions", "import"),
                         {"items": items}, timeout=60)

    def tidy_questions(self):
        return self._req("POST", self._path("questions", "tidy"), {})

    # ---- 实操题库 ----
    def practice(self):
        return self._req("GET", self._path("practice"), timeout=20)

    def create_practice(self, data):
        return self._req("POST", self._path("practice"), data)

    def practice_item(self, pid):
        return self._req("GET", self._path("practice", int(pid)))

    def update_practice(self, pid, data):
        return self._req("PUT", self._path("practice", int(pid)), data)

    def delete_practice(self, pid):
        return self._req("DELETE", self._path("practice", int(pid)))

    # ---- 学生机 ----
    def dashboard_students(self):
        return self._req("GET", self._path("dashboard", "students"), timeout=10)

    def reset_student_token(self, hostname):
        return self._req("POST", self._path("students", "reset-token"),
                         {"hostname": hostname})

    # 与路由命名一致的可读别名，便于维护和外部调用。
    exam_questions = set_exam_questions
    exam_practice = set_exam_practice
    exam_roster = set_roster
    exam_start = start_exam
    exam_pause = pause_exam
    exam_resume = resume_exam
    exam_phase = set_phase
    exam_stop = stop_exam
    exam_scores = scores
    exam_review = review
    exam_stats = stats
    exam_export = export_exam
    exam_regrade = regrade
    exam_rejudge_code = rejudge_code
    add_question = create_question
    question_update = update_question
    question_delete = delete_question
    add_practice = create_practice
    practice_update = update_practice
    practice_delete = delete_practice
    student_reset_token = reset_student_token
