"""
ikee考务中心 —— 服务端
两阶段流程：理论题 → 实操题（自动拉起 WPS）
"""
import csv
import io
import ipaddress
import json
import os
import queue as _queue
import re
import secrets
import sys
import threading
import time
import uuid
from functools import wraps

from flask import (Flask, request, jsonify, render_template, send_file,
                   redirect, url_for, flash, session)

# ⚠️ Windows 控制台默认是 GBK：日志里的 ✅/⚠️ 这类字符会抛 UnicodeEncodeError，
#    把整个服务端带崩（输出被重定向到文件时必崩，双击运行也可能）。
#    这里统一降级：编不出的字符输出成 '?'，日志照写，服务不受影响。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")
    except Exception:
        pass

BASE = os.path.dirname(os.path.abspath(__file__))
PACKAGE_DIR = os.path.dirname(BASE)
SRC_DIR = os.path.dirname(PACKAGE_DIR)
ROOT = BASE
sys.path.insert(0, BASE)
sys.path.insert(0, SRC_DIR)
from ikeexam.shared.config_schema import RULE_TYPES

# 打包成 EXE 后路径规则不同（只读资源在解包目录、可写数据在 exe 旁边）：
# 见 runtime.py 的说明。必须在 import db / auth 之前准备好，它们顶层会读环境变量。
try:
    from . import runtime
    runtime.prepare()
    from . import db
    from . import exam as exm
    from . import auth
except ImportError:
    import runtime
    runtime.prepare()
    import db
    import exam as exm
    import auth

MATERIAL_DIR = runtime.MATERIAL_DIR   # 素材
UPLOAD_DIR = runtime.UPLOAD_DIR       # 学生提交
EXAM_DIR = runtime.EXAM_DIR           # 考试包导出
QIMG_DIR = runtime.QIMG_DIR           # 理论题选项图片（img:文件名 引用）
BANK_DIR = runtime.BANK_DIR           # 题库（老师的 xlsx / json 放这里）
for d in (MATERIAL_DIR, UPLOAD_DIR, EXAM_DIR, QIMG_DIR, BANK_DIR):
    os.makedirs(d, exist_ok=True)

# 模板/静态文件是只读资源：EXE 模式从解包目录取
app = Flask(__name__, template_folder=runtime.TEMPLATE_DIR,
            static_folder=runtime.STATIC_DIR)


def _safe_name(name, fallback=""):
    """
    净化文件/目录名：去掉路径分隔符和通配符，防 '../' 穿越。
    fallback 用于空值兜底（如班级没填 → 未分班）。
    """
    s = "".join(ch for ch in str(name or "")
                if ch not in r'\/:*?"<>|' and ch >= " ")
    return s.strip().strip(".") or fallback


def _valid_office_upload(upload):
    """校验 Office 文件确实是包含对应主文档的有效 ZIP 包。"""
    import zipfile
    ext = os.path.splitext(upload.filename or "")[1].lower()
    required = {".docx": "word/document.xml", ".xlsx": "xl/workbook.xml",
                ".pptx": "ppt/presentation.xml"}
    if ext not in required:
        return False
    stream = upload.stream
    pos = 0
    try:
        pos = stream.tell()
        if not zipfile.is_zipfile(stream):
            return False
        stream.seek(pos)
        with zipfile.ZipFile(stream) as z:
            names = set(z.namelist())
            if required[ext] not in names or "[Content_Types].xml" not in names:
                return False
            total = sum(info.file_size for info in z.infolist())
            return total <= 500 * 1024 * 1024
    except Exception:
        return False
    finally:
        try:
            stream.seek(pos)
        except Exception:
            pass


def _valid_image_upload(upload):
    """校验常见图片签名，避免把任意文件伪装成 PNG/JPEG。"""
    stream = upload.stream
    pos = 0
    try:
        pos = stream.tell()
        head = stream.read(16)
        return (head.startswith(b"\x89PNG\r\n\x1a\n")
                or head.startswith(b"\xff\xd8\xff"))
    except Exception:
        return False
    finally:
        try:
            stream.seek(pos)
        except Exception:
            pass


def _icon(name, size=16):
    """
    内联 SVG 图标（模板里写 {{ icon('play') }} 就行，不用 import）。

    图标本体在 templates/_icons.html 的宏里，这里把它注册成 Jinja 全局函数 ——
    每个子模板都去 {% from %} 一遍太容易漏（漏了就 Undefined 报错）。
    取不到图标时返回空串，绝不因为一个图标把页面搞崩。
    """
    try:
        tpl = app.jinja_env.get_template("_icons.html")
        return tpl.module.icon(name, size)
    except Exception:
        return ""


app.jinja_env.globals["icon"] = _icon


def _secret_key():
    """
    会话签名密钥。
    优先环境变量 EXAM_SECRET_KEY；否则用「可写目录」下的 secret.key（首次运行自动生成）。
    不在源码里硬编码默认值 —— 源码会随系统分发，硬编码等于公开。
    ⚠️ 打包成 EXE 后必须写到 exe 旁边（runtime.SECRET_FILE）：
       解包目录每次启动都变，写进去等于每次都换新密钥、老会话全失效。
    """
    env = os.environ.get("EXAM_SECRET_KEY", "").strip()
    if env:
        return env
    import secrets
    key_file = runtime.SECRET_FILE
    try:
        if os.path.isfile(key_file):
            with open(key_file, "r", encoding="utf-8") as f:
                k = f.read().strip()
            if k:
                return k
        k = secrets.token_hex(32)
        with open(key_file, "w", encoding="utf-8") as f:
            f.write(k)
        return k
    except Exception:
        # 文件系统异常时退化为随机密钥（重启后旧会话失效，可接受）
        return secrets.token_hex(32)


app.secret_key = _secret_key()
ADMIN_SESSION_SECONDS = 24 * 3600
FINAL_SUBMIT_GRACE_SECONDS = 5 * 60
app.permanent_session_lifetime = ADMIN_SESSION_SECONDS
app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024   # 单文件 100MB
# 会话 Cookie 加固：SameSite=Lax 可拦下大多数跨站 POST（配合下面的 CSRF token）
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

TYPE_NAME = {"word": "WPS文字", "excel": "WPS表格", "ppt": "WPS演示"}
# 题型中文名（含程序填空）
QTYPE_CN = {"single": "单选", "multi": "多选", "judge": "判断",
            "blank": "填空", "code": "程序填空", "drag": "拖拽题"}
EXT_OF = {"word": ".docx", "excel": ".xlsx", "ppt": ".pptx"}

# 服务名 / 端口：给学生端自动发现用
SERVER_NAME = os.environ.get("EXAM_SERVER_NAME", "机房考试服务器")
HTTP_PORT = int(os.environ.get("EXAM_HTTP_PORT", "9000"))

# 广播对象，启动时填充
beacon_obj = None


def _csrf_token():
    """取/生成本次会话的 CSRF token（存 session，模板渲染与请求校验共用同一份）"""
    tok = session.get("_csrf")
    if not tok:
        tok = secrets.token_hex(16)
        session["_csrf"] = tok
    return tok


@app.context_processor
def _inject():
    """给所有模板注入公共变量"""
    out = {"is_default": False}
    try:
        out["is_default"] = auth.is_default()
    except Exception:
        pass
    try:
        out["csrf_token"] = _csrf_token()
    except Exception:
        out["csrf_token"] = ""
    out["session_expires_in"] = 0
    try:
        if _admin_session_active():
            remaining = ADMIN_SESSION_SECONDS - (time.time() - float(session["at"]))
            out["session_expires_in"] = max(0, int(remaining))
    except (KeyError, TypeError, ValueError):
        pass
    return out


@app.template_filter("todate")
def _todate(ts):
    """时间戳 -> 本地时间字符串"""
    if not ts:
        return "-"
    try:
        return time.strftime("%m-%d %H:%M:%S", time.localtime(float(ts)))
    except Exception:
        return "-"


# 让模板里能直接用 json.loads
app.jinja_env.globals["json"] = json


def _host(d):
    """
    取客户端标识。
    不同接口历史上用过 host / hostname 两个名字，这里都接受，
    避免客户端写错字段名导致 400（实测踩过）。

    ⚠️ client_id（机器唯一标识，基于 MAC）优先：以前直接用 Windows 机器名，
       同 Ghost / 同型号的机器机器名大量重复，服务端按机器名做主键会互相覆盖
       （38 台机器只剩 16 个学生）。现在新客户端都带 client_id，
       老客户端没带时回退到机器名，行为不变。
    """
    cid = (d.get("client_id") or "").strip()
    if cid:
        return cid
    return ((d.get("host") or d.get("hostname") or "") or "").strip()


def _host_req():
    """从请求里取客户端标识（URL 参数 + JSON 表单都认）"""
    d = request.args.to_dict()
    try:
        d.update(request.get_json(silent=True) or {})
    except Exception:
        pass
    try:
        d.update({k: v for k, v in request.form.items()})
    except Exception:
        pass
    return _host(d)


def _request_token(d=None):
    token = request.headers.get("X-Exam-Token", "").strip()
    if token:
        return token
    if d is None:
        d = request.args.to_dict()
        try:
            d.update(request.get_json(silent=True) or {})
        except Exception:
            pass
        try:
            d.update(request.form.to_dict())
        except Exception:
            pass
    return str((d or {}).get("token") or "").strip()


def _int_field(value, default=0):
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default


def _student_auth(d=None):
    """学生 API 统一机器认证，返回 (hostname, student, error)。"""
    host = _host(d) if d is not None else _host_req()
    if not host:
        return "", None, "缺少机器标识"
    if not db.verify_student(host, _request_token(d)):
        return host, None, "机器凭证无效，请重新连接或联系老师重置"
    return host, db.get_student(host), None


def _student_write_allowed(ex):
    """考试进行中可写；老师收卷后给客户端 5 分钟完成最终上传。"""
    if ex.get("status") in ("running", "paused"):
        return True
    if ex.get("status") != "stopped":
        return False
    try:
        return time.time() <= float(ex.get("stop_at") or 0) + FINAL_SUBMIT_GRACE_SECONDS
    except (TypeError, ValueError):
        return False


# ==================== 老师端登录保护 ====================
#
# 学生端 API（/api/*）不设防 —— 学生机不登录也要能答题。
# 其余后台页面一律要密码。
#
# 用 before_request 统一拦截，比给 30 个路由逐个加装饰器可靠：
# 以后新增页面自动受保护，不会漏。
LOGIN_EXEMPT = (
    "/api/",         # 学生端接口
    "/admin_api/",   # 管理员 JSON API（桌面老师端；接口内部自认证）
    "/static/",      # 静态资源
    "/qimg/",        # 理论题选项图片（学生端答题时加载，无敏感信息）
)
# 完整路径豁免（不用前缀匹配：/loginX、/screen-foo 这类路径不该混进来）
LOGIN_EXEMPT_EXACT = (
    "/login",
    "/setup",
    "/logout",
    "/screen",      # 公开大屏（投屏用，只读进度，无敏感信息）
    "/screen.json",
)

# 首次运行只做副作用检查：有环境变量则重置密码；无密码则保持未初始化，
# 等老师第一次打开后台页面时在 /setup 设置初始密码。
auth.ensure()


def _admin_session_active():
    if not session.get("admin"):
        return False
    if session.get("auth_at") != auth.updated_at():
        return False
    try:
        return time.time() < float(session.get("at", 0)) + ADMIN_SESSION_SECONDS
    except (TypeError, ValueError):
        return False


@app.before_request
def _require_login():
    if _admin_session_active():
        return None
    if session.get("admin"):
        session.clear()
    path = request.path
    if path in LOGIN_EXEMPT_EXACT:
        return None
    for pre in LOGIN_EXEMPT:
        if path.startswith(pre):
            return None
    # 首次运行还没设置密码：所有后台页都先引导去设置初始密码
    if auth.needs_setup():
        return redirect(url_for("setup"))
    return redirect(url_for("login", next=path))


@app.before_request
def _csrf_protect():
    """CSRF 防护：会改变状态的请求必须携带会话 token。

    学生端 /api/* 豁免（学生机不登录、拿不到 session token）。
    其余 POST/PUT/PATCH/DELETE（含登录、改密码、考试启停/删改）都要 token，
    否则攻击者仅凭一张图/一个链接无法跨站改后台。
    """
    if request.method not in ("POST", "PUT", "PATCH", "DELETE"):
        return None
    if request.path.startswith("/api/"):
        return None
    if request.path.startswith("/admin_api/"):
        if request.path in ("/admin_api/login", "/admin_api/setup", "/admin_api/logout"):
            return None
        if not _admin_session_active():
            session.clear()
            return jsonify({"ok": False, "msg": "登录已失效，请重新登录"}), 401
        token = session.get("_csrf")
        sent = request.headers.get("X-CSRF-Token", "")
        if not token or not sent or not secrets.compare_digest(token, sent):
            return jsonify({"ok": False, "msg": "请求校验失败，请重新登录"}), 400
        return None
    token = session.get("_csrf")
    sent = request.form.get("_csrf") or request.headers.get("X-CSRF-Token") or ""
    if not token or not sent or not secrets.compare_digest(token, sent):
        return jsonify({"ok": False, "msg": "请求校验失败，请刷新页面后重试"}), 400
    return None


def _safe_next(target):
    """登录跳转只允许本站相对路径，防开放重定向（//http: 开头都不行）"""
    t = (target or "").strip()
    if t.startswith("/") and not t.startswith("//"):
        return t
    return "/"


def _setup_allowed():
    return (request.remote_addr in ("127.0.0.1", "::1")
            or os.environ.get("EXAM_ALLOW_REMOTE_SETUP") == "1")


@app.route("/login", methods=["GET", "POST"])
def login():
    # 还没设置密码时，强制先去设置页
    if auth.needs_setup():
        return redirect(url_for("setup"))
    if request.method == "POST":
        # 先查是否被锁
        left = auth.locked()
        if left:
            return render_template("login.html",
                                   err=f"尝试次数过多，请 {left} 秒后再试",
                                   next=request.args.get("next", "/"))
        pwd = (request.form.get("password") or "").strip()
        if auth.verify(pwd):
            auth.note_ok()
            session.permanent = True
            session["admin"] = True
            session["at"] = int(time.time())
            session["auth_at"] = auth.updated_at()
            flash("登录成功")
            return redirect(_safe_next(request.args.get("next")) or url_for("index"))
        # 失败
        n = auth.note_fail()
        if n > 0:
            err = f"密码错误，还可尝试 {n} 次"
        else:
            err = f"尝试次数过多，锁定 {auth.LOCK_SECONDS} 秒"
        return render_template("login.html", err=err,
                               next=request.args.get("next", "/"))
    return render_template("login.html", next=request.args.get("next", "/"))


@app.route("/setup", methods=["GET", "POST"])
def setup():
    """首次运行：设置初始密码。设置过密码后此页自动失效（跳回登录）。"""
    if not auth.needs_setup():
        return redirect(url_for("login"))
    if not _setup_allowed():
        return "首次设置密码只允许在本机进行，或设置 EXAM_ALLOW_REMOTE_SETUP=1", 403
    if request.method == "POST":
        pwd = (request.form.get("password") or "").strip()
        pwd2 = (request.form.get("password2") or "").strip()
        if len(pwd) < 6:
            return render_template("setup.html", err="密码至少 6 位")
        if pwd != pwd2:
            return render_template("setup.html", err="两次输入的密码不一致")
        auth.set_password(pwd)
        flash("初始密码已设置，请用新密码登录")
        return redirect(url_for("login"))
    return render_template("setup.html")


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/settings", methods=["GET", "POST"])
def settings():
    """修改密码"""
    if request.method == "POST":
        old = (request.form.get("old") or "").strip()
        new = (request.form.get("new") or "").strip()
        new2 = (request.form.get("new2") or "").strip()
        if not auth.verify(old):
            return render_template("settings.html", err="原密码不正确")
        if len(new) < 6:
            return render_template("settings.html", err="新密码至少 6 位")
        if new != new2:
            return render_template("settings.html", err="两次输入的新密码不一致")
        auth.set_password(new)
        session.clear()
        flash("密码已修改，请重新登录")
        return redirect(url_for("index"))
    return render_template("settings.html", updated=auth.updated_at())


# ==================== 后台评分队列 ====================
#
# 50 人一起交卷时，如果在上传请求里同步评分（解析 docx/xlsx 要 0.3~1 秒），
# 请求线程会被占满，学生端表现为"点了提交半天没反应"。
# 改成：上传落盘 + 建记录就立刻返回，评分丢给后台线程排队做。
_grade_q = _queue.Queue()


def _do_grade(sub_id, path, checks, host):
    try:
        total, full, detail = exm.grade_file(path, checks)
        db.update_score(sub_id, total, full, detail)
        errs = [d for d in detail if d.get("error")]
        if errs:
            db.log(host, "warn",
                   f"自动评分完成 {total}/{full}，但 {len(errs)} 个评分点执行失败，需人工复核")
        else:
            db.log(host, "info", f"自动评分完成 {total}/{full}")
    except Exception as e:
        db.log(host, "error", f"自动评分失败: {e}")


def _grade_worker():
    while True:
        item = _grade_q.get()
        if item is None:
            continue
        try:
            _do_grade(*item)
        except Exception as e:
            # _do_grade 内部已 try/except 记日志；这里兜底防止记日志本身抛异常
            db.log(item[3] if item else "", "error", f"评分线程异常: {e}")
        finally:
            _grade_q.task_done()


def enqueue_grade(sub_id, path, checks, host):
    """把一次评分丢进后台队列，返回排队位置"""
    _grade_q.put((sub_id, path, list(checks or []), host))
    return _grade_q.qsize()


# 2 个评分线程：评分是 CPU 活（受 GIL 限制），开太多反而互相抢
for _i in range(2):
    threading.Thread(target=_grade_worker, daemon=True).start()


# ==================== 管理员 JSON API（Windows 桌面老师端） ====================
# 桌面老师端不打开网页，而是通过这些 JSON 接口操作。
# 认证：/admin_api/login 建立 session（signed cookie），其余接口检查 session["admin"]。
# CSRF：登录后写请求必须携带服务端返回的 X-CSRF-Token。

@app.route("/admin_api/ping")
def admin_api_ping():
    """探活 + 是否需首次设密（桌面老师端启动时先调这个）"""
    return jsonify({"ok": True, "name": "ikee考务中心", "version": "2.1.1",
                    "setup": auth.needs_setup()})


@app.route("/admin_api/about")
def admin_api_about():
    """关于：署名 / 协议 / 仓库（桌面老师端「关于」对话框用）"""
    return jsonify({
        "name": "ikee考务中心",
        "author": "小可",
        "site": "https://www.ikee002.top/",
        "repo": "https://github.com/zk26/ikeexam",
        "license": "MIT",
        "desc": "机房考试系统：理论题在线作答，WPS 实操题自动评分。",
    })


@app.route("/admin_api/setup", methods=["POST"])
def admin_api_setup():
    """首次设置初始密码"""
    if not auth.needs_setup():
        return jsonify({"ok": False, "msg": "已设置过密码"}), 400
    if not _setup_allowed():
        return jsonify({"ok": False, "msg": "首次设置密码只允许在本机进行"}), 403
    d = request.get_json(silent=True) or {}
    pwd = (d.get("password") or "").strip()
    if len(pwd) < 6:
        return jsonify({"ok": False, "msg": "密码至少 6 位"}), 400
    auth.set_password(pwd)
    return jsonify({"ok": True})


@app.route("/admin_api/login", methods=["POST"])
def admin_api_login():
    d = request.get_json(silent=True) or {}
    pwd = (d.get("password") or "").strip()
    left = auth.locked()
    if left:
        return jsonify({"ok": False, "msg": f"尝试过多，{left} 秒后再试"}), 429
    if not auth.verify(pwd):
        auth.note_fail()
        return jsonify({"ok": False, "msg": "密码错误"}), 401
    auth.note_ok()
    session.permanent = True
    session["admin"] = True
    session["at"] = int(time.time())
    session["auth_at"] = auth.updated_at()
    return jsonify({"ok": True, "csrf_token": _csrf_token(),
                    "expires_in": ADMIN_SESSION_SECONDS})


def _admin_ok():
    """管理员接口内部认证：桌面客户端登录后 session 会带 admin"""
    if not _admin_session_active():
        session.clear()
        return False
    return True


@app.route("/admin_api/exams")
def admin_api_exams():
    if not _admin_ok():
        return jsonify({"ok": False, "msg": "未登录"}), 401
    return jsonify({"ok": True, "exams": db.list_exams()})


@app.route("/admin_api/logout", methods=["POST"])
def admin_api_logout():
    session.clear()
    return jsonify({"ok": True})


def _admin_fail(msg, code=400):
    return jsonify({"ok": False, "msg": msg}), code


def _admin_exam_or_404(eid):
    ex = db.get_exam(eid)
    if not ex:
        return None, _admin_fail("考试不存在", 404)
    return ex, None


def _clean_id_list(values):
    out = []
    for value in values or []:
        try:
            n = int(value)
        except (TypeError, ValueError):
            continue
        if n > 0 and n not in out:
            out.append(n)
    return out


def _is_ip(value):
    try:
        return str(ipaddress.ip_address(str(value or "").strip()))
    except ValueError:
        return ""


