#!/usr/bin/env python3
"""
ikee考务中心 —— 学生端
两阶段：理论题 → 实操题（自动拉起 WPS）

设计要点：
  1. tkinter 延迟导入 —— 这样在没有 GUI 的环境也能 import 本模块跑逻辑测试
  2. 全屏置顶 + 屏蔽热键 + 定时查杀违禁进程
  3. 心跳拉取服务端阶段，阶段变化自动切换界面
"""
import json
import os
import platform
import re
import shutil
import socket
import sys
import threading
import time
import urllib.parse
import urllib.request
import uuid

CLIENT_DIR = os.path.dirname(os.path.abspath(__file__))
PACKAGE_DIR = os.path.dirname(CLIENT_DIR)
SRC_DIR = os.path.dirname(PACKAGE_DIR)
sys.path.insert(0, CLIENT_DIR)
sys.path.insert(0, SRC_DIR)


def _app_dir():
    """
    需要持久化的文件（config.json 等）该放哪。

    ⚠️ PyInstaller --onefile 打包后，__file__ 指向每次启动解包的
    _MEIxxxx 临时目录（退出即删）—— config 写进去就等于每次丢。
    打包成 exe 时改用 exe 所在目录，源码运行时用脚本目录。
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return CLIENT_DIR

import guard      # noqa: E402
import wps        # noqa: E402

IS_WIN = platform.system() == "Windows"

# 学生机工作目录：ikee_Exam
# 优先级：exe 同级 > 桌面 > D:\ikee_Exam
def _find_work_root():
    """自动查找最佳工作目录"""
    folder = "ikee_Exam"
    # 1. exe 同级目录（打包后 exe 所在目录）
    try:
        exe_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
        candidate = os.path.join(exe_dir, folder)
        if os.path.isdir(candidate) or os.access(exe_dir, os.W_OK):
            os.makedirs(candidate, exist_ok=True)
            return candidate
    except Exception:
        pass
    # 2. 桌面
    try:
        desktop = os.path.join(os.path.expanduser("~"), "Desktop")
        if os.path.isdir(desktop):
            candidate = os.path.join(desktop, folder)
            os.makedirs(candidate, exist_ok=True)
            return candidate
    except Exception:
        pass
    # 3. D 盘兜底
    return os.path.join("D:\\", folder) if IS_WIN else os.path.join("/tmp", folder)

WORK_ROOT = os.environ.get("EXAM_WORK", "") or _find_work_root()

# 目录名里不允许出现的字符（Windows 最严，顺手把路径分隔符也挡掉防穿越）
_INVALID_DIR_CHARS = r'\/:*?"<>|'


def _safe_dir_name(name, fallback=""):
    """把学生姓名/考试编号净化成安全的目录名（非法字符、首尾空格点都去掉）"""
    s = "".join(ch for ch in str(name or "")
                if ch not in _INVALID_DIR_CHARS and ch >= " ")
    return s.strip().strip(".") or fallback

TYPE_LABEL = {"single": "单选题", "multi": "多选题", "judge": "判断题",
              "blank": "填空题", "code": "程序填空", "drag": "拖拽题"}


# ==================== 配置 ====================
class Config:
    """
    server 留空 = 自动发现（推荐）
    填了具体地址 = 优先直连，连不上再自动发现
    """
    FILE = os.path.join(_app_dir(), "config.json")

    def __init__(self):
        self.server = ""          # 空 = 自动寻找
        # 备用直连地址：留空。广播/主动探测都找不到时，学生在界面上手动输入即可；
        # 手动填过一次会记住，下次优先直连。不再写死任何公网段 IP。
        self.fallback = ""
        self.hostname = platform.node()
        # 机器唯一标识：以前直接用「Windows 机器名」当身份，同 Ghost / 同型号
        # 的机器机器名大量重复（38 台机器只有 16 个不同名字），服务端按机器名
        # 做主键，同名机器互相覆盖 → 老师端只看到 16 个学生。
        # 现在换成 MAC（局域网内唯一），生成一次就写进 config.json 固定下来。
        self.client_id = ""
        self.token = ""
        self.name = ""            # 学生姓名（登录时填）
        self.seat = ""            # 座位号/学号（登录时填）
        self.logged_in = False    # 是否已登录（填过姓名座位号或免密码进入）
        self.passwordless = False
        self.work_dir = WORK_ROOT
        self.auto_discover = True
        # 窗口形态
        #   normal（默认）  普通窗口：能拖动、缩放、最小化、随时关闭，
        #                   和别的软件一样。老师要求不要全屏。
        #   fullscreen      严格模式（老的防作弊行为）：理论题全屏置顶 +
        #                   考试中屏蔽 Alt+F4/Esc/Alt+Tab、禁止退出。
        #                   需要时在 client/config.json 里手动填 "fullscreen"。
        self.window_mode = "normal"
        self.load()

    def load(self):
        if os.path.isfile(self.FILE):
            try:
                with open(self.FILE, "r", encoding="utf-8") as f:
                    d = json.load(f)
                self.server = d.get("server", self.server) or ""
                self.fallback = d.get("fallback", self.fallback) or ""
                self.hostname = d.get("hostname", self.hostname)
                # 老配置没有 client_id：按当前 MAC 补一个并立刻存盘
                self.client_id = (d.get("client_id") or "").strip()
                self.token = (d.get("token") or "").strip()
                if not self.client_id:
                    self.client_id = self._new_client_id()
                    self._save_quiet()
                self.name = d.get("name", self.name) or ""
                self.seat = d.get("seat", self.seat) or ""
                self.logged_in = bool(d.get("logged_in", False))
                self.passwordless = bool(d.get("passwordless", False))
                self.auto_discover = bool(d.get("auto_discover", True))
                # work_dir：如果配置的路径不可用，用新的默认路径
                saved_wd = d.get("work_dir", "")
                if saved_wd and os.path.isdir(saved_wd):
                    self.work_dir = saved_wd
                else:
                    self.work_dir = WORK_ROOT
                # 老配置里的 "auto"/"window" 一律按普通窗口处理 ——
                # 以前默认是"理论题全屏"，老师明确要求改成正常窗口，
                # 所以这里把历史值迁移过来，只认显式写的 "fullscreen"。
                wm = (d.get("window_mode") or "").strip().lower()
                self.window_mode = "fullscreen" if wm == "fullscreen" else "normal"
            except Exception as e:
                # 配置损坏：保留默认值继续启动（学生重走登录即可），
                # 但别再静默 —— 打印到控制台，方便机房管理员排查
                try:
                    print(f"[config] 读取 config.json 失败，已用默认值: {e}",
                          flush=True)
                except Exception:
                    pass

    def save(self):
        with open(self.FILE, "w", encoding="utf-8") as f:
            json.dump({"server": self.server, "fallback": self.fallback,
                       "hostname": self.hostname,
                       "client_id": self.client_id,
                       "token": self.token,
                       "name": self.name, "seat": self.seat,
                       "logged_in": self.logged_in,
                       "passwordless": self.passwordless,
                       "work_dir": self.work_dir,
                       "auto_discover": self.auto_discover,
                       "window_mode": self.window_mode},
                       f, ensure_ascii=False, indent=2)

    def _save_quiet(self):
        """配置迁移时用：写失败也不该影响启动"""
        try:
            self.save()
        except Exception:
            pass

    @staticmethod
    def _new_client_id():
        """
        生成本机唯一标识。优先 MAC（换网卡才会变，一般不会）；
        MAC 取不到（虚拟网卡 / 全是 0）就生成一个随机号并写进配置固定下来。
        """
        mac = (Api.local_mac() or "").replace(":", "").upper()
        # 只排除「取不到 MAC」的占位值（全 0 / 全 F），其余都算有效。
        # 不能用 mac.strip("0F") 判断：真实 MAC 也可能只由 0/F 组成
        # （如 0F0F0F0F0F0F），会被误判成空，导致身份退化成随机号漂移。
        if len(mac) == 12 and mac not in ("000000000000", "FFFFFFFFFFFF"):
            return "PC-" + mac
        return "PC-" + uuid.uuid4().hex[:12].upper()


# ==================== 与服务端通信 ====================
class Api:
    def __init__(self, cfg):
        self.cfg = cfg

    def _url(self, path):
        return self.cfg.server.rstrip("/") + path

    def _post(self, path, data=None, timeout=10):
        return self._request(path, data=data, timeout=timeout)

    def _get(self, path, timeout=10):
        return self._request(path, timeout=timeout)

    def _headers(self, extra=None):
        headers = dict(extra or {})
        if getattr(self.cfg, "token", ""):
            headers["X-Exam-Token"] = self.cfg.token
        return headers

    def _request(self, path, data=None, timeout=10):
        """
        统一请求入口。
        服务端会用 403/400 表示「不允许」（如实操阶段还交理论题），
        这里不能让异常冒出去把学生端搞崩，统一转成 {ok:False, msg:...}
        """
        try:
            if data is None:
                req = urllib.request.Request(self._url(path), method="GET",
                                             headers=self._headers())
            else:
                req = urllib.request.Request(
                    self._url(path),
                    data=json.dumps(data).encode("utf-8"),
                    headers=self._headers({"Content-Type": "application/json"}),
                    method="POST")
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            # 尽量把服务端返回的错误信息读出来
            detail = ""
            try:
                body = json.loads(e.read().decode("utf-8"))
                detail = body.get("msg", "")
            except Exception:
                pass
            return {"ok": False, "msg": detail or f"服务器拒绝（HTTP {e.code}）"}
        except Exception as e:
            return {"ok": False, "msg": f"{type(e).__name__}: {e}"}

    def hello(self, status=None):
        """握手：上报姓名座位号 + 拉取当前状态"""
        r = self._post("/api/hello", {
            "hostname": self.cfg.hostname,
            "client_id": self.cfg.client_id,
            "ip": self.local_ip(),
            "mac": self.local_mac(),
            "status": status,
            "name": getattr(self.cfg, "name", ""),
            "seat": getattr(self.cfg, "seat", ""),
        })
        if r.get("ok"):
            student = r.get("student") or {}
            if student.get("name"):
                self.cfg.name = str(student["name"])
            if student.get("seat"):
                self.cfg.seat = str(student["seat"])
            if (r.get("exam") or {}).get("passwordless"):
                self.cfg.logged_in = True
                self.cfg.passwordless = True
            if r.get("token"):
                self.cfg.token = r["token"]
            try:
                self.cfg.save()
            except Exception:
                pass
        elif not r.get("ok") and self.cfg.token and "机器凭证" in (r.get("msg") or ""):
            self.cfg.token = ""
            try:
                self.cfg.save()
            except Exception:
                pass
            return self.hello(status)
        return r

    def poll(self, progress=None):
        params = {"host": self.cfg.hostname, "client_id": self.cfg.client_id}
        if progress:
            params["progress"] = json.dumps(progress, ensure_ascii=False)
            if progress.get("status"):
                params["status"] = progress["status"]
        q = urllib.parse.urlencode(params)
        return self._get(f"/api/poll?{q}")

    def paper(self):
        q = urllib.parse.urlencode({"host": self.cfg.hostname,
                                    "client_id": self.cfg.client_id})
        return self._get(f"/api/paper?{q}")

    def submit_theory(self, exam_id, answers):
        return self._post("/api/theory/submit",
                          {"host": self.cfg.hostname,
                           "client_id": self.cfg.client_id,
                           "exam_id": exam_id, "answers": answers})

    def finish_theory(self, exam_id):
        return self._post("/api/theory/finish",
                          {"host": self.cfg.hostname,
                           "client_id": self.cfg.client_id,
                           "exam_id": exam_id})

    def typing_report(self, exam_id, reports):
        """打字模块（小学）进度上报"""
        return self._post("/api/typing/report",
                          {"host": self.cfg.hostname,
                           "client_id": self.cfg.client_id,
                           "exam_id": exam_id, "reports": reports})

    def ack_commands(self, ids):
        try:
            return self._post("/api/commands/ack", {"ids": list(ids)})
        except Exception:
            return {"ok": False}

    def get_bytes(self, path, timeout=10):
        """原样下载（选项图片等二进制资源）"""
        req = urllib.request.Request(self._url(path), headers=self._headers())
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()

    def log(self, level, msg):
        try:
            self._post("/api/log", {"host": self.cfg.hostname,
                                    "client_id": self.cfg.client_id,
                                    "level": level, "msg": msg})
        except Exception:
            pass

    def download_material(self, name, dest_dir, on_progress=None):
        """
        下载素材到 dest_dir，可回调下载进度。
        :param on_progress: fn(got_bytes, total_bytes) —— 进度条用
        先写 .part，下完再改名：中途断了不会留半个文件被当成学生答卷。
        """
        os.makedirs(dest_dir, exist_ok=True)
        # 素材名来自服务端，净化掉路径分隔符，防止 ../ 目录穿越；
        # 下载链接也统一用净化后的名字，保证两边一致
        safe = _safe_dir_name(name, "material")
        dest = os.path.join(dest_dir, safe)
        tmp = dest + ".part"
        url = self._url(f"/api/material/{urllib.parse.quote(safe)}")
        try:
            with urllib.request.urlopen(
                urllib.request.Request(url, headers=self._headers()),
                timeout=120) as r:
                try:
                    total = int(r.headers.get("Content-Length") or 0)
                except Exception:
                    total = 0
                got = 0
                with open(tmp, "wb") as f:
                    while True:
                        chunk = r.read(65536)
                        if not chunk:
                            break
                        f.write(chunk)
                        got += len(chunk)
                        if on_progress:
                            try:
                                on_progress(got, total)
                            except Exception:
                                pass
            try:
                os.replace(tmp, dest)
            except OSError:
                # 目标被 WPS 占用等：退回直接写
                with open(dest, "wb") as f:
                    with open(tmp, "rb") as g:
                        f.write(g.read())
        finally:
            # 不管成功失败，都清掉 .part：中断下载不该留下半个文件
            try:
                if os.path.isfile(tmp):
                    os.remove(tmp)
            except OSError:
                pass
        return dest

    def upload(self, exam_id, path, task_id=0):
        """上传答卷（multipart，手写不依赖 requests）。task_id：哪一道操作题"""
        boundary = uuid.uuid4().hex
        fname = os.path.basename(path)
        with open(path, "rb") as f:
            content = f.read()

        body = b""
        body += f"--{boundary}\r\n".encode()
        body += f'Content-Disposition: form-data; name="host"\r\n\r\n{self.cfg.hostname}\r\n'.encode()
        body += f"--{boundary}\r\n".encode()
        body += (f'Content-Disposition: form-data; name="client_id"\r\n\r\n'
                 f'{self.cfg.client_id}\r\n').encode()
        body += f"--{boundary}\r\n".encode()
        body += f'Content-Disposition: form-data; name="exam_id"\r\n\r\n{exam_id}\r\n'.encode()
        body += f"--{boundary}\r\n".encode()
        body += f'Content-Disposition: form-data; name="task_id"\r\n\r\n{int(task_id or 0)}\r\n'.encode()
        body += f"--{boundary}\r\n".encode()
        body += (f'Content-Disposition: form-data; name="file"; filename="{fname}"\r\n'
                 f"Content-Type: application/octet-stream\r\n\r\n").encode()
        body += content + b"\r\n"
        body += f"--{boundary}--\r\n".encode()

        req = urllib.request.Request(
            self._url("/api/upload"), data=body, method="POST",
            headers=self._headers({"Content-Type": f"multipart/form-data; boundary={boundary}"}))
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read().decode("utf-8"))

    def upload_drawing(self, exam_id, data, ext=".png", kind="draw"):
        """
        上传画图作品（一张压缩图的字节）。重复上传服务端以最新为准。
        kind: draw=画板创作（自由画）, beads=拼豆题 —— 两道小题各传各的。
        """
        kind = "beads" if kind == "beads" else "draw"
        boundary = uuid.uuid4().hex
        tag = "拼豆" if kind == "beads" else "画板"
        fname = tag + (ext if ext.startswith(".") else "." + ext)
        body = b""
        body += f"--{boundary}\r\n".encode()
        body += f'Content-Disposition: form-data; name="host"\r\n\r\n{self.cfg.hostname}\r\n'.encode()
        body += f"--{boundary}\r\n".encode()
        body += (f'Content-Disposition: form-data; name="client_id"\r\n\r\n'
                 f'{self.cfg.client_id}\r\n').encode()
        body += f"--{boundary}\r\n".encode()
        body += f'Content-Disposition: form-data; name="exam_id"\r\n\r\n{int(exam_id)}\r\n'.encode()
        body += f"--{boundary}\r\n".encode()
        body += f'Content-Disposition: form-data; name="kind"\r\n\r\n{kind}\r\n'.encode()
        body += f"--{boundary}\r\n".encode()
        body += (f'Content-Disposition: form-data; name="file"; filename="{fname}"\r\n'
                 f"Content-Type: application/octet-stream\r\n\r\n").encode()
        body += data + b"\r\n"
        body += f"--{boundary}--\r\n".encode()
        req = urllib.request.Request(
            self._url("/api/drawing/upload"), data=body, method="POST",
            headers=self._headers({"Content-Type": f"multipart/form-data; boundary={boundary}"}))
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read().decode("utf-8"))

    def submit_empty(self, exam_id, task_id=0):
        """
        缺卷提交：交卷时这道题根本没做（没找到答卷），
        让服务端记一笔「未作答，按 0 分」，不再弹「提交失败让我重做」。
        """
        body = json.dumps({"host": self.cfg.hostname,
                           "client_id": self.cfg.client_id,
                           "exam_id": int(exam_id),
                           "task_id": int(task_id or 0)}).encode("utf-8")
        req = urllib.request.Request(
            self._url("/api/upload_empty"), data=body, method="POST",
            headers=self._headers({"Content-Type": "application/json"}))
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))

    @staticmethod
    def local_ip():
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("192.168.10.1", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            try:
                return socket.gethostbyname(socket.gethostname())
            except Exception:
                return ""

    @staticmethod
    def local_mac():
        try:
            return ":".join(["{:02x}".format((uuid.getnode() >> i) & 0xff)
                             for i in range(0, 48, 8)][::-1])
        except Exception:
            return ""


# ==================== 答题状态（纯逻辑，可测试） ====================
class AnswerSheet:
    """管理理论题的作答状态"""

    def __init__(self, questions):
        self.questions = questions
        self.answers = {}          # qid -> 答案字符串
        self.index = 0

    def load_saved(self, saved):
        """载入服务端已保存的答案"""
        for k, v in (saved or {}).items():
            try:
                self.answers[int(k)] = v
            except (TypeError, ValueError):
                pass

    def set(self, qid, value):
        v = str(value or "").strip()
        if v:
            self.answers[qid] = v
        else:
            self.answers.pop(qid, None)

    def get(self, qid):
        return self.answers.get(qid, "")

    def answered_count(self):
        return len(self.answers)

    def total(self):
        return len(self.questions)

    def unanswered(self):
        """返回未作答的题号列表（1 起）"""
        out = []
        for i, q in enumerate(self.questions, 1):
            if q["id"] not in self.answers:
                out.append(i)
        return out

    def current(self):
        if 0 <= self.index < len(self.questions):
            return self.questions[self.index]
        return None

    def goto(self, i):
        if 0 <= i < len(self.questions):
            self.index = i
            return True
        return False

    def next(self):
        return self.goto(self.index + 1)

    def prev(self):
        return self.goto(self.index - 1)

    def payload(self):
        """提交给服务端的格式：{qid字符串: 答案}"""
        return {str(k): v for k, v in self.answers.items()}

    def is_complete(self):
        return len(self.answers) >= len(self.questions)


# ==================== 答案批量同步 ====================
class AnswerSync:
    """
    答案同步器（计算机二级那种做法）：

        · 学生每改一次答案 → 只写本地 + 打个「脏」标记，不发网络请求
        · 每隔 interval 秒   → 把这段时间攒下的改动一次性提交
        · 交卷 / 切页 / 退出 / 断线恢复 → 立刻提交一次

    以前是「每敲一个字符就发一次 HTTP」：填空题敲 10 个字就是 10 个请求，
    50 台机器一起敲，服务端 SQLite 是单连接串行的，请求全排队，
    界面就是这么一卡一卡的。
    """
    INTERVAL = 10          # 常规同步间隔（秒）

    def __init__(self, client, exam_id, interval=None):
        self.client = client
        self.exam_id = exam_id
        self.interval = interval or self.INTERVAL
        self.dirty = {}                  # 待提交的答案 {qid: value}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self.on_saved = None             # 提交成功回调（界面显示"已保存 xx:xx"）
        self.last_saved = 0
        self.failed = 0                  # 连续失败次数（断线时不要刷屏报错）

    # ---------- 对外 ----------
    def update(self, answers):
        """学生改了答案：只记下来，不联网"""
        with self._lock:
            self.dirty.update(answers)

    def flush(self):
        """立刻提交攒下的答案（交卷 / 切页 / 退出前必调）"""
        with self._lock:
            if not self.dirty:
                return True
            payload = dict(self.dirty)
            self.dirty.clear()
        return self._send(payload)

    def start(self):
        threading.Thread(target=self._loop, daemon=True).start()
        return self

    def stop(self):
        self._stop.set()

    @property
    def pending(self):
        with self._lock:
            return len(self.dirty)

    # ---------- 内部 ----------
    def _send(self, payload):
        # 用 client.api（重连后 api 会被换掉，这里每次取最新的）
        try:
            r = self.client.api.submit_theory(self.exam_id, payload)
            if r.get("ok"):
                self.last_saved = time.time()
                self.failed = 0
                if self.on_saved:
                    try:
                        self.on_saved(r.get("answered", 0))
                    except Exception:
                        pass
                return True
            msg = r.get("msg", "")
        except Exception as e:
            msg = str(e)
        # 失败：还回队列，下轮重试（答案宁可晚到，不能丢）
        with self._lock:
            self.dirty.update(payload)
        self.failed += 1
        if self.failed <= 3:
            self.client._log("warn", f"答案同步失败（稍后重试）: {msg}")
        return False

    def _loop(self):
        while not self._stop.wait(self.interval):
            if self._stop.is_set():
                break
            self.flush()
        self.flush()      # 退出前最后交一次


# ==================== 考试窗口 ====================
class LockedRoot:
    """
    考试窗口，两个维度分开控制：

        locked（能不能退出）
            False  可以退出：Esc / 关闭窗口 会弹确认框
            True   不能退出：Alt+F4 / Esc / Alt+Tab 全部屏蔽

        cover（要不要盖住屏幕）
            True   全屏  —— 只在**理论题答题**时用（严格模式下的防作弊）
            False  普通窗口，不置顶

    ⚠️ 默认是**普通窗口**（window_mode="normal"）：
       能拖动、能缩放、能最小化、随时能关，和别的软件一样。
       想恢复"全屏 + 禁止退出"的老防作弊行为，把 client/config.json 里的
       window_mode 改成 "fullscreen"（见 Config 里的说明）。

    为什么 cover 要能关：
      实操题学生要用 WPS 打开素材做操作，考试窗口再全屏置顶，
      WPS 就被挡在后面了 —— 学生根本没法做题。
    """

    # 严格模式下要屏蔽的键。
    # ⚠️ 只保留 Tk 真正能拦的：Alt+F4（触发 WM_DELETE_WINDOW）、Esc、Ctrl+Esc。
    #   Alt+Tab / Ctrl+Alt+Del 是 Windows 系统级快捷键，Tk 根本拦不住；
    #   <Alt-Key> 会把所有 Alt 组合键（含正常输入）一并吞掉，误伤输入。
    BLOCK_KEYS = ("<Alt-F4>", "<Escape>", "<Control-Escape>")

    def __init__(self, tk, title="ikee考务中心", window_mode="normal"):
        """
        :param window_mode: normal = 普通窗口（默认，可缩放/最小化/关闭）
                            fullscreen = 严格模式：理论题全屏 + 禁止退出
        """
        self.tk = tk
        self.window_mode = (window_mode
                            if window_mode in ("normal", "fullscreen")
                            else "normal")
        self.root = tk.Tk()
        self.root.title(title)
        self.root.configure(bg="#f0f2f5")
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self._locked = False           # 是否锁死（禁止退出）
        self._cover = False            # 是否全屏盖住屏幕
        self.on_exit = None            # 退出回调（由 GUI 设置）
        self._top_job = None
        self._normal_placed = False    # 普通窗口只摆一次位置/尺寸

        # Esc 单独绑：未锁定时用来退出
        self.root.bind_all("<Escape>", self._on_escape)
        self.root.bind_all("<F11>", self._noop)
        self.set_cover(False)          # 打开时是普通窗口
        self._keep_top()

    # ---------- 状态切换 ----------
    def set_locked(self, locked, cover=None):
        """
        切换锁定状态。**线程安全**：心跳线程也会调用本方法，
        真正的 Tk 操作（bind / attributes / state / lift）必须回主线程执行
        —— tkinter 不是线程安全的，后台线程直接操作在 Windows 上会随机崩溃。
        :param locked: True = 禁止退出
        :param cover:  True = 全屏置顶；False = 最大化不置顶；None = 跟随 locked
        """
        self._locked = bool(locked)
        want_cover = self._locked if cover is None else bool(cover)
        try:
            self.root.after(0, lambda: self._apply_locked(self._locked, want_cover))
        except Exception:
            # 主循环可能已经退出，忽略即可
            pass
        return self._locked

    def _apply_locked(self, locked, cover):
        """在主线程里执行真正的锁定/解绑/窗口形态切换"""
        try:
            if locked:
                for seq in self.BLOCK_KEYS:
                    self.root.bind_all(seq, self._block)
            else:
                # 只保留 Esc（用于退出），其余解绑
                for seq in self.BLOCK_KEYS:
                    try:
                        self.root.unbind_all(seq)
                    except Exception:
                        pass
                self.root.bind_all("<Escape>", self._on_escape)
        except Exception:
            pass
        self.set_cover(locked if cover is None else cover)

    def set_cover(self, cover):
        """
        cover=True  全屏（仅严格模式 window_mode=fullscreen）
        cover=False 普通窗口，不置顶
        """
        want = bool(cover) and self.window_mode == "fullscreen"
        self._cover = want
        try:
            if want:
                self.root.attributes("-fullscreen", True)
                # ⚠️ 不加 -topmost：窗口置顶会把中文输入法的候选框压在底下，
                # 学生打字时"看不见拼音、只看见结果"。
                # 全屏本身已经挡住其他窗口，防作弊足够。
                self.root.lift()
            elif self.window_mode == "normal":
                # 普通窗口：打开就最大化占满屏幕，但能缩放能最小化能关
                self.root.attributes("-fullscreen", False)
                self.root.attributes("-topmost", False)
                self._place_normal_window()
            else:
                self.root.attributes("-fullscreen", False)
                self.root.attributes("-topmost", False)
                # 严格模式下的非全屏（实操阶段）：最大化，方便看题目
                try:
                    self.root.attributes("-zoomed", True)
                except Exception:
                    try:
                        self.root.state("zoomed")
                    except Exception:
                        pass
        except Exception:
            pass
        return self._cover

    def _place_normal_window(self):
        """
        普通窗口模式：**打开就占满屏幕**（最大化），只摆一次。

        老师要求"打开默认全屏显示、不要窗口化"—— 所以这里最大化而不是
        开个小窗。但保留标题栏和最小化/关闭按钮：学生随时能最小化、还原、
        关掉（不像严格模式那样锁死），也不会盖住任务栏。

        摆好之后完全交给系统 —— 学生自己拖动/缩放/最小化都不会被抢回来。
        """
        if self._normal_placed:
            return
        self._normal_placed = True
        try:
            self.root.update_idletasks()
            self.root.resizable(True, True)
            self.root.minsize(900, 620)        # 再小答题卡就放不下了
            # Windows / 多数 Linux：直接最大化（保留任务栏和标题栏）
            try:
                self.root.state("zoomed")
                return
            except Exception:
                pass
            try:
                self.root.attributes("-zoomed", True)
                return
            except Exception:
                pass
            # 兜底：按屏幕尺寸铺满，底部给任务栏留 48px
            sw = self.root.winfo_screenwidth() or 1280
            sh = self.root.winfo_screenheight() or 800
            w, h = int(sw), max(600, int(sh) - 48)
            self.root.geometry(f"{w}x{h}+0+0")
        except Exception:
            pass

    @property
    def cover(self):
        return self._cover

    @property
    def locked(self):
        return self._locked

    # ---------- 事件 ----------
    @staticmethod
    def _noop(event=None):
        return "break"

    def _block(self, event=None):
        """考试中：所有退出尝试一律吞掉"""
        return "break"

    def _on_escape(self, event=None):
        """未开考时按 Esc = 请求退出"""
        if self._locked:
            return "break"
        self.request_exit()
        return "break"

    def _on_close(self):
        """点窗口关闭 / Alt+F4"""
        if self._locked:
            return                     # 考试中直接忽略
        self.request_exit()

    def request_exit(self):
        """
        请求退出。交给 GUI 去弹确认框（GUI 更清楚当前在哪个阶段）。
        GUI 没设置回调就直接退出。
        """
        if self._locked:
            return
        if self.on_exit:
            try:
                self.on_exit()
            except Exception:
                self.destroy()
        else:
            self.destroy()

    def destroy(self):
        """真正退出"""
        # 留个记录：学生反馈"程序自己关了"时，日志里能看到是哪条路径退的
        try:
            self._log("warn", "客户端退出")
        except Exception:
            pass
        try:
            self.root.attributes("-fullscreen", False)
            self.root.attributes("-topmost", False)
            try:
                self.root.attributes("-zoomed", False)
            except Exception:
                pass
        except Exception:
            pass
        try:
            self.root.quit()
            self.root.destroy()
        except Exception:
            pass

    def _keep_top(self):
        """
        定时把窗口抬回前台（只给严格模式用）。
        · 普通窗口模式完全不管 —— 学生切到 WPS 是正常的做题动作，
          老是把窗口抢回来反而添乱；
        · 只在窗口失焦时才 lift —— 无条件 lift 会打断学生打字。
        """
        try:
            if self.window_mode == "fullscreen" and self._locked and self._cover:
                # 失焦了才把窗口抬回来（保持前置）；
                # 不设 topmost，免得挡住输入法候选框
                if self.root.focus_displayof() is None:
                    self.root.lift()
        except Exception:
            pass
        self._top_job = self.root.after(3000, self._keep_top)

    def mainloop(self):
        self.root.mainloop()


# ==================== 主应用 ====================
class ExamClient:
    def __init__(self, cfg=None, headless=False):
        self.cfg = cfg or Config()
        self.api = Api(self.cfg)
        self.headless = headless        # True 时不启动 GUI，只跑逻辑（测试用）
        self.root = None                # LockedRoot 实例，GUI 启动后填充
        self.guard = guard.Guardian(interval=5, logger=self._log)
        self.state = {
            "exam": None, "phase": "idle", "remaining": None,
            "sheet": None, "work_dir": None, "materials": [],
            "submitted": False, "server_name": "",
            # section = 学生自己所在的板块（overview/theory/practice/wait_practice）
            # phase   = 服务端权威阶段（idle/theory/practice/paused/ended）
            # 两者分开，学生做题时才不会被心跳"以为阶段变了"踢回主菜单
            "section": None,
            "theory_answered": 0, "theory_total": 0,
            "practice_submitted": False,
            "ui_progress": {},
            # 本场考试的开考时刻：找答卷时用它排除"上一次考试留下的老文件"
            "exam_started_at": None,
            # 本机"刚考完哪一场"。
            # 只有自己参加过的那场考试结束，才显示「考试已结束」；
            # 换了一批学生（或换个同学来这台机器登录）时，
            # 应该回到待考界面等老师重新开始，而不是停在上一场的结束页。
            "ended_exam_id": None,
            # 本机这台机器"当前这个人已经确认过身份了吗"。
            # 考试一结束就置 False：下一场开考前必须先重新填姓名/座位号，
            # 否则老师一开考，坐在这台机器上的新同学会被当成上一个同学拉进去。
            "confirmed": bool(self.cfg.logged_in),
        }
        self.answer_sync = None        # 理论题答案批量同步器
        self._stop = threading.Event()
        self._heartbeat_thread = None  # 心跳线程引用（stop 时 join，避免双心跳）
        # ---- 连接状态（断线重连用）----
        self.conn = {
            "online": False,       # 当前是否连着
            "fails": 0,            # 连续失败次数
            "retrying": False,     # 正在重连中
            "last_ok": 0,          # 上次成功时间
            "attempt": 0,          # 已尝试次数（日志/界面显示）
        }
        self.on_conn = None        # 连接状态变化回调：(online:bool, msg:str)
        self.on_status = None      # 文字进度回调（自动发现/重连时会用）
        self.on_state = None       # 心跳状态更新回调（界面据此做低成本刷新）
        # 老师结束考试时的「强制交卷」回调（GUI 设置，走它交卷最稳：
        # GUI 知道当前有哪些题、哪些已经交过）
        self.on_force_submit = None
        self._seen_commands = set()

    # ---------- 答案同步（理论题）----------
    def begin_answer_sync(self, exam_id):
        """进入理论题时启动：答案先落本地，定时批量上传"""
        if (self.answer_sync and self.answer_sync.exam_id == exam_id
                and not self.answer_sync._stop.is_set()):
            return self.answer_sync
        if self.answer_sync:
            self.answer_sync.stop()
        self.answer_sync = AnswerSync(self, exam_id).start()
        return self.answer_sync

    def queue_answers(self, answers):
        """学生改了答案：入队，不联网（返回待提交数量）"""
        if self.answer_sync:
            self.answer_sync.update(answers)
            return self.answer_sync.pending
        return 0

    def flush_answers(self):
        """立刻把答案交上去（交卷 / 离开理论题 / 时间到前必调）"""
        if self.answer_sync:
            return self.answer_sync.flush()
        return True

    def stop_answer_sync(self):
        if self.answer_sync:
            self.answer_sync.stop()
            self.answer_sync = None

    # ---------- 自动发现 ----------
    def discover(self, on_status=None, timeout=8, force=False):
        """
        自动寻找考试服务器。
        :param force: True = 忽略 config 里写死的地址，强制重新找
        :return: url 或 None
        """
        if on_status is None:
            on_status = self.on_status
        import discover

        # 明确写了地址 → 先直连（force 时跳过）
        if self.cfg.server and not force:
            if on_status:
                on_status(f"连接 {self.cfg.server} …")
            if discover.try_url(self.cfg.server, timeout=2):
                if on_status:
                    on_status(f"已连接 {self.cfg.server}")
                return self.cfg.server
            if on_status:
                on_status("配置的地址不通，改为自动寻找…")

        url = discover.find_server(timeout=timeout, on_status=on_status,
                                   fallback=self.cfg.fallback)
        if url:
            # 记住这次的地址，但不写进 config（下次仍优先自动发现）
            discover.save_cache(url)
        return url

    # ---------- 工具 ----------
    def _log(self, level, msg):
        print(f"[{level}] {msg}", flush=True)
        try:
            self.api.log(level, msg)
        except Exception:
            pass

    # ---------- 工作目录（ikee_Exam/学生名字/{考试编号 + IkeeAnswer}）----------
    def student_root(self):
        """
        本机当前学生的目录：ikee_Exam/学生名字

        姓名可能为空（换考生、还没登录），依次退回座位号、机器名，
        保证任何情况下都能建出目录，不会因为空名字写到 ikee_Exam 根上。
        """
        name = (_safe_dir_name(self.cfg.name) or _safe_dir_name(self.cfg.seat)
                or _safe_dir_name(self.cfg.hostname, "unknown"))
        d = os.path.join(self.cfg.work_dir, name)
        os.makedirs(d, exist_ok=True)
        return d

    def exam_code(self, exam=None):
        """这场考试的编号（握手下发的 exam.code）；老服务器没有就退回 exam{id}"""
        ex = exam if exam else (self.state.get("exam") or {})
        return (_safe_dir_name(ex.get("code"))
                or f"exam{ex.get('id') or 0}")

    def exam_dir(self, exam_id, exam=None):
        """
        素材目录：ikee_Exam/学生名字/{考试编号}
        学生的作答文件默认也落在附近（WPS 打开素材后就存在这儿）。
        """
        ex = exam or {}
        if not ex:
            cur = self.state.get("exam") or {}
            ex = cur if (not exam_id or cur.get("id") == exam_id) \
                else {"id": exam_id}
        d = os.path.join(self.student_root(), self.exam_code(ex))
        os.makedirs(d, exist_ok=True)
        self.state["work_dir"] = d
        return d

    def answer_dir(self, create=True):
        """
        答卷目录：ikee_Exam/学生名字/IkeeAnswer
        ⚠️ 所有操作题**共用**这一个目录（老师定的结构），靠文件名区分题目。
        """
        d = os.path.join(self.student_root(), "IkeeAnswer")
        if create:
            os.makedirs(d, exist_ok=True)
        return d

    def ensure_work_dir(self, exam_id, task_id=0):
        """
        素材目录（保留旧签名，调用点不用改）。
        ⚠️ 多道操作题**共用同一个素材目录** —— 新结构不再按题分目录。
        """
        return self.exam_dir(exam_id)

    # ---------- 心跳 + 断线重连 ----------
    def set_ui_progress(self, progress):
        self.state["ui_progress"] = dict(progress or {})

    def progress_snapshot(self):
        progress = dict(self.state.get("ui_progress") or {})
        if not progress.get("status"):
            progress["status"] = ("working" if self.state.get("phase") in
                                  ("theory", "practice", "paused") else "idle")
        return progress

    def heartbeat_loop(self, interval=2):
        """
        心跳循环。断线后自动重连，用指数退避：

            3s → 6s → 12s → 24s（封顶）

        为什么退避而不是固定 3 秒狂试：
          服务器重启需要几十秒，50 台机器每 3 秒重试一次
          会制造大量无效流量。退避让重试逐渐稀疏，服务器一起来就能接上。

        为什么失败 2 次才判定断线：
          偶发抖动（网络一个丢包）不该弹「连接已断开」吓到学生。
        """
        while not self._stop.is_set():
            try:
                r = self.api.poll(self.progress_snapshot())
                if not r.get("ok"):
                    raise RuntimeError(r.get("msg") or "服务端返回失败")

                # ---- 成功 ----
                if not self.conn["online"]:
                    self._set_online(True, "已重新连接服务器")
                self.conn["fails"] = 0
                self.conn["attempt"] = 0
                self.conn["last_ok"] = time.time()

                self._handle_commands(r.get("commands") or [])
                new_phase = r.get("phase")
                if new_phase and new_phase != self.state["phase"]:
                    self._on_phase_change(new_phase)
                self.state["remaining"] = r.get("remaining")
                self.state["theory_answered"] = r.get("theory_answered", 0)
                self.state["theory_total"] = r.get("theory_total", 0)
                self.state["practice_submitted"] = bool(r.get("practice_submitted"))
                # 老师可能刚改了实操配置（装/删操作题）：跟着更新，
                # 学生端不用重连就能看到最新要求
                if "tasks" in r:
                    self.state["tasks"] = r.get("tasks") or []
                # 打字模块（小学）配置随心跳下发（没勾打字题时是 None）
                if "typing" in r:
                    self.state["typing"] = r.get("typing")
                if "drawing" in r:
                    self.state["drawing"] = r.get("drawing")
                ex = self.state.get("exam")
                if ex:
                    for k in ("requirement", "materials", "etype", "save_as",
                              "finish_note"):
                        if k in r:
                            ex[k] = r[k]
                # 界面刷新统一走这里（后台线程 → GUI 自己转主线程）
                if self.on_state:
                    try:
                        self.on_state(self.state)
                    except Exception:
                        pass
                # 用 Event.wait 代替 sleep：stop() 能立刻打断，而不是干等整段 interval
                if self._stop.wait(interval):
                    return

            except Exception as e:
                self.conn["fails"] += 1
                # 失败 2 次以上才认定断线，避免偶发抖动误报
                if self.conn["fails"] >= 2 and self.conn["online"]:
                    self._set_online(False, f"连接断开: {e}")
                self._log("error", f"心跳失败({self.conn['fails']}): {e}")

                # 退避等待（可被 stop 打断）
                wait = self._backoff(self.conn["fails"])
                for _ in range(wait * 10):
                    if self._stop.is_set():
                        return
                    time.sleep(0.1)

                # 尝试重连
                if not self._stop.is_set():
                    self._try_reconnect()

    def _backoff(self, fails):
        """退避秒数：3 → 6 → 12 → 24（封顶 24，约 5 分钟内重试 10 次）"""
        return int(min(24, 3 * (2 ** max(0, min(fails - 1, 3)))))

    def _set_online(self, online, msg=""):
        """连接状态变化：更新标志 + 通知界面"""
        if self.conn["online"] == online:
            return
        self.conn["online"] = online
        self._log("info" if online else "warn", msg or ("已连接" if online else "已断开"))
        if self.on_conn:
            try:
                self.on_conn(online, msg)
            except Exception:
                pass

    def _try_reconnect(self):
        """
        重连一次。先原地址，再广播重新发现。
        成功就恢复心跳；失败由外层循环继续退避重试。
        """
        if self.conn["retrying"]:
            return
        self.conn["retrying"] = True
        self.conn["attempt"] += 1
        say = self.on_status or (lambda m: None)

        try:
            # 1. 原地址还在吗（服务器只是卡了一下）
            try:
                h = self.api.hello()
                if h.get("ok", True):
                    self._set_online(True, "连接已恢复")
                    return True
            except Exception:
                pass

            # 2. 原地址不行 → 重新找服务器（IP 可能变了）
            say(f"正在重新寻找服务器（第 {self.conn['attempt']} 次）…")
            url = self.discover(timeout=6, force=True)
            if url:
                self.cfg.server = url
                self.api = Api(self.cfg)
                h = self.api.hello()
                self.state["server_name"] = h.get("server_name", "")
                self.state["exam"] = h.get("exam")
                self.state["materials"] = (h.get("exam") or {}).get("materials") or []
                self.state["typing"] = h.get("typing")
                self.state["drawing"] = h.get("drawing")
                self._set_online(True, f"已重新连接 {url}")
                # 重连后同步锁定：可能错过"开始考试"命令
                self.sync_lock()
                # 重连后补交未提交的答卷
                self._resubmit_if_needed()
                return True

            say("仍未找到服务器，稍后重试")
            return False
        except Exception as e:
            self._log("error", f"重连失败: {e}")
            return False
        finally:
            self.conn["retrying"] = False

    # ---------- 打字模块（小学） ----------
    def typing_config(self):
        """本场考试的打字配置 dict；没有打字题返回 None"""
        cfg = self.state.get("typing")
        if isinstance(cfg, dict) and cfg.get("tasks"):
            return cfg
        return None

    def drawing_config(self):
        """本场考试的画图配置 dict（含 brief 画题要求）；没有画图题返回 None"""
        cfg = self.state.get("drawing")
        return cfg if isinstance(cfg, dict) else None

    def has_drawing(self):
        return self.drawing_config() is not None

    def drawing_styles(self):
        """
        这场考试考哪几道画图小题：
        draw=画板创作（自由画），beads=拼豆题。两道可以同时考。
        老服务端只下发 style 字段时按它算；都没有 → ['draw']。
        """
        cfg = self.drawing_config()
        if not cfg:
            return []
        styles = cfg.get("styles")
        if not styles:
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
        return out or ["draw"]

    def drawing_state(self, kind="draw"):
        """
        画图内容容器：按小题分开（draw=画板自由画 / beads=拼豆），
        跨标签页共享（切标签重建面板也不丢画）。
        items 是画布对象列表、beads 是拼豆格子，由 drawing_ui 导出成压缩图上传。
        """
        kind = "beads" if kind == "beads" else "draw"
        ex = self.state.get("exam") or {}
        eid = ex.get("id")
        states = getattr(self, "_drawing_states", None)
        if not isinstance(states, dict):
            states = {}
            self._drawing_states = states
        st = states.get(kind)
        if not st or st.get("exam_id") != eid:
            st = {"exam_id": eid, "kind": kind, "items": [], "beads": None}
            states[kind] = st
        return st

    def has_typing(self):
        return self.typing_config() is not None

    def typing_state(self):
        """
        打字统计容器：**跨顶部标签页共享**。
        子任务提到顶部导航后，学生切标签会重建面板 —— 统计放在这里，
        切来切去进度/速度都不会丢（否则会被 0 覆盖回服务器）。
        """
        ex = self.state.get("exam") or {}
        eid = ex.get("id")
        st = getattr(self, "_typing_state", None)
        if not st or st.get("exam_id") != eid:
            st = {"exam_id": eid, "stats": {}}
            self._typing_state = st
        return st

    def ensure_typing_reporter(self):
        """启一次打字上报线程（每 3 秒把全班进度报给服务端），整个考试期间常驻"""
        t = getattr(self, "_typing_rep", None)
        if t is not None and t.is_alive():
            return

        def loop():
            while not self._stop.is_set():
                for _ in range(30):          # 3 秒，可被 stop 立刻打断
                    if self._stop.is_set():
                        return
                    time.sleep(0.1)
                self.flush_typing()
            self.flush_typing()              # 退出前最后报一次

        t = threading.Thread(target=loop, daemon=True)
        t.start()
        self._typing_rep = t

    def flush_typing(self):
        """把打字统计里已经有进度的任务报给服务端"""
        keys = ("task", "progress", "total", "hits", "typed", "correct_chars",
                "elapsed_ms", "wpm", "accuracy", "reaction_ms", "finished")
        try:
            st = self.typing_state()
            if not st.get("exam_id"):
                return
            reports = []
            for v in (st.get("stats") or {}).values():
                if not (v.get("finished") or v.get("progress") or v.get("typed")):
                    continue
                reports.append({k: v.get(k) for k in keys})
            if reports:
                self.api.typing_report(st["exam_id"], reports)
        except Exception:
            pass

    # ---------- 实操题清单 ----------
    def practice_tasks(self):
        """
        本场考试要做哪几道实操题。
        多道题时返回任务列表；老的单题考试返回一个"伪任务"（task_id=0）。
        这样界面只需要认一种结构。
        """
        tasks = list(self.state.get("tasks") or [])
        if tasks:
            return tasks
        ex = self.state.get("exam") or {}
        if not ex or not ex.get("has_practice", True):
            return []
        etype = ex.get("etype") or "word"
        if isinstance(etype, str) and etype.startswith("["):
            try:
                got = json.loads(etype)
                etype = got[0] if isinstance(got, list) and got else "word"
            except Exception:
                etype = "word"
        return [{
            "task_id": 0, "no": 1, "name": ex.get("title", "实操题"),
            "etype": etype,
            "ext": {"word": ".docx", "excel": ".xlsx",
                    "ppt": ".pptx"}.get(etype, ".docx"),
            "requirement": ex.get("requirement", ""),
            "materials": ex.get("materials") or [],
            "save_as": ex.get("save_as") or f"座位号_姓名{ext}",
            "submitted": bool(self.state.get("submitted")),
        }]

    def _resubmit_if_needed(self):
        """断线期间可能错过了收卷命令，重连后补交一次"""
        if self.state.get("submitted"):
            return
        if self.state.get("phase") not in ("practice", "ended"):
            return
        if not self.state.get("exam"):
            return
        try:
            tasks = self.practice_tasks()
            if len(tasks) > 1:
                for t in tasks:
                    if t.get("submitted"):
                        continue
                    ok, _ = self.auto_submit(t)
                    if ok:
                        self._log("info", f"重连后自动补交：{t.get('name')}")
            else:
                ok, _ = self.auto_submit(tasks[0] if tasks else None)
                if ok:
                    self._log("info", "重连后自动补交答卷")
        except Exception:
            pass

    def _force_exit(self, clear_login=False):
        """
        老师点「结束考试」→ 强制交卷 + 关闭客户端。

        在心跳线程里被调用：交卷是网络/文件操作，放后台线程没问题；
        弹提示和关窗口必须回主线程（tkinter 不是线程安全的），用 root.after。
        """
        self._log("warn", "收到结束考试指令：强制交卷并退出"
                          + ("（清除登录信息）" if clear_login else "（保留登录信息）"))
        # 1) 尽力交卷：理论题 + 每道实操题（没做的按缺卷 0 分）+ 打字统计
        try:
            ex = self.state.get("exam") or {}
            eid = ex.get("id")
            on_force = getattr(self, "on_force_submit", None)
            if eid and on_force:
                on_force(eid)
            elif eid:
                try:
                    self.flush_answers()
                except Exception:
                    pass
                for _t in (self.practice_tasks() or [None]):
                    try:
                        self.auto_submit(_t, allow_empty=True)
                    except Exception:
                        pass
                try:
                    self.flush_typing()
                except Exception:
                    pass
        except Exception as e:
            self._log("error", f"结束前交卷失败: {e}")

        # 2) 清登录信息（按老师的选择）
        if clear_login:
            try:
                self.cfg.name = ""
                self.cfg.seat = ""
                self.cfg.logged_in = False
                self.cfg.passwordless = False
                self.cfg.save()
            except Exception:
                pass

        try:
            self.api.ack_commands(list(self._seen_commands))
        except Exception:
            pass
        # 3) 主线程：提示 → 关闭
        if self.root:
            try:
                self.root.after(0, self._finish_force_exit)
            except Exception:
                self.destroy()
        else:
            self.destroy()

    def _finish_force_exit(self):
        """主线程：显示结束提示后关闭客户端"""
        try:
            import tkinter.messagebox as _mb
            _mb.showinfo(
                "考试已结束",
                "老师已结束考试，你的答卷已提交。\n\n"
                "客户端现在关闭，可以安静地离开座位了。")
        except Exception:
            pass
        try:
            self.state["phase"] = "ended"
            self.state["section"] = None
            self._notify_ui()
        except Exception:
            pass
        self.destroy()

    def _handle_commands(self, cmds):
        acked = []
        for c in cmds:
            cid = c.get("id")
            if cid is not None and str(cid) in self._seen_commands:
                self.api.ack_commands([cid])
                continue
            t = c.get("type")
            p = c.get("payload") or {}
            self._log("info", f"收到命令 {t}")
            if t == "force_exit":
                if cid is not None:
                    self._seen_commands.add(str(cid))
                self._force_exit(bool(p.get("clear_login")))
                continue
            if t == "start_exam":
                # 开考 → 直接进答题界面（理论题/实操题在顶部标签里自选）
                self.state["section"] = None
                # 记开考时刻：找答卷时用它排除上一次考试留下的老文件
                self.state["exam_started_at"] = time.time()
                self._on_phase_change(p.get("phase") or "theory")
            elif t == "phase":
                # 老师切阶段 → 学生跟着走（section 由老师决定）
                self.state["section"] = p.get("phase")
                self._on_phase_change(p.get("phase", "theory"))
            elif t == "pause":
                # 记住学生在哪一块，恢复后让他接着做，而不是被切到别的标签
                self.state["_prev_section"] = self.state.get("section")
                self._on_phase_change("paused")
            elif t == "resume":
                self.state["section"] = self.state.get("_prev_section")
                self._notify_ui()
            elif t == "open_app":
                self.open_wps(p.get("app", "word"))
            elif t == "collect":
                # 收卷：多道操作题就每道都交一次（None = 老的单题模式）
                for _t in (self.practice_tasks() or [None]):
                    self.auto_submit(_t)
            elif t == "stop":
                self._on_phase_change("ended")
            elif t == "lock":
                self.guard.lock()
            elif t == "unlock":
                self.guard.unlock()
            if cid is not None:
                acked.append(cid)
                self._seen_commands.add(str(cid))
        if acked:
            self.api.ack_commands(acked)

    # 哪些阶段算"考试中"（禁止退出）
    LOCKED_PHASES = ("theory", "practice", "paused")

    def strict_mode(self):
        """是不是"严格模式"（老的防作弊行为：全屏 + 考试中禁止退出）"""
        return self.cfg.window_mode == "fullscreen"

    def want_lock(self):
        """
        现在该禁止退出吗。

        ⚠️ 默认的普通窗口模式下**永远不锁** ——
        老师要求窗口"和正常软件一样，随时能缩小和关闭"，
        所以答题中也能点 X / 按 Esc 退出（退出前会弹一次确认并保存答案）。

        只有显式开了严格模式（config.json: window_mode=fullscreen）才锁，
        并且考试结束/空闲一律不锁 —— 以前结束页还判"考试进行中不能退出"，
        学生被死死锁在结束页上出不来。
        """
        if not self.strict_mode():
            return False
        phase = self.state.get("phase") or "idle"
        if phase in ("ended", "idle"):
            return False
        section = self.state.get("section")
        return (phase in self.LOCKED_PHASES
                or section in ("theory", "practice"))

    def want_cover(self):
        """
        要不要全屏盖住屏幕（只有严格模式会全屏）。

        规则：考试中一律全屏，**除了**学生切到「实操题」标签时 ——
        实操要用 WPS，全屏会把 WPS 挡在后面，学生没法做题。

        section=None 表示这次开考后还没切过标签（默认落在理论题上），
        按全屏处理，否则严格模式下开考头几分钟是"没全屏"的。
        """
        if not self.strict_mode():
            return False
        if self.state.get("phase") in ("ended", "idle"):
            return False
        return self.state.get("section") != "practice"

    def sync_lock(self):
        """按当前状态同步窗口形态（连接成功 / 重连成功都要调）"""
        phase = self.state.get("phase") or "idle"
        want = self.want_lock()
        cover = self.want_cover()
        if self.root:
            try:
                self.root.set_locked(want, cover=cover)
                self._log("info", f"窗口状态同步: phase={phase} "
                                  f"section={self.state.get('section')} "
                                  f"mode={self.cfg.window_mode} "
                                  f"-> locked={want} cover={cover}")
            except Exception as e:
                self._log("error", f"同步窗口状态失败: {e}")
        # 进程守卫（Alt+Tab / 任务管理器屏蔽）只在严格模式下启用。
        # 普通模式要能自由切到 WPS 做题，锁住反而添乱。
        try:
            if self.strict_mode() and self.state.get("section") == "theory":
                self.guard.lock()
            else:
                self.guard.unlock()
        except Exception:
            pass
        return want

    def _on_phase_change(self, phase):
        old = self.state["phase"]
        self.state["phase"] = phase
        self._log("info", f"阶段 {old} -> {phase}")

        if phase == "ended":
            # 记下"这场是我考的"，结束页才显示给本人。
            # ⚠️ 必须判断 old：客户端刚打开时是 idle -> ended（上一场早就结束了），
            # 那种情况不能算"我考过"，否则新来的同学会看到上一场的结束页。
            ex = self.state.get("exam") or {}
            if old in ("theory", "practice", "paused") and ex.get("id"):
                self.state["ended_exam_id"] = ex["id"]
            # 顺便清掉板块，避免残留的 theory 让窗口继续处于锁定态
            self.state["section"] = None
            # 下一场开考前必须重新确认身份（这台机器可能已经换人）
            self.state["confirmed"] = False
            # 登录信息不再在这里自动清：老师在「结束考试」时可以选
            # 「保留登录信息」（下一场开机直接待考，不用再填一遍）。
            # 换学生坐同一台机器时，用界面上的「换考生」按钮清。

        # 进程守卫（屏蔽 Alt+Tab / 任务管理器）只在严格模式下启用 ——
        # 普通模式是"和正常软件一样"，锁住反而让学生切不到 WPS 做题
        try:
            if self.strict_mode() and self.state.get("section") == "theory":
                self.guard.lock()
            else:
                self.guard.unlock()
        except Exception:
            pass

        # 同步窗口状态：考试中禁止退出；是否在最前面看板块
        want = self.want_lock()
        cover = self.want_cover()
        if self.root:
            try:
                self.root.set_locked(want, cover=cover)
                self._log("info", "窗口已锁定（禁止退出）" if want
                                  else "窗口已解锁（可退出）")
            except Exception as e:
                self._log("error", f"窗口锁定状态切换失败: {e}")
        self._notify_ui()

    def _notify_ui(self):
        """
        让界面按当前状态重新决定显示哪个页面。
        界面内部会做幂等判断：已经在正确页面就不重建，
        避免学生在答题途中被"重建"回第一题。
        """
        if hasattr(self, "on_phase") and self.on_phase:
            try:
                self.on_phase(self.state.get("phase"))
            except Exception as e:
                self._log("error", f"界面切换失败: {e}")

    # ---------- 实操 ----------
    def open_wps(self, component, file_path=None):
        """拉起 WPS，可同时打开素材文件"""
        ok, msg = wps.launch(component, file_path)
        self._log("info" if ok else "error", msg)
        return ok, msg

    def fetch_materials(self, exam_id, task=None, on_progress=None, force=False):
        """
        下载素材，返回本地路径列表。task：多道操作题时指定哪一道
        :param on_progress: fn(第几个, 共几个, 文件名, 已下载字节, 总字节)
        :param force: True 才重新下载；默认已经下过就跳过（不然每次都重下，很慢）
        """
        task_id = int((task or {}).get("task_id") or 0)
        names = (task or {}).get("materials") or self.state["materials"]
        d = self.ensure_work_dir(exam_id, task_id)
        got = []
        total_n = len(names)
        for idx, name in enumerate(names, 1):
            dest = os.path.join(d, name)
            # 已经下过（且不是空文件）就不要再下一遍
            if not force and os.path.isfile(dest) and os.path.getsize(dest) > 0:
                got.append(dest)
                if on_progress:
                    try:
                        on_progress(idx, total_n, name, 1, 1)
                    except Exception:
                        pass
                continue

            def _cb(got_b, total_b, _i=idx, _n=total_n, _name=name):
                if on_progress:
                    try:
                        on_progress(_i, _n, _name, got_b, total_b)
                    except Exception:
                        pass

            try:
                p = self.api.download_material(name, d, on_progress=_cb)
                got.append(p)
                self._log("info", f"素材已下载: {name}")
            except Exception as e:
                self._log("error", f"素材下载失败 {name}: {e}")
                raise
        return got

    def material_path(self, exam_id, task, name):
        """素材在本机的完整路径（下载后要用 WPS 打开它）"""
        return os.path.join(self.exam_dir(exam_id), name)

    def answer_tag(self, task_id, task=None):
        """
        这道题在 IkeeAnswer 里的题号标记，如「第2题」。

        为什么要它：老师定的新结构里**所有操作题的答卷共用一个 IkeeAnswer
        文件夹**，只能靠文件名区分，否则第 2 题会抓到第 1 题的文件。
        """
        no = (task or {}).get("no")
        if not no and task_id:
            try:
                for i, t in enumerate(self.practice_tasks(), 1):
                    if int(t.get("task_id") or 0) == int(task_id):
                        no = i
                        break
            except Exception:
                no = None
        return f"第{no}题" if no else ""

    def _looks_like_answer(self, fname, ext):
        """像不像学生的答卷（排除 WPS 临时文件 ~$xxx）"""
        low = fname.lower()
        return (low.endswith(ext.lower()) and not fname.startswith("~$")
                and not fname.startswith(".") and not low.endswith(".tmp"))

    def _file_exam_code(self, fname):
        """文件名里带的考试编号（…_ikeexam-20260924-003_第1题.xlsx），没有返回 ''"""
        m = re.search(r"([A-Za-z0-9]{2,}-\d{8}-\d{3})", fname)
        return m.group(1) if m else ""

    def _other_task_answer(self, fname, tag, mine=""):
        """
        这个文件是不是"别的题 / 别的场次"的答卷：
          · 文件名里的题号不是这道题（…_第2题.xlsx）
          · 文件名里带着另一场考试的编号
        """
        m = re.search(r"第(\d+)题", fname)
        if m and tag and f"第{m.group(1)}题" != tag:
            return True
        other = self._file_exam_code(fname)
        if other and mine and other != mine:
            return True
        return False

    def _material_names(self, task=None):
        """
        本场考试的素材文件名（这些**不是**答卷，必须排除）。

        不排除的话：学生一下载素材，程序就会说"发现答卷：
        成绩表_基础_素材.xlsx"，甚至可能把没动过的素材当答卷交上去。
        """
        names = set()
        for t in [task] + list(self.state.get("tasks") or []):
            for m in ((t or {}).get("materials") or []):
                names.add(str(m).lower())
        for m in (self.state.get("materials") or []):
            names.add(str(m).lower())
        return names

    def _answer_candidates(self, exam_id, ext, tag, floor_ts=0.0, task=None):
        """
        这道题可能的答卷文件（按修改时间从新到旧）。
        两处都找：IkeeAnswer（老师要求存这里）+ 考试编号目录（学生直接
        另存到了打开素材的位置，WPS 默认就是这样）。
        floor_ts：只认这个时间点之后动过的文件，避免翻出上一次考试的老答卷；
                  本场已经收好的那份（文件名带本场编号+本题号）不受它限制。
        """
        mine = self.exam_code()
        mats = self._material_names(task)
        dirs = [self.answer_dir(), self.exam_dir(exam_id)]
        out = []
        for d in dirs:
            if not os.path.isdir(d):
                continue
            try:
                names = os.listdir(d)
            except OSError:
                continue
            for f in names:
                p = os.path.join(d, f)
                try:
                    if not os.path.isfile(p) or not self._looks_like_answer(f, ext):
                        continue
                    if f.lower() in mats:
                        continue        # 这是老师发的素材，不是学生的答卷
                    if self._other_task_answer(f, tag, mine):
                        continue        # 别的题 / 别的场次的，别抓
                    mt = os.path.getmtime(p)
                except OSError:
                    continue
                ours = bool(mine) and mine in f and bool(tag) and tag in f
                if floor_ts and not ours and mt < floor_ts:
                    continue        # 这次开考之前的老文件（上一场留下的）
                out.append((mt, p, f))
        out.sort(key=lambda x: x[0], reverse=True)
        return out

    def collected_answer(self, exam_id, task):
        """
        这道题已经收进 IkeeAnswer 的答卷（文件名带题号标记）。

        ⚠️ IkeeAnswer 是**跨考试复用**的（ikee_Exam/学生名字/IkeeAnswer），
        所以收纳时文件名里带了本场考试的编号：
            07_张三_ikeexam-20260924-003_第1题.xlsx
        只有编号和题号都对上的，才算"这道题已经收好的"，
        上一场留下的 …_第1题.xlsx 不会被当成这一场的。
        """
        tag = self.answer_tag(int((task or {}).get("task_id") or 0), task)
        if not tag:
            return None
        mine = self.exam_code()
        d = self.answer_dir()
        if not os.path.isdir(d):
            return None
        hits = []
        for f in os.listdir(d):
            p = os.path.join(d, f)
            if not (os.path.isfile(p) and tag in f and not f.startswith("~$")):
                continue
            if mine and mine not in f:
                continue            # 不是本场收的
            other = self._file_exam_code(f)
            if other and mine and other != mine:
                continue            # 名字里写明了是别的场次
            try:
                hits.append((os.path.getmtime(p), p))
            except OSError:
                continue
        if not hits:
            return None
        hits.sort(reverse=True)
        return hits[0][1]

    def pending_answer(self, exam_id, task):
        """
        这道题"还没收进 IkeeAnswer、但看起来是答卷"的文件（界面提示用）。
        返回 (路径, 文件名) 或 (None, "")。
        """
        ex = self.state.get("exam") or {}
        etype = (task or {}).get("etype") or ex.get("etype") or "word"
        ext = {"word": ".docx", "excel": ".xlsx", "ppt": ".pptx"}.get(etype, ".docx")
        tag = self.answer_tag(int((task or {}).get("task_id") or 0), task)
        floor = float(self.state.get("exam_started_at") or 0)
        cands = self._answer_candidates(exam_id, ext, tag, floor, task)
        return (cands[0][1], cands[0][2]) if cands else (None, "")

    def collect_answer(self, exam_id, task):
        """
        把这道题的答卷收进 IkeeAnswer，统一命名 {座位号}_{姓名}_第N题.ext。

        为什么要"收"而不是直接传原文件：
          所有操作题共用一个 IkeeAnswer，学生按题目要求存的名字都一样
          （座位号_姓名.xlsx），第 2 题的存档会把第 1 题的覆盖掉。收的时候
          加上题号、另存一份，两题的答卷就都留住了。
        返回 (答卷路径 or None, 说明文字)
        """
        ex = self.state.get("exam") or {}
        etype = (task or {}).get("etype") or ex.get("etype") or "word"
        ext = {"word": ".docx", "excel": ".xlsx", "ppt": ".pptx"}.get(etype, ".docx")
        task_id = int((task or {}).get("task_id") or 0)

        tag = self.answer_tag(task_id, task)
        # 只认"这次开考之后"动过的文件：IkeeAnswer 是跨场次共用的，
        # 不然上一场留下的 …_第1题.xlsx 会被当成这一场第 1 题的答卷交上去。
        # （客户端中途重启时拿不到开考时刻，那时不做时间过滤，宁可找到也不漏交）
        floor = float(self.state.get("exam_started_at") or 0)
        cands = self._answer_candidates(exam_id, ext, tag, floor, task)

        got = self.collected_answer(exam_id, task)
        if got:
            # 已收好的快照还是最新的（没有更新的工作文件）→ 直接用。
            # 但学生若提交后又回 WPS 改过，工作文件 mtime 会更新、排到最前，
            # 这时必须重新收纳，否则交上去的还是第一次那份旧文件。
            if not cands or os.path.abspath(cands[0][1]) == os.path.abspath(got):
                return got, ""

        if not cands:
            return None, f"没找到 {ext} 答卷"

        src = cands[0][1]
        seat = _safe_dir_name(self.cfg.seat)
        name = _safe_dir_name(self.cfg.name)
        stem = "_".join(x for x in (seat, name) if x) or _safe_dir_name(
            self.cfg.hostname, "答卷")
        # 文件名里带上考试编号 + 题号：IkeeAnswer 是所有场次共用的，
        # 这样一看文件名就知道是哪一场哪一题，也不会互相认错
        parts = [stem, self.exam_code(), tag]
        fname = "_".join(x for x in parts if x) + ext
        dest = os.path.join(self.answer_dir(), fname)
        try:
            if os.path.abspath(src) != os.path.abspath(dest):
                # 用 copy 而不是 copy2：副本的修改时间要是"现在"，
                # 否则下次按开考时间过滤时，这份刚收好的答卷会被当成老文件
                shutil.copy(src, dest)
            self._log("info", f"答卷已收进 IkeeAnswer: {os.path.basename(dest)}")
            return dest, ""
        except OSError as e:
            # 复制不了就原地提交（能用总比卡住强）
            self._log("error", f"收纳答卷失败，改为原地提交: {e}")
            return src, ""

    def find_answer_file(self, exam_id, ext, task_id=0, task=None):
        """
        找这道题的答卷（会顺手收进 IkeeAnswer）。
        兼容旧签名：老调用点只传 (exam_id, ext, task_id)。
        """
        path, _msg = self.collect_answer(exam_id, task or {"task_id": task_id})
        return path

    def auto_submit(self, task=None, allow_empty=False):
        """
        自动查找并上传答卷。
        task：多道操作题时指定哪一道（None = 老的单题模式）
        allow_empty=True：交卷（学生已确认没做完也交）时用 —— 找不到答卷就在服务端
                          记一笔「缺卷，按 0 分」，不再报「提交失败」
        """
        ex = self.state.get("exam")
        if not ex:
            # 缓存里没有（比如刚启动），主动问一次服务端
            try:
                h = self.api.hello()
                ex = h.get("exam")
                if ex:
                    self.state["exam"] = ex
                    self.state["materials"] = ex.get("materials") or []
                    self.state["tasks"] = h.get("tasks") or []
            except Exception:
                ex = None
        if not ex:
            return False, "没有进行中的考试"
        eid = ex["id"]
        task_id = int((task or {}).get("task_id") or 0)
        # 先把答卷收进 IkeeAnswer（统一 {座位号}_{姓名}_第N题），再上传 ——
        # 多道题共用答卷目录时，这一步避免第 2 题覆盖第 1 题
        path, msg = self.collect_answer(eid, task)
        if not path:
            # 交卷时学生已经确认「没做完也交」→ 服务端记缺卷 0 分
            if allow_empty:
                try:
                    r = self.api.submit_empty(eid, task_id)
                    if r.get("ok"):
                        self._log("info", f"缺卷提交（第 {task_id or 1} 题，按 0 分）")
                        return True, {"score": 0, "full": 0, "empty": True,
                                      "msg": "未作答，按 0 分处理"}
                except Exception as e:
                    self._log("error", f"缺卷提交失败: {e}")
                    return False, f"缺卷提交失败：{e}"
            return False, (msg or "没找到答卷") + "（另存为到 IkeeAnswer 文件夹后再试）"
        return self.submit_file(eid, path, task_id)

    def submit_file(self, exam_id, path, task_id=0):
        if not os.path.isfile(path):
            return False, "文件不存在"
        try:
            r = self.api.upload(exam_id, path, task_id=task_id)
            if r.get("ok"):
                self.state["submitted"] = True
                self._log("info", f"答卷已提交: {os.path.basename(path)}")
                return True, r.get("auto", {})
            return False, r.get("msg", "提交失败")
        except Exception as e:
            self._log("error", f"提交失败: {e}")
            return False, str(e)

    # ---------- 启动 ----------
    def start(self, on_status=None):
        # 1. 先确定服务器在哪
        if not self.cfg.server or self.cfg.auto_discover:
            url = self.discover(on_status=on_status)
            if not url:
                self._log("error", "未找到考试服务器")
                return False
            self.cfg.server = url
            self.api = Api(self.cfg)

        # 2. 握手
        try:
            h = self.api.hello()
            if not h.get("ok", True):
                # 服务端拒了（比如姓名不在本场名单里）—— 把原因带回界面显示
                self.state["hello_error"] = h.get("msg") or "服务器拒绝了登录"
                self._log("error", self.state["hello_error"])
                return False
            self.state.pop("hello_error", None)
            self.state["server_name"] = h.get("server_name", "")
            self._log("info", f"已连接服务器 {self.cfg.server}，阶段={h.get('phase')}")
            self.state["exam"] = h.get("exam")
            self.state["phase"] = h.get("phase")
            self.state["tasks"] = h.get("tasks") or []
            self.state["typing"] = h.get("typing")
            self.state["drawing"] = h.get("drawing")
            if h.get("exam"):
                self.state["materials"] = h["exam"].get("materials") or []
            self._handle_commands(h.get("commands") or [])
        except Exception as e:
            self._log("error", f"无法连接服务器: {e}")
            return False

        # ⚠️ 关键：连上时考试可能已经在进行（比如学生中途重启了机器、
        # 或者迟到开机）。这里必须立刻同步锁定状态，否则窗口是可退出的，
        # 学生就能在考试中溜出去。阶段变化走 _on_phase_change，
        # 首次连接走这里。
        self.sync_lock()

        self.guard.start()
        self.conn["online"] = True
        self.conn["fails"] = 0
        self.conn["last_ok"] = time.time()
        if on_status:
            self.on_status = on_status
        ht = threading.Thread(target=self.heartbeat_loop, daemon=True)
        self._heartbeat_thread = ht
        ht.start()
        return True

    def reconnect(self, on_status=None):
        """重新找服务器（换了网段、老师重启了服务时用）"""
        import discover
        discover.clear_cache()
        self.cfg.server = ""
        return self.start(on_status=on_status)

    def stop(self):
        self._stop.set()
        # 退出前把攒下的答案交上去，别丢
        try:
            self.flush_answers()
        except Exception:
            pass
        self.stop_answer_sync()
        self.guard.stop()
        self.guard.unlock()
        # 等心跳线程真正退出：retry_connect 会 stop → clear → start，
        # 不 join 的话旧心跳线程还活着，和新线程一起跑出「双心跳」，
        # 导致重复 poll、重复处理命令。
        ht = getattr(self, "_heartbeat_thread", None)
        if ht is not None and ht.is_alive() and ht is not threading.current_thread():
            ht.join(timeout=10)


# ==================== 自我检测 ====================
def selftest(server=None):
    """不启动界面的自检：能连服务器吗？WPS 在吗？目录可写吗？"""
    cfg = Config()
    if server:
        cfg.server = server
    print("=" * 52)
    print(" 学生端自检")
    print("=" * 52)
    print(f"机器名   : {cfg.hostname}")
    print(f"服务器   : {cfg.server or '（留空 = 自动寻找）'}")
    print(f"备用地址 : {cfg.fallback}")
    print(f"本机 IP  : {Api.local_ip()}")
    print(f"工作目录 : {cfg.work_dir}")

    print("\n--- 1. WPS 检测 ---")
    print(wps.report())

    print("\n--- 2. 目录可写 ---")
    try:
        d = os.path.join(cfg.work_dir, "_test")
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, "t.txt")
        with open(p, "w") as f:
            f.write("ok")
        os.remove(p)
        os.rmdir(d)
        print(f"✅ 可写: {cfg.work_dir}")
    except Exception as e:
        print(f"❌ 不可写: {e}")

    print("\n--- 3. 代码运行环境（程序填空「运行」按钮用）---")
    try:
        import runner
        env = runner.detect()
        if env["python"]:
            print(f"✅ Python: {env['python']}")
            # 实测一下能不能跑
            r = runner.run_python("print('ok')", timeout=8)
            print("   实测运行: " + ("✅ 正常" if r.get("ok") and
                                   "ok" in (r.get("out") or "")
                                   else f"❌ {r.get('msg', '')[:50]}"))
        else:
            print("❌ 没检测到 Python，Python 程序填空无法运行")
            print("   （其他题型不受影响）")
        if env["cpp"]:
            print(f"✅ C++ 编译器: {env['cpp']}")
        else:
            print("⚠️ 没检测到 g++，C++ 程序填空无法运行（Python 题不受影响）")
    except Exception as e:
        print(f"❌ 运行环境检测失败: {e}")

    print("\n--- 4. 违禁进程扫描 ---")
    found = guard.scan_running()
    print("   " + ("、".join(found) if found else "✅ 未发现"))

    print("\n--- 5. 自动发现服务器 ---")
    import discover as D
    url = D.find_server(timeout=8, on_status=lambda m: print("   " + m),
                        fallback=cfg.fallback)
    if not url:
        print("   ❌ 没能确定服务器地址")
        print("   排查：")
        print("     1. 服务器 app.py 启动了吗（应显示 📡 广播已启动）")
        print(f"     2. 防火墙放行 UDP {D.BEACON_PORT} 与 TCP 9000")
        print(f"     3. 备用地址 {cfg.fallback} 是否正确")
        print("\n--- 6. 握手 ---")
        print("   （跳过：没找到服务器）")
    else:
        cfg.server = url
        D.save_cache(url)
        c = ExamClient(cfg, headless=True)
        print("\n--- 6. 握手 ---")
        try:
            h = c.api.hello()
            print(f"✅ 已连接，阶段={h.get('phase')}")
            if h.get("server_name"):
                print(f"   服务器名：{h['server_name']}")
            if h.get("exam"):
                print(f"   当前考试：{h['exam']['title']}")
        except Exception as e:
            print(f"❌ 握手失败: {e}")

    print("\n--- 7. 答题状态逻辑 ---")
    qs = [{"id": 1, "qtype": "single"}, {"id": 2, "qtype": "multi"}, {"id": 3, "qtype": "blank"}]
    s = AnswerSheet(qs)
    s.set(1, "A")
    print(f"   已答 {s.answered_count()}/{s.total()}，未答 {s.unanswered()}，"
          f"提交体 {s.payload()}")
    s.set(3, "1024")
    print(f"   再答一题 -> 已答 {s.answered_count()}，完整={s.is_complete()}")
    print("   ✅ 逻辑正常")
    print("\n" + "=" * 52)


_SINGLE_MUTEX = None


def acquire_single_instance(tag="Global\\ExamSystemClientSingleInstance"):
    """
    单实例保护：考试程序已经开着时，双击第二次直接提示并退出。
    学生重复打开多个客户端会重复登录、重复交卷，把服务器上的数据搞乱。
    Windows 用命名互斥体（进程被强杀也会由系统自动释放，不会留下死锁）；
    其他系统用临时目录的 pid 锁文件兜底。
    """
    global _SINGLE_MUTEX
    if os.name == "nt":
        try:
            import ctypes
            h = ctypes.windll.kernel32.CreateMutexW(None, False, tag)
            if not h:
                return True                      # 创建失败就别拦着考试
            if ctypes.windll.kernel32.GetLastError() == 183:   # 已存在
                ctypes.windll.kernel32.CloseHandle(h)
                return False
            _SINGLE_MUTEX = h
            return True
        except Exception:
            return True
    import tempfile
    lock = os.path.join(tempfile.gettempdir(), "exam-client-single.lock")
    try:
        if os.path.exists(lock):
            with open(lock) as f:
                pid = int((f.read().strip() or "0"))
            alive = False
            if pid and pid != os.getpid():
                try:
                    os.kill(pid, 0)
                    alive = True
                except OSError:
                    alive = False
            if alive:
                return False
        with open(lock, "w") as f:
            f.write(str(os.getpid()))
        _SINGLE_MUTEX = lock
        return True
    except Exception:
        return True


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="ikee考务中心学生端")
    p.add_argument("--server", help="服务器地址，如 http://192.168.10.1:9000")
    p.add_argument("--selftest", action="store_true", help="自检，不启动界面")
    p.add_argument("--gui", action="store_true", help="启动图形界面（默认）")
    a = p.parse_args()

    if a.selftest:
        selftest(a.server)
    else:
        # 已经开着一个了 → 弹窗提醒并退出（防学生开多个重复交卷）
        if not acquire_single_instance():
            try:
                import tkinter as tk
                from tkinter import messagebox
                _r = tk.Tk()
                _r.withdraw()
                messagebox.showwarning(
                    "考试程序已在运行",
                    "考试程序已经打开了一个窗口。\n\n"
                    "请检查任务栏里是不是已经有一个了，\n"
                    "不要重复打开（重复打开会重复交卷）。")
                _r.destroy()
            except Exception:
                pass
            sys.exit(0)
        if a.server:
            cfg = Config()
            cfg.server = a.server
            cfg.save()
        # 延迟导入 GUI，避免无界面环境报错
        try:
            from gui import run_gui
        except Exception as e:
            # --noconsole 模式下 print 看不到，用 tkinter 弹窗报错
            try:
                import tkinter as tk
                from tkinter import messagebox
                root = tk.Tk()
                root.withdraw()
                messagebox.showerror(
                    "启动失败",
                    f"无法加载图形界面模块。\n\n"
                    f"错误: {e}\n\n"
                    f"可能原因：\n"
                    f"1. 打包时缺少 --hidden-import=gui\n"
                    f"2. 缺少 psutil 依赖\n"
                    f"3. Python 环境异常\n\n"
                    f"请重新打包，确保使用完整的打包命令。")
                root.destroy()
            except Exception:
                print(f"启动失败: {e}")
            sys.exit(1)
        run_gui(Config())