def _roster_rows(text):
    """名单文本 → [(seat, name, ip)]，支持姓名、座位、IP 任意常见顺序。"""
    rows = []
    seen = set()
    for line in str(text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [x for x in re.split(r"[,，|｜\s]+", line) if x]
        if not parts:
            continue
        name, seat = "", ""
        ips = [_is_ip(part) for part in parts]
        ip = next((value for value in ips if value), "")
        plain = [part for part, value in zip(parts, ips) if not value]
        if ip:
            if len(plain) >= 2:
                name, seat = plain[0], plain[1]
            elif len(plain) == 1:
                if plain[0].isdigit():
                    seat, name = plain[0], "IP学生"
                else:
                    name = plain[0]
            else:
                name = "IP学生"
        else:
            name = plain[0] if plain else parts[0]
            seat = plain[1] if len(plain) > 1 else ""
        key = (name, seat, ip)
        if key not in seen:
            seen.add(key)
            rows.append((seat, name, ip))
    return rows


def _question_payload(data, current=None):
    """把桌面端 JSON 题目转成与网页表单一致的数据库字段。"""
    d = dict(data or {})
    old = dict(current or {})
    qtype = str(d.get("qtype", old.get("qtype", "single"))).lower()
    if qtype not in ("single", "multi", "judge", "blank", "code", "drag"):
        raise ValueError("不支持的题型")
    stem = str(d.get("stem", old.get("stem", ""))).strip()
    answer = str(d.get("answer", old.get("answer", ""))).strip()
    if not stem:
        raise ValueError("题干不能为空")
    if not answer:
        raise ValueError("答案不能为空")
    try:
        score = float(d.get("score", old.get("score", 2)))
    except (TypeError, ValueError):
        raise ValueError("分值格式不对")
    if score <= 0 or score > 1000:
        raise ValueError("分值必须在 0～1000 之间")
    opts = d.get("options", old.get("options") or [])
    if isinstance(opts, str):
        try:
            opts = json.loads(opts)
        except Exception:
            opts = [x.strip() for x in opts.splitlines() if x.strip()]
    if not isinstance(opts, list):
        raise ValueError("选项格式不对")
    opts = [str(x).strip() for x in opts if str(x).strip()]
    if qtype in ("single", "multi"):
        if len(opts) < 2:
            raise ValueError("选择题至少需要两个选项")
        letters = set("ABCDEFGH"[:len(opts)])
        picked = {x.upper() for x in re.findall(r"[A-Za-z]", answer)} & letters
        if not picked or picked != {x.upper() for x in re.findall(r"[A-Za-z]", answer)}:
            raise ValueError("答案字母超出选项范围")
        if qtype == "single" and len(picked) != 1:
            raise ValueError("单选题只能有一个答案")
        if qtype == "multi" and len(picked) < 2:
            raise ValueError("多选题至少需要两个答案")
        answer = "".join(sorted(picked))
        opts = [f"{'ABCDEFGH'[i]}. {_strip_opt_prefix(x)}" for i, x in enumerate(opts)]
    elif qtype == "judge":
        if db.norm_judge(answer) not in ("T", "F"):
            raise ValueError("判断题答案只能是正确或错误")
        if not opts:
            opts = ["正确", "错误"]
    elif qtype == "code":
        code = str(d.get("code", old.get("code", "")) or "")
        if db.count_blanks(code) == 0:
            raise ValueError("代码模板需要 ____ 空位")
        if len([x for x in answer.split("||") if x.strip()]) != db.count_blanks(code):
            raise ValueError("答案段数必须与空位数一致，并用 || 分隔")
    elif qtype == "drag":
        slots = d.get("slots", old.get("slots") or [])
        if not slots:
            try:
                slots = (json.loads(old.get("payload") or "{}").get("slots") or [])
            except Exception:
                slots = []
        if len(opts) < 2 or len(slots) < 2:
            raise ValueError("拖拽题至少需要两个拖拽项和两个槽位")
        pairs, plain = [], []
        for part in re.split(r"[|｜，,;；\n]+", answer):
            part = part.strip()
            m = re.match(r"^(\d+)\s*[:：=]\s*([A-Za-z])$", part)
            if m:
                pairs.append(f"{m.group(1)}:{m.group(2).upper()}")
            elif re.match(r"^[A-Za-z]$", part):
                plain.append(part.upper())
        if not pairs and plain:
            pairs = [f"{i + 1}:{x}" for i, x in enumerate(plain)]
        if len(pairs) != len(slots):
            raise ValueError("拖拽题答案段数必须与槽位数一致")
        old_layout = "grid"
        try:
            old_layout = (json.loads(old.get("payload") or "{}").get("layout")
                          or "grid")
        except (TypeError, ValueError, AttributeError):
            pass
        layout = str(d.get("layout") or old_layout or "grid").lower()
        if layout not in ("grid", "horizontal"):
            layout = "grid"
        answer = "|".join(pairs)
    return {
        "qtype": qtype, "stem": stem, "options": opts, "answer": answer,
        "score": score, "category": str(d.get("category", old.get("category", "")) or ""),
        "difficulty": str(d.get("difficulty", old.get("difficulty", "")) or ""),
        "source": str(d.get("source", old.get("source", "")) or ""),
        "language": str(d.get("language", old.get("language", "python")) or "python")
                    if qtype == "code" else "",
        "code": str(d.get("code", old.get("code", "")) or "") if qtype == "code" else "",
        "stdin_data": str(d.get("stdin_data", old.get("stdin_data", "")) or "")
                      if qtype == "code" else "",
        "expected_output": str(d.get("expected_output", old.get("expected_output", "")) or "")
                           if qtype == "code" else "",
        "payload": json.dumps({"slots": slots, "layout": layout}, ensure_ascii=False)
                   if qtype == "drag" else "",
    }


def _validate_rules(checks):
    if not isinstance(checks, list):
        raise ValueError("评分规则必须是数组")
    clean = []
    for index, rule in enumerate(checks, 1):
        if not isinstance(rule, dict):
            raise ValueError(f"第 {index} 个评分点必须是对象")
        kind = str(rule.get("type") or "").strip()
        if not kind:
            raise ValueError(f"第 {index} 个评分点缺少 type")
        try:
            score = float(rule.get("score", 0))
        except (TypeError, ValueError):
            raise ValueError(f"第 {index} 个评分点分值格式不对")
        if not 0 <= score <= 1000:
            raise ValueError(f"第 {index} 个评分点分值必须在 0～1000 之间")
        item = dict(rule)
        item["score"] = round(score, 2)
        clean.append(item)
    return clean


def _validate_material_names(materials):
    if not isinstance(materials, list):
        raise ValueError("素材必须是数组")
    out = []
    for name in materials:
        safe = _safe_name(name, "")
        if not safe or safe != str(name):
            raise ValueError("素材文件名不能包含路径分隔符")
        if safe not in out:
            out.append(safe)
    return out


def _practice_material_names(eid, pids=None):
    if pids is None:
        pids = [task["id"] for task in db.exam_tasks(eid)]
    names = set()
    for pid in pids:
        task = db.get_practice(pid)
        if task:
            names.update(task.get("materials") or [])
    return names


def _save_exam_config(eid, data, creating=False):
    ex = db.get_exam(eid)
    if not ex:
        raise ValueError("考试不存在")
    if ex["status"] in ("running", "paused", "stopped"):
        raise ValueError("考试进行中或已结束，配置已锁定")
    title = str(data.get("title", ex["title"]) or "").strip()
    if not title:
        raise ValueError("考试名称不能为空")
    try:
        duration = int(data.get("duration", ex["duration"]))
    except (TypeError, ValueError):
        raise ValueError("时长格式不对")
    # 理论题和实操题共用一个总时长；旧客户端即使仍传 theory_duration 也忽略。
    theory_duration = duration
    if not 1 <= duration <= 600:
        raise ValueError("时长必须在 1～600 分钟之间")
    types = data.get("question_types")
    if types is None:
        try:
            types = json.loads(ex.get("question_types") or "[]")
        except Exception:
            types = []
    if not isinstance(types, list):
        raise ValueError("题型格式不对")
    allowed_types = {"single", "multi", "judge", "blank", "code", "drag",
                     "practice", "typing", "drawing"}
    types = [x for x in types if x in allowed_types]
    if not types:
        raise ValueError("请至少选择一种考试题型")
    etype = data.get("etype", ex.get("etype", ""))
    if isinstance(etype, list):
        etype = json.dumps(etype, ensure_ascii=False)
    checks = data.get("checks", ex.get("checks") or [])
    if isinstance(checks, str):
        try:
            checks = json.loads(checks)
        except Exception:
            raise ValueError("评分规则格式不对")
    if not isinstance(checks, list):
        raise ValueError("评分规则格式不对")
    checks = _validate_rules(checks)
    materials = data.get("materials", ex.get("materials") or [])
    if isinstance(materials, str):
        try:
            materials = json.loads(materials)
        except Exception:
            raise ValueError("素材格式不对")
    if not isinstance(materials, list):
        raise ValueError("素材格式不对")
    materials = _validate_material_names(materials)
    qids = _clean_id_list(data.get("question_ids")) if "question_ids" in data else None
    pids = _clean_id_list(data.get("practice_ids")) if "practice_ids" in data else None
    if qids is not None and any(not db.get_question(qid) for qid in qids):
        raise ValueError("试卷中包含不存在的题目")
    if pids is not None and any(not db.get_practice(pid) for pid in pids):
        raise ValueError("考试中包含不存在的实操题")
    used_materials = _practice_material_names(eid, pids)
    materials = [name for name in materials if name not in used_materials]
    db.update_exam(eid, {
        "title": title, "etype": str(etype or ""), "duration": duration,
        "theory_duration": theory_duration,
        "requirement": str(data.get("requirement", ex.get("requirement", "")) or ""),
        "materials": materials, "save_as": "",
        "checks": checks, "require_complete": int(bool(data.get("require_complete", ex.get("require_complete")))),
        "passwordless": int(bool(data.get("passwordless", ex.get("passwordless")))),
        "finish_note": str(data.get("finish_note", ex.get("finish_note", "")) or ""),
        "klass": str(data.get("klass", ex.get("klass", "")) or "").strip(),
    })
    db.set_exam_klass(eid, str(data.get("klass", ex.get("klass", "")) or "").strip())
    db.set_question_types(eid, types)
    if qids is not None:
        db.set_exam_questions(eid, qids)
    if pids is not None:
        c = db.conn()
        c.execute("DELETE FROM exam_practice WHERE exam_id=?", (eid,))
        c.commit()
        c.close()
        for pid in pids:
            db.add_task_to_exam(eid, pid)
        db._sync_exam_legacy(eid)
        # 任务自身 save_as 保留在 practice_questions；考试级字段不再保存教师输入。
        db.update_exam(eid, {"save_as": ""})
    if "roster" in data:
        db.set_roster(eid, _roster_rows(data.get("roster")))
    if "typing_config" in data and isinstance(data.get("typing_config"), dict):
        db.set_typing_config(eid, data["typing_config"])
    if "drawing_config" in data and isinstance(data.get("drawing_config"), dict):
        db.set_drawing_config(eid, data["drawing_config"])
    db.invalidate()
    return db.get_exam(eid)


@app.route("/admin_api/dashboard")
def admin_api_dashboard():
    if not _admin_ok():
        return _admin_fail("未登录", 401)
    return jsonify({
        "ok": True, "exams": db.list_exams(), "questions": db.list_questions(),
        "practice": db.list_practice(), "students": db.list_students(),
        "materials": sorted(f for f in os.listdir(MATERIAL_DIR)
                            if os.path.isfile(os.path.join(MATERIAL_DIR, f)))
                    if os.path.isdir(MATERIAL_DIR) else [],
        "qimgs": _qimg_list(), "practice_filters": db.practice_filters(),
        "typing_passages": _load_typing_passages(),
        "typing_passages_en": _load_typing_passages("小学英语打字范文.json"),
    })


@app.route("/admin_api/exams", methods=["POST"])
def admin_api_exam_create():
    if not _admin_ok():
        return _admin_fail("未登录", 401)
    data = request.get_json(silent=True) or {}
    if not str(data.get("title") or "").strip():
        return _admin_fail("考试名称不能为空")
    etype = data.get("etype", "")
    if isinstance(etype, list):
        etype = json.dumps(etype, ensure_ascii=False)
    try:
        duration = int(data.get("duration", 60))
    except (TypeError, ValueError):
        return _admin_fail("时长格式不对")
    if not 1 <= duration <= 600:
        return _admin_fail("时长必须在 1～600 分钟之间")
    eid = db.create_exam({
        "title": str(data["title"]).strip(),
        "etype": etype,
        "duration": duration,
        "theory_duration": duration,
        "requirement": data.get("requirement", ""),
        "materials": data.get("materials") or [],
        "save_as": "",
        "checks": data.get("checks") or [],
        "require_complete": int(bool(data.get("require_complete"))),
        "klass": data.get("klass", ""),
    })
    try:
        _save_exam_config(eid, data, creating=True)
    except ValueError as exc:
        db.delete_exam(eid)
        return _admin_fail(str(exc))
    except Exception:
        db.delete_exam(eid)
        app.logger.exception("创建考试失败")
        return _admin_fail("创建考试失败，请查看服务器日志", 500)
    return jsonify({"ok": True, "exam": db.get_exam(eid)})


@app.route("/admin_api/exam/<int:eid>", methods=["GET", "PUT", "DELETE"])
def admin_api_exam_detail(eid):
    if not _admin_ok():
        return _admin_fail("未登录", 401)
    ex, error = _admin_exam_or_404(eid)
    if error:
        return error
    if request.method == "GET":
        return jsonify({
            "ok": True, "exam": ex,
            "question_ids": db.exam_question_ids(eid),
            "practice_ids": [x["id"] for x in db.exam_tasks(eid)],
            "roster": db.roster_text_of(eid),
            "typing_config": _typing_config_of(ex),
            "drawing_config": _drawing_payload(ex),
            "stats": db.stats(eid),
        })
    if request.method == "DELETE":
        if ex["status"] in ("running", "paused", "stopped"):
            return _admin_fail("进行中或已结束的考试不能删除")
        db.delete_exam(eid)
        return jsonify({"ok": True})
    try:
        ex = _save_exam_config(eid, request.get_json(silent=True) or {})
    except ValueError as e:
        return _admin_fail(str(e))
    return jsonify({"ok": True, "exam": ex})


def _copy_exam_config(eid):
    """复制考试配置为新未开始考试；网页与桌面 API 共用同一套逻辑。"""
    ex = db.get_exam(eid)
    if not ex:
        raise ValueError("考试不存在")
    try:
        materials = json.loads(ex.get("materials") or "[]")
    except (TypeError, ValueError):
        materials = []
    try:
        checks = json.loads(ex.get("checks") or "[]")
    except (TypeError, ValueError):
        checks = []
    if not isinstance(materials, list):
        materials = []
    if not isinstance(checks, list):
        checks = []
    new_id = db.create_exam({
        "title": f"{ex['title']}-副本",
        "etype": ex.get("etype") or "",
        "duration": ex.get("duration") or 60,
        "theory_duration": ex.get("duration") or 60,
        "requirement": ex.get("requirement") or "",
        "materials": materials,
        "save_as": "",
        "checks": checks,
        "require_complete": bool(ex.get("require_complete")),
        "passwordless": bool(ex.get("passwordless")),
        "klass": ex.get("klass") or "",
    })
    # create_exam 的旧 INSERT 未包含 finish_note，复制时显式补齐。
    db.update_exam(new_id, {"finish_note": ex.get("finish_note") or ""})
    db.set_question_types(new_id, _selected_types(ex))
    db.set_exam_questions(new_id, db.exam_question_ids(eid))
    for task in db.exam_tasks(eid):
        db.add_task_to_exam(new_id, task["id"])
    db.set_roster(new_id, db.roster_text_of(eid))
    db.set_typing_config(new_id, _typing_config_of(ex))
    # 用表单配置而非下发配置，关闭画图题型时也能保留老师以前填的内容。
    db.set_drawing_config(new_id, _drawing_form_cfg(ex))
    db.invalidate()
    return new_id


@app.route("/admin_api/exam/<int:eid>/copy", methods=["POST"])
def admin_api_exam_copy(eid):
    if not _admin_ok():
        return _admin_fail("未登录", 401)
    ex, error = _admin_exam_or_404(eid)
    if error:
        return error
    try:
        new_id = _copy_exam_config(eid)
    except ValueError as exc:
        return _admin_fail(str(exc))
    except Exception:
        app.logger.exception("复制考试失败")
        return _admin_fail("复制考试失败，请查看服务器日志", 500)
    return jsonify({"ok": True, "exam": db.get_exam(new_id)})


@app.route("/admin_api/exam/<int:eid>/questions", methods=["POST"])
def admin_api_exam_questions(eid):
    if not _admin_ok():
        return _admin_fail("未登录", 401)
    ex, error = _admin_exam_or_404(eid)
    if error:
        return error
    if ex["status"] in ("running", "paused", "stopped"):
        return _admin_fail("考试进行中或已结束，试卷已锁定")
    qids = _clean_id_list((request.get_json(silent=True) or {}).get("question_ids"))
    if any(not db.get_question(qid) for qid in qids):
        return _admin_fail("包含不存在的题目")
    db.set_exam_questions(eid, qids)
    db.invalidate()
    return jsonify({"ok": True, "question_ids": qids})


@app.route("/admin_api/exam/<int:eid>/practice", methods=["POST"])
def admin_api_exam_practice(eid):
    if not _admin_ok():
        return _admin_fail("未登录", 401)
    ex, error = _admin_exam_or_404(eid)
    if error:
        return error
    if ex["status"] in ("running", "paused", "stopped"):
        return _admin_fail("考试进行中或已结束，实操题已锁定")
    pids = _clean_id_list((request.get_json(silent=True) or {}).get("practice_ids"))
    if any(not db.get_practice(pid) for pid in pids):
        return _admin_fail("包含不存在的实操题")
    c = db.conn()
    c.execute("DELETE FROM exam_practice WHERE exam_id=?", (eid,))
    c.commit()
    c.close()
    for pid in pids:
        db.add_task_to_exam(eid, pid)
    db._sync_exam_legacy(eid)
    return jsonify({"ok": True, "practice_ids": pids})


@app.route("/admin_api/exam/<int:eid>/roster", methods=["PUT"])
def admin_api_exam_roster(eid):
    if not _admin_ok():
        return _admin_fail("未登录", 401)
    ex, error = _admin_exam_or_404(eid)
    if error:
        return error
    if ex["status"] == "stopped":
        return _admin_fail("已结束考试为只读，名单已锁定")
    data = request.get_json(silent=True) or {}
    rows = _roster_rows(data.get("text", ""))
    if data.get("mode") == "append":
        have = {(r["name"], r["seat"], r.get("ip") or "")
                for r in db.roster_of(eid)}
        rows = [(r["seat"], r["name"], r.get("ip") or "")
                for r in db.roster_of(eid)]
        rows += [(seat, name, ip) for seat, name, ip in _roster_rows(data.get("text", ""))
                 if (name, seat, ip) not in have]
    db.set_roster(eid, rows)
    return jsonify({"ok": True, "count": len(db.roster_of(eid)), "roster": db.roster_text_of(eid)})


@app.route("/admin_api/exam/<int:eid>/materials", methods=["POST"])
def admin_api_exam_material_upload(eid):
    if not _admin_ok():
        return _admin_fail("未登录", 401)
    ex, error = _admin_exam_or_404(eid)
    if error:
        return error
    if ex["status"] in ("running", "paused", "stopped"):
        return _admin_fail("考试进行中或已结束，素材已锁定")
    f = request.files.get("file")
    if not f or not f.filename:
        return _admin_fail("请选择素材文件")
    ext = os.path.splitext(f.filename)[1].lower()
    if ext in (".docx", ".xlsx", ".pptx"):
        if not _valid_office_upload(f):
            return _admin_fail("Office 素材文件无效")
    elif ext in (".png", ".jpg", ".jpeg"):
        if not _valid_image_upload(f):
            return _admin_fail("图片素材文件无效")
    elif ext not in (".pdf", ".txt", ".csv"):
        return _admin_fail("不支持的素材类型")
    safe = _safe_name(f.filename, "material" + ext)
    if safe in _practice_material_names(eid):
        return _admin_fail("该素材已属于本场实操题，无需重复添加到考试素材")
    dest = os.path.join(MATERIAL_DIR, safe)
    f.save(dest)
    files = json.loads(ex.get("materials") or "[]")
    if safe not in files:
        files.append(safe)
        db.update_exam(eid, {"materials": files})
    return jsonify({"ok": True, "materials": files})


@app.route("/admin_api/exam/<int:eid>/materials/<path:name>", methods=["DELETE"])
def admin_api_exam_material_delete(eid, name):
    if not _admin_ok():
        return _admin_fail("未登录", 401)
    ex, error = _admin_exam_or_404(eid)
    if error:
        return error
    if ex["status"] in ("running", "paused", "stopped"):
        return _admin_fail("考试进行中或已结束，素材已锁定")
    safe = os.path.basename(name)
    files = [x for x in json.loads(ex.get("materials") or "[]") if x != safe]
    db.update_exam(eid, {"materials": files})
    path = os.path.join(MATERIAL_DIR, safe)
    if os.path.isfile(path) and not db.material_used_by_others(safe, exclude_exam=eid):
        try:
            os.remove(path)
        except OSError:
            pass
    return jsonify({"ok": True, "materials": files})


@app.route("/admin_api/exam/<int:eid>/start", methods=["POST"])
def admin_api_exam_start(eid):
    if not _admin_ok():
        return _admin_fail("未登录", 401)
    ex, error = _admin_exam_or_404(eid)
    if error:
        return error
    if ex["status"] == "stopped":
        return _admin_fail("已结束考试只能查看和导出，请复制后开启新考试")
    if ex["status"] in ("running", "paused"):
        return _admin_fail("考试已经在进行中")
    other = db.active_exam()
    if other and other["id"] != eid:
        return _admin_fail("请先结束其他考试")
    ok, err, _warn = _exam_ready(eid)
    if not ok:
        return _admin_fail(err)
    klass = str((request.get_json(silent=True) or {}).get("klass") or "").strip()
    if not klass:
        return _admin_fail("请填写班级")
    db.set_exam_klass(eid, klass)
    db.set_exam_status(eid, "running")
    db.set_phase(eid, "theory")
    db.bind_exam(eid)
    db.push_command("all", "start_exam", {"exam_id": eid, "phase": "theory"})
    return jsonify({"ok": True, "exam": db.get_exam(eid)})


@app.route("/admin_api/exam/<int:eid>/pause", methods=["POST"])
def admin_api_exam_pause(eid):
    if not _admin_ok():
        return _admin_fail("未登录", 401)
    ex, error = _admin_exam_or_404(eid)
    if error:
        return error
    if ex["status"] != "running":
        return _admin_fail("只有进行中的考试可以暂停")
    db.set_exam_status(eid, "paused")
    db.push_command("all", "pause", {"exam_id": eid})
    return jsonify({"ok": True, "exam": db.get_exam(eid)})


@app.route("/admin_api/exam/<int:eid>/resume", methods=["POST"])
def admin_api_exam_resume(eid):
    if not _admin_ok():
        return _admin_fail("未登录", 401)
    ex, error = _admin_exam_or_404(eid)
    if error:
        return error
    if ex["status"] != "paused":
        return _admin_fail("考试当前未暂停")
    db.set_exam_status(eid, "running")
    db.push_command("all", "resume", {"exam_id": eid})
    return jsonify({"ok": True, "exam": db.get_exam(eid)})


@app.route("/admin_api/exam/<int:eid>/phase", methods=["POST"])
def admin_api_exam_phase(eid):
    if not _admin_ok():
        return _admin_fail("未登录", 401)
    ex, error = _admin_exam_or_404(eid)
    if error:
        return error
    phase = (request.get_json(silent=True) or {}).get("phase")
    if phase not in ("theory", "practice"):
        return _admin_fail("未知阶段")
    if ex["status"] not in ("running", "paused"):
        return _admin_fail("考试未在进行")
    if phase == "practice" and not ({"practice", "typing"} & set(_selected_types(ex))):
        return _admin_fail("本场没有实操或打字题")
    db.set_phase(eid, phase)
    payload = {"exam_id": eid, "phase": phase}
    if phase == "practice":
        payload.update(etype=ex["etype"], open_wps=True)
    db.push_command("all", "phase", payload)
    return jsonify({"ok": True, "exam": db.get_exam(eid)})


@app.route("/admin_api/exam/<int:eid>/stop", methods=["POST"])
def admin_api_exam_stop(eid):
    if not _admin_ok():
        return _admin_fail("未登录", 401)
    ex, error = _admin_exam_or_404(eid)
    if error:
        return error
    if ex["status"] not in ("running", "paused"):
        return _admin_fail("考试未在进行")
    clear_login = bool((request.get_json(silent=True) or {}).get("clear_login"))
    db.snapshot_exam_students(eid)
    db.set_exam_status(eid, "stopped")
    db.set_phase(eid, "ended")
    db.push_command("all", "force_exit", {"exam_id": eid, "clear_login": clear_login})
    db.push_command("all", "stop", {"exam_id": eid})
    return jsonify({"ok": True, "exam": db.get_exam(eid)})


@app.route("/admin_api/exam/<int:eid>/scores")
def admin_api_exam_scores(eid):
    if not _admin_ok():
        return _admin_fail("未登录", 401)
    ex, error = _admin_exam_or_404(eid)
    if error:
        return error
    return jsonify({"ok": True, "exam": ex, "rows": db.full_report(eid),
                    "summary": db.class_summary(eid), "stats": db.stats(eid),
                    "tasks": db.exam_tasks(eid)})


@app.route("/admin_api/exam/<int:eid>/stats")
def admin_api_exam_stats(eid):
    if not _admin_ok():
        return _admin_fail("未登录", 401)
    ex, error = _admin_exam_or_404(eid)
    if error:
        return error
    payload = _stats_payload(eid)
    payload.update({"exam": ex, "rows": db.full_report(eid)})
    return jsonify(payload)


@app.route("/admin_api/exam/<int:eid>/export")
def admin_api_exam_export(eid):
    if not _admin_ok():
        return _admin_fail("未登录", 401)
    ex, error = _admin_exam_or_404(eid)
    if error:
        return error
    fmt = request.args.get("fmt", "json")
    if fmt == "csv":
        return scores_csv(eid)
    if fmt == "xlsx":
        return scores_xlsx(eid)
    if fmt == "zip":
        return scores_zip(eid)
    response = _exam_export_response(eid)
    if response is None:
        return _admin_fail("考试不存在", 404)
    return response


def _batch_exams_xlsx_response(ids):
    """把多场考试的成绩合并为一个 xlsx，每场考试一个 sheet。"""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    exams = [(eid, db.get_exam(eid)) for eid in ids]
    exams = [(eid, exam) for eid, exam in exams if exam]
    if not exams:
        return None
    workbook = Workbook()
    workbook.remove(workbook.active)
    used_names = set()
    for eid, exam in exams:
        base_name = re.sub(r"[\\/*?:\[\]\x00-\x1f]", "_",
                           exam.get("title") or f"考试{eid}").strip().strip("'")[:31]
        sheet_name = base_name or f"考试{eid}"
        suffix = 2
        while sheet_name.casefold() in used_names:
            tail = f"-{suffix}"
            sheet_name = base_name[:31 - len(tail)] + tail
            suffix += 1
        used_names.add(sheet_name.casefold())
        sheet = workbook.create_sheet(sheet_name)
        sheet.append(["座位号", "姓名", "机器标识", "IP", "理论题", "理论满分",
                      "实操题", "实操满分", "总分", "已交卷", "答卷文件"])
        for row in db.full_report(eid):
            sheet.append([
                row.get("seat") or "", row.get("name") or "", row.get("hostname") or "",
                row.get("ip") or "", row.get("theory"), row.get("theory_full"),
                row.get("practice") if row.get("practice") is not None else "",
                row.get("practice_full"), row.get("total"),
                "是" if row.get("submitted") else "否", row.get("filename") or "",
            ])
        for cell in sheet[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="27324A")
            cell.alignment = Alignment(horizontal="center", vertical="center")
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
        sheet.sheet_view.showGridLines = False
        for index, width in enumerate((10, 14, 20, 14, 9, 9, 9, 9, 9, 9, 34), 1):
            sheet.column_dimensions[get_column_letter(index)].width = width
    output = io.BytesIO()
    workbook.save(output)
    output.seek(0)
    return send_file(output, as_attachment=True, download_name="多场考试成绩.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.route("/admin_api/exams/export", methods=["POST"])
def admin_api_exams_export():
    if not _admin_ok():
        return _admin_fail("未登录", 401)
    ids = _clean_id_list((request.get_json(silent=True) or {}).get("ids"))
    if not ids:
        return _admin_fail("请先选择要导出的考试")
    response = _batch_exams_xlsx_response(ids)
    if response is None:
        return _admin_fail("选择的考试不存在", 404)
    return response


@app.route("/admin_api/exam/<int:eid>/regrade", methods=["POST"])
def admin_api_exam_regrade(eid):
    if not _admin_ok():
        return _admin_fail("未登录", 401)
    ex, error = _admin_exam_or_404(eid)
    if error:
        return error
    if ex["status"] == "stopped":
        return _admin_fail("已结束考试只能查看和导出")
    n = 0
    for s in db.list_submissions(eid):
        if s["saved_path"] and os.path.isfile(s["saved_path"]):
            enqueue_grade(s["id"], s["saved_path"], db.checks_for_submission(ex, s), s["hostname"])
            n += 1
    return jsonify({"ok": True, "count": n})


@app.route("/admin_api/exam/<int:eid>/rejudge-code", methods=["POST"])
def admin_api_exam_rejudge(eid):
    if not _admin_ok():
        return _admin_fail("未登录", 401)
    _ex, error = _admin_exam_or_404(eid)
    if error:
        return error
    if _ex["status"] == "stopped":
        return _admin_fail("已结束考试只能查看和导出")
    n, ok = db.rejudge_code_answers(eid)
    return jsonify({"ok": True, "count": n, "correct": ok})


@app.route("/admin_api/questions", methods=["GET", "POST"])
def admin_api_questions():
    if not _admin_ok():
        return _admin_fail("未登录", 401)
    if request.method == "GET":
        return jsonify({"ok": True, "questions": db.list_questions(
            keyword=request.args.get("kw") or None,
            qtype=request.args.get("qtype") or None,
            category=request.args.get("category") or None)})
    try:
        qid = db.add_question(_question_payload(request.get_json(silent=True) or {}))
    except ValueError as e:
        return _admin_fail(str(e))
    return jsonify({"ok": True, "question": db.get_question(qid)})


@app.route("/admin_api/question/<int:qid>", methods=["GET", "PUT", "DELETE"])
def admin_api_question(qid):
    if not _admin_ok():
        return _admin_fail("未登录", 401)
    q = db.get_question(qid)
    if not q:
        return _admin_fail("题目不存在", 404)
    if request.method == "GET":
        return jsonify({"ok": True, "question": q})
    if request.method == "DELETE":
        db.delete_question(qid)
        return jsonify({"ok": True})
    try:
        db.update_question(qid, _question_payload(request.get_json(silent=True) or {}, q))
    except ValueError as e:
        return _admin_fail(str(e))
    return jsonify({"ok": True, "question": db.get_question(qid)})


@app.route("/admin_api/questions/import", methods=["POST"])
def admin_api_questions_import():
    if not _admin_ok():
        return _admin_fail("未登录", 401)
    data = request.get_json(silent=True) or {}
    items = data.get("items")
    if not isinstance(items, list):
        return _admin_fail("items 必须是题目数组")
    clean = []
    for item in items:
        try:
            clean.append(_question_payload(item))
        except ValueError:
            continue
    ok, skip = db.import_questions(clean)
    return jsonify({"ok": True, "added": ok, "skipped": skip})


@app.route("/admin_api/questions/tidy", methods=["POST"])
def admin_api_questions_tidy():
    if not _admin_ok():
        return _admin_fail("未登录", 401)
    n_d, n_s = db.tidy_question_meta()
    return jsonify({"ok": True, "difficulty": n_d, "source": n_s})


@app.route("/admin_api/practice", methods=["GET", "POST"])
def admin_api_practice():
    if not _admin_ok():
        return _admin_fail("未登录", 401)
    if request.method == "GET":
        return jsonify({"ok": True, "practice": db.list_practice()})
    data = request.get_json(silent=True) or {}
    name = str(data.get("name") or "").strip()
    etype = str(data.get("etype") or "").strip()
    checks = data.get("checks") or []
    if not name or etype not in ("word", "excel", "ppt"):
        return _admin_fail("请填写题目名称和有效的 Office 类型")
    if not isinstance(checks, list):
        return _admin_fail("评分规则格式不对")
    try:
        checks = _validate_rules(checks)
        materials = _validate_material_names(data.get("materials") or [])
    except ValueError as e:
        return _admin_fail(str(e))
    pid, is_new = db.add_practice({
        "name": name, "etype": etype, "category": data.get("category", ""),
        "level": data.get("level", ""), "materials": materials,
        "requirement": data.get("requirement", ""), "save_as": data.get("save_as", ""),
        "checks": checks,
    })
    return jsonify({"ok": True, "practice": db.get_practice(pid), "is_new": is_new})


@app.route("/admin_api/practice/<int:pid>", methods=["GET", "PUT", "DELETE"])
def admin_api_practice_detail(pid):
    if not _admin_ok():
        return _admin_fail("未登录", 401)
    task = db.get_practice(pid)
    if not task:
        return _admin_fail("实操题不存在", 404)
    if request.method == "GET":
        return jsonify({"ok": True, "practice": task})
    if request.method == "DELETE":
        db.delete_practice(pid)
        return jsonify({"ok": True})
    data = request.get_json(silent=True) or {}
    if not str(data.get("name") or "").strip() or data.get("etype") not in ("word", "excel", "ppt"):
        return _admin_fail("请填写题目名称和有效的 Office 类型")
    if not isinstance(data.get("checks") or [], list):
        return _admin_fail("评分规则格式不对")
    try:
        item = dict(task)
        item.update(data)
        item["checks"] = _validate_rules(item.get("checks") or [])
        item["materials"] = _validate_material_names(item.get("materials") or [])
    except ValueError as e:
        return _admin_fail(str(e))
    db.update_practice(pid, item)
    return jsonify({"ok": True, "practice": db.get_practice(pid)})


@app.route("/admin_api/students/reset-token", methods=["POST"])
def admin_api_student_reset_token():
    if not _admin_ok():
        return _admin_fail("未登录", 401)
    host = str((request.get_json(silent=True) or {}).get("hostname") or "").strip()
    if not db.get_student(host):
        return _admin_fail("学生机不存在", 404)
    db.reset_student_token(host)
    return jsonify({"ok": True})


# ==================== 学生端 API ====================

@app.route("/api/ping")
def api_ping():
    """
    服务探活 —— 学生端自动发现时用
    不含任何敏感信息，只回一句「我是考试服务器」
    """
    return jsonify({
        "ok": True,
        "service": "exam",
        "name": SERVER_NAME,
        "port": HTTP_PORT,
        "ts": int(time.time()),
    })


def _student_exam():
    """
    学生端眼里的"当前考试"：
      进行中 → 用 active_exam()
      已结束 → 用最近一场 stopped 的（否则学生看不到"考试已结束"）
    """
    return db.active_exam() or db.last_stopped_exam()


def _exam_phase(ex):
    """
    统一阶段口径：theory / practice / paused / ended / idle
    以前学生端拿到的是 running，客户端拿它跟 theory 比，
    一比就不相等，于是把正在答题的学生踢回主菜单 —— 卡顿+跳页的元凶之一。
    """
    if not ex:
        return "idle"
    if ex["status"] == "stopped":
        return "ended"
    if ex["status"] == "paused":
        return "paused"
    return ex.get("phase") or "theory"


def _system_save_as(etype):
    """按实操题型生成学生端文件名，不再要求老师填写考试级文件名。"""
    ext = EXT_OF.get(str(etype or "").lower(), "")
    return f"座位号_姓名{ext}" if ext else ""


def _exam_save_as(ex, tasks=None):
    """
    学生端看到的实操文件名。
    纯理论考试返回空；有多道实操题时保留任务自身文件名，空值才用系统名。
    """
    if not ex:
        return ""
    if tasks is None:
        tasks = db.exam_tasks(ex["id"])
    if tasks:
        for task in tasks:
            if task.get("save_as"):
                return str(task["save_as"])
        return _system_save_as(tasks[0].get("etype"))
    if "practice" not in _selected_types(ex):
        return ""
    try:
        practice_types = json.loads(ex.get("etype") or "[]")
        if not isinstance(practice_types, list):
            practice_types = [practice_types]
    except (TypeError, ValueError, json.JSONDecodeError):
        practice_types = [ex.get("etype") or ""]
    etype = next((str(t).lower() for t in practice_types if str(t).lower() in EXT_OF),
                 "word")
    return _system_save_as(etype)


def _tasks_for(exam_id, host, participant_id=None):
    """
    这场考试的实操题清单（给某个学生看，带"这道交了没"）。
    一场考试可以装多道操作题，每道有自己的素材、要求、评分规则。
    """
    out = []
    for i, t in enumerate(db.exam_tasks(exam_id), 1):
        sub = db.submission_of_task(exam_id, host, t["id"], participant_id)
        out.append({
            "task_id": t["id"], "no": i, "name": t["name"],
            "etype": t["etype"], "ext": EXT_OF.get(t["etype"], ".docx"),
            "requirement": t["requirement"], "materials": t["materials"],
            "save_as": t["save_as"] or _system_save_as(t["etype"]), "submitted": bool(sub),
        })
    return out


def _progress(ex, host, participant_id=None):
    """
    学生进度。心跳每 3 秒一次，这里必须只走轻量查询：
      · 题目数用 COUNT，不查整份试卷
      · 是否交卷用 EXISTS，不拉全班答卷
    """
    if not ex:
        return 0, 0, False
    total = db.count_exam_questions(ex["id"])
    _, answered = db.theory_score(ex["id"], host, participant_id)
    submitted = db.has_submission(ex["id"], host, participant_id)
    return answered, total, submitted


def _write_participant(eid, host, status=None):
    """写接口再次校验本场名单，避免登录后名单变更或空姓名绕过。"""
    stu = db.get_student(host) or {}
    name = (stu.get("name") or "").strip()
    if not name:
        return 0, "请先填写学生姓名"
    ok, msg = db.roster_check(eid, name, stu.get("seat") or "")
    if not ok:
        return 0, msg
    return _mark_participant(eid, host, status), ""


def _mark_participant(eid, host, status=None):
    """有作答证据了就补一笔参与名单，并返回本场参与者 ID。"""
    try:
        stu = db.get_student(host) or {}
        if not (stu.get("name") or "").strip():
            return 0
        pid = db.add_participant(eid, host, stu.get("name") or "",
                                 stu.get("seat") or "",
                                 request.remote_addr or stu.get("ip") or "")
        db.set_current_participant(host, eid, pid)
        if status:
            db.touch_participant(eid, host, status)
        return pid
    except Exception:
        return 0


@app.route("/api/hello", methods=["POST"])
def api_hello():
    """学生端注册 + 拉取当前状态"""
    d = request.get_json(force=True) or {}
    host = _host(d)
    if not host:
        return jsonify({"ok": False, "msg": "缺少 hostname"}), 400

    name = (d.get("name") or "").strip()
    seat = (d.get("seat") or "").strip()
    client_ip = request.remote_addr or d.get("ip", "") or ""
    ex = _student_exam()
    passwordless = bool(ex and ex.get("passwordless"))
    if ex:
        if passwordless:
            matched = db.roster_by_ip(ex["id"], client_ip)
            if matched:
                name = matched.get("name") or name
                seat = matched.get("seat") or seat
            elif not name:
                name = f"IP-{client_ip or host}"
        ok_r, msg_r = db.roster_check(
            ex["id"], name, seat, ip=client_ip, passwordless=passwordless)
        if not ok_r:
            return jsonify({"ok": False, "msg": msg_r}), 403

    ok, msg, issued = db.register_student(
        host, client_ip, d.get("mac", ""),
        name=name, seat=seat, token=_request_token(d))
    if not ok:
        return jsonify({"ok": False, "msg": msg}), 403
    db.heartbeat(host, d.get("status"))

    if not ex:
        return jsonify({"ok": True, "exam": None, "phase": "idle",
                        "server_name": SERVER_NAME,
                        "student": {"name": name, "seat": seat, "hostname": host},
                        "token": issued, "commands": db.fetch_commands(host)})

    pid = 0
    if ex["status"] in ("running", "paused"):
        if name:
            pid = db.add_participant(ex["id"], host, name, seat, client_ip)
            db.touch_participant(ex["id"], host, d.get("status") or "idle")
        db.set_current_participant(host, ex["id"], pid)
    who = {"name": name, "seat": seat, "hostname": host, "ip": client_ip}

    cmds = db.fetch_commands(host)
    qtypes = json.loads(ex.get("question_types") or "[]")
    if not qtypes:
        # 没设置题型时默认全部开启（兼容旧考试）
        has_theory = True
        has_practice = True
    else:
        has_theory = any(t in qtypes for t in THEORY_TYPES)
        has_practice = "practice" in qtypes
    has_typing = "typing" in qtypes
    has_drawing = "drawing" in qtypes

    # 学生进度（轻量查询）
    theory_n, theory_total, _ = _progress(ex, host, pid)
    phase = _exam_phase(ex)

    # 这场考试装了哪些实操题（一场可以装多道）
    tasks = _tasks_for(ex["id"], host, pid)
    has_submitted = (all(t["submitted"] for t in tasks) if tasks
                     else db.has_submission(ex["id"], host, pid))

    materials = json.loads(ex["materials"] or "[]")

    resp = {
        "ok": True,
        "server_name": SERVER_NAME,
        "phase": phase,
        "commands": cmds,
        # 本机当前登录的学生（姓名/座位号，学生端显示用）
        "student": who,
        "participant_id": pid,
        "token": issued,
        "exam": {
            "id": ex["id"],
            "title": ex["title"],
            "etype": ex["etype"],
            # 考试编号 / 班级：学生端用它建目录（ikee_Exam/姓名/编号）并显示
            "code": ex.get("code") or "",
            "klass": ex.get("klass") or "",
            "requirement": ex["requirement"],
            "materials": materials,
            "save_as": _exam_save_as(ex, tasks),
            # 交卷结束语：学生交卷后弹窗里显示（做完/没做完都会显示）
            "finish_note": ex.get("finish_note") or "",
            "has_theory": has_theory,
            "has_practice": has_practice,
            "has_typing": has_typing,
            "has_drawing": has_drawing,
            "theory_total": theory_total,
            "theory_answered": theory_n,
            "practice_submitted": has_submitted,
            "require_complete": bool(ex.get("require_complete")),
            "passwordless": passwordless,
        },
        # 实操题清单（可能多道）。老客户端不认这个字段也没关系。
        "tasks": tasks,
        # 打字模块（小学）配置：轮数 / 每轮秒数 / 打字范文；没勾打字题型为 None
        "typing": _typing_payload(ex, host),
        # 画板创作配置（画题要求）；没勾画板题型为 None
        "drawing": _drawing_payload(ex),
    }
    # 始终返回实操信息（如果有实操）
    if has_practice:
        # etype 可能是 JSON 数组 ["word","excel"] 或单个字符串 "word"
        raw_etype = ex["etype"]
        try:
            practice_types = json.loads(raw_etype)
            if not isinstance(practice_types, list):
                practice_types = [raw_etype]
        except (json.JSONDecodeError, TypeError):
            practice_types = [raw_etype]
        resp["practice"] = {
            "types": practice_types,
            "etype": practice_types[0] if practice_types else "word",
            "ext": EXT_OF.get(practice_types[0], ".docx") if practice_types else ".docx",
            "materials": materials,
            "save_as": _exam_save_as(ex, tasks),
            "requirement": ex["requirement"],
        }
    return jsonify(resp)


@app.route("/api/poll")
def api_poll():
    """轻量心跳，只取状态和命令"""
    host, _stu, err = _student_auth()
    if err:
        return jsonify({"ok": False, "msg": err}), 403
    try:
        progress = json.loads(request.args.get("progress") or "{}")
    except Exception:
        progress = {}
    db.heartbeat(host, request.args.get("status") or None, progress)
    cmds = db.fetch_commands(host)
    ex = _student_exam()
    pid = db.current_participant(host, ex["id"]) if ex else 0
    if ex and ex["status"] in ("running", "paused"):
        db.touch_participant(ex["id"], host)
    phase = _exam_phase(ex)

    # 学生进度（轻量查询：COUNT + EXISTS，不查整份试卷、不拉全班答卷）
    theory_n, theory_total, has_sub = _progress(ex, host, pid)

    # 老师的实操配置可能刚改过（比如刚装了一道操作题），
    # 这里一并下发，学生端就能立刻跟上，不用重连
    tasks = _tasks_for(ex["id"], host, pid) if ex else []
    has_submitted = all(t["submitted"] for t in tasks) if tasks else has_sub

    return jsonify({
        "ok": True, "phase": phase, "commands": cmds,
        "theory_answered": theory_n, "theory_total": theory_total,
        "practice_submitted": has_submitted,
        "remaining": db.phase_remaining(ex["id"]) if ex else None,
        "tasks": tasks,
        # 打字模块（小学）配置随心跳下发：老师改了下一场考试的配置也能跟上
        "typing": _typing_payload(ex, host),
        "has_drawing": bool(ex and "drawing" in _selected_types(ex)),
        "drawing": _drawing_payload(ex),
        "requirement": ex["requirement"] if ex else "",
        "finish_note": (ex.get("finish_note") or "") if ex else "",
        "materials": json.loads(ex["materials"] or "[]") if ex else [],
        "etype": ex["etype"] if ex else "",
        "save_as": _exam_save_as(ex, tasks) if ex else "",
    })


@app.route("/api/paper")
def api_paper():
    """取理论题试卷（不含答案）"""
    host, _stu, err = _student_auth()
    if err:
        return jsonify({"ok": False, "msg": err}), 403
    ex = db.active_exam()
    if not ex:
        return jsonify({"ok": False, "msg": "当前没有考试"})
    if ex["status"] not in ("running", "paused"):
        return jsonify({"ok": False, "msg": "考试已结束"})

    eid = ex["id"]

    # 试卷内容只跟考试有关，缓存起来。
    # 50 人一起进理论题时不用各查一遍整份试卷再各序列化一次 JSON。
    def _build_paper():
        qs = db.get_exam_questions(eid)
        items = []
        for i, q in enumerate(qs, 1):
            item = {
                "id": q["id"], "no": i, "qtype": q["qtype"],
                "stem": q["stem"], "options": q["options"], "score": q["score"],
            }
            # 程序填空：把代码模板、语言、运行输入一起下发
            # （答案不下发，学生抓包也看不到）
            if q["qtype"] == "code":
                item["language"] = q.get("language") or "python"
                item["code"] = q.get("code") or ""
                item["stdin_data"] = q.get("stdin_data") or ""
                item["blanks"] = db.count_blanks(q.get("code") or "")
            # 拖拽题：把槽位下发（拖拽项就是 options；答案不下发）
            elif q["qtype"] == "drag":
                try:
                    drag_payload = json.loads(q.get("payload") or "{}")
                    item["slots"] = drag_payload.get("slots") or []
                    item["layout"] = drag_payload.get("layout") or "grid"
                except (TypeError, ValueError):
                    item["slots"] = []
                    item["layout"] = "grid"
            items.append(item)
        return items

    paper = db.hot_get(f"paper:{eid}", _build_paper)
    pid = db.current_participant(host, eid)
    answered = db.theory_answers_of(eid, host, pid)
    return jsonify({
        "ok": True,
        "exam_id": eid,
        "title": ex["title"],
        "remaining": db.phase_remaining(eid),
        "full": db.exam_theory_full(eid),
        "questions": paper,
        "answered": answered,
    })


@app.route("/api/theory/submit", methods=["POST"])
def api_theory_submit():
    """提交理论题答案（可多次调用，逐题保存）"""
    d = request.get_json(force=True) or {}
    host, _stu, err = _student_auth(d)
    if err:
        return jsonify({"ok": False, "msg": err}), 403
    eid = _int_field(d.get("exam_id"))
    answers = d.get("answers") or {}
    if not isinstance(answers, dict):
        return jsonify({"ok": False, "msg": "answers 格式不对"}), 400
    if not eid:
        return jsonify({"ok": False, "msg": "参数不全"}), 400

    ex = db.get_exam(int(eid))
    if not ex or not _student_write_allowed(ex):
        return jsonify({"ok": False, "msg": "考试未在进行或最终收卷时间已过"}), 403

    # 学生可以在理论/实操导航间来回检查，只要考试仍在进行就允许保存理论答案。
    # 已结束考试仍拒绝写入，成绩单保持锁定。
    pid, msg = _write_participant(int(eid), host, "working")
    if not pid:
        return jsonify({"ok": False, "msg": msg}), 403
    db.save_theory_answers(int(eid), host, answers, pid)
    score, n = db.theory_score(int(eid), host, pid)
    return jsonify({"ok": True, "score": score, "answered": n})


@app.route("/api/theory/finish", methods=["POST"])
def api_theory_finish():
    """学生点「交卷并进入实操」"""
    d = request.get_json(force=True) or {}
    host, _stu, err = _student_auth(d)
    if err:
        return jsonify({"ok": False, "msg": err}), 403
    eid = _int_field(d.get("exam_id"))
    ex = db.get_exam(eid)
    if not ex or not _student_write_allowed(ex):
        return jsonify({"ok": False, "msg": "考试未在进行或最终收卷时间已过"}), 403
    if ex["status"] != "stopped" and db.get_phase(eid) in ("practice", "ended"):
        return jsonify({"ok": False, "msg": "当前阶段不能交理论卷"}), 403
    pid, msg = _write_participant(eid, host, "theory_done")
    if not pid:
        return jsonify({"ok": False, "msg": msg}), 403
    db.set_student_status(host, "theory_done", eid)
    score, n = db.theory_score(eid, host, pid)
    db.log(host, "info", f"理论题交卷，得分 {score}，作答 {n} 题")
    return jsonify({"ok": True, "score": score, "answered": n,
                    "msg": "已交卷，等待老师开启实操阶段"})


@app.route("/api/typing/report", methods=["POST"])
def api_typing_report():
    """
    打字模块（小学）进度上报：学生端每 3 秒一次，增量覆盖。
    只存实时数据（速度/正确率），不打分 —— 按老师要求不计分。
    """
    d = request.get_json(force=True) or {}
    host, _stu, err = _student_auth(d)
    if err:
        return jsonify({"ok": False, "msg": err}), 403
    eid = d.get("exam_id")
    reports = d.get("reports") or []
    if not host or not eid:
        return jsonify({"ok": False, "msg": "参数不全"}), 400
    if not isinstance(reports, list):
        return jsonify({"ok": False, "msg": "reports 格式不对"}), 400

    ex = db.get_exam(int(eid))
    if not ex or not _student_write_allowed(ex):
        return jsonify({"ok": False, "msg": "考试未在进行或最终收卷时间已过"}), 403
    if "typing" not in _selected_types(ex):
        return jsonify({"ok": False, "msg": "本场考试没有打字题"}), 403

    pid, msg = _write_participant(int(eid), host, "working")
    if not pid:
        return jsonify({"ok": False, "msg": msg}), 403
    db.save_typing_report(int(eid), host, reports[:8], pid)
    return jsonify({"ok": True, "participant_id": pid})


@app.route("/api/material/<path:name>")
def api_material(name):
    """下载实操素材"""
    _host, _stu, err = _student_auth()
    if err:
        return jsonify({"ok": False, "msg": err}), 403
    # 防路径穿越：素材全部平铺存储，只取文件名部分
    safe = os.path.basename(name)
    p = os.path.join(MATERIAL_DIR, safe)
    if not safe or not os.path.isfile(p):
        return "素材不存在", 404
    return send_file(p, as_attachment=True, download_name=safe)


# ---------------- 理论题选项图片（小学模块：选项可以放图片） ----------------
# 用法：选项文字写成  img:文件名.png 选项文字  —— 学生端会先显示图再显示文字。
QIMG_EXT = (".png", ".gif", ".jpg", ".jpeg")


def _qimg_list():
    try:
        return sorted(f for f in os.listdir(QIMG_DIR)
                      if os.path.isfile(os.path.join(QIMG_DIR, f))
                      and os.path.splitext(f)[1].lower() in QIMG_EXT)
    except OSError:
        return []


@app.route("/qimg/<path:name>")
def qimg_file(name):
    """选项图片（学生端免登录取图；只读文件名部分，防路径穿越）"""
    safe = os.path.basename(name)
    if os.path.splitext(safe)[1].lower() not in QIMG_EXT:
        return "不支持的图片格式", 400
    p = os.path.join(QIMG_DIR, safe)
    if not safe or not os.path.isfile(p):
        return "图片不存在", 404
    return send_file(p)


@app.route("/questions/qimg", methods=["POST"])
def qimg_upload():
    """老师上传选项图片（题库页）"""
    f = request.files.get("file")
    if not f or not f.filename:
        flash("请选择图片文件", "err")
        return redirect(url_for("questions"))
    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in QIMG_EXT:
        flash("只支持 png / gif / jpg 图片", "err")
        return redirect(url_for("questions"))
    safe = "".join(ch for ch in os.path.basename(f.filename)
                   if ch not in r'\/:*?"<>|#?%').replace(" ", "_").strip("._")
    if not safe:
        safe = "qimg" + ext
    f.save(os.path.join(QIMG_DIR, safe))
    flash(f"图片已上传：{safe}，在选项里写  img:{safe} 即可引用")
    return redirect(url_for("questions"))


@app.route("/api/upload", methods=["POST"])
def api_upload():
    """上传实操答卷"""
    # 身份标识和 hello/poll 对齐：优先 client_id（MAC 唯一），否则回退机器名。
    # 以前这里只认 host（机器名），机器名重复时答卷会串到别人头上，
    # 且下面防顶替检查 db.get_student(host) 查不到 → 校验被静默绕过。
    host, _stu, err = _student_auth(request.form)
    if err:
        return jsonify({"ok": False, "msg": err}), 403
    eid = _int_field(request.form.get("exam_id"))
    task_id = _int_field(request.form.get("task_id"))
    f = request.files.get("file")
    if not host or not f or not f.filename:
        return jsonify({"ok": False, "msg": "参数不全"}), 400

    ex = db.get_exam(eid)
    if not ex:
        return jsonify({"ok": False, "msg": "考试不存在"}), 404

    # 状态/阶段校验：未开考或已结束都不能再提交答卷（对齐理论题提交的校验）
    if not _student_write_allowed(ex):
        return jsonify({"ok": False, "msg": "考试未在进行或最终收卷时间已过，不能提交答卷"}), 403
    if ex["status"] != "stopped" and db.get_phase(eid) == "ended":
        return jsonify({"ok": False, "msg": "考试已结束，不能再提交答卷"}), 403

    # 机器令牌已验证，来源 IP 仅记录，不再作为唯一防伪手段。
    pid, msg = _write_participant(eid, host, "working")
    if not pid:
        return jsonify({"ok": False, "msg": msg}), 403
    if not _valid_office_upload(f):
        return jsonify({"ok": False, "msg": "文件不是有效的 Word / Excel / PPT 文档"}), 400
    safe = _safe_name(f.filename, "答卷")

    # 一场考试可能装了多道操作题：这道题用哪份评分规则
    checks_raw = ex["checks"] or "[]"
    if task_id:
        # task_id 必须属于本场考试，否则归档/评分都会串
        if not db.task_in_exam(eid, task_id):
            return jsonify({"ok": False, "msg": "这道操作题不属于本场考试"}), 400
        task = db.get_practice(task_id)
        if task:
            checks_raw = json.dumps(task["checks"], ensure_ascii=False)
            ex = dict(ex, checks=checks_raw)

    # host 来自客户端表单，同样要净化，防止 ../ 写出目录
    safe_host = _safe_name(host, "unknown")
    # 答卷按「班级 / 编号_机器名_文件名」归档：
    #   uploads/ikee_Exam/高一3班/ikeexam-20260924-003_PC01_座位12_张三_第1题.xlsx
    # 老师可以直接在服务器文件夹里按班级翻；没填班级（老流程）落到「未分班」。
    klass = _safe_name(ex.get("klass"), "未分班")
    code = _safe_name(ex.get("code"), f"exam{eid}")
    dest_dir = os.path.join(UPLOAD_DIR, "ikee_Exam", klass)
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, f"{code}_{safe_host}_p{pid}_{safe}")
    f.save(dest)

    sub_id = db.save_submission(host, eid, safe, dest,
                                os.path.getsize(dest), task_id=task_id,
                                participant_id=pid)
    db.set_student_status(host, "submitted", eid)
    db.touch_participant(eid, host, "submitted")
    db.log(host, "info",
           f"提交答卷 {safe}" + (f"（操作题 #{task_id}）" if task_id else ""))

    # 评分交给后台队列：这里立刻返回，50 人同时交卷也不会堵住
    auto = {}
    try:
        checks = json.loads(ex["checks"] or "[]")
        if checks:
            n = enqueue_grade(sub_id, dest, checks, host)
            auto = {"pending": True, "msg": f"已提交，正在评分（队列 {n}）"}
        else:
            auto = {"msg": "本场考试未配置评分规则"}
    except Exception as e:
        db.log(host, "error", f"入队评分失败: {e}")
        auto = {"error": str(e)}

    return jsonify({"ok": True, "filename": safe, "auto": auto})


@app.route("/api/drawing/upload", methods=["POST"])
def api_drawing_upload():
    """
    画图作品上传：一张压缩图（PNG/JPEG），重复上传以最新一份为准。
    kind=draw（画板创作）/ beads（拼豆题），两道小题各交各的。
    一场考试里可能还有实操大文件在传，客户端会把画图放进等待队列排队传。
    图片按「班级 / 编号_机器名_画板.png（或_拼豆）」归档到 uploads/drawing/。
    """
    f = request.files.get("file")
    host, _stu, err = _student_auth(request.form)
    if err:
        return jsonify({"ok": False, "msg": err}), 403
    eid = _int_field(request.form.get("exam_id"))
    kind = (request.form.get("kind") or "draw").strip().lower()
    kind = "beads" if kind == "beads" else "draw"
    if not f or not host or not eid:
        return jsonify({"ok": False, "msg": "参数不全"}), 400
    ex = db.get_exam(eid)
    if not ex:
        return jsonify({"ok": False, "msg": "考试不存在"}), 404
    if "drawing" not in _selected_types(ex):
        return jsonify({"ok": False, "msg": "本场考试没有画图题"}), 403
    if kind not in (_drawing_payload(ex) or {}).get("styles", []):
        tag_no = "拼豆" if kind == "beads" else "画板"
        return jsonify({"ok": False,
                        "msg": f"本场考试没有这道画图小题（{tag_no}）"}), 403
    if not _student_write_allowed(ex):
        return jsonify({"ok": False, "msg": "考试未在进行或最终收卷时间已过，不能提交"}), 403
    if ex["status"] != "stopped" and db.get_phase(eid) == "ended":
        return jsonify({"ok": False, "msg": "考试已结束，不能再提交"}), 403
    pid, msg = _write_participant(eid, host, "working")
    if not pid:
        return jsonify({"ok": False, "msg": msg}), 403
    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in (".png", ".jpg", ".jpeg") or not _valid_image_upload(f):
        return jsonify({"ok": False, "msg": "只支持有效的 PNG / JPG 图片"}), 400

    safe_host = _safe_name(host, "unknown")
    klass = _safe_name(ex.get("klass"), "未分班")
    code = _safe_name(ex.get("code"), f"exam{eid}")
    tag = "拼豆" if kind == "beads" else "画板"
    dest_dir = os.path.join(UPLOAD_DIR, "drawing", klass)
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, f"{code}_{safe_host}_p{pid}_{tag}{ext}")
    f.save(dest)
    size = os.path.getsize(dest)
    db.save_drawing(eid, host, os.path.basename(dest), dest, size, kind=kind,
                    participant_id=pid)
    db.touch_participant(eid, host, "submitted")
    db.log(host, "info", f"提交{tag}作品（{size // 1024} KB）")
    return jsonify({"ok": True, "size": size})


@app.route("/api/upload_empty", methods=["POST"])
def api_upload_empty():
    """
    缺卷提交：老师没设「未做完禁止提交」、学生确认「没做完也交」后，
    没做的操作题在服务端记一笔 0 分（评讲/成绩里显示「缺卷」），
    学生端不会再弹「提交失败让你重做」。
    """
    d = request.get_json(silent=True) or {}
    host, _stu, err = _student_auth(d)
    if err:
        return jsonify({"ok": False, "msg": err}), 403
    eid = _int_field(d.get("exam_id"))
    task_id = _int_field(d.get("task_id"))
    if not eid:
        return jsonify({"ok": False, "msg": "参数不全"}), 400

    ex = db.get_exam(eid)
    if not ex:
        return jsonify({"ok": False, "msg": "考试不存在"}), 404
    if not _student_write_allowed(ex):
        return jsonify({"ok": False, "msg": "考试未在进行或最终收卷时间已过，不能提交答卷"}), 403
    if ex["status"] != "stopped" and db.get_phase(eid) == "ended":
        return jsonify({"ok": False, "msg": "考试已结束，不能再提交答卷"}), 403
    if task_id and not db.task_in_exam(eid, task_id):
        return jsonify({"ok": False, "msg": "这道操作题不属于本场考试"}), 400
    pid, msg = _write_participant(eid, host, "submitted")
    if not pid:
        return jsonify({"ok": False, "msg": msg}), 403
    checks = db.checks_for_submission(ex, {"task_id": task_id})
    full = round(sum(float(c.get("score", 0)) for c in checks), 2)
    sub_id = db.save_submission(host, eid, "（缺卷·未作答）", "", 0,
                                task_id=task_id, participant_id=pid)
    db.update_score(sub_id, 0.0, full,
                    [{"id": "缺卷", "got": 0, "full": full,
                      "msg": "学生确认交卷时未作答，按 0 分处理"}])
    db.set_student_status(host, "submitted", eid)
    db.log(host, "info",
           f"缺卷提交（操作题 #{task_id or 1}，未作答按 0 分）" if task_id
           else "缺卷提交（未作答按 0 分）")
    return jsonify({"ok": True, "auto": {"score": 0, "full": full,
                                         "empty": True}})


@app.route("/api/commands/ack", methods=["POST"])
def api_commands_ack():
    d = request.get_json(silent=True) or {}
    host, _stu, err = _student_auth(d)
    if err:
        return jsonify({"ok": False, "msg": err}), 403
    ids = d.get("ids") or []
    if not isinstance(ids, list):
        return jsonify({"ok": False, "msg": "ids 格式不对"}), 400
    return jsonify({"ok": True, "acked": db.ack_commands(host, ids)})


@app.route("/api/log", methods=["POST"])
def api_log():
    """学生端上报异常（如被杀掉的进程、锁屏被突破）"""
    d = request.get_json(force=True) or {}
    host, _stu, err = _student_auth(d)
    if err:
        return jsonify({"ok": False, "msg": err}), 403
    db.log(host, d.get("level", "info"), str(d.get("msg") or "")[:2000])
    return jsonify({"ok": True})


# ==================== 教师后台 ====================

@app.route("/")
def index():
    ex = db.active_exam()
    students = db.list_students()
    exams = db.list_exams()
    # 标一下每场考试的题型、题数、能不能开始
    qn = db.exam_question_counts()
    pn = db.exam_practice_counts()
    for e in exams:
        e["has_practice"] = "practice" in _selected_types(e)
        e["has_typing"] = "typing" in _selected_types(e)
        e["has_drawing"] = "drawing" in _selected_types(e)
        e["practice_types"] = _practice_types_of(e)
        # 模板里原来直接读 e.theory_n，但 list_exams() 根本没这个字段，
        # 于是每场考试都显示"0 题"（哪怕卷子里有 30 题）
        e["theory_n"] = qn.get(e["id"], 0)
        e["practice_n"] = pn.get(e["id"], 0)
        # 任一题型就能开考：理论 / 实操 / 打字 / 画板 都算「有内容」
        # （打字、画板不需要装题，勾了就是一场考试）
        e["ready"] = bool(e["theory_n"] or e["practice_n"]
                          or e["has_typing"] or e["has_drawing"]
                          or _has_rules(e.get("checks")))
        # 运行中/已暂停的考试：控制台卡片上显示「剩余 总时长」
        if e["status"] == "running":
            left = db.phase_remaining(e["id"])
        elif e["status"] == "paused":
            left = db.phase_remaining(e["id"], include_paused=True)
        else:
            left = None
        e["remaining_text"] = _fmt_mmss(left)
    st = db.stats(ex["id"]) if ex else {"students": len(students), "online": 0, "submitted": 0}
    theory_n = len(db.get_exam_questions(ex["id"])) if ex else 0
    return render_template("index.html", exam=ex, exams=exams, students=students,
                           stats=st, theory_n=theory_n,
                           remaining=db.phase_remaining(ex["id"]) if ex else None,
                           type_name=TYPE_NAME)


@app.route("/exam/<int:eid>/copy", methods=["POST"])
def exam_copy(eid):
    """网页控制台复制考试；沿用桌面 API 的配置复制逻辑。"""
    ex = db.get_exam(eid)
    if not ex:
        flash("考试不存在", "err")
        return redirect(url_for("index"))
    try:
        new_id = _copy_exam_config(eid)
    except ValueError as exc:
        flash(str(exc), "err")
        return redirect(url_for("index"))
    except Exception:
        app.logger.exception("网页复制考试失败")
        flash("复制考试失败，请查看服务器日志", "err")
        return redirect(url_for("index"))
    new_ex = db.get_exam(new_id)
    flash(f"已复制为「{new_ex['title']}」，可直接编辑后开考")
    db.log("teacher", "info", f"复制考试 #{eid} → #{new_id}")
    return redirect(url_for("index"))


@app.route("/exams/export", methods=["POST"])
def exams_batch_export():
    """网页控制台批量导出：一个 xlsx，每场考试一个成绩 sheet。"""
    ids = _clean_id_list(request.form.getlist("ids"))
    if not ids:
        flash("请先勾选要导出的考试", "err")
        return redirect(url_for("index"))
    response = _batch_exams_xlsx_response(ids)
    if response is None:
        flash("选择的考试不存在", "err")
        return redirect(url_for("index"))
    return response


# ---------------- 公开大屏（免登录，投屏用） ----------------
def _screen_data():
    """
    公开大屏的数据（页面首屏渲染 + JSON 轮询共用）。
    只含进度信息：阶段、倒计时、在线/交卷、每人状态 —— 不含答案与评分规则。
    """
    ex = db.active_exam()
    st = db.stats(ex["id"]) if ex else {"students": 0, "online": 0, "submitted": 0}
    theory_total = len(db.get_exam_questions(ex["id"])) if ex else 0
    remaining = db.phase_remaining(ex["id"]) if ex else None

    # 一次查询拿本场参与者的答题数，历史考试与未参与机器不会混入。
    counts = db.theory_counts(ex["id"]) if ex else {}
    rows = []
    parts = db.participants_for(ex["id"]) if ex else []
    if ex and not parts:
        parts = [{"id": 0, "seat": s.get("seat", ""), "name": s.get("name", ""),
                  "status": s.get("status", "idle"), "last_seen": s.get("last_seen"),
                  "hostname": s["hostname"]}
                 for s in db.list_students() if s.get("exam_id") == ex["id"]]
    now_ = time.time()
    for p in parts:
        is_online = bool(p.get("last_seen") and now_ - p["last_seen"] < db.ONLINE_TIMEOUT)
        raw = p.get("status") or "idle"
        if not is_online and raw != "submitted":
            disp = "掉线"
        else:
            disp = {"idle": "待考", "working": "答题中", "theory_done": "理论已交",
                    "submitted": "已交卷"}.get(raw, raw)
        rows.append({
            "id": p["id"], "seat": p.get("seat", ""), "name": p.get("name", ""),
            "online": is_online, "status": disp, "submitted": raw == "submitted",
            "theory_n": counts.get(p["id"], 0),
        })
    # 座位号自然序（1、2、10），没座位号的按参与者 ID 排最后
    def _key(r):
        try:
            return (0, int(r["seat"]), r["id"])
        except (TypeError, ValueError):
            return (1, 0, r["id"])
    rows.sort(key=_key)

    sc = db.status_counts(ex["id"]) if ex else {}
    theory_done = (sc.get("theory_done", 0) + sc.get("submitted", 0))
    data = {
        "exam": {"id": ex["id"], "title": ex["title"], "status": ex["status"],
                 "phase": ex["phase"]} if ex else None,
        "phase_cn": ({"theory": "理论题阶段", "practice": "实操阶段",
                      "ended": "考试已结束"}.get(ex["phase"], ex["phase"])
                     if ex else "等待考试开始"),
        "remaining": remaining,
        "stats": st,
        "theory_total": theory_total,
        "rows": rows,
        # 图表用
        "theory_done": theory_done,
        "practice_done": sc.get("submitted", 0),
        "dist": db.score_distribution(ex["id"]) if ex else {"labels": [], "values": []},
        "server_name": SERVER_NAME,
    }
    return data


@app.route("/screen")
def public_screen():
    """公开大屏（免登录，投屏用）"""
    d = _screen_data()
    return render_template(
        "screen.html", exam=d["exam"], rows=d["rows"], stats=d["stats"],
        theory_total=d["theory_total"], remaining=d["remaining"],
        phase_cn=d["phase_cn"], server_name=SERVER_NAME,
        dist=d["dist"], theory_done=d["theory_done"],
        practice_done=d["practice_done"])


@app.route("/screen.json")
def public_screen_json():
    """大屏局部刷新用（免登录，内容与 /screen 完全一致）"""
    return jsonify(_screen_data())


# ---------------- 考试管理 ----------------
THEORY_TYPES = ("single", "multi", "judge", "blank", "code", "drag")
# drawing = 画板创作（画笔/形状/电学·网络·化学元件，交一张压缩图片），排最后
ALL_TYPES = THEORY_TYPES + ("practice", "typing", "drawing")

# ---------------- 打字模块（小学） ----------------
# 子任务的默认配置；老师可在「编辑考试」里按场调整、逐项开关。
# 打地鼠排最前：先练鼠标点击，再进打字测试。
TYPING_TASK_ORDER = ("whack_mole", "key_press", "case_press", "typing_en",
                     "typing_zh", "name_typing")
TYPING_TASK_NAME = {"whack_mole": "打地鼠", "key_press": "看键按键",
                    "case_press": "小写字母找按键",
                    "typing_en": "英文打字", "typing_zh": "中文打字",
                    "name_typing": "打名字测试"}
# 打地鼠三档难度：档位越高，地鼠越小、冒出时间越短（客户端按此绘制）
WHACK_LEVELS = ("easy", "normal", "hard")
WHACK_LEVEL_NAME = {"easy": "简单", "normal": "普通", "hard": "困难"}
# show_ms=冒出到缩回的时长 gap_ms=两只间隔 size=地鼠直径(px)
WHACK_LEVEL_PARAMS = {
    "easy":   {"name": "简单", "show_ms": 1800, "gap_ms": 650, "size": 56},
    "normal": {"name": "普通", "show_ms": 1300, "gap_ms": 500, "size": 44},
    "hard":   {"name": "困难", "show_ms": 900, "gap_ms": 380, "size": 34},
}
# 打名字测试：每个词三个回合（先拼音提示 → 再汉字提示 → 最后不给提示）
NAME_TYPING_STAGES = 3
# 默认词表（老师可在「编辑考试」里改）。拼音带声调，客户端照原样显示。
NAME_TYPING_WORDS = [
    {"word": "雅安", "pinyin": "yǎ ān"},
    {"word": "北附", "pinyin": "běi fù"},
    {"word": "实验", "pinyin": "shí yàn"},
    {"word": "中学", "pinyin": "zhōng xué"},
    {"word": "学校", "pinyin": "xué xiào"},
    {"word": "同学", "pinyin": "tóng xué"},
    {"word": "老师", "pinyin": "lǎo shī"},
    {"word": "语文", "pinyin": "yǔ wén"},
    {"word": "数学", "pinyin": "shù xué"},
    {"word": "英语", "pinyin": "yīng yǔ"},
    {"word": "文明", "pinyin": "wén míng"},
    {"word": "礼貌", "pinyin": "lǐ mào"},
    {"word": "努力", "pinyin": "nǔ lì"},
    {"word": "独立", "pinyin": "dú lì"},
    {"word": "中国", "pinyin": "zhōng guó"},
    {"word": "四川", "pinyin": "sì chuān"},
]
TYPING_DEFAULT_TEXT_EN = ("The quick brown fox jumps over the lazy dog. "
                          "Practice makes perfect. Keep your fingers on the "
                          "home row and type every letter carefully.")
TYPING_DEFAULT_TEXT_ZH = ("我们是小学生，学电脑，练打字。"
                          "眼睛看屏幕，手指放基准键，不看键盘也能打。"
                          "天天练一练，打字快又准。")


_PASSAGE_CACHE = {}          # 文件名 -> (mtime, 解析结果)，避免每次打开页面都重读


def _load_typing_passages(filename="小学语文打字范文.json"):
    """
    读打字范文库（题库/下），老师在「编辑考试」里下拉选用、还可截断：
      小学语文打字范文.json  生词 / 必背古诗 / 雅安主题课文段落（长短都有）
      小学英语打字范文.json  英文字母 / 单词 / 句子 / 短文
    带 mtime 缓存：同一个文件没改过就不重复读盘（页面每次要读两份范文）。
    """
    path = os.path.join(BANK_DIR, filename)
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        _PASSAGE_CACHE.pop(filename, None)
        return []
    hit = _PASSAGE_CACHE.get(filename)
    if hit and hit[0] == mtime:
        return hit[1]
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            out = [p for p in data
                   if isinstance(p, dict) and (p.get("text") or "").strip()]
    except Exception:
        out = []
    _PASSAGE_CACHE[filename] = (mtime, out)
    return out


# ---------------- 虚拟键盘（老师点选这场要考哪些键） ----------------
# id 与客户端 typing_ui.py 里的约定保持一致（小写字母 / 数字 / 功能键名）
TYPING_FUNC_KEYS = [
    ("esc", "Esc"), ("tab", "Tab"), ("capslock", "CapsLock"),
    ("shift", "Shift"), ("enter", "Enter"), ("backspace", "⌫退格"),
    ("delete", "Delete"), ("space", "空格"),
    ("up", "↑"), ("down", "↓"), ("left", "←"), ("right", "→"),
]
TYPING_LETTER_ROWS = ("qwertyuiop", "asdfghjkl", "zxcvbnm")
TYPING_DIGITS = "1234567890"
TYPING_ALL_KEYS = ([k for k, _l in TYPING_FUNC_KEYS] + list(TYPING_DIGITS)
                   + [c for row in TYPING_LETTER_ROWS for c in row])


def _keyboard_layout():
    """给模板用的键盘布局（功能键行 / 数字行 / 字母三行）"""
    return {"func": TYPING_FUNC_KEYS, "digits": list(TYPING_DIGITS),
            "letters": [list(r) for r in TYPING_LETTER_ROWS]}


def _clean_key_pool(raw, letters_only=False):
    """把表单里的键位选择洗一遍：只留合法的键 id；空 = 全部（客户端随机出题）"""
    try:
        got = json.loads(raw or "[]")
    except (TypeError, ValueError):
        got = []
    if not isinstance(got, list):
        return []
    allowed = ([c for row in TYPING_LETTER_ROWS for c in row] if letters_only
               else TYPING_ALL_KEYS)
    out = []
    for k in got:
        k = str(k or "").strip().lower()
        if k in allowed and k not in out:
            out.append(k)
    return out


def _typing_defaults():
    # enabled: 这个子任务考不考（和题型勾选一样，可以有可以没有）
    # seconds: 每轮/限时秒数，0 = 不限时
    return {
        # 打地鼠（鼠标练习）：level = 三关连打(all) 或只打某一关(easy/normal/hard)
        # rounds = 每关冒出几只地鼠；档位越高地鼠越小、冒出时间越短
        "whack_mole": {"enabled": True, "level": "all", "rounds": 8},
        # keys: 这场要考哪些键（老师用虚拟键盘点选），空 = 全部键都随机出
        "key_press":  {"enabled": True, "rounds": 10, "seconds": 5, "keys": []},
        "case_press": {"enabled": True, "rounds": 10, "seconds": 5, "keys": []},
        "typing_en":  {"enabled": True, "seconds": 180,
                       "text": TYPING_DEFAULT_TEXT_EN},
        "typing_zh":  {"enabled": True, "seconds": 180,
                       "text": TYPING_DEFAULT_TEXT_ZH},
        # 打名字测试：rounds = 打几个词，每个词 3 个回合（拼音→汉字→不给提示）
        # seconds = 每个回合的限时，0 = 不限时
        "name_typing": {"enabled": True, "rounds": 5, "seconds": 0,
                        "words": [dict(w) for w in NAME_TYPING_WORDS]},
    }


_TYPING_CFG_CACHE = {}


def _typing_config_of(ex):
    """这场考试的打字配置：库里存的覆盖默认值，缺哪项补哪项（按原文缓存）"""
    raw = (ex or {}).get("typing_config") or ""
    hit = _TYPING_CFG_CACHE.get(raw)
    if hit is not None:
        return {k: dict(v) for k, v in hit.items()}
    cfg = _typing_defaults()
    try:
        saved = json.loads((ex or {}).get("typing_config") or "{}")
    except (TypeError, ValueError):
        saved = {}
    if isinstance(saved, dict):
        for task, item in saved.items():
            if task in cfg and isinstance(item, dict):
                cfg[task].update(item)
    if len(_TYPING_CFG_CACHE) > 64:
        _TYPING_CFG_CACHE.clear()
    _TYPING_CFG_CACHE[raw] = {k: dict(v) for k, v in cfg.items()}
    return cfg


def _typing_payload(ex, host=""):
    """
    下发给学生端的打字模块配置（不含任何答案类敏感信息）。
    这场考试没勾「打字练习」→ None。
    host 用来取这个学生的姓名（打名字测试要打他**自己**的名字）。
    """
    if not ex or "typing" not in _selected_types(ex):
        return None
    cfg = _typing_config_of(ex)
    tasks = []
    for task in TYPING_TASK_ORDER:
        item = dict(cfg[task])
        if not item.pop("enabled", True):
            continue               # 没勾的子任务不下发（学生端不显示）
        item["task"] = task
        item["name"] = TYPING_TASK_NAME[task]
        if task == "whack_mole":
            item["levels"] = WHACK_LEVEL_PARAMS    # 三档难度（大小/时长）参数表
        if task == "name_typing":
            item["stages"] = NAME_TYPING_STAGES      # 拼音→汉字→不给提示
            # 学生本人的姓名（含拼音与「多音字/查不到的字」标注）
            stu = db.get_student(host) if host else None
            name = ((stu or {}).get("name") or "").strip()
            if name:
                table, duoyin = pinyin_table()
                py, missing, has_duo = word_pinyin(name, table)
                item["student"] = {
                    "name": name, "pinyin": py,
                    "missing": missing,        # 拼音表没收录的字
                    "duoyin": has_duo,         # 含多音字（提示以本人确认为准）
                    "duoyin_chars": [c for c in name if c in duoyin],
                }
                # 认出了学生本人 → 只打他自己的名字（不再掺老师词表里的词）
                item["words"] = []
        tasks.append(item)
    return {"tasks": tasks}


def _drawing_form_cfg(ex):
    """
    「编辑考试」表单回显的画图配置（不管勾没勾画图题，老师填过的都回显）。
    styles = 这场考哪几道画图小题：draw=画板创作（自由画），beads=拼豆题。
             两道可以同时考（独立勾选，学生端各出一个标签、各交一份作品）。
    """
    try:
        cfg = json.loads((ex or {}).get("drawing_config") or "{}")
    except (TypeError, ValueError):
        cfg = {}
    if not isinstance(cfg, dict):
        cfg = {}
    styles = cfg.get("styles")
    if not styles:
        # 老配置只有一个 style（free/beads）→ 翻译成新格式
        legacy = str(cfg.get("style") or "free").strip().lower()
        styles = ["beads" if legacy == "beads" else "draw"]
    if not isinstance(styles, list):
        styles = [styles]
    out = []
    for s in styles:
        s = str(s or "").strip().lower()
        s = {"free": "draw", "bead": "beads"}.get(s, s)
        if s in ("draw", "beads") and s not in out:
            out.append(s)
    if not out:
        out = ["draw"]
    try:
        grid = max(16, min(36, int(cfg.get("grid") or 28)))
    except (TypeError, ValueError):
        grid = 28
    return {"brief": (cfg.get("brief") or "").strip()[:500],
            "styles": out, "grid": grid}


def _drawing_payload(ex):
    """
    下发给学生端的画图题配置。这场考试没勾「画图题」→ None。
    brief = 老师写的画题要求（学生端显示在画板上方）。
    styles = 考哪几道画图小题：draw=画板创作（自由画），beads=拼豆题。
    """
    if not ex or "drawing" not in _selected_types(ex):
        return None
    cfg = _drawing_form_cfg(ex)
    return {"brief": cfg["brief"],
            "styles": cfg["styles"],
            # 兼容旧学生端：style 取第一道小题
            "style": cfg["styles"][0],
            "grid": cfg["grid"]}


def _typing_form_config(form):
    """从「编辑考试」表单里读打字配置（非法输入回退默认值）"""
    cfg = _typing_defaults()

    def _pos_int(name, default, lo=1, hi=3600):
        try:
            v = int(float(str(form.get(name) or "").strip()))
        except (TypeError, ValueError):
            return default
        return max(lo, min(v, hi))

    # 每个子任务考不考（复选框没勾 = 不考）
    for task in TYPING_TASK_ORDER:
        cfg[task]["enabled"] = bool(form.get(f"typing_enable_{task}"))
    # 打地鼠：难度档 + 每关只数
    level = (form.get("typing_whack_level") or "").strip().lower()
    cfg["whack_mole"]["level"] = (level if level in WHACK_LEVELS + ("all",)
                                  else "all")
    cfg["whack_mole"]["rounds"] = _pos_int("typing_whack_rounds", 8, 1, 50)
    # 洞口布局（不一定是 3×3，老师按场地/难度挑）
    grid = (form.get("typing_whack_grid") or "").strip().lower()
    cfg["whack_mole"]["grid"] = grid if grid in ("3x3", "3x4", "4x3", "4x4",
                                                 "4x5", "5x4", "5x5") else "3x3"
    # 秒数允许 0 = 不限时
    cfg["key_press"]["rounds"] = _pos_int("typing_key_rounds", 10, 1, 100)
    cfg["key_press"]["seconds"] = _pos_int("typing_key_seconds", 5, 0, 600)
    cfg["case_press"]["rounds"] = _pos_int("typing_case_rounds", 10, 1, 100)
    cfg["case_press"]["seconds"] = _pos_int("typing_case_seconds", 5, 0, 600)
    # 要考哪些键（虚拟键盘点选；空 = 全部随机）
    cfg["key_press"]["keys"] = _clean_key_pool(form.get("typing_key_pool"))
    cfg["case_press"]["keys"] = _clean_key_pool(
        form.get("typing_case_pool"), letters_only=True)
    cfg["typing_en"]["seconds"] = _pos_int("typing_en_seconds", 180, 0, 3600)
    cfg["typing_zh"]["seconds"] = _pos_int("typing_zh_seconds", 180, 0, 3600)
    en = (form.get("typing_en_text") or "").strip()
    zh = (form.get("typing_zh_text") or "").strip()
    if en:
        cfg["typing_en"]["text"] = en[:2000]
    if zh:
        cfg["typing_zh"]["text"] = zh[:2000]
    # 打名字测试：打几遍（每个学生打的都是自己名字，老师不用录词表）
    # 兼容：老表单里还有「词表」输入框时仍然收下，只当没认出学生时的备用词
    cfg["name_typing"]["rounds"] = _pos_int("typing_name_rounds", 5, 1, 30)
    cfg["name_typing"]["seconds"] = _pos_int("typing_name_seconds", 0, 0, 600)
    words = _parse_word_list(form.get("typing_name_words"))
    if len(words) >= 2:
        cfg["name_typing"]["words"] = words
    return cfg


def _name_words_text(cfg):
    """打名字测试的词表 → 编辑框文本（一行一个「词语|拼音」）"""
    words = (cfg.get("name_typing") or {}).get("words") or []
    return "\n".join(f"{w.get('word', '')}|{w.get('pinyin', '')}"
                     for w in words if isinstance(w, dict) and w.get("word"))


_pinyin_cache = {"mtime": None, "map": {}, "duoyin": set(), "sur": {}}


def pinyin_table():
    """
    常用字/姓氏拼音表。打名字测试要用：学生自己的名字自动生成拼音提示。

    数据两层：内置全量字表（pinyin_data.py，2 万字）兜底 +
    题库/拼音常用字表.json 覆盖（老师可自己改，人工校对优先）。
    以前只有 JSON 一层，老师电脑上是旧版小字表时「可」这类常用字都会缺，
    所以内置一层保底，升级包不覆盖旧数据文件也能正常解析。
    """
    path = os.path.join(BANK_DIR, "拼音常用字表.json")
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        mtime = None
    # mtime 为 None（JSON 不存在）时也要能命中缓存，但空表不算 ——
    # 空表说明内置兜底还没加载成功，得重试
    if _pinyin_cache["mtime"] == mtime and _pinyin_cache["map"]:
        return _pinyin_cache["map"], _pinyin_cache["duoyin"]
    table, duoyin, sur = {}, set(), {}
    try:
        import pinyin_data
        base = pinyin_data.builtin()
        table = dict(base.get("字") or {})
        duoyin = set(base.get("多音") or [])
        sur = dict(base.get("姓氏") or {})
    except Exception:
        table, duoyin, sur = {}, set(), {}
    if mtime is not None:
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            table.update({str(k): str(v)
                          for k, v in (data.get("字") or {}).items()})
            duoyin |= {str(c) for c in (data.get("多音") or [])}
            sur.update({str(k).strip(): str(v).strip()
                        for k, v in (data.get("姓氏") or {}).items()
                        if str(k).strip()})
        except Exception:
            pass
    _pinyin_cache.update(mtime=mtime, map=table, duoyin=duoyin, sur=sur)
    return table, duoyin


def pinyin_surnames():
    """多音字姓氏的「姓氏读音」表（含两字复姓），读不到时为空"""
    pinyin_table()                      # 触发加载/缓存
    return _pinyin_cache.get("sur") or {}


def word_pinyin(word, table=None, as_name=True):
    """
    给一个词按字拼拼音。返回 (拼音字符串, 未收录的字列表, 是否含多音字)
    优先级：老师标注（整词 > 单字） > 姓氏读音（人名首字） > 常用字表 > "?" 占位。
    as_name=True 时按人名处理：首字（或复姓）用姓氏读音 ——
    「曾」要读 zēng、「尉迟」要读 yù chí，按常用读音拼会把学生名字读错。
    查不到的字用 "?" 占位 —— 学生端会显示「拼音待老师标注」而不是瞎猜。
    """
    word = str(word or "")
    table = table if table is not None else pinyin_table()[0]
    duoyin = pinyin_table()[1]
    ovr = pinyin_overrides()
    if word in ovr:                      # 整词标注优先（一个词一个读音时最省事）
        return ovr[word], [], False
    sur = pinyin_surnames() if as_name else {}
    parts, missing = [], []
    fixed_at = set()                     # 已定了读音的「位置」（乐乐这种重字不能按字去重）
    i = 0
    if sur and word:
        two, one = word[:2], word[:1]
        if two in sur:                   # 复姓（万俟 / 尉迟 / 长孙 …）
            parts.extend(sur[two].split())
            fixed_at.update((0, 1))
            i = 2
        elif one in sur:                 # 单姓多音（曾 / 解 / 仇 …）
            parts.append(sur[one])
            fixed_at.add(0)
            i = 1
    for idx, ch in enumerate(word):
        if idx < i:
            continue
        if ch in ovr:
            parts.append(ovr[ch])
            fixed_at.add(idx)
        elif ch in table:
            parts.append(table[ch])
        elif ch.strip():
            parts.append("?")
            missing.append(ch)
    has_duo = any(ch in duoyin and idx not in fixed_at and ch not in ovr
                  for idx, ch in enumerate(word))
    return " ".join(parts), missing, has_duo


def exam_roster(exam_id):
    """
    本场考试自动识别到的学生（打名字测试用，老师不用手工录词表）。
    学生机登录时填过姓名就会出现在这里；拼音按常用字表自动生成。
    返回 {"people": [{"name","pinyin"}...], "unknown": [...], "duoyin": [...],
          "count": 认出来的学生数, "unnamed": 还没填姓名的机器数}
    """
    table, duoyin = pinyin_table()
    c = db.conn()
    rows = c.execute(
        """SELECT name FROM students
           WHERE exam_id=? OR exam_id IS NULL
           ORDER BY hostname""", (exam_id,)).fetchall()
    c.close()
    people, unknown, duo, seen, unnamed = [], [], [], set(), 0
    for r in rows:
        name = (r["name"] or "").strip()
        if not name:
            unnamed += 1
            continue
        if name in seen:
            continue
        seen.add(name)
        py, missing, has_duo = word_pinyin(name, table)
        if missing:
            unknown.append(name + "（" + "".join(missing) + "）")
        if has_duo:
            duo.append(name)
        people.append({"name": name, "pinyin": py})
    return {"people": people, "unknown": unknown, "duoyin": duo,
            "count": len(people), "unnamed": unnamed}


def student_name_lines(exam_id):
    """本场学生姓名 → 词表文本（一行一个「姓名|拼音」；老接口，保留兼容）"""
    r = exam_roster(exam_id)
    return ("\n".join(f"{p['name']}|{p['pinyin']}" for p in r["people"]),
            r["unknown"], r["duoyin"])


_pinyin_override_cache = {"mtime": None, "map": {}}


def pinyin_overrides():
    """
    老师手工标注的拼音（题库/拼音标注.json，一行一个「字或词|拼音」）。
    打名字测试碰到拼音表没收录 / 多音字的姓名，老师标一次就长期生效，
    学生端下发的拼音提示也会跟着变。
    """
    path = os.path.join(BANK_DIR, "拼音标注.json")
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return {}
    if _pinyin_override_cache["mtime"] == mtime:
        return _pinyin_override_cache["map"]
    out = {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            out = {str(k).strip(): str(v).strip()
                   for k, v in data.items()
                   if str(k).strip() and str(v).strip()}
    except Exception:
        out = {}
    _pinyin_override_cache.update(mtime=mtime, map=out)
    return out


def save_pinyin_overrides(text):
    """把老师标注的拼音写进 题库/拼音标注.json，返回有效条数"""
    out, seen = {}, set()
    for line in str(text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "|" not in line:
            continue
        k, v = line.split("|", 1)
        k, v = k.strip(), v.strip()
        if not k or not v or k in seen:
            continue
        seen.add(k)
        out[k] = v
    path = os.path.join(BANK_DIR, "拼音标注.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    _pinyin_override_cache.update(mtime=None, map=out)   # 立即生效
    return len(out)


def pinyin_override_text():
    """已标注的拼音 → 编辑框文本（一行一个「字或词|拼音」）"""
    return "\n".join(f"{k}|{v}" for k, v in sorted(pinyin_overrides().items()))


def _parse_word_list(text):
    """
    词表解析（一行一个词）：
        雅安|yǎ ān      带拼音
        雅安 yǎ ān      空格分隔也认
        雅安            只写词 —— 拼音自动查内置表，查不到就空着
    返回 [{"word": "...", "pinyin": "..."}]，最多 60 个、每词最多 6 个字。
    """
    if not text or not str(text).strip():
        return []
    builtin = {w["word"]: w["pinyin"] for w in NAME_TYPING_WORDS}
    # 常用字表也一起用：老师只写「姓名」，拼音自动补（查不到就空着，学生端会标注）
    table = pinyin_table()[0]
    out, seen = [], set()
    for line in str(text).splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "|" in line or "｜" in line or "\t" in line:
            parts = re.split(r"[|｜\t]", line, maxsplit=1)
            word = parts[0].strip()
            pinyin = parts[1].strip() if len(parts) > 1 else ""
        else:
            m = re.match(r"^([\u4e00-\u9fa5A-Za-z0-9]+)\s+(.+)$", line)
            if m:
                word, pinyin = m.group(1), m.group(2).strip()
            else:
                word, pinyin = line, ""
        word = word.strip()[:6]
        if not word or word in seen:
            continue
        seen.add(word)
        if not pinyin:
            pinyin = builtin.get(word, "")
            if not pinyin:
                # 用常用字表按字拼；查不到的字会拼成 "?"，前端标注给学生/老师
                pinyin = word_pinyin(word, table)[0]
        out.append({"word": word, "pinyin": (pinyin or "")[:24]})
        if len(out) >= 60:
            break
    return out


_SEL_TYPES_CACHE = {}        # question_types 原文 -> 解析结果（心跳每 3 秒要算好几次）


def _selected_types(ex):
    """
    这场考试勾选了哪些题型。
    没设置过（旧考试 question_types 为空）→ 按老行为视为「理论题全勾 + 操作题」。
    ⚠️ 不含后来才加的打字模块 —— 不然老考试会平白多出「打字练习」标签。
    结果只取决于 question_types 原文，所以按原文缓存（避免每次心跳重复 json.loads）。
    """
    raw = (ex or {}).get("question_types") or "[]"
    hit = _SEL_TYPES_CACHE.get(raw)
    if hit is not None:
        return list(hit)
    try:
        got = json.loads(raw)
    except (TypeError, ValueError):
        got = []
    if not isinstance(got, list) or not got:
        out = list(THEORY_TYPES) + ["practice"]
    else:
        out = [t for t in got if t in ALL_TYPES]
    if len(_SEL_TYPES_CACHE) > 64:       # 老师改题型会生成新 key，简单限一下大小
        _SEL_TYPES_CACHE.clear()
    _SEL_TYPES_CACHE[raw] = tuple(out)
    return list(out)


# ==================== 表单字段解析（防 500） ====================
# 老师端的输入框都是普通文本框，手一抖填个"40分"/"两分"/留空，
# 直接 int()/float() 就是 500 错误页。统一从这里过一道。
INT_MAX_MINUTES = 600        # 单场考试最多 10 小时
MAX_SCORE = 1000             # 单题最高分


def _to_minutes(raw, default):
    """时长：非法输入回退默认值，并夹在 1 ~ 600 分钟"""
    try:
        v = int(float(str(raw).strip()))
    except (TypeError, ValueError):
        return default
    return max(1, min(v, INT_MAX_MINUTES))


def _fmt_mmss(sec):
    """剩余秒数 → 「分:秒」/「时:分:秒」，空值返回空串"""
    if sec is None:
        return ""
    h, rest = divmod(int(sec), 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _to_score(raw, default=2.0):
    """分值：非法输入回退默认值，夹在 0 ~ 1000"""
    try:
        v = float(str(raw).strip())
    except (TypeError, ValueError):
        return float(default)
    return max(0.0, min(v, float(MAX_SCORE)))


def _to_int(raw, default=0):
    """安全转整数（表单/URL 参数用）"""
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return default


def _has_rules(blob):
    """评分规则是不是空的（数据库里存 JSON 字符串）"""
    txt = (blob or "").strip() if isinstance(blob, str) else blob
    if isinstance(txt, list):
        return len(txt) > 0
    return bool(txt) and txt not in ("[]", "{}")


def _checks_total(checks):
    """评分规则总分；兼容 JSON 字符串并跳过损坏的旧规则。"""
    if isinstance(checks, str):
        try:
            checks = json.loads(checks or "[]")
        except (TypeError, ValueError):
            return 0.0
    if not isinstance(checks, list):
        return 0.0
    total = 0.0
    for rule in checks:
        try:
            total += float((rule or {}).get("score") or 0)
        except (TypeError, ValueError, AttributeError):
            continue
    return round(total, 2)


def _exam_content(ex):
    """
    这场考试到底有没有内容。
    返回 (理论题数, 实操题数)。老的单题考试规则挂在 exams 表上，也算 1 道。
    """
    theory_n = len(db.get_exam_questions(ex["id"])) if ex else 0
    tasks = db.exam_tasks(ex["id"]) if ex else []
    if tasks:
        prac_n = len(tasks)
    else:
        prac_n = 1 if _has_rules(ex.get("checks")) else 0
    return theory_n, prac_n


def _exam_ready(eid):
    """
    能不能开始考试。返回 (ok, 错误提示, 警告列表)。
    没题目就开考 → 学生端进去一片空白、还锁着屏退出不了，必须拦住。
    """
    ex = db.get_exam(eid)
    if not ex:
        return False, "考试不存在", []
    theory_n, prac_n = _exam_content(ex)
    has_typing = "typing" in _selected_types(ex)
    has_drawing = "drawing" in _selected_types(ex)
    if not theory_n and not prac_n and not has_typing and not has_drawing:
        return (False, "这场考试还没有任何题目，不能开始。请先点「编辑」加题目"
                       "（理论题从题库勾选，实操题从实操题库挑）", [])

    warn = []
    if (theory_n or prac_n) and not ex.get("duration"):
        warn.append("考试时长是 0 分钟")
    if prac_n:
        tasks = db.exam_tasks(eid)
        if tasks:
            no_rule = [t["name"] for t in tasks if not _has_rules(t.get("checks"))]
        else:
            no_rule = [] if _has_rules(ex.get("checks")) else ["本场考试"]
        if no_rule:
            warn.append("这些实操题没配评分规则，交上来会没分数："
                        + "、".join(no_rule[:3]))
        missing = []
        for t in tasks:
            for m in (t.get("materials") or []):
                if not os.path.isfile(os.path.join(MATERIAL_DIR, m)):
                    missing.append(m)
        if not tasks:
            missing = [m for m in json.loads(ex["materials"] or "[]")
                       if not os.path.isfile(os.path.join(MATERIAL_DIR, m))]
        if missing:
            warn.append("素材文件找不到（学生下载会 404）："
                        + "、".join(sorted(set(missing))[:3]))
    return True, "", warn


def _practice_types_of(ex):
    """这场考试的操作题类型列表（etype 可能是 JSON 数组或单个字符串）"""
    raw = (ex or {}).get("etype") or ""
    try:
        got = json.loads(raw)
        if isinstance(got, list):
            return [t for t in got if t]
        if isinstance(got, str) and got:
            return [got]
    except (TypeError, ValueError):
        pass
    return [raw] if raw else []


@app.route("/exam/new", methods=["GET", "POST"])
def exam_new():
    if request.method == "POST":
        qtypes = [t for t in request.form.getlist("qtypes") if t in ALL_TYPES]
        if not qtypes:
            qtypes = ["single", "multi", "judge", "blank"]
        # 只有勾了「WPS 操作题」才记操作题类型，
        # 否则留空 —— 以前默认写成 "word"，结果纯理论题的考试也被当成有操作题。
        if "practice" in qtypes:
            practice_types = request.form.getlist("practice_types") or ["excel"]
            etype = json.dumps(practice_types, ensure_ascii=False)
        else:
            etype = ""
        duration = _to_minutes(request.form.get("duration"), 60)
        eid = db.create_exam({
            "title": (request.form.get("title") or "").strip() or "未命名考试",
            "etype": etype,
            # 考试只有一个总时长（理论题、实操题共用）；theory_duration
            # 列写入同值，老代码读它也不会出错
            "duration": duration,
            "theory_duration": duration,
            "requirement": request.form.get("requirement", ""),
            "save_as": "",
            "require_complete": 1 if request.form.get("require_complete") else 0,
        })
        # 保存题型选择
        db.set_question_types(eid, qtypes)
        new_ex = db.get_exam(eid) or {}
        db.log("teacher", "info",
               f"创建考试 #{eid} 编号={new_ex.get('code') or '-'}，"
               f"题型: {','.join(qtypes)}")
        flash(f"已创建：{new_ex.get('code') or ''}".strip("："))
        return redirect(url_for("exam_edit", eid=eid))
    return render_template("exam_new.html", type_name=TYPE_NAME, ext_of=EXT_OF)


@app.route("/exam/<int:eid>", methods=["GET", "POST"])
def exam_edit(eid):
    ex = db.get_exam(eid)
    if not ex:
        return "考试不存在", 404

    if request.method == "POST":
        # 考试进行中（含暂停）不允许保存改动：
        # 改时长会直接把倒计时算歪，改题目/素材会让学生手里的卷子和服务器对不上
        if ex["status"] in ("running", "paused", "stopped"):
            state = "已结束" if ex["status"] == "stopped" else "正在进行或已暂停"
            flash(f"「{ex['title']}」{state}，配置不能改。"
                  f"已结束考试只读，可查看成绩、评讲或导出。", "err")
            return redirect(url_for("index"))
        files = json.loads(ex["materials"] or "[]")
        # 素材上传（文件名里的空格/#/? 会让下载链接断开，一并换成下划线）
        up = request.files.get("material")
        if up and up.filename:
            ext = os.path.splitext(up.filename)[1].lower()
            if ext in (".docx", ".xlsx", ".pptx"):
                if not _valid_office_upload(up):
                    flash("Office 素材文件无效", "err")
                    return redirect(url_for("exam_edit", eid=eid))
            elif ext in (".png", ".jpg", ".jpeg"):
                if not _valid_image_upload(up):
                    flash("图片素材文件无效", "err")
                    return redirect(url_for("exam_edit", eid=eid))
            elif ext not in (".pdf", ".txt", ".csv"):
                flash("不支持的素材文件类型", "err")
                return redirect(url_for("exam_edit", eid=eid))
            safe = "".join(ch for ch in up.filename if ch not in r'\/:*?"<>|#?%')
            safe = safe.replace(" ", "_").strip("._") or "material"
            up.save(os.path.join(MATERIAL_DIR, safe))
            if safe not in files:
                files.append(safe)

        try:
            checks = json.loads(request.form.get("checks") or "[]")
        except Exception:
            checks = json.loads(ex["checks"] or "[]")

        # 题型开关散在各题型标签页里（勾选框常驻表单）
        new_etype = ex["etype"]
        if "qtypes" in request.form and ex["status"] not in ("running", "paused"):
            qtypes = [t for t in request.form.getlist("qtypes") if t in ALL_TYPES]
            if not qtypes:
                qtypes = list(THEORY_TYPES)
            db.set_question_types(eid, qtypes)
            # 操作题类型：勾了操作题才需要；没勾也留着（下次勾上还在）
            pts = request.form.getlist("practice_types")
            if "practice" in qtypes:
                new_etype = json.dumps(pts or ["excel"], ensure_ascii=False)
            else:
                new_etype = json.dumps(pts, ensure_ascii=False) if pts else ""
            # 打字模块（小学）配置：表单里带着就存，勾不勾都不丢老师填的参数
            if "typing" in qtypes or "typing_key_rounds" in request.form:
                db.set_typing_config(eid, _typing_form_config(request.form))
            # 画图题：画题要求 + 考哪几道小题（画板/拼豆独立勾选，勾不勾都保留老师填的）
            if "drawing" in qtypes or "drawing_brief" in request.form:
                try:
                    dgrid = max(16, min(36, int(
                        request.form.get("drawing_grid") or 28)))
                except (TypeError, ValueError):
                    dgrid = 28
                # 画板创作（draw）/ 拼豆题（beads）独立复选；两道可同时考
                dst = []
                for v in request.form.getlist("drawing_styles"):
                    v = (v or "").strip().lower()
                    v = {"free": "draw", "bead": "beads"}.get(v, v)
                    if v in ("draw", "beads") and v not in dst:
                        dst.append(v)
                if not dst:
                    # 兼容旧表单单选字段；都没有 → 默认只考画板
                    legacy = (request.form.get("drawing_style") or ""
                              ).strip().lower()
                    dst = ["beads" if legacy == "beads" else "draw"]
                db.set_drawing_config(
                    eid, {"brief": (request.form.get("drawing_brief")
                                    or "").strip()[:500],
                          "styles": dst,
                          "grid": dgrid})

        duration = _to_minutes(request.form.get("duration"), ex["duration"])
        db.update_exam(eid, {
            "title": request.form.get("title", ex["title"]),
            "etype": new_etype,
            "duration": duration,
            "theory_duration": duration,
            "requirement": request.form.get("requirement", ""),
            "materials": files,
            "save_as": "",
            "checks": checks,
            "require_complete": 1 if request.form.get("require_complete") else 0,
            "passwordless": 1 if request.form.get("passwordless") else 0,
            # 交卷结束语（学生交卷后看到的话；做完 / 没做完都显示）
            "finish_note": request.form.get("finish_note", ""),
        })
        n_q = len(db.get_exam_questions(eid))
        flash(f"已保存「{request.form.get('title', ex['title'])}」，共 {n_q} 题"
              + ("" if n_q else "（还没选题，记得先添加题目）"))
        if request.form.get("save_mode") == "questions":
            return redirect(url_for("exam_edit", eid=eid,
                                    tab=request.form.get("tab") or "practice"))
        # 「保存全部设置」用于基本/打字/画板设置，保存后回控制台。
        return redirect(url_for("index"))

    qs = db.get_exam_questions(eid)
    allq = db.list_questions()
    qsum = round(sum(float(q["score"] or 0) for q in qs), 2)
    tab = request.args.get("tab", "")
    sel = _selected_types(ex)
    try:
        checks_list = json.loads(ex.get("checks") or "[]")
        if not isinstance(checks_list, list):
            checks_list = []
    except (TypeError, ValueError):
        checks_list = []
    # 全部题型都做成标签页（老师要求「题型设置都在上面」），
    # 勾选开关搬进了每个题型页开头；没勾的只是不下发给学生
    tasks = db.exam_tasks(eid)
    practice_n = len(tasks) or (1 if checks_list else 0)
    practice_full = (round(sum(_checks_total(t.get("checks")) for t in tasks), 2)
                     if tasks else _checks_total(checks_list))
    total_full = round(qsum + practice_full, 2)
    return render_template("exam_edit.html", exam=ex, questions=qs, qs=qs,
                           allq=allq,
                           categories=db.list_categories(),
                           type_name=TYPE_NAME,
                           qtype_cn=QTYPE_CN,
                           qsum=qsum,
                           theory_full=db.exam_theory_full(eid),
                           practice_n=practice_n,
                           practice_full=practice_full,
                           total_full=total_full,
                           config_locked=ex["status"] in ("running", "paused", "stopped"),
                           tasks=tasks,
                           active_tab=tab,
                           sel_types=sel,
                           show_practice=("practice" in sel),
                           show_typing=("typing" in sel),
                           show_drawing=("drawing" in sel),
                           drawing_cfg=_drawing_form_cfg(ex),
                           typing_cfg=_typing_config_of(ex),
                           typing_roster=exam_roster(eid),
                           roster_text=db.roster_text_of(eid),
                           roster=db.roster_of(eid),
                           cats_by_type=_cats_by_type(allq),
                           pinyin_text=pinyin_override_text(),
                           typing_passages=_load_typing_passages(),
                           typing_passages_en=_load_typing_passages(
                               "小学英语打字范文.json"),
                           typing_kb=_keyboard_layout(),
                           practice_types=_practice_types_of(ex),
                           checks_total=_checks_total,
                           rule_types=RULE_TYPES, checks_list=checks_list)


def _paper_locked(ex):
    """
    考试进行中不许改试卷结构。
    改了会直接串味：学生端手里的题号、题量、满分跟服务器对不上，
    已经答到第 10 题的人忽然发现卷子只剩 8 题，答案全错位。
    """
    if ex and ex["status"] in ("running", "paused", "stopped"):
        state = "已结束，试卷只读" if ex["status"] == "stopped" else "正在进行或已暂停，试卷已锁定"
        flash(f"「{ex['title']}」{state}。已结束考试可查看成绩、评讲或导出。", "err")
        return True
    return False


def _clean_type_question_ids(qtype, values):
    """只保留该题型的有效题号；主表单携带其他页签复选框时也不会串题。"""
    qids = []
    for qid in _clean_id_list(values):
        question = db.get_question(qid)
        if not question:
            return None, "包含不存在的题目"
        if question["qtype"] == qtype and qid not in qids:
            qids.append(qid)
    return qids, ""


def _submitted_type_question_ids(qtype):
    """读取网页选题；qids_order 优先，保证右侧显示顺序就是保存后的顺序。"""
    qids, error = _clean_type_question_ids(qtype, request.form.getlist("qids"))
    if error:
        return None, error
    order_key = f"qids_order_{qtype}"
    if order_key in request.form or "qids_order" in request.form:
        raw_order = request.form.get(order_key, request.form.get("qids_order", ""))
        ordered, order_error = _clean_type_question_ids(qtype, raw_order.split(","))
        if order_error:
            return None, order_error
        # 顺序字段不能偷偷加入未勾选的题；不一致时回退到复选框列表。
        if set(ordered) == set(qids):
            return ordered, ""
    return qids, ""


def _replace_exam_type_questions(eid, qtype, qids):
    """全量替换一个题型，其他题型及其相对顺序保持不变。"""
    kept = [q["id"] for q in db.get_exam_questions(eid) if q["qtype"] != qtype]
    db.set_exam_questions(eid, kept + qids)
    db.invalidate()


@app.route("/exam/<int:eid>/questions/<qtype>", methods=["POST"])
def exam_save_type_questions(eid, qtype):
    """网页选题底部按钮：保存当前题型的全部勾选状态。"""
    ex = db.get_exam(eid)
    if not ex:
        flash("考试不存在", "err")
        return redirect(url_for("index"))
    if qtype not in THEORY_TYPES:
        flash("不支持的题型", "err")
        return redirect(url_for("exam_edit", eid=eid, tab="basic"))
    if _paper_locked(ex):
        return redirect(url_for("exam_edit", eid=eid, tab=qtype))
    qids, error = _submitted_type_question_ids(qtype)
    if error:
        flash(error, "err")
    else:
        _replace_exam_type_questions(eid, qtype, qids)
        flash(f"已保存{QTYPE_CN.get(qtype, qtype)}选题，共 {len(qids)} 题")
    return redirect(url_for("exam_edit", eid=eid, tab=qtype))


@app.route("/exam/<int:eid>/delq/<int:qid>", methods=["POST"])
def exam_delq(eid, qid):
    ex = db.get_exam(eid)
    if not ex:
        flash("考试不存在", "err")
        return redirect(url_for("index"))
    if _paper_locked(ex):
        return redirect(url_for("exam_edit", eid=eid, tab="basic"))
    db.invalidate()          # 试卷变了，清缓存
    c = db.conn()
    c.execute("DELETE FROM exam_questions WHERE exam_id=? AND question_id=?", (eid, qid))
    c.commit()
    c.close()
    tab = request.form.get("tab", "")
    return redirect(url_for("exam_edit", eid=eid, tab=tab))


@app.route("/exam/<int:eid>/addq", methods=["POST"])
def exam_addq(eid):
    ex = db.get_exam(eid)
    if not ex:
        flash("考试不存在", "err")
        return redirect(url_for("index"))
    if _paper_locked(ex):
        return redirect(url_for("exam_edit", eid=eid, tab="basic"))
    db.invalidate()          # 试卷变了，清缓存
    # 勾选提交上来的题号，非法值直接跳过（以前一个非法值就 500）
    qids = [q for q in (_to_int(x, 0) for x in request.form.getlist("qids")) if q]
    tab = request.form.get("tab", "")
    have = db.exam_question_ids(eid)
    added = 0
    c = db.conn()
    for qid in qids:
        if qid not in have:
            c.execute("INSERT OR IGNORE INTO exam_questions(exam_id,question_id,qorder) VALUES(?,?,?)",
                      (eid, qid, len(have)))
            added += 1
            have.append(qid)
    c.commit()
    c.close()
    if added:
        flash(f"已加入 {added} 题")
    else:
        flash("没有新题目加入（可能都已经在试卷里了）", "err")
    return redirect(url_for("exam_edit", eid=eid, tab=tab))


@app.route("/exam/<int:eid>/quickset", methods=["POST"])
def exam_quickset(eid):
    """
    配置页的勾选框「点一下就存」，不用再按「保存全部设置」：
    题型开关、操作题类型、打字子任务开关。
    口径和「保存全部设置」完全一致；考试进行中拒绝修改。
    """
    ex = db.get_exam(eid)
    if not ex:
        return {"ok": False, "msg": "考试不存在"}
    if ex["status"] in ("running", "paused", "stopped"):
        return {"ok": False, "msg": "考试进行中或已结束，配置已锁定"}
    data = request.get_json(silent=True) or {}
    types = [t for t in (data.get("types") or []) if t in ALL_TYPES]
    if not types:
        types = list(THEORY_TYPES)          # 全不勾 = 理论题全开（同保存逻辑）
    db.set_question_types(eid, types)
    pts = [p for p in (data.get("practice_types") or [])
           if p in ("word", "excel", "ppt")]
    if "practice" in types:
        db.update_exam(eid, {"duration": ex["duration"],
                            "theory_duration": ex["duration"],
                            "etype": json.dumps(pts or ["excel"],
                                                ensure_ascii=False)})
    else:
        db.update_exam(eid, {"duration": ex["duration"],
                            "theory_duration": ex["duration"],
                            "etype": json.dumps(pts, ensure_ascii=False)
                             if pts else ""})
    en = data.get("typing_enable")
    if isinstance(en, dict) and en:
        cfg = _typing_config_of(ex)
        for task, on in en.items():
            if task in cfg:
                cfg[task]["enabled"] = bool(on)
        db.set_typing_config(eid, cfg)
    db.invalidate()
    return {"ok": True, "types": types}


@app.route("/exam/<int:eid>/pinyin", methods=["POST"])
def exam_pinyin(eid):
    """
    老师标注姓名拼音（打名字测试用）：一行一个「字或词|拼音」。
    写进 题库/拼音标注.json，全局生效；返回最新名单方便页面就地刷新。
    """
    if not db.get_exam(eid):
        return {"ok": False, "msg": "考试不存在"}
    data = request.get_json(silent=True) or {}
    n = save_pinyin_overrides(data.get("text", ""))
    return {"ok": True, "count": n, "roster": exam_roster(eid)}


def _cats_by_type(allq):
    """每种题型**实际有题**的分类（分类下拉只列有题的，免得选了个空分类）"""
    out = {}
    for q in allq or []:
        cat = (q["category"] or "").strip()
        if not cat:
            continue
        out.setdefault(q["qtype"], set()).add(cat)
    return {k: sorted(v) for k, v in out.items()}


@app.route("/exam/<int:eid>/random", methods=["POST"])
def exam_random(eid):
    """随机抽题：先保存页面上的待保存勾选，再立即加入随机题。"""
    ex = db.get_exam(eid)
    if not ex:
        flash("考试不存在", "err")
        return redirect(url_for("index"))
    if _paper_locked(ex):
        return redirect(url_for("exam_edit", eid=eid, tab="basic"))
    cat = request.form.get("category", "")
    qtype = request.form.get("qtype", "")
    tab = request.form.get("tab", qtype)
    if qtype in THEORY_TYPES:
        # 勾选后还没点「保存选题」就随机抽题，也不能丢掉这些待保存题。
        if request.form.get("selection_present") == "1":
            qids, error = _submitted_type_question_ids(qtype)
            if error:
                flash(error, "err")
                return redirect(url_for("exam_edit", eid=eid, tab=tab))
            _replace_exam_type_questions(eid, qtype, qids)
        elif request.form.get("replace") == "1":
            # 兼容旧调用：替换时也只清当前题型，不清其他题型。
            _replace_exam_type_questions(eid, qtype, [])
    # 抽题数量夹在 1~200：填 0 抽不到、填 10 万会把卡住
    try:
        n = int(float(request.form.get("n", 5)))
    except (TypeError, ValueError):
        n = 5
    n = max(1, min(n, 200))
    picked = db.random_pick(eid, cat, n, qtype=qtype)
    db.invalidate()
    if picked:
        scope = " / ".join(x for x in (cat, QTYPE_CN.get(qtype, qtype)) if x)
        flash(f"已随机抽取 {len(picked)} 题" + (f"（{scope}）" if scope else ""))
    else:
        flash("没有符合筛选条件的题目", "err")
    return redirect(url_for("exam_edit", eid=eid, tab=tab))


@app.route("/exam/<int:eid>/material/del/<path:name>", methods=["POST"])
def exam_del_material(eid, name):
    ex = db.get_exam(eid)
    if not ex:
        flash("考试不存在", "err")
        return redirect(url_for("index"))
    if _paper_locked(ex):
        return redirect(url_for("exam_edit", eid=eid, tab="practice"))
    files = json.loads(ex["materials"] or "[]")
    files = [f for f in files if f != name]
    c = db.conn()
    c.execute("UPDATE exams SET materials=? WHERE id=?", (json.dumps(files, ensure_ascii=False), eid))
    c.commit()
    c.close()
    # 磁盘上的文件一并删掉，不然 materials/ 会越堆越多；
    # 但删之前先确认没有别的考试/实操题还在用这份素材
    p = os.path.join(MATERIAL_DIR, os.path.basename(name))
    try:
        if os.path.isfile(p):
            used = db.material_used_by_others(name, exclude_exam=eid)
            if used:
                flash(f"记录已从本场考试移除；素材文件保留（{'、'.join(used[:3])} 仍在引用）")
            else:
                os.remove(p)
    except OSError as e:
        flash(f"记录已删除，但文件没删掉：{e}", "err")
    return redirect(url_for("exam_edit", eid=eid))


# ---------------- 考试控制 ----------------
def _last_klass():
    """上次开考填的班级（表单预填 —— 机房一般同一个班连着考好几场）"""
    try:
        for e in db.list_exams():
            k = (e.get("klass") or "").strip()
            if k:
                return k
    except Exception:
        pass
    return ""


@app.route("/exam/<int:eid>/start", methods=["GET", "POST"])
def exam_start(eid):
    """
    开始考试。GET 先让老师填班级，POST 才真的开考。

    以前这里什么都不检查：空卷也能开、重复点会把倒计时重置、
    还能同时开两场（学生端只认最后一场，另一场静默失效）。
    现在前置校验一个不少，另外**班级必填** —— 答卷要按班级归档。
    """
    ex = db.get_exam(eid)
    if not ex:
        flash("考试不存在", "err")
        return redirect(url_for("index"))

    if ex["status"] == "stopped":
        flash("已结束考试为只读，不能重新开考", "err")
        return redirect(url_for("index"))
    if ex["status"] in ("running", "paused"):
        flash(f"「{ex['title']}」已经在进行中了。"
              f"想重来请先点「结束考试」再重新开始。", "err")
        return redirect(url_for("index"))

    other = db.active_exam()
    if other and other["id"] != eid:
        flash(f"「{other['title']}」还在进行中，"
              f"请先结束它 —— 同时只能有一场考试", "err")
        return redirect(url_for("index"))

    ok, err, warn = _exam_ready(eid)
    if not ok:
        flash(err, "err")
        return redirect(url_for("exam_edit", eid=eid))

    if request.method == "GET":
        theory_n, prac_n = _exam_content(ex)
        return render_template("exam_start.html", exam=ex,
                               theory_n=theory_n, prac_n=prac_n,
                               warn=warn, klass=_last_klass(),
                               type_name=TYPE_NAME)

    klass = (request.form.get("klass") or "").strip()
    if not klass:
        flash("请填写班级（考场编号）—— 学生答卷会按班级归档", "err")
        return redirect(url_for("exam_start", eid=eid))

    db.set_exam_klass(eid, klass)
    db.set_exam_status(eid, "running")
    db.set_phase(eid, "theory")
    db.bind_exam(eid)
    db.push_command("all", "start_exam",
                    {"exam_id": eid, "phase": "theory"})
    db.log("teacher", "info",
           f"开始考试 #{eid}「{ex['title']}」编号={ex.get('code') or '-'} "
           f"班级={klass}，阶段=理论题")
    theory_n, prac_n = _exam_content(ex)
    flash(f"考试已开始（班级：{klass}）：理论题 {theory_n} 题"
          + (f"、实操题 {prac_n} 道" if prac_n else "")
          + "，学生端已收到通知")
    for w in warn:
        flash("注意：" + w, "err")
    return redirect(url_for("index"))


@app.route("/exam/<int:eid>/pause", methods=["POST"])
def exam_pause(eid):
    """暂停考试：学生端停止倒计时，不能交卷"""
    ex = db.get_exam(eid)
    if not ex:
        flash("考试不存在", "err")
        return redirect(url_for("index"))
    # 对没开始的考试"暂停"会让学生端凭空看到一场已暂停的考试
    if ex["status"] != "running":
        flash("只有正在进行中的考试才能暂停", "err")
        return redirect(url_for("index"))
    db.set_exam_status(eid, "paused")
    db.push_command("all", "pause", {"exam_id": eid})
    db.log("teacher", "info", f"暂停考试 #{eid}")
    flash("考试已暂停，学生端停止计时")
    return redirect(url_for("index"))


@app.route("/exam/<int:eid>/resume", methods=["POST"])
def exam_resume(eid):
    """恢复考试：继续倒计时"""
    ex = db.get_exam(eid)
    if not ex:
        flash("考试不存在", "err")
        return redirect(url_for("index"))
    if ex["status"] != "paused":
        flash("这场考试当前不是暂停状态，不需要恢复", "err")
        return redirect(url_for("index"))
    db.set_exam_status(eid, "running")
    db.push_command("all", "resume", {"exam_id": eid})
    db.log("teacher", "info", f"恢复考试 #{eid}")
    flash("考试已恢复")
    return redirect(url_for("index"))


@app.route("/exam/<int:eid>/phase/<phase>", methods=["POST"])
def exam_set_phase(eid, phase):
    ex = db.get_exam(eid)
    if not ex:
        flash("考试不存在", "err")
        return redirect(url_for("index"))
    if phase not in ("theory", "practice", "ended"):
        flash("未知阶段", "err")
        return redirect(url_for("index"))
    # 没开始的考试切阶段没有意义；已经结束的再切会造成状态错位
    if ex["status"] not in ("running", "paused"):
        flash("考试还没开始（或已经结束），不能切换阶段", "err")
        return redirect(url_for("index"))
    # 没勾操作题（含打字）的考试不能切到实操阶段 ——
    # 切过去学生会卡在"已进入实操"，理论题也交不上了
    if phase == "practice" and not ({"practice", "typing"} & set(_selected_types(ex))):
        flash("本场考试没有实操/打字题，不能切到实操阶段", "err")
        return redirect(url_for("index"))
    if phase == "ended":
        # 走和「结束考试」同一条路，避免出现 status=running 但 phase=ended
        return exam_stop(eid)
    db.set_phase(eid, phase)
    payload = {"exam_id": eid, "phase": phase}
    if phase == "practice":
        payload["etype"] = ex["etype"]
        payload["open_wps"] = True
    db.push_command("all", "phase", payload)
    db.log("teacher", "info", f"切换阶段 -> {phase}")
    flash("已切到" + ("实操阶段" if phase == "practice" else "理论题阶段"))
    return redirect(url_for("index"))


@app.route("/exam/<int:eid>/stop", methods=["POST"])
def exam_stop(eid):
    """
    结束考试：强制所有人交卷 → 通知学生端关闭客户端。

    ?clear_login=1  同时清掉学生端保存的登录信息（姓名/座位号），
                    下一场换学生坐这台机器必须重新填；
    ?clear_login=0  保留登录信息，下一场开机直接进待考界面。
    """
    ex = db.get_exam(eid)
    if not ex:
        flash("考试不存在", "err")
        return redirect(url_for("index"))
    if ex["status"] not in ("running", "paused"):
        flash("这场考试没有在进行中，不需要结束", "err")
        return redirect(url_for("index"))
    clear_login = request.form.get("clear_login") == "1"
    n_online = db.stats(eid)["online"]
    db.snapshot_exam_students(eid)
    db.set_exam_status(eid, "stopped")
    db.set_phase(eid, "ended")
    # 新客户端：交卷 → 提示 → 关闭客户端（按选择决定是否清登录信息）
    db.push_command("all", "force_exit",
                    {"exam_id": eid, "clear_login": clear_login})
    # 老客户端只认 stop：保留一条，保证老版本也至少能显示"考试已结束"
    db.push_command("all", "stop", {"exam_id": eid})
    db.log("teacher", "info",
           f"结束考试 #{eid}「{ex['title']}」（强制交卷并关闭学生端"
           f"{'，清除登录信息' if clear_login else '，保留登录信息'}）")
    flash(f"考试已结束：已通知 {n_online} 台在线学生机交卷并关闭客户端"
          + ("，并清除了学生端的登录信息" if clear_login
             else "，学生端登录信息保留（下一场开机直接待考）"))
    return redirect(url_for("index"))


# ---------------- 打字模块（小学）实时看板 ----------------
@app.route("/exam/<int:eid>/typing")
def exam_typing_board(eid):
    """打字实时统计页：按速度 / 正确率展示，不打分"""
    ex = db.get_exam(eid)
    if not ex:
        flash("考试不存在", "err")
        return redirect(url_for("index"))
    return render_template("typing.html", exam=ex,
                           task_order=TYPING_TASK_ORDER,
                           task_name=TYPING_TASK_NAME)


@app.route("/exam/<int:eid>/typing.json")
def exam_typing_board_json(eid):
    """看板 3 秒轮询的数据"""
    ex = db.get_exam(eid)
    if not ex:
        return jsonify({"ok": False, "msg": "考试不存在"}), 404
    # 只展示本场启用的子任务列
    cfg = _typing_config_of(ex)
    enabled = [t for t in TYPING_TASK_ORDER if cfg.get(t, {}).get("enabled", True)]
    return jsonify({
        "ok": True,
        "exam": {"id": ex["id"], "title": ex["title"], "status": ex["status"],
                 "remaining": db.phase_remaining(eid)},
        "task_order": enabled,
        "task_name": TYPING_TASK_NAME,
        "rows": db.typing_board(eid),
    })


@app.route("/exam/<int:eid>/typing/name-words")
def exam_name_words(eid):
    """
    把本场学生姓名生成「姓名|拼音」词表（打名字测试用）。
    顺手把「拼音表没收录的字」和「多音字」挑出来给老师看 ——
    学生名字读错很难看，宁可让老师手工确认一下。
    """
    ex = db.get_exam(eid)
    if not ex:
        return jsonify({"ok": False, "msg": "考试不存在"}), 404
    text, unknown, duo = student_name_lines(eid)
    return jsonify({"ok": True, "text": text, "unknown": unknown,
                    "duoyin": duo,
                    "count": len([x for x in text.splitlines() if x])})


# ---------------- 实操题库（老师挑本堂课考什么）----------------
@app.route("/practice")
def practice_bank():
    """
    实操题库：列出所有 WPS 操作题，老师按 类型/分类/难度 筛选，
    选一道装进某场考试（?to=<eid>），或直接用它新建一场考试。
    """
    etype = request.args.get("etype", "")
    cat = request.args.get("cat", "")
    level = request.args.get("level", "")
    sort = request.args.get("sort", "")
    to_eid = request.args.get("to", "")
    items = db.list_practice(etype=etype or None,
                             category=cat or None, level=level or None)
    # 从考试配置挑题时，只显示这场考试勾了的操作题类型 ——
    # 勾了「WPS 表格」就只能装表格题，避免类型勾选变成摆设
    allow_types = []
    if to_eid.isdigit():
        exx = db.get_exam(int(to_eid))
        allow_types = _practice_types_of(exx) if exx else []
        if allow_types:
            items = [it for it in items
                     if (it.get("etype") or "") in allow_types]
    # 难度不再当筛选条件，改成排序（入门 → 基础 → 进阶 或反过来）
    if sort in ("level", "-level"):
        rank = {"入门": -1, "基础": 0, "进阶": 1, "挑战": 2}
        items.sort(key=lambda it: rank.get(it.get("level") or "", 9),
                   reverse=(sort == "-level"))
    cats, levels = db.practice_filters()
    return render_template("practice.html", items=items, cats=cats,
                           levels=levels, etype=etype, cat=cat, level=level,
                           sort=sort, to_eid=to_eid, type_name=TYPE_NAME,
                           allow_types="、".join(TYPE_NAME.get(t, t)
                                                 for t in allow_types),
                           exam=db.get_exam(int(to_eid)) if to_eid.isdigit() else None)


@app.route("/practice/<int:pid>/use", methods=["POST"])
def practice_use(pid):
    """
    把这道题装进考试（**追加**，一场可以装多道）；
    没指定考试就用它新建一场。
    """
    to_eid = request.args.get("to", "")
    if to_eid.isdigit():
        eid = int(to_eid)
        ex = db.get_exam(eid)
        if not ex:
            flash("考试不存在", "err")
            return redirect(url_for("practice_bank"))
        if _paper_locked(ex):
            return redirect(url_for("practice_bank", to=to_eid))
        had_type = "practice" in _selected_types(ex)
        # 类型联动：只让装这场考试勾了的操作题类型；
        # 一个类型都没勾 → 装的时候自动把该题类型勾上（顺手帮老师配好）
        pts = _practice_types_of(ex)
        task = db.get_practice(pid)
        ttype = (task or {}).get("etype") or ""
        if pts and ttype and ttype not in pts:
            flash(f"这场考试没勾「{TYPE_NAME.get(ttype, ttype)}」，"
                  "先回「编辑考试 → 操作题」勾上这个类型再装", "err")
            return redirect(url_for("practice_bank", to=to_eid))
        if not pts and ttype:
            db.update_exam(eid, {"etype": json.dumps([ttype], ensure_ascii=False)})
        if db.add_task_to_exam(eid, pid):
            n = len(db.exam_tasks(eid))
            # 题型里没有「操作题」的话，add_task_to_exam 已经自动补上了
            flash(f"已装进这场考试，现在共 {n} 道操作题"
                  + ("" if had_type else "（已自动勾上「WPS 操作题」题型）"))
            return redirect(url_for("exam_edit", eid=eid, tab="practice"))
        flash("这道题不存在", "err")
        return redirect(url_for("practice_bank"))
    eid = db.new_exam_from_practice(pid, duration=40, theory_duration=40)
    if not eid:
        flash("这道题不存在", "err")
        return redirect(url_for("practice_bank"))
    db.update_exam(eid, {"save_as": ""})
    flash(f"已用这道题新建考试 #{eid}，可以继续去实操题库加题")
    return redirect(url_for("exam_edit", eid=eid, tab="practice"))


@app.route("/exam/<int:eid>/deltask/<int:pid>", methods=["POST"])
def exam_del_task(eid, pid):
    ex = db.get_exam(eid)
    if not ex:
        flash("考试不存在", "err")
        return redirect(url_for("index"))
    if _paper_locked(ex):
        target_tab = "overview" if request.form.get("tab") == "overview" else "basic"
        return redirect(url_for("exam_edit", eid=eid, tab=target_tab))
    db.remove_task_from_exam(eid, pid)
    flash("已移除这道操作题")
    target_tab = "overview" if request.form.get("tab") == "overview" else "practice"
    return redirect(url_for("exam_edit", eid=eid, tab=target_tab))


@app.route("/practice/<int:pid>/del", methods=["POST"])
def practice_del(pid):
    db.delete_practice(pid)
    flash("已从实操题库删除")
    return redirect(url_for("practice_bank"))


# ---------------- 题库 ----------------
@app.route("/questions")
def questions():
    cat = request.args.get("cat", "")
    kw = request.args.get("kw", "")
    qtype = request.args.get("qtype", "")
    diff = request.args.get("difficulty", "")
    src = request.args.get("source", "")
    qs = db.list_questions(category=cat or None, keyword=kw or None,
                           qtype=qtype or None,
                           difficulty=diff or None, source=src or None)
    return render_template("questions.html", questions=qs,
                           categories=db.list_categories(),
                           cat=cat, kw=kw, qtype_cn=QTYPE_CN,
                           current_cat=cat, current_type=qtype,
                           current_diff=diff, current_source=src,
                           difficulties=db.DIFFICULTIES,
                           sources=db.list_field_values("source"),
                           difficulty_counts=db.question_meta_counts(),
                           qimgs=_qimg_list(),
                           type_name=QTYPE_CN)


@app.route("/questions/tidy", methods=["POST"])
def questions_tidy():
    """一键整理题库分类：给没填难度/来源的题补上（幂等）"""
    n_d, n_s = db.tidy_question_meta()
    if n_d or n_s:
        flash(f"已整理题库：补全难度 {n_d} 题、来源 {n_s} 题")
    else:
        flash("题库分类已经整理过了，无需再补")
    return redirect(url_for("questions"))


QUESTION_TYPES = ("single", "multi", "judge", "blank", "code", "drag")


def _strip_opt_prefix(text):
    """
    去掉选项前面已有的 "A. " / "B、" 前缀。

    选项在库里是带着字母存好的（"A. 甲"），编辑时表单又原样带回来，
    以前会再拼一次前缀，改一次就变成 "A. A. 甲"，改两次变三个。
    """
    s = (text or "").strip()
    if len(s) >= 2 and s[0] in "ABCDEFGHabcdefgh" and s[1] in ".、．)）":
        s = s[2:].strip()
    return s


def _parse_question_form():
    """
    解析题库表单 + 内容校验，返回 (数据 dict, 错误列表)。

    为什么要校验：老师手填的东西一旦入库就是"脏题"，
    学生答对了也判错（比如单选答案写了 E，但只有 A-D 四个选项），
    而且考试能正常开始、成绩出来全班都错，很难查。
    """
    qtype = request.form.get("qtype", "single")
    if qtype not in QUESTION_TYPES:
        qtype = "single"

    stem = (request.form.get("stem") or "").strip()
    answer = (request.form.get("answer") or "").strip()
    score = _to_score(request.form.get("score"), 2)

    opts = [_strip_opt_prefix(request.form.get(f"opt{i}")) for i in range(4)]
    opts = [o for o in opts if o]
    if qtype == "judge" and not opts:
        opts = ["正确", "错误"]

    code = request.form.get("code") or ""
    lang = (request.form.get("language") or "python").strip()
    stdin_data = request.form.get("stdin_data") or ""
    # 期望输出：填了就启用「跑一遍看输出」判分（见 server/coderun.py）
    expected_output = request.form.get("expected_output") or ""
    category = (request.form.get("category_custom") or "").strip() \
        if request.form.get("category") == "__custom__" \
        else (request.form.get("category") or "").strip()
    # 题库分类：难度（基础/进阶/挑战）+ 来源（自创题 / 2024年雅安会考 …）
    difficulty = (request.form.get("difficulty") or "").strip()
    source = (request.form.get("source") or "").strip()

    errs = []
    if not stem:
        errs.append("题干不能为空")
    if score <= 0:
        errs.append("分值必须大于 0")

    if qtype in ("single", "multi"):
        if len(opts) < 2:
            errs.append("选择题至少要填 2 个选项")
        letters = set("ABCD"[:len(opts)])
        picked = {ch for ch in answer.upper() if ch in "ABCDEFGH"}
        if not picked:
            errs.append("请填写正确答案（选项字母，如 A 或 ABC）")
        elif picked - letters:
            errs.append(f"答案里的 " + "、".join(sorted(picked - letters)) +
                        f" 超出了选项范围（当前只有 "
                        f"{'、'.join(sorted(letters)) or '无'}）")
        if qtype == "single" and len(picked) > 1:
            errs.append("单选题只能有 1 个答案，多选请改题型")
        if qtype == "multi" and 0 < len(picked) < 2:
            errs.append("多选题至少要 2 个答案，单选请改题型")
        answer = "".join(sorted(picked)) or answer.upper()

    elif qtype == "judge":
        if db.norm_judge(answer) not in ("T", "F"):
            errs.append("判断题答案只能是「正确 / 错误」"
                        "（对/错、T/F、√/× 也认）")

    elif qtype == "blank":
        if not answer:
            errs.append("填空题答案不能为空")

    elif qtype == "code":
        n_blank = db.count_blanks(code)
        n_ans = len([x for x in answer.split("||") if x.strip()])
        if n_blank == 0:
            errs.append("程序填空：代码里没有 ____ 空位（至少 4 个下划线）")
        elif n_ans != n_blank:
            errs.append(f"程序填空：空位 {n_blank} 个，但答案有 {n_ans} 段，"
                        f"请用 || 分隔每个空的答案")

    # 拖拽题：选项 = 可拖拽的项（一行一个），slots = 槽位（一行一个）
    elif qtype == "drag":
        opts = [_strip_opt_prefix(x) for x in
                (request.form.get("drag_items") or "").splitlines()]
        opts = [o.strip() for o in opts if o.strip()]
        slots = [s.strip() for s in
                 (request.form.get("drag_slots") or "").splitlines() if s.strip()]
        if len(opts) < 2:
            errs.append("拖拽题至少要 2 个拖拽项（一行一个）")
        if len(slots) < 2:
            errs.append("拖拽题至少要 2 个槽位（一行一个）")
        if len(opts) > 8:
            errs.append("拖拽项最多 8 个（学生端一屏放不下）")
        # 答案形如 1:A|2:B|3:C；也允许老师写成 A|B|C（按槽位顺序）
        pairs, plain = [], []
        for part in re.split(r"[|｜，,;；\n]+", answer):
            part = part.strip()
            if not part:
                continue
            m = re.match(r"^(\d+)\s*[:：=]\s*([A-Za-z])$", part)
            if m:
                pairs.append(f"{m.group(1)}:{m.group(2).upper()}")
            elif re.match(r"^[A-Za-z]$", part):
                plain.append(part.upper())
        if not pairs and plain:
            pairs = [f"{i + 1}:{ch}" for i, ch in enumerate(plain)]
        if len(pairs) != len(slots):
            errs.append(f"拖拽题：{len(slots)} 个槽位就要 {len(slots)} 个答案，"
                        f"按槽位顺序写，如 1:A|2:B|3:C")
        else:
            used = {p.split(":")[1] for p in pairs}
            allowed = {chr(ord("A") + i) for i in range(len(opts))}
            bad = used - allowed
            if bad:
                errs.append("答案里的 " + "、".join(sorted(bad)) +
                            f" 超出拖拽项范围（只有 "
                            f"{'、'.join(sorted(allowed))}）")
            answer = "|".join(pairs)
        layout = (request.form.get("drag_layout") or "grid").strip().lower()
        if layout not in ("grid", "horizontal"):
            layout = "grid"
        drag_slots_json = json.dumps({"slots": slots, "layout": layout}, ensure_ascii=False)

    data = {
        "qtype": qtype,
        "stem": stem,
        "options": ([f"{'ABCD'[i]}. {o}" for i, o in enumerate(opts)]
                    if qtype != "drag" else list(opts)),
        "answer": answer,
        "score": score,
        "category": category,
        "difficulty": difficulty,
        "source": source,
        "language": lang if qtype == "code" else "",
        "code": code if qtype == "code" else "",
        "stdin_data": stdin_data if qtype == "code" else "",
        "expected_output": expected_output if qtype == "code" else "",
        "payload": drag_slots_json if qtype == "drag" else "",
    }
    return data, errs


def _flash_question_errors(errs):
    for e in errs:
        flash("题目未保存：" + e, "err")


def _back_to_questions(qid=None):
    """
    回到题库页，保持老师所在的分类/题型筛选，并定位到刚改的那道题。
    （以前一律跳回"全部"，改一道程序填空就被踢出当前分类，很烦）
    """
    args = {}
    for key, form_key in (("cat", "ret_cat"), ("qtype", "ret_qtype")):
        v = (request.form.get(form_key) or "").strip()
        if v:
            args[key] = v
    return url_for("questions", **args) + (f"#q{qid}" if qid else "")


@app.route("/question/add", methods=["POST"])
def question_add():
    data, errs = _parse_question_form()
    if errs:
        _flash_question_errors(errs)
        return redirect(_back_to_questions())
    db.invalidate()          # 题库变了，清缓存
    db.add_question(data)
    flash(f"已添加：{data['stem'][:20]}")
    return redirect(_back_to_questions())


@app.route("/question/<int:qid>/edit", methods=["POST"])
def question_edit(qid):
    if not db.get_question(qid):
        flash("题目不存在（可能已被删除）", "err")
        return redirect(_back_to_questions())
    data, errs = _parse_question_form()
    if errs:
        _flash_question_errors(errs)
        return redirect(_back_to_questions(qid))
    db.invalidate()          # 题目改了，清缓存
    # language/code/stdin_data 一定要带上 ——
    # update_question 的策略是"传了才更新"，不传等于编辑程序填空题时改了代码不生效
    db.update_question(qid, data)
    flash(f"已保存第 {qid} 题的修改")
    return redirect(_back_to_questions(qid))


@app.route("/question/<int:qid>/del", methods=["POST"])
def question_del(qid):
    db.invalidate()          # 题库变了，清缓存
    db.delete_question(qid)
    return redirect(url_for("questions"))


@app.route("/questions/import", methods=["POST"])
def questions_import():
    db.invalidate()          # 题库变了，清缓存
    f = request.files.get("file")
    if not f or not f.filename:
        flash("请选择文件")
        return redirect(url_for("questions"))
    name = (f.filename or "").lower()
    tmp = os.path.join(UPLOAD_DIR, "_import_tmp_" + uuid.uuid4().hex +
                       (".json" if name.endswith(".json") else ".xlsx"))
    f.save(tmp)
    try:
        if name.endswith(".json"):
            # JSON 导入（题库备份文件）
            with open(tmp, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if not isinstance(data, list):
                raise ValueError("JSON 格式应为题目数组")
            items = []
            for d in data:
                qt = str(d.get("qtype", "")).strip().lower()
                if qt not in ("single", "multi", "judge", "blank", "code",
                              "drag"):
                    continue
                if not d.get("stem"):
                    continue
                items.append({
                    "qtype": qt, "stem": d["stem"],
                    "options": d.get("options") or [],
                    "answer": str(d.get("answer", "")).strip(),
                    "score": float(d.get("score", 2)),
                    "category": d.get("category", ""),
                    "language": d.get("language", ""),
                    "code": d.get("code", ""),
                    "stdin_data": d.get("stdin_data", ""),
                    "expected_output": d.get("expected_output", ""),
                    "difficulty": d.get("difficulty", ""),
                    "source": d.get("source", ""),
                    "slots": d.get("slots") or [],
                })
            ok, skip = db.import_questions(items)
            flash(f"导入完成：新增 {ok} 题，跳过 {skip} 题")
        else:
            import import_excel
            items, skipped = import_excel.parse_excel(tmp)
            ok, skip = db.import_questions(items)
            msg = f"导入完成：新增 {ok} 题，跳过 {skip} 题"
            if skipped:
                msg += "（前几条：" + "；".join(
                    f"第{r}行 {why}" for r, why in skipped[:3]) + "）"
            flash(msg)
    except Exception as e:
        # 别把内部异常详情（路径/库版本等）直接回显给浏览器，记日志即可
        app.logger.exception("题库导入失败: %s", e)
        flash("导入失败，请检查文件格式，或查看服务器日志", "err")
    finally:
        if os.path.isfile(tmp):
            os.remove(tmp)
    return redirect(url_for("questions"))


@app.route("/questions/template")
def questions_template():
    import import_excel
    p = os.path.join(EXAM_DIR, "题库模板.xlsx")
    import_excel.make_template(p)
    return send_file(p, as_attachment=True, download_name="题库模板.xlsx")


# ---------------- 成绩 ----------------
@app.route("/scores/<int:eid>")
def scores(eid):
    ex = db.get_exam(eid)
    if not ex:
        return "考试不存在", 404
    rows = db.full_report(eid)

    # 统计（后端算好，模板里不做复杂运算）
    cs = db.class_summary(eid)
    summary = {
        "count": cs["count"],
        "submitted": cs["submitted"],
        "avg": cs["avg"],
        "max": cs["max"],
        "min": cs["min"],
        "avg_pct": cs["avg_pct"],
        "pass_rate": cs["pass_rate"],
        "good_rate": cs["good_rate"],
        "pass_n": cs["pass_n"],
        "good_n": cs["good_n"],
        "full_total": cs["full_total"],
    }
    return render_template("scores.html", exam=ex, rows=rows,
                           summary=summary, type_name=TYPE_NAME,
                           theory_full=db.exam_theory_full(eid),
                           # 图表数据（ECharts 直接读）
                           dist=db.score_distribution(eid),
                           qrates=db.question_rates(eid),
                           crates=db.check_rates(eid),
                           qtype_cn=QTYPE_CN)


def _stats_payload(eid):
    """
    图表数据（ECharts 用）。
    ⚠️ 路由不能放在 /api/ 下 —— /api/ 是学生端免登录白名单，
    成绩分析属于老师数据，必须走需要登录的路径。
    """
    return {
        "ok": True,
        "exam_id": eid,
        "summary": db.class_summary(eid),
        "dist": db.score_distribution(eid),
        "questions": db.question_rates(eid),
        "checks": db.check_rates(eid),
        "live": db.stats(eid),
        "status_counts": db.status_counts(eid),
        "theory_full": db.exam_theory_full(eid),
    }


@app.route("/exam/<int:eid>/stats.json")
def exam_stats_json(eid):
    if not db.get_exam(eid):
        return jsonify({"ok": False, "msg": "考试不存在"}), 404
    return jsonify(_stats_payload(eid))


@app.route("/stats/active.json")
def active_stats_json():
    """控制台/大屏自动刷新用：当前进行中的考试"""
    ex = db.active_exam() or db.last_stopped_exam()
    if not ex:
        return jsonify({"ok": False, "msg": "没有考试"})
    return jsonify(_stats_payload(ex["id"]))


@app.route("/scores/<int:eid>/csv")
def scores_csv(eid):
    out = os.path.join(EXAM_DIR, f"成绩_exam{eid}.csv")
    db.export_full_csv(eid, out)
    return send_file(out, as_attachment=True,
                     download_name=f"成绩_exam{eid}.csv")


@app.route("/scores/<int:eid>/xlsx")
def scores_xlsx(eid):
    """
    导出这场考试的**全部明细**（Excel，4 张表）：
      ① 成绩汇总   每人理论/实操/总分、交卷状态、文件名
      ② 理论题逐题  每题得分 + 对错 + 学生作答
      ③ 操作题逐题  每道操作题得分/满分、是否缺卷、文件名
      ④ 打字统计    每个子任务的完成情况/正确率/速度/用时/综合分
    """
    ex = db.get_exam(eid)
    if not ex:
        flash("考试不存在", "err")
        return redirect(url_for("index"))
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.utils import get_column_letter

    def _style_head(ws, ncol):
        for i in range(1, ncol + 1):
            c = ws.cell(row=1, column=i)
            c.font = Font(bold=True, color="FFFFFF")
            c.fill = PatternFill("solid", fgColor="4472C4")
            c.alignment = Alignment(horizontal="center", vertical="center")
        ws.freeze_panes = "A2"

    def _autosize(ws, widths):
        for i, w in enumerate(widths, 1):
            ws.column_dimensions[get_column_letter(i)].width = w

    rows = db.full_report(eid)
    qs = db.get_exam_questions(eid)
    tasks = db.exam_tasks(eid)
    subs = db.list_submissions(eid)
    typing = db.typing_board(eid)

    wb = Workbook()

    # ---------- ① 成绩汇总 ----------
    ws = wb.active
    ws.title = "成绩汇总"
    ws.append(["座位号", "姓名", "机器标识", "IP", "理论题", "理论满分",
               "实操题", "实操满分", "总分", "已交卷", "文件名"])
    for r in rows:
        ws.append([r.get("seat", ""), r.get("name", ""), r["hostname"],
                   r.get("ip", ""), r["theory"], r["theory_full"],
                   r["practice"] if r["practice"] is not None else "",
                   r["practice_full"], r["total"],
                   "是" if r["submitted"] else "否",
                   r.get("filename") or ""])
    _style_head(ws, 11)
    _autosize(ws, [10, 14, 20, 14, 9, 9, 9, 9, 9, 9, 34])

    # ---------- ② 理论题逐题 ----------
    ws2 = wb.create_sheet("理论题逐题")
    head = ["座位号", "姓名", "机器标识"]
    for i, q in enumerate(qs, 1):
        head.append(f"第{i}题({QTYPE_CN.get(q['qtype'], q['qtype'])}，"
                    f"{q['score']}分)")
    head += ["理论得分", "理论满分"]
    ws2.append(head)
    for r in rows:
        pid = r.get("participant_id") or 0
        ans = db.theory_answers_of(eid, r["hostname"], pid)
        line = [r.get("seat", ""), r.get("name", ""), r["hostname"]]
        got = 0.0
        for q in qs:
            a = ans.get(q["id"])
            if a is None:
                line.append("未答")
                continue
            correct, sc = db.judge(q, a)
            got += float(sc or 0)
            line.append(("√ " if correct else "× ") + str(a))
        line += [round(got, 2), r["theory_full"]]
        ws2.append(line)
    _style_head(ws2, len(head))
    _autosize(ws2, [10, 14, 20] + [16] * len(qs) + [10, 10])

    # ---------- ③ 操作题逐题 ----------
    ws3 = wb.create_sheet("操作题逐题")
    ws3.append(["座位号", "姓名", "机器标识", "操作题", "类型",
                "得分", "满分", "是否缺卷", "文件名"])
    subs_by_pid = {}
    for s in subs:
        pid = s.get("participant_id") or 0
        subs_by_pid.setdefault(pid, {})[s.get("task_id") or 0] = s
    for r in rows:
        group = subs_by_pid.get(r.get("participant_id") or 0) or {}
        if tasks:
            for t in tasks:
                s = group.get(t["id"])
                if not s:
                    ws3.append([r.get("seat", ""), r.get("name", ""),
                                r["hostname"], t["name"],
                                t.get("etype", ""), "未交", "", "", ""])
                    continue
                ws3.append([r.get("seat", ""), r.get("name", ""),
                            r["hostname"], t["name"], t.get("etype", ""),
                            s["score"] if s["score"] is not None else "评分中",
                            s["full"],
                            "缺卷" if (s.get("filename") or "").startswith("（缺卷")
                            else "",
                            s.get("filename") or ""])
        else:
            s = group.get(0)
            if s:
                ws3.append([r.get("seat", ""), r.get("name", ""), r["hostname"],
                            ex.get("title", "实操题"), ex.get("etype", ""),
                            s["score"] if s["score"] is not None else "评分中",
                            s["full"],
                            "缺卷" if (s.get("filename") or "").startswith("（缺卷")
                            else "",
                            s.get("filename") or ""])
    _style_head(ws3, 9)
    _autosize(ws3, [10, 14, 20, 26, 10, 9, 9, 10, 34])

    # ---------- ④ 打字统计 ----------
    ws4 = wb.create_sheet("打字统计")
    task_names = {t: (TYPING_TASK_NAME.get(t) or t)
                  for t in TYPING_TASK_ORDER}
    ws4.append(["座位号", "姓名", "机器标识"] +
               [f"{task_names[t]}(完成/正确率/速度/用时)"
                for t in TYPING_TASK_ORDER] +
               ["完成项数", "总项数", "总用时(分)", "平均正确率(%)",
                "平均速度(字/分)", "综合评分(0-100)"])
    for row in typing:
        tk = row.get("tasks") or {}
        line = [row.get("seat") or "", row.get("name") or row["hostname"],
                row["hostname"]]
        done_n = acc_sum = wpm_sum = sec_sum = 0.0
        n_acc = n_wpm = 0
        for t in TYPING_TASK_ORDER:
            st = tk.get(t)
            if not st:
                line.append("未开始")
                continue
            mins = (st.get("elapsed_ms") or 0) / 60000.0
            sec_sum += mins
            line.append(f"{st.get('progress', 0)}/{st.get('total', 0)} · "
                        f"{round(st.get('accuracy') or 0)}% · "
                        f"{round(st.get('wpm') or 0)} 字/分 · "
                        f"{mins:.1f} 分")
            if st.get("finished"):
                done_n += 1
            if st.get("accuracy") is not None:
                acc_sum += float(st["accuracy"])
                n_acc += 1
            if st.get("wpm"):
                wpm_sum += float(st["wpm"])
                n_wpm += 1
        acc = acc_sum / n_acc if n_acc else 0.0
        wpm = wpm_sum / n_wpm if n_wpm else 0.0
        # 综合评分：正确率 60% + 速度 40%（25 字/分算速度满分）
        speed_score = min(wpm / 25.0, 1.0) * 100 if wpm else 0.0
        overall = round(acc * 0.6 + speed_score * 0.4, 1)
        line += [int(done_n), len(TYPING_TASK_ORDER), round(sec_sum, 1),
                 round(acc, 1), round(wpm, 1), overall]
        ws4.append(line)
    _style_head(ws4, 3 + len(TYPING_TASK_ORDER) + 6)
    _autosize(ws4, [10, 14, 20] + [30] * len(TYPING_TASK_ORDER) +
              [10, 10, 12, 14, 14, 14])

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    safe = "".join(ch for ch in (ex["title"] or "考试")
                   if ch not in r'\/:*?"<>|').strip() or "考试"
    return send_file(buf, as_attachment=True,
                     download_name=f"明细_{safe}.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument"
                              ".spreadsheetml.sheet")


@app.route("/scores/<int:eid>/regrade", methods=["POST"])
def scores_regrade(eid):
    """重新评分：丢后台队列，50 份也不用干等（等完页面早超时了）"""
    try:
        ex = db.get_exam(eid)
        if not ex:
            flash("考试不存在", "err")
            return redirect(url_for("index"))
        if ex["status"] == "stopped":
            flash("已结束考试只能查看和导出", "err")
            return redirect(url_for("scores", eid=eid))
        n = 0
        for s in db.list_submissions(eid):
            if s["saved_path"] and os.path.isfile(s["saved_path"]):
                # 多题考试按每道题各自的评分规则重评，别统一套 exams.checks
                checks = db.checks_for_submission(ex, s)
                enqueue_grade(s["id"], s["saved_path"], checks, s["hostname"])
                n += 1
        flash(f"已在后台重新评分 {n} 份，稍后刷新本页查看成绩" if n
              else "没有可评分的答卷")
    except Exception as e:
        flash(f"评分失败: {e}")
    return redirect(url_for("scores", eid=eid))


@app.route("/scores/<int:eid>/rejudge-code", methods=["POST"])
def scores_rejudge_code(eid):
    """
    程序填空按「运行结果」重新判分。

    什么时候点：
      · 改了某题的期望输出，但学生早就交过（旧分数不会自己变）
      · 判分时服务器缺环境（如没装 g++），后来补上了
    """
    try:
        ex = db.get_exam(eid)
        if not ex:
            flash("考试不存在", "err")
            return redirect(url_for("index"))
        if ex["status"] == "stopped":
            flash("已结束考试只能查看和导出", "err")
            return redirect(url_for("scores", eid=eid))
        n, ok = db.rejudge_code_answers(eid)
        flash(f"已重新判分 {n} 条程序填空答案，其中 {ok} 条判对" if n
              else "本场没有程序填空题，无需重判")
    except Exception as e:
        flash(f"重新判分失败: {e}")
    return redirect(url_for("scores", eid=eid))


@app.route("/scores/<int:eid>/zip", methods=["POST"])
def scores_zip(eid):
    """
    打包答卷。

    ?scope=klass —— 按班级归档目录打包（老师要交给教务/存档的那份）：
       zip 里保留「班级/编号_机器名_文件名」结构，文件名 答卷_班级_编号.zip
    不带 scope  —— 老行为：按成绩表收集本场考试的所有答卷。
    """
    ex = db.get_exam(eid)
    if not ex:
        flash("考试不存在", "err")
        return redirect(url_for("index"))

    scope = (request.form.get("scope") or "").strip()
    if scope == "klass":
        klass = (ex.get("klass") or "").strip() or "未分班"
        code = (ex.get("code") or "").strip()
        src = os.path.join(UPLOAD_DIR, "ikee_Exam", _safe_name(klass, "未分班"))
        name = f"答卷_{klass}_{code or ('exam' + str(eid))}.zip"
        out = os.path.join(EXAM_DIR, name)
        exm.collect_zip(eid, out, from_dir=src, exam_code=code, klass=klass)
        db.log("teacher", "info", f"打包本班答卷 #{eid}：{name}")
        return send_file(out, as_attachment=True, download_name=name)

    out = os.path.join(EXAM_DIR, f"答卷_exam{eid}.zip")
    exm.collect_zip(eid, out)
    return send_file(out, as_attachment=True, download_name=f"答卷_exam{eid}.zip")


# ---------------- 自动出题（标准答案 → 评分规则）----------------
@app.route("/exam/<int:eid>/autorule", methods=["POST"])
def exam_autorule(eid):
    """
    上传老师做好的标准答案，自动生成评分规则并回填
    """
    ex = db.get_exam(eid)
    if not ex:
        flash("考试不存在")
        return redirect(url_for("index"))
    if ex["status"] in ("running", "paused", "stopped"):
        flash("考试进行中或已结束，不能修改评分规则", "err")
        return redirect(url_for("index"))
    f = request.files.get("answer")
    if not f or not f.filename:
        flash("请选择标准答案文件")
        return redirect(url_for("exam_edit", eid=eid))

    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in (".docx", ".xlsx", ".pptx"):
        flash("标准答案必须是 .docx / .xlsx / .pptx")
        return redirect(url_for("exam_edit", eid=eid))
    if not _valid_office_upload(f):
        flash("标准答案文件不是有效的 Office 文档", "err")
        return redirect(url_for("exam_edit", eid=eid))

    tmp = os.path.join(UPLOAD_DIR, f"_answer_{uuid.uuid4().hex}{ext}")
    f.save(tmp)
    try:
        sys.path.insert(0, ROOT)
        try:
            from . import auto_rule
        except ImportError:
            import auto_rule
        pack = auto_rule.auto_rule(tmp, default_score=10, max_items=12)
        checks = auto_rule.clean(pack["checks"])

        # 自评验证：标准答案自己应该拿满分
        total, full, detail = auto_rule.verify(tmp, pack)
        ok = abs(total - full) < 0.01

        db.update_exam(eid, {
            "title": ex["title"], "etype": ex["etype"],
            "duration": ex["duration"], "theory_duration": ex["duration"],
            "requirement": ex["requirement"],
            "materials": json.loads(ex["materials"] or "[]"),
            "save_as": "",
            "checks": checks,
        })
        bad = [d["id"] for d in detail if d["got"] < d["full"]]
        if ok:
            flash(f"✅ 已生成 {len(checks)} 个评分点，自评满分 {full} 分，可直接用")
        else:
            flash(f"⚠️ 已生成 {len(checks)} 个评分点，但自评只有 {total}/{full} 分。"
                  f"需要手动调整：{('、'.join(bad[:5])) or '见明细'}")
    except Exception as e:
        app.logger.exception("自动生成评分规则失败: %s", e)
        flash("生成失败，请检查标准答案文件，或查看服务器日志")
    finally:
        if os.path.isfile(tmp):
            os.remove(tmp)
    return redirect(url_for("exam_edit", eid=eid))


@app.route("/exam/<int:eid>/rule")
def exam_download_rule(eid):
    """下载当前评分规则 JSON"""
    ex = db.get_exam(eid)
    if not ex:
        return "考试不存在", 404
    checks = json.loads(ex["checks"] or "[]")
    out = os.path.join(EXAM_DIR, f"评分规则_exam{eid}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(checks, f, ensure_ascii=False, indent=2)
    return send_file(out, as_attachment=True,
                     download_name=f"评分规则_exam{eid}.json")


# ---------------- 抄袭检测 ----------------
def _exam_file_type(ex):
    """
    抄袭检测要知道按哪种文件解析（word/excel/ppt）。
    ⚠️ exams.etype 可能是 JSON 数组（["excel","ppt"]，多类型操作题考试），
       直接拿去查表会 KeyError → 整个抄袭检测页 500。
    """
    types = _practice_types_of(ex)
    # 只认 plagiarism.FP 支持的类型，历史遗留的 "mixed" 等直接兜底 word，
    # 否则查表 KeyError 会让整个抄袭检测页/CSV 500
    for t in types:
        if t in ("word", "excel", "ppt"):
            return t
    return "word"


@app.route("/scores/<int:eid>/plagiarism")
def scores_plagiarism(eid):
    ex = db.get_exam(eid)
    if not ex:
        return "考试不存在", 404
    try:
        sys.path.insert(0, ROOT)
        try:
            from . import plagiarism
        except ImportError:
            import plagiarism
        subs = db.list_submissions(eid)
        files = [(s["hostname"], s["saved_path"]) for s in subs
                 if s["saved_path"] and os.path.isfile(s["saved_path"])]
        if len(files) < 2:
            flash("至少需要 2 份答卷才能检测")
            return redirect(url_for("scores", eid=eid))
        all_pairs, suspects, info = plagiarism.run(files, _exam_file_type(ex),
                                                   verbose=False)
        # 统计在后端算好，模板不做复杂过滤（Jinja2 的 selectattr 没有 containing 测试）
        cnt = {
            "hard": sum(1 for x in suspects if x["verdict"] == "hard"),
            "high": sum(1 for x in suspects if x["verdict"] == "high"),
            "warn": sum(1 for x in suspects if x["verdict"] == "warn"),
        }
        return render_template("plagiarism.html", exam=ex, pairs=all_pairs,
                               suspects=suspects, info=info, n=len(files),
                               cnt=cnt, verdict=plagiarism.VERDICT)
    except Exception as e:
        flash(f"检测失败: {e}")
        return redirect(url_for("scores", eid=eid))


@app.route("/scores/<int:eid>/plagiarism/csv")
def plagiarism_csv(eid):
    try:
        from . import plagiarism
    except ImportError:
        import plagiarism
    ex = db.get_exam(eid)
    if not ex:
        return "考试不存在", 404
    try:
        subs = db.list_submissions(eid)
        files = [(s["hostname"], s["saved_path"]) for s in subs
                 if s["saved_path"] and os.path.isfile(s["saved_path"])]
        all_pairs, _, _ = plagiarism.run(files, _exam_file_type(ex), verbose=False)
        out = os.path.join(EXAM_DIR, f"抄袭检测_exam{eid}.csv")
        os.makedirs(EXAM_DIR, exist_ok=True)
        with open(out, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=["学生A", "学生B", "综合相似度",
                                              "偏离(σ)", "文本", "结构",
                                              "公式内容", "内容完全相同", "判定"])
            w.writeheader()
            for r in all_pairs:
                w.writerow({"学生A": r["a"], "学生B": r["b"],
                            "综合相似度": r["score"], "偏离(σ)": r["z"],
                            "文本": r["text"], "结构": r["struct"],
                            "公式内容": r["formula"],
                            "内容完全相同": "是" if r["identical"] else "",
                            "判定": plagiarism.VERDICT[r["verdict"]]})
        return send_file(out, as_attachment=True,
                         download_name=f"抄袭检测_exam{eid}.csv")
    except Exception as e:
        app.logger.exception("导出抄袭检测 CSV 失败: %s", e)
        flash(f"导出失败: {e}")
        return redirect(url_for("scores", eid=eid))


@app.route("/detail/<int:eid>/<host>")
def detail(eid, host):
    ex = db.get_exam(eid)
    pid = request.args.get("participant_id", type=int) or 0
    st = db.participant_snapshot(eid, pid) or db.get_student(host) or {}
    # 一场考试可能装多道操作题 → 每人多条提交，按题号排
    subs = [s for s in db.list_submissions(eid)
            if s["hostname"] == host and (s.get("participant_id") or 0) == pid]
    subs.sort(key=lambda s: s.get("task_id") or 0)

    # 理论题逐题判分结果（后端算，模板只负责显示）
    qs = db.get_exam_questions(eid)
    ans = db.theory_answers_of(eid, host, pid)
    TNAME = QTYPE_CN      # 题型中文名统一用 QTYPE_CN（以前这里漏了程序填空/拖拽题）
    theory_rows, got_sum, full_sum = [], 0.0, 0.0
    answered_n = correct_n = 0
    type_stat = {}
    for i, q in enumerate(qs, 1):
        student = ans.get(q["id"])
        answered = student is not None and str(student).strip() != ""
        correct, got = db.judge(q, student) if answered else (0, 0)
        got_sum += got
        full_sum += float(q["score"])
        if answered:
            answered_n += 1
            if correct:
                correct_n += 1
        t = type_stat.setdefault(q["qtype"], [0.0, 0.0, 0, 0, 0])
        t[0] += got
        t[1] += float(q["score"])
        t[2] += 1 if answered else 0
        t[3] += 1 if correct else 0
        t[4] += 1
        theory_rows.append({
            "no": i, "type_name": TNAME.get(q["qtype"], q["qtype"]),
            "stem": q["stem"], "answer": student or "", "std": q["answer"],
            "correct": correct, "got": got, "full": q["score"], "answered": answered,
        })
    theory_got = round(got_sum, 2)
    theory_full = round(full_sum, 2)

    # 实操：逐份提交解出评分明细；多道题时每一份一块展示
    ck_total = ck_full = 0
    prac_names = {}       # task_id -> 名称缓存，避免同一道题被每份提交重复查库
    for s in subs:
        try:
            s["_detail"] = json.loads(s["detail"] or "[]")
        except Exception:
            s["_detail"] = []
        tid = s.get("task_id") or 0
        if tid:
            if tid not in prac_names:
                p = db.get_practice(tid)
                prac_names[tid] = (p or {}).get("name") or f"实操题 {tid}"
            s["_task_name"] = prac_names[tid]
        else:
            s["_task_name"] = "实操题"
        for d in s["_detail"]:
            ck_total += 1
            if float(d.get("got") or 0) >= float(d.get("full") or 0):
                ck_full += 1
    graded = [s for s in subs if s.get("score") is not None]
    practice_score = round(sum(s["score"] or 0 for s in graded), 2) if graded else None
    practice_full = round(sum(s["full"] or 0 for s in graded), 2) if graded else 0

    def pct(g, f):
        return round(g / f * 100, 1) if f else 0

    total_got = round(theory_got + (practice_score or 0), 2)
    total_full = round(theory_full + practice_full, 2)
    stats = {
        "total_got": total_got, "total_full": total_full,
        "total_rate": pct(total_got, total_full),
        "theory_got": theory_got, "theory_full": theory_full,
        "theory_rate": pct(theory_got, theory_full),
        "answered": answered_n, "total": len(qs),
        "correct": correct_n, "correct_rate": pct(correct_n, len(qs)),
        "practice_score": practice_score, "practice_full": practice_full,
        "practice_rate": pct(practice_score or 0, practice_full),
        "ck_total": ck_total, "ck_full": ck_full,
        "submitted": bool(subs), "files": len(subs),
    }
    type_stats = [{
        "type": TNAME.get(k, k), "got": v[0], "full": v[1],
        "answered": v[2], "correct": v[3], "total": v[4],
        "rate": pct(v[0], v[1]),
    } for k, v in type_stat.items()]

    # 画图作品：画板 / 拼豆可能各交一份
    drawings = []
    for kind, kname in (("draw", "画板"), ("beads", "拼豆")):
        d = db.drawing_of(eid, host, kind, pid)
        if d:
            d["kind"] = kind
            d["kind_name"] = kname
            drawings.append(d)
    return render_template("detail.html", host=host, participant_id=pid, exam=ex, student=st,
                           subs=subs, stats=stats, type_stats=type_stats,
                           theory_rows=theory_rows,
                           theory_got=theory_got, theory_full=theory_full,
                           drawings=drawings,
                           has_drawing=("drawing" in _selected_types(ex)))


def _drawing_req_kind():
    """老师端查看/下载画图作品的 ?kind= 参数：draw / beads"""
    k = (request.args.get("kind") or "draw").strip().lower()
    return "beads" if k == "beads" else "draw"


def _drawing_req_participant():
    return request.args.get("participant_id", type=int) or 0


@app.route("/exam/<int:eid>/drawing/<path:host>")
def exam_drawing_file(eid, host):
    """查看某个学生的画图作品（老师端）；?kind=beads 看拼豆那份"""
    d = db.drawing_of(eid, host, _drawing_req_kind(), _drawing_req_participant())
    if not d or not d.get("saved_path") or not os.path.isfile(d["saved_path"]):
        return "画图作品不存在（可能还没提交）", 404
    return send_file(d["saved_path"], as_attachment=False,
                     download_name=d.get("filename") or "画板.png")


@app.route("/exam/<int:eid>/drawing/<path:host>/download")
def exam_drawing_download(eid, host):
    """下载某个学生的画图作品；?kind=beads 下拼豆那份"""
    kind = _drawing_req_kind()
    d = db.drawing_of(eid, host, kind, _drawing_req_participant())
    if not d or not d.get("saved_path") or not os.path.isfile(d["saved_path"]):
        return "画图作品不存在（可能还没提交）", 404
    snap = db.participant_snapshot(eid, _drawing_req_participant()) or {}
    current = db.get_student(host) or {}
    name = (snap.get("name") or current.get("name") or host).strip()
    ext = os.path.splitext(d["saved_path"])[1] or ".png"
    tag = "拼豆" if kind == "beads" else "画板"
    return send_file(d["saved_path"], as_attachment=True,
                     download_name=f"{name}_{tag}{ext}")


@app.route("/exam/<int:eid>/submission/<int:sid>/file")
def submission_file(eid, sid):
    """老师端下载学生的实操答卷原件（评讲时投影他做的东西）"""
    for s in db.list_submissions(eid):
        if s["id"] == sid:
            p = s.get("saved_path") or ""
            if not os.path.isfile(p):
                return "答卷文件不存在（这题可能没做，或文件已被删除）", 404
            return send_file(p, as_attachment=True,
                             download_name=s.get("filename") or "答卷")
    return "没有这份答卷", 404


def _opts_of(q):
    """题目的选项列表（库里可能是 JSON 字符串，也可能是已解析的列表）"""
    raw = q.get("options") if hasattr(q, "get") else q["options"]
    if isinstance(raw, (list, tuple)):
        return list(raw)
    try:
        v = json.loads(raw or "[]")
        return v if isinstance(v, list) else []
    except Exception:
        return []


def _answer_text(q):
    """
    把标准答案渲染成"看得懂"的内容（评讲页用）：
      单选/多选 → "A. 选项文字　B. 选项文字"
      拖拽题    → "第1格 → A. 选项文字"
      判断/填空/程序填空 → 答案文本本身
    """
    qtype, ans = q["qtype"], str(q["answer"] or "")
    opts = _opts_of(q)
    letters = "ABCDEFGH"

    def opt_txt(letter):
        i = letters.find(letter.upper())
        if 0 <= i < len(opts):
            return f"{letter}. {opts[i]}"
        return letter

    if qtype in ("single", "multi"):
        parts = [opt_txt(ch) for ch in re.findall(r"[A-Z]", ans.upper())]
        return "　".join(parts) or ans
    if qtype == "drag":
        out = []
        for part in ans.split("|"):
            part = part.strip()
            if ":" not in part:
                continue
            slot, item = part.split(":", 1)
            slot = re.sub(r"[^0-9A-Za-z]", "", slot)
            item = re.sub(r"[^0-9A-Za-z]", "", item).upper()
            out.append(f"第{slot}格 → {opt_txt(item)}")
        return "　".join(out) or ans
    return ans


def _q_img_html(s):
    """
    题干/选项里的「img:文件名 说明」→ <img> 标签（评讲页投影显示图片选项）。
    其余文字照常转义，题库内容只有老师能录，风险可控。
    """
    from markupsafe import Markup, escape
    s = str(s or "")
    out, last = [], 0
    for m in re.finditer(r"img:(\S+)(?:[ \t]+([^\n]*))?", s):
        out.append(escape(s[last:m.start()]))
        out.append(Markup(f'<img src="/qimg/{m.group(1)}" '
                          f'style="max-height:110px;vertical-align:middle;'
                          f'margin:2px 8px;border-radius:4px" alt="图片">'))
        tail = (m.group(2) or "").strip()
        if tail:
            out.append(Markup(" ") + escape(tail))
        last = m.end()
    out.append(escape(s[last:]))
    return Markup("".join(str(x) for x in out))


def _review_payload(eid):
    """
    生成网页端与桌面端共用的评讲数据。
    试卷评讲页（投影用）：
    - 全部考题按卷面顺序列出，正确答案默认藏起来，点「看答案」才显示
    - 每道题显示 多少人做对 / 多少人做错 / 多少人没答，点人数弹出名单
    - 可以按正确率排序（错得多的排前面）、只看有错的题、投影放大字体
    - 操作题：平均得分 / 已交 / 缺卷，点人数弹出每人得分
    - 打字练习：速度与正确率榜单（不计分，用来表扬）
    """
    ex = db.get_exam(eid)
    if not ex:
        return "考试不存在", 404
    qs = db.get_exam_questions(eid)
    tasks = db.exam_tasks(eid)
    subs = db.list_submissions(eid)
    ans_rows = db.theory_answer_rows(eid)

    # 名单走参与快照（清了学生记录、机器换人都在），同一机器不同学生分行显示。
    students = {}
    for r in db.full_report(eid):
        key = (r.get("participant_id") or 0, r["hostname"])
        students[key] = {"hostname": r["hostname"],
                         "participant_id": r.get("participant_id") or 0,
                         "name": r.get("name") or "", "seat": r.get("seat") or "",
                         "online": r.get("online")}
    submitted_hosts = {(s.get("participant_id") or 0, s["hostname"]) for s in subs}
    attended = list(students.values())
    attended.sort(key=lambda s: ((s.get("seat") or "ZZ"), s["hostname"],
                                 s.get("participant_id") or 0))

    def who(host, answer=None, correct=None, participant_id=0):
        stu = students.get((participant_id or 0, host)) or {}
        return {"host": host, "participant_id": participant_id or 0,
                "name": stu.get("name") or host,
                "seat": stu.get("seat") or "", "answer": answer,
                "correct": correct}

    per = {q["id"]: {"right": [], "wrong": [], "seen": set()} for q in qs}
    for r in ans_rows:
        slot = per.get(r["question_id"])
        if slot is None:
            continue
        w = who(r["hostname"], r["answer"], bool(r["correct"]),
                r.get("participant_id") or 0)
        seen_key = (r.get("participant_id") or 0, r["hostname"])
        slot["seen"].add(seen_key)
        (slot["right"] if r["correct"] else slot["wrong"]).append(w)

    cards = []
    for no, q in enumerate(qs, 1):
        slot = per[q["id"]]
        right_n, wrong_n = len(slot["right"]), len(slot["wrong"])
        answered_n = right_n + wrong_n
        blank_n = max(0, len(submitted_hosts) - answered_n)
        rate = round(right_n / answered_n * 100, 1) if answered_n else None
        std_letters = set()
        if q["qtype"] in ("single", "multi"):
            std_letters = set(re.findall(r"[A-Z]", str(q["answer"]).upper()))
        opts = []
        for j, opt in enumerate(_opts_of(q)):
            letter = chr(ord("A") + j)
            opts.append({"letter": letter, "html": _q_img_html(opt),
                         "ok": letter in std_letters})
        cards.append({
            "no": no, "q": q, "qtype": QTYPE_CN.get(q["qtype"], q["qtype"]),
            "stem_html": _q_img_html(q["stem"]),
            "opts": opts, "answer": q["answer"],
            "answer_text": _answer_text(q),
            "category": q["category"] or "",
            "score": q["score"], "right": slot["right"], "wrong": slot["wrong"],
            "right_n": right_n, "wrong_n": wrong_n, "blank_n": blank_n,
            "rate": rate,
        })

    # 操作题：每题的交卷情况（缺卷也显示）
    task_cards = []
    for t in tasks:
        rows = []
        for s in subs:
            if (s.get("task_id") or 0) != t["id"]:
                continue
            stu = students.get((s.get("participant_id") or 0, s["hostname"])) or {}
            try:
                det = json.loads(s.get("detail") or "[]")
            except (TypeError, ValueError):
                det = []
            if not isinstance(det, list):
                det = []
            ck_ok = sum(1 for d in det
                        if float(d.get("got") or 0) >= float(d.get("full") or 0))
            rows.append({"name": stu.get("name") or s["hostname"],
                         "seat": stu.get("seat") or "",
                         "score": s.get("score"), "full": s.get("full"),
                         "empty": (s.get("filename") or "").startswith("（缺卷"),
                         "file": s.get("filename"),
                         "sid": s.get("id"),
                         "has_file": bool(s.get("saved_path")),
                         "detail": det,
                         "ck_ok": ck_ok, "ck_n": len(det)})
        rows.sort(key=lambda r: ((r["seat"] or "ZZ"), r["name"]))
        graded = [r for r in rows if r["score"] is not None]
        full = round(sum(float(c.get("score", 0))
                         for c in (t.get("checks") or [])), 2) or (
            graded[0]["full"] if graded else 0)
        avg = (round(sum(r["score"] for r in graded) / len(graded), 1)
               if graded else None)
        empty_n = sum(1 for r in rows if r["empty"])
        task_cards.append({"t": t, "rows": rows, "n": len(rows),
                           "avg": avg, "full": full, "empty_n": empty_n,
                           "rate": round(avg / full * 100, 1)
                           if avg is not None and full else None})

    # 打字排名（全部同学）+ 每人各子任务详情：评讲表扬用，也能查谁没练完。
    # 只统计「做完的」当成绩，但排名里没做完的也在（进度条写在详情里）。
    def _typing_task_line(task, st):
        bits = []
        if not st.get("finished"):
            bits.append("进度 %s/%s（没做完）" % (st.get("progress") or 0,
                                                st.get("total") or "?"))
        elif st.get("progress") is not None and st.get("total"):
            bits.append("完成 %s/%s" % (st.get("progress"), st.get("total")))
        if st.get("accuracy") is not None:
            bits.append("正确率 %s%%" % round(float(st["accuracy"]), 1))
        if st.get("wpm"):
            bits.append("速度 %s 字/分" % round(float(st["wpm"]), 1))
        if st.get("reaction_ms"):
            bits.append("平均反应 %s 秒" % round(float(st["reaction_ms"]) / 1000, 2))
        if st.get("elapsed_ms"):
            bits.append("用时 %s 秒" % int(float(st["elapsed_ms"]) / 1000))
        return " · ".join(bits) if bits else "还没有数据"

    agg = {}
    # 排名覆盖全班：参与名单里的人都在（没练过的排最后，显示「还没练」）
    for key, disp in students.items():
        agg[key] = {"host": disp["hostname"],
                  "participant_id": disp.get("participant_id") or 0,
                  "name": disp.get("name") or disp["hostname"],
                  "seat": disp.get("seat") or "",
                  "wpm": 0.0, "acc": 0.0, "acc_n": 0,
                  "task_rows": [], "done_n": 0}
    for row in db.typing_board(eid):
        h = row["hostname"]
        key = (row.get("participant_id") or 0, h)
        disp = students.get(key) or {}
        a = agg.setdefault(key, {
            "host": h, "participant_id": row.get("participant_id") or 0,
            "name": disp.get("name") or row.get("name") or h,
            "seat": disp.get("seat") or row.get("seat") or "",
            "wpm": 0.0, "acc": 0.0, "acc_n": 0,
            "task_rows": [], "done_n": 0})
        for task, st in (row.get("tasks") or {}).items():
            a["task_rows"].append({
                "name": TYPING_TASK_NAME.get(task, task),
                "line": _typing_task_line(task, st)})
            if st.get("finished"):
                a["done_n"] += 1
                a["wpm"] = max(a["wpm"], float(st.get("wpm") or 0))
                if st.get("accuracy") is not None:
                    a["acc"] += float(st["accuracy"])
                    a["acc_n"] += 1
    order = list(TYPING_TASK_ORDER)
    for a in agg.values():
        a["acc"] = round(a["acc"] / a["acc_n"], 1) if a["acc_n"] else 0.0
        # 综合评分：正确率 60% + 速度 40%（25 字/分算速度满分）
        a["overall"] = round(a["acc"] * 0.6
                             + (min(a["wpm"] / 25.0, 1.0) * 100 if a["wpm"] else 0)
                             * 0.4, 1)
        # 子任务按打字模块的固定顺序排（打地鼠 → 看键按键 → …）
        a["task_rows"].sort(
            key=lambda r: next((i for i, t in enumerate(order)
                                if TYPING_TASK_NAME.get(t, t) == r["name"]), 99))
    # 全部同学都进排名（没练的排最后），不再只取前 12
    typing_rows = sorted(agg.values(),
                         key=lambda a: (-a["overall"], -a["done_n"], a["seat"]))
    for i, a in enumerate(typing_rows, 1):
        a["rank"] = i

    total_score = round(sum(float(q["score"] or 0) for q in qs), 2)
    drawings = db.drawings_for(eid)
    draw_js = [{"host": d["hostname"], "participant_id": d.get("participant_id") or 0,
                "kind": d.get("kind") or "draw",
                "name": ("%s %s" % (d.get("seat") or "",
                                    d.get("name") or d["hostname"])).strip()
                        + ("（拼豆）" if (d.get("kind") or "draw") == "beads"
                           else "")}
               for d in drawings]
    return {
        "exam": ex, "qs": qs, "cards": cards,
        "task_cards": task_cards, "typing_rows": typing_rows,
        "drawings": drawings, "draw_js": draw_js,
        "has_typing": "typing" in _selected_types(ex),
        "has_practice": "practice" in _selected_types(ex),
        "has_drawing": "drawing" in _selected_types(ex),
        "attended": attended,
        "n_submitted": len(submitted_hosts),
        "n_attended": len(attended),
        "total_score": total_score,
    }


@app.route("/exam/<int:eid>/review")
def exam_review(eid):
    payload = _review_payload(eid)
    if not payload:
        return "考试不存在", 404
    return render_template("review.html", **payload)


@app.route("/admin_api/exam/<int:eid>/review")
def admin_api_exam_review(eid):
    if not _admin_ok():
        return _admin_fail("未登录", 401)
    payload = _review_payload(eid)
    if not payload:
        return _admin_fail("考试不存在", 404)
    return jsonify(payload)


@app.route("/logs")
def logs():
    return render_template("logs.html", logs=db.get_logs(300))


@app.route("/admin_api/dashboard/students")
def api_dashboard_students():
    """老师端自动刷新用：返回学生列表 JSON"""
    students = db.list_students()
    online = sum(1 for s in students if s["online"])
    submitted = sum(1 for s in students if s.get("status") == "submitted")
    rows = []
    t = time.time()
    for s in students:
        last = s["last_seen"] or 0
        last_str = time.strftime("%H:%M:%S", time.localtime(last)) if last else "-"
        is_online = s["online"]
        # 掉线时长
        offline_sec = int(t - last) if last and not is_online else 0
        if offline_sec > 0:
            if offline_sec < 60:
                offline_str = f"{offline_sec}秒前"
            else:
                offline_str = f"{offline_sec//60}分钟前"
        else:
            offline_str = ""
        # 状态文字
        raw_status = s.get("status", "idle")
        if not is_online and raw_status not in ("submitted",):
            display_status = "掉线"
        else:
            display_status = {"idle":"待考","working":"答题中",
                              "theory_done":"理论已交","submitted":"已交卷"}.get(raw_status, raw_status)
        rows.append({
            "hostname": s["hostname"],
            "ip": s.get("ip", ""),
            "name": s.get("name", ""),
            "seat": s.get("seat", ""),
            "status": display_status,
            "raw_status": raw_status,
            "online": is_online,
            "last_seen": last_str,
            "offline_since": offline_str,
            "progress": s.get("progress") or {},
        })
    return jsonify({"ok": True, "students": rows,
                    "total": len(students), "online": online,
                    "submitted": submitted})


@app.route("/students")
def students_page():
    return render_template("students.html", students=db.list_students())


@app.route("/students/clear", methods=["POST"])
def students_clear():
    """清空学生机记录（POST + CSRF：破坏性操作不能被 GET 或一张图触发）"""
    c = db.conn()
    c.execute("DELETE FROM students")
    c.commit()
    c.close()
    flash("已清空所有学生机记录")
    return redirect(url_for("students_page"))


@app.route("/students/clear_offline", methods=["POST"])
def students_clear_offline():
    """把掉线的学生机记录清掉（在线的保留），控制台表格里好清干净"""
    n = db.clear_offline_students()
    flash(f"已清除 {n} 台离线学生机记录" if n else "没有离线的学生机记录")
    return redirect(url_for("index"))


def _parse_roster_text(text):
    return _roster_rows(text)


@app.route("/exam/<int:eid>/roster", methods=["POST"])
def exam_roster_save(eid):
    """
    保存本场学生名单（老师端配置，学生端登录时按名单校验）。
    body: {"text": "小明,01\n小红 02", "mode": "replace"|"append"}
    """
    ex = db.get_exam(eid)
    if not ex:
        return {"ok": False, "msg": "考试不存在"}, 404
    if ex["status"] == "stopped":
        return {"ok": False, "msg": "已结束考试为只读，名单已锁定"}, 400
    data = request.get_json(silent=True) or {}
    rows = _parse_roster_text(data.get("text", ""))
    if (data.get("mode") or "replace") == "append":
        added = sum(1 for seat, name, ip in rows
                    if db.add_roster(eid, seat, name, ip))
        return {"ok": True, "added": added, "count": len(db.roster_of(eid))}
    db.set_roster(eid, rows)
    db.invalidate()
    return {"ok": True, "count": len(rows)}


@app.route("/exam/<int:eid>/roster/clear", methods=["POST"])
def exam_roster_clear(eid):
    ex = db.get_exam(eid)
    if not ex:
        return {"ok": False, "msg": "考试不存在"}, 404
    if ex["status"] == "stopped":
        return {"ok": False, "msg": "已结束考试为只读，名单已锁定"}, 400
    db.clear_roster(eid)
    db.invalidate()
    return {"ok": True, "count": 0}


@app.route("/exam/<int:eid>/roster/<int:rid>/delete", methods=["POST"])
def exam_roster_del(eid, rid):
    ex = db.get_exam(eid)
    if not ex:
        return {"ok": False, "msg": "考试不存在"}, 404
    if ex["status"] == "stopped":
        return {"ok": False, "msg": "已结束考试为只读，名单已锁定"}, 400
    db.del_roster(eid, rid)
    db.invalidate()
    return {"ok": True, "count": len(db.roster_of(eid))}


@app.route("/students/<int:sid>/reset-token", methods=["POST"])
def student_reset_token(sid):
    c = db.conn()
    row = c.execute("SELECT hostname FROM students WHERE id=?", (sid,)).fetchone()
    c.close()
    if row:
        db.reset_student_token(row["hostname"])
        flash("机器凭证已重置，学生端重新连接后会领取新凭证")
    return redirect(url_for("students_page"))


@app.route("/students/<int:sid>/delete", methods=["POST"])
def student_delete(sid):
    db.delete_student(sid)
    flash("已删除该学生机记录")
    return redirect(url_for("students_page"))


@app.route("/exam/<int:eid>/delete", methods=["POST"])
def exam_delete(eid):
    ex = db.get_exam(eid)
    if not ex:
        flash("考试不存在", "err")
        return redirect(url_for("index"))
    # 页面上 running/paused 都藏了删除按钮，后端以前只挡 running，
    # 直接访问 URL 就能把一场暂停中的（=进行中的）考试连同答卷全删了
    if ex["status"] in ("running", "paused"):
        flash(f"「{ex['title']}」正在进行中（或已暂停），"
              f"请先「结束考试」再删除", "err")
        return redirect(url_for("index"))
    if ex["status"] == "stopped":
        flash("已结束考试为只读，不能删除；可查看成绩、评讲或导出", "err")
        return redirect(url_for("index"))
    sub_n = len(db.list_submissions(eid))
    db.delete_exam(eid)
    flash(f"已删除考试「{ex['title']}」" + (f"及其 {sub_n} 份答卷" if sub_n else ""))
    db.log("teacher", "warn", f"删除考试 #{eid}「{ex['title']}」"
                              f"（含 {sub_n} 份答卷）")
    return redirect(url_for("index"))


@app.route("/scores/<int:eid>/submission/<int:sid>/delete", methods=["POST"])
def submission_delete(eid, sid):
    ex = db.get_exam(eid)
    if ex and ex["status"] == "stopped":
        flash("已结束考试只能查看和导出，不能删除提交记录", "err")
        return redirect(url_for("scores", eid=eid))
    db.delete_submission(sid)
    flash("已删除该提交记录")
    return redirect(url_for("scores", eid=eid))


@app.route("/discover")
def discover_page():
    """服务发现状态页：看广播发出去没有、学生能不能自动找到"""
    import beacon
    st = beacon_obj.stats() if beacon_obj else {"running": False}
    return render_template("discover.html", stats=st,
                           name=SERVER_NAME, port=HTTP_PORT)


# ==================== 题库导出 ====================
@app.route("/questions/export")
def questions_export():
    """
    导出题库。
      ?fmt=xlsx   Excel（可用 WPS 编辑，含程序填空全部字段）
      ?fmt=json   JSON（备份 / 迁移）
      ?category=  只导出某分类
      ?qtype=     只导出某题型
    """
    fmt = request.args.get("fmt", "xlsx").lower()
    cat = request.args.get("category", "")
    qtype = request.args.get("qtype", "")
    qs = db.list_questions(category=cat or None, qtype=qtype or None)

    if fmt == "json":
        data = [{
            "qtype": q["qtype"], "stem": q["stem"],
            "options": q["options"], "answer": q["answer"],
            "score": q["score"], "category": q["category"],
            "language": q.get("language", ""),
            "code": q.get("code", ""),
            "stdin_data": q.get("stdin_data", ""),
            "expected_output": q.get("expected_output", ""),
        } for q in qs]
        buf = io.BytesIO(json.dumps(data, ensure_ascii=False, indent=2)
                         .encode("utf-8"))
        return send_file(buf, as_attachment=True,
                         download_name="题库备份.json",
                         mimetype="application/json")

    # ---- Excel ----
    import import_excel as ie
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    wb = Workbook()
    ws = wb.active
    ws.title = "题库"
    for i, h in enumerate(ie.HEADERS, 1):
        c = ws.cell(row=1, column=i, value=h)
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="4472C4")
        c.alignment = Alignment(horizontal="center")

    T2C = {"single": "单选", "multi": "多选", "judge": "判断",
           "blank": "填空", "code": "code"}
    for r, q in enumerate(qs, 2):
        vals = [T2C.get(q["qtype"], q["qtype"]), q["stem"]]
        opts = q["options"] or []
        for i in range(4):
            vals.append(opts[i] if i < len(opts) else "")
        vals += [q["answer"], q["score"], q["category"] or "",
                 q.get("language") or "", q.get("code") or "",
                 q.get("stdin_data") or ""]
        for i, v in enumerate(vals, 1):
            cell = ws.cell(row=r, column=i, value=v)
            # 防止 == 这类答案被 Excel 当公式
            if isinstance(v, str) and v.startswith("="):
                cell.data_type = "s"
            if i == 11:      # 代码列
                cell.font = Font(name="Consolas", size=10)
                cell.alignment = Alignment(wrap_text=True, vertical="top")

    for col, w in zip("ABCDEFGHIJKL",
                      (8, 44, 20, 20, 20, 20, 24, 7, 12, 9, 46, 12)):
        ws.column_dimensions[col].width = w

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return send_file(
        buf, as_attachment=True, download_name="题库导出.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument."
                 "spreadsheetml.sheet")


# ==================== 考试包 导出 / 导入 ====================
def _exam_export_response(eid):
    data = db.export_bundle(eid)
    if not data:
        return None
    ex = db.get_exam(eid)
    safe = "".join(ch for ch in (ex["title"] or "考试")
                   if ch not in r'\/:*?"<>|').strip() or "考试"
    return send_file(io.BytesIO(json.dumps(data, ensure_ascii=False, indent=2)
                                .encode("utf-8")),
                     as_attachment=True, download_name=f"考试_{safe}.json",
                     mimetype="application/json")


@app.route("/exam/<int:eid>/export")
def exam_export(eid):
    """导出这场考试（配置 + 全部题目）为 JSON"""
    response = _exam_export_response(eid)
    if response is None:
        flash("考试不存在")
        return redirect(url_for("index"))
    return response


@app.route("/exams/import", methods=["POST"])
def exams_import():
    """导入考试包 JSON（可一次选多个文件）"""
    files = request.files.getlist("files")
    if not files or not any(f.filename for f in files):
        flash("没选文件")
        return redirect(url_for("index"))
    ok_n = 0
    msgs = []
    for f in files:
        if not f.filename:
            continue
        try:
            raw = f.read().decode("utf-8")
            data = json.loads(raw)
        except Exception as e:
            msgs.append(f"{f.filename}: 解析失败 {e}")
            continue
        eid, info = db.import_bundle(data)
        if eid:
            ok_n += 1
            ex = db.get_exam(eid)
            msgs.append(f"{f.filename}: {info.get('msg','')} →「{ex['title']}」")
        else:
            msgs.append(f"{f.filename}: {info.get('msg','导入失败')}")
    flash(f"导入完成：成功 {ok_n} 场。" + "；".join(msgs[:3]))
    return redirect(url_for("index"))


# ==================== 组卷快捷操作 ====================
@app.route("/exam/<int:eid>/addall", methods=["POST"])
def exam_addall(eid):
    """按筛选条件批量加入；先保存当前页签待保存勾选，不清其他题型。"""
    ex = db.get_exam(eid)
    if not ex:
        flash("考试不存在", "err")
        return redirect(url_for("index"))
    if _paper_locked(ex):
        return redirect(url_for("exam_edit", eid=eid, tab="basic"))
    cat = request.form.get("category", "")
    qtype = request.form.get("qtype", "")
    tab = request.form.get("tab", qtype)
    if qtype in THEORY_TYPES:
        if request.form.get("selection_present") == "1":
            qids, error = _submitted_type_question_ids(qtype)
            if error:
                flash(error, "err")
                return redirect(url_for("exam_edit", eid=eid, tab=tab))
            _replace_exam_type_questions(eid, qtype, qids)
        elif request.form.get("replace") == "1":
            _replace_exam_type_questions(eid, qtype, [])
    n = db.add_questions_by_filter(eid, cat, qtype)
    db.invalidate()
    if n:
        scope = ("全部题库" if not (cat or qtype)
                 else " / ".join(x for x in (cat, QTYPE_CN.get(qtype, qtype)) if x))
        flash(f"已加入 {n} 题（{scope}）")
    else:
        flash("没有符合筛选条件的题目", "err")
    return redirect(url_for("exam_edit", eid=eid, tab=tab))


@app.route("/exam/<int:eid>/clearq", methods=["POST"])
def exam_clearq(eid):
    """清空试卷"""
    ex = db.get_exam(eid)
    if not ex:
        flash("考试不存在", "err")
        return redirect(url_for("index"))
    if _paper_locked(ex):
        return redirect(url_for("exam_edit", eid=eid, tab="basic"))
    db.invalidate()          # 试卷变了，清缓存
    tab = request.form.get("tab", "")
    n = len(db.get_exam_questions(eid))
    db.clear_exam_questions(eid)
    flash(f"已清空试卷（清掉 {n} 题）")
    return redirect(url_for("exam_edit", eid=eid, tab=tab))



if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=9000)
    p.add_argument("--lan-ip", default=None,
                   help="对外广播的 IP（学生端自动发现会拿到这个地址）。"
                        "不填就自动探测")
    p.add_argument("--name", default=SERVER_NAME, help="服务名，学生端会显示")
    p.add_argument("--no-beacon", action="store_true", help="关闭自动广播")
    p.add_argument("--multicast", action="store_true", help="同时用组播（需交换机支持）")
    p.add_argument("--no-setup", action="store_true", help="跳过自动初始化（题库/素材/测试考试）")
    p.add_argument("--no-browser", action="store_true",
                   help="启动后不自动打开浏览器（EXE 双击默认会自动打开）")
    p.add_argument("--data-dir", default=None,
                   help="可写数据目录（题库/答卷/数据库都放这里）。"
                        "默认：EXE 旁边；源码运行 = 项目根目录")
    a = p.parse_args()

    if a.data_dir:
        # 换数据目录：改 runtime 的路径 + 让 db/auth 也跟着走。
        # （此时数据库连接还没建立 —— db.init() 在下面才调用）
        os.environ["EXAM_DATA"] = a.data_dir
        runtime.DATA_DIR = os.path.abspath(a.data_dir)
        runtime.DB_FILE = os.path.join(runtime.DATA_DIR, "exam.db")
        runtime.AUTH_FILE = os.path.join(runtime.DATA_DIR, "auth.json")
        runtime.SECRET_FILE = os.path.join(runtime.DATA_DIR, "secret.key")
        runtime.BANK_DIR = os.path.join(runtime.DATA_DIR, "题库")
        runtime.MATERIAL_DIR = os.path.join(runtime.DATA_DIR, "materials")
        runtime.UPLOAD_DIR = os.path.join(runtime.DATA_DIR, "uploads")
        runtime.EXAM_DIR = os.path.join(runtime.DATA_DIR, "exams")
        runtime.QIMG_DIR = os.path.join(runtime.DATA_DIR, "qimg")
        runtime.prepare()
        os.environ["EXAM_DB"] = runtime.DB_FILE
        os.environ["EXAM_AUTH_FILE"] = runtime.AUTH_FILE
        db.DB_PATH = runtime.DB_FILE
        auth.AUTH_FILE = runtime.AUTH_FILE
        BANK_DIR = runtime.BANK_DIR
        MATERIAL_DIR = runtime.MATERIAL_DIR
        UPLOAD_DIR = runtime.UPLOAD_DIR
        EXAM_DIR = runtime.EXAM_DIR
        QIMG_DIR = runtime.QIMG_DIR

    db.init()          # 建表（要在数据目录确定之后）
    auth.ensure()      # 首次运行创建默认密码（auth 模块导入时也调用过，这里兜底）

    SERVER_NAME = a.name
    HTTP_PORT = a.port

    # ==================== 开机自动装载内置资源 ====================
    #
    # 老师不用手动导入任何东西：
    #   · 内置理论题库（含雅安会考模拟题）→ 进题库，重复的自动跳过
    #   · 内置实操题库（素材 + 要求 + 评分规则）→ 进「实操题库」
    #
    # ⚠️ 这里**只装题库，不自动建考试**。
    #    本堂课考什么由老师在「实操题库」里自己挑，一键装进考试。
    if not a.no_setup:
        # 题库/素材都用「可写目录」那份（EXE 首次运行已从内置资源复制过来），
        # 老师往这里放自己的 xlsx 也能被装载
        BANK_DIR = runtime.BANK_DIR
        MATERIAL_DIR = runtime.MATERIAL_DIR

        print("\n" + "=" * 50)
        print("  正在装载内置资源...")
        print("=" * 50)

        # 1) 内置题库（Excel + JSON，按题干去重，重复启动不会重复灌题）
        for name in ("题库_WPS三件套.xlsx", "题库_初高中信息技术.xlsx",
                     "题库_雅安会考模拟.xlsx"):
            p = os.path.join(BANK_DIR, name)
            if os.path.isfile(p):
                try:
                    import import_excel
                    items, _ = import_excel.parse_excel(p)
                    # 来源标注：文件名带「会考」的按会考模拟题归类
                    src = "雅安会考模拟题" if "会考" in name else "自创题"
                    for it in items or []:
                        if not it.get("source"):
                            it["source"] = src
                    if items:
                        ok, skip = db.import_questions(items)
                        print(f"  ✅ {name}：新增 {ok} 题，已有 {skip} 题")
                except Exception as e:
                    print(f"  ⚠️  {name} 导入失败: {e}")

        # JSON 题库：题库目录下**所有** .json 都装
        # （老师自己丢一个 new.json 进去，重启就进题库）
        # 排除：实操题库（下面单独装）、打字范文库（是范文不是题）、_ 开头的临时文件
        SKIP_JSON = {"实操题库.json", "小学语文打字范文.json",
                     "小学英语打字范文.json"}
        bank_files = []
        if os.path.isdir(BANK_DIR):
            bank_files = [f for f in sorted(os.listdir(BANK_DIR))
                          if f.lower().endswith(".json")
                          and f not in SKIP_JSON and not f.startswith("_")]
        for bank_name in bank_files:
            bank_path = os.path.join(BANK_DIR, bank_name)
            try:
                with open(bank_path, "r", encoding="utf-8") as f:
                    items = json.load(f)
                if not isinstance(items, list):
                    continue
                src = "雅安会考模拟题" if "会考" in bank_name else "自创题"
                for it in items:
                    if isinstance(it, dict) and not it.get("source"):
                        it["source"] = src
                ok, skip = db.import_questions(items)
                print(f"  ✅ {bank_name}：新增 {ok} 题，已有 {skip} 题")
            except Exception as e:
                print(f"  ⚠️  {bank_name} 导入失败: {e}")

        # 分类整理：没填难度/来源的老题补上（幂等，重复启动不重复干活）
        try:
            n_d, n_s = db.tidy_question_meta()
            if n_d or n_s:
                print(f"  ✅ 题库分类整理：补难度 {n_d} 题、补来源 {n_s} 题")
        except Exception as e:
            print(f"  ⚠️  题库分类整理失败: {e}")

        # 2) 实操题库（重名更新；只进题库，不建考试）
        pq_path = os.path.join(BANK_DIR, "实操题库.json")
        if os.path.isfile(pq_path):
            try:
                with open(pq_path, "r", encoding="utf-8") as f:
                    bank = json.load(f)
                n_new = n_upd = 0
                for item in bank:
                    _pid, is_new = db.add_practice(item)
                    n_new += 1 if is_new else 0
                    n_upd += 0 if is_new else 1
                print(f"  ✅ 实操题库：新增 {n_new} 套，更新 {n_upd} 套")
            except Exception as e:
                print(f"  ⚠️  实操题库装载失败: {e}")

        print(f"  📚 理论题库 {len(db.list_questions())} 题 / "
              f"实操题库 {len(db.list_practice())} 套 / "
              f"已有考试 {len(db.list_exams())} 场")
        print("=" * 50 + "\n")

    # ==================== 启动服务器 ====================
    # 传给 beacon：学生端自动发现时广播的就是这个地址
    if a.lan_ip:
        os.environ["EXAM_LAN_IP"] = a.lan_ip.strip()

    if not a.no_beacon:
        import beacon as beacon_mod
        beacon_obj = beacon_mod.Beacon(http_port=a.port, name=a.name,
                                       use_multicast=a.multicast,
                                       logger=lambda m: print(m, flush=True))
        beacon_obj.start()

    shown = a.lan_ip or "自动探测"
    total_q = len(db.list_questions())
    total_e = len(db.list_exams())
    print(f"\n  老师端:  http://{shown}:{a.port}（需登录）")
    if auth.needs_setup():
        print("  ⚠️ 首次运行：打开后台页面，按提示设置初始密码")
    print(f"  后台:    http://127.0.0.1:{a.port}")
    print(f"  学生端:  http://{shown}:{a.port}（学生端无需配置，会自动发现）")
    print(f"  题库:    {total_q} 题 / {total_e} 场考试")
    if a.lan_ip:
        print(f"  自动发现: 广播地址 http://{a.lan_ip}:{a.port}")
    else:
        print(f"  自动发现: 广播本机探测到的 IP（想固定请用 --lan-ip）")
    print()
    print("  ⚠️ 这个窗口不要关！关掉窗口 = 服务停止，学生就交不了卷了。")
    print()

    # 双击 EXE 启动时自动打开后台页面（源码运行时保持老行为，不打扰）
    want_browser = (not a.no_browser) and getattr(sys, "frozen", False)
    if want_browser:
        def _open_later():
            time.sleep(1.5)
            try:
                import webbrowser
                webbrowser.open(f"http://127.0.0.1:{a.port}/")
            except Exception:
                pass
        threading.Thread(target=_open_later, daemon=True).start()

    app.run(host=a.host, port=a.port, threaded=True)
