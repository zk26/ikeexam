# -*- coding: utf-8 -*-
"""ikee考务中心 —— Windows Tkinter 老师端。

界面只使用 tkinter/ttk；网络请求由后台线程执行，并通过队列回到主线程
更新控件。默认连接本机服务，未运行时会自动拉起内置服务并等待端口就绪。
"""
import datetime
import json
import os
import queue
import random
import re
import subprocess
import sys
import threading
import time
import webbrowser
from urllib.parse import urlsplit

TEACHER_DIR = os.path.dirname(os.path.abspath(__file__))
PACKAGE_DIR = os.path.dirname(TEACHER_DIR)
SRC_DIR = os.path.dirname(PACKAGE_DIR)
PROJECT_ROOT = os.path.dirname(SRC_DIR)
sys.path.insert(0, TEACHER_DIR)
sys.path.insert(0, SRC_DIR)

from ikeexam.shared import ui_theme
ui_theme.enable_high_dpi()
import tkinter as tk
from tkinter import filedialog, messagebox as mb, scrolledtext, ttk

try:
    from .api import AdminAPI
    from .visual_config import (ScrollableFrame, RuleEditor, TypingConfigEditor,
                                DrawingConfigEditor)
    from .review_charts import ReviewWindow, ChartsWindow
except ImportError:
    from api import AdminAPI
    from visual_config import (ScrollableFrame, RuleEditor, TypingConfigEditor,
                               DrawingConfigEditor)
    from review_charts import ReviewWindow, ChartsWindow

APP_NAME = "ikee考务中心"
SIGNATURE = "大茂小可 | https://www.ikee002.top/"
ABOUT = {
    "name": APP_NAME,
    "author": "大茂小可 · 小可",
    "author_note": "小可属于大茂小可",
    "site": "https://www.ikee002.top/",
    "repo": "https://github.com/zk26/ikeexam",
    "license": "MIT",
    "desc": "机房考试系统：理论题在线作答，WPS 实操题自动评分。",
}

# 配色与学生端共用同一套设计令牌。
_UI = ui_theme.LIGHT
BG = _UI["bg"]
CARD = _UI["surface"]
INK = _UI["text"]
GRAY = _UI["text_muted"]
BLUE = _UI["primary"]
BLUE_D = _UI["primary_hover"]
BLUE_L = _UI["primary_soft"]
RED = _UI["danger"]
GREEN = _UI["success"]
ORANGE = _UI["warning"]
LINE = _UI["border"]
FONT = "Microsoft YaHei"

_CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
QTYPES = (
    ("single", "单选题"), ("multi", "多选题"), ("judge", "判断题"),
    ("blank", "填空题"), ("code", "程序填空"), ("drag", "拖拽题"),
)
QTYPE_NAMES = dict(QTYPES)
EXAM_TYPES = (
    ("single", "单选题"), ("multi", "多选题"), ("judge", "判断题"),
    ("blank", "填空题"), ("code", "程序填空"), ("drag", "拖拽题"),
    ("practice", "实操题"), ("typing", "打字练习"), ("drawing", "画板题"),
)
STATUS_NAMES = {
    "draft": "未开始", "running": "进行中", "paused": "已暂停",
    "stopped": "已结束", "idle": "未开始",
}
PHASE_NAMES = {"theory": "理论阶段", "practice": "实操阶段",
               "ended": "已结束", "idle": "—", "running": "—"}
STUDENT_STATUS = {"idle": "空闲", "working": "作答中", "submitted": "已交卷"}
OFFICE_NAMES = {"word": "Word", "excel": "Excel", "ppt": "PowerPoint",
                "mixed": "综合"}


def load_server():
    try:
        with open(_CONFIG, "r", encoding="utf-8") as fileobj:
            return (json.load(fileobj) or {}).get("server", "127.0.0.1:9000")
    except Exception:
        return "127.0.0.1:9000"


def save_server(addr):
    try:
        with open(_CONFIG, "w", encoding="utf-8") as fileobj:
            json.dump({"server": addr}, fileobj, ensure_ascii=False, indent=2)
    except Exception:
        pass


def norm_base(addr):
    value = (addr or "").strip()
    if not value:
        return ""
    if not value.startswith(("http://", "https://")):
        value = "http://" + value
    return value.rstrip("/")


def open_site(event=None):
    webbrowser.open(ABOUT["site"])


def signature_label(parent, bg=BG):
    label = tk.Label(parent, text=SIGNATURE, bg=bg, fg=GRAY,
                     font=(FONT, 9), anchor="e", cursor="hand2")
    label.bind("<Button-1>", open_site)
    return label


def show_about(parent):
    from PIL import Image, ImageTk

    win = tk.Toplevel(parent)
    win.title(f"关于 {APP_NAME}")
    win.configure(bg=CARD)
    win.resizable(False, False)
    win.transient(parent)
    win.bind("<Escape>", lambda event: win.destroy())
    icon = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.ico")
    if os.path.isfile(icon):
        win.iconbitmap(icon)

    body = tk.Frame(win, bg=CARD, padx=24, pady=22)
    body.pack(fill="both", expand=True)
    info = tk.Frame(body, bg=CARD)
    info.pack(side="left", fill="both", expand=True, padx=(0, 22))

    tk.Label(info, text=APP_NAME, bg=CARD, fg=INK,
             font=(FONT, 18, "bold")).pack(anchor="w")
    tk.Label(info, text=ABOUT["author_note"], bg=CARD, fg=BLUE,
             font=(FONT, 11, "bold")).pack(anchor="w", pady=(6, 10))
    tk.Label(info, text=ABOUT["desc"], bg=CARD, fg=INK,
             font=(FONT, 10), wraplength=300, justify="left").pack(anchor="w")

    site = tk.Label(info, text=ABOUT["site"], bg=CARD, fg=BLUE,
                    font=(FONT, 10), cursor="hand2")
    site.pack(anchor="w", pady=(14, 4))
    site.bind("<Button-1>", open_site)
    repo = tk.Label(info, text=ABOUT["repo"], bg=CARD, fg=BLUE,
                    font=(FONT, 9), cursor="hand2")
    repo.pack(anchor="w")
    repo.bind("<Button-1>", lambda event: webbrowser.open(ABOUT["repo"]))
    tk.Label(info, text=f"{ABOUT['license']} License", bg=CARD, fg=GRAY,
             font=(FONT, 9)).pack(anchor="w", pady=(10, 0))

    qr_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "about_qr.png")
    image = Image.open(qr_path)
    image.thumbnail((250, 250), Image.Resampling.LANCZOS)
    photo = ImageTk.PhotoImage(image)
    win._about_qr = photo
    tk.Label(body, image=photo, bg=CARD).pack(side="right")
    tk.Label(body, text="赞助作者", bg=CARD, fg=GRAY,
             font=(FONT, 9)).pack(side="right", pady=(6, 0))

    tk.Button(win, text="关闭", command=win.destroy, font=(FONT, 10),
              padx=22, pady=4, relief="flat", cursor="hand2").pack(pady=(0, 18))
    win.update_idletasks()
    x = parent.winfo_rootx() + max(0, (parent.winfo_width() - win.winfo_width()) // 2)
    y = parent.winfo_rooty() + max(0, (parent.winfo_height() - win.winfo_height()) // 2)
    win.geometry(f"+{x}+{y}")
    ui_theme.apply_to_tk(win, getattr(parent, "_ui_tokens", _UI))
    win.grab_set()
    return win


def as_list(value):
    if isinstance(value, list):
        return list(value)
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except Exception:
            return []
    return []


def json_object(value, default=None):
    if isinstance(value, dict):
        return dict(value)
    try:
        parsed = json.loads(value or "{}")
        return parsed if isinstance(parsed, dict) else dict(default or {})
    except Exception:
        return dict(default or {})


def positive_int(value, label, default=None):
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        if default is not None:
            return default
        raise ValueError(f"{label}必须是整数")
    if not 1 <= number <= 600:
        raise ValueError(f"{label}必须在 1～600 之间")
    return number


def safe_filename(name):
    value = re.sub(r'[\\/:*?"<>|]', "_", str(name or "导出文件")).strip()
    return value.strip(". ") or "导出文件"


def format_time(value):
    if not value:
        return "—"
    try:
        return datetime.datetime.fromtimestamp(float(value)).strftime(
            "%m-%d %H:%M:%S")
    except (TypeError, ValueError, OSError):
        return str(value)


def widget_state(widget):
    try:
        return str(widget.cget("state"))
    except Exception:
        return None


def set_widget_state(widget, state):
    if state is None:
        return
    try:
        if isinstance(widget, (tk.Text, scrolledtext.ScrolledText)):
            widget.configure(state=state)
        else:
            widget.configure(state=state)
    except Exception:
        pass


def make_button(parent, text, command, accent=False, width=None):
    style = "Accent.TButton" if accent else "TButton"
    kwargs = {"text": text, "command": command, "style": style}
    if width:
        kwargs["width"] = width
    return ttk.Button(parent, **kwargs)


def make_tree(parent, columns, headings, widths, stretch=None, height=10):
    """创建带双向滚动条的树；返回 (容器, Treeview)。"""
    holder = ttk.Frame(parent)
    holder.grid_rowconfigure(0, weight=1)
    holder.columnconfigure(0, weight=1)
    tree = ttk.Treeview(holder, columns=columns, show="headings",
                        selectmode="browse", height=height, style="Data.Treeview")
    ui_theme.style_tree_tags(tree)
    vertical = ttk.Scrollbar(holder, orient="vertical", command=tree.yview)
    horizontal = ttk.Scrollbar(holder, orient="horizontal", command=tree.xview)
    tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
    ui_theme.bind_mousewheel(tree, tree)
    tree.grid(row=0, column=0, sticky="nsew")
    vertical.grid(row=0, column=1, sticky="ns")
    horizontal.grid(row=1, column=0, sticky="ew")
    for index, (column, title) in enumerate(zip(columns, headings)):
        tree.heading(column, text=title)
        tree.column(column, width=widths[index], minwidth=35,
                    anchor="w", stretch=(column == stretch))
    return holder, tree


class TeacherApp:
    """应用、后台任务队列和本机服务生命周期。"""

    def __init__(self):
        self.root = tk.Tk()
        icon = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.ico")
        if os.path.isfile(icon):
            try:
                self.root.iconbitmap(icon)
            except tk.TclError:
                pass
        self._configure_style()
        self.root.bind("<F5>", lambda event: self._refresh_current())
        self.api = AdminAPI(norm_base(load_server()))
        self._work = queue.Queue()
        self._events = queue.Queue()
        self._pending = 0
        self._closed = False
        self._starting = False
        self._control_states = {}
        self._worker = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker.start()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self._build_login()
        self.root.after(80, self._drain_events)

    def _refresh_current(self):
        main = getattr(self, "main_window", None)
        if main is not None:
            main.refresh_all()

    def _schedule_login_startup(self):
        """服务器地址修改后重新检查，避免沿用上一次地址的就绪状态。"""
        login = getattr(self, "login_window", None)
        if login is None or self.busy:
            return
        checked_base = getattr(login, "checked_base", None)
        current_base = norm_base(login.e_server.get())
        if checked_base == current_base:
            return
        self.startup(login)

    def _configure_style(self):
        global FONT
        FONT = ui_theme.pick_family(self.root)
        self.ui_tokens = ui_theme.apply_theme(self.root)
        ui_theme.apply_tk_scaling(self.root)
        ui_theme.install_exception_handler(self.root, "ikeexam-teacher")

    @property
    def busy(self):
        return self._pending > 0

    def _worker_loop(self):
        while True:
            token, function, success, error, controls, status, busy_text = \
                self._work.get()
            try:
                result = function()
                failure = None
            except Exception as exc:
                result = None
                failure = exc
            self._events.put(("done", token, result, failure, success, error,
                              controls, status, busy_text))

    def task(self, function, on_success=None, error=None, controls=(),
             status=None, busy_text="正在处理…"):
        """把网络/文件操作交给后台线程；成功和失败回调均在 Tk 主线程。"""
        if self._closed or self.busy:
            if status is not None:
                try:
                    status.configure(text="请等待当前操作完成…", foreground=ORANGE)
                except Exception:
                    pass
            return False
        self._pending += 1
        token = self._pending
        for widget in controls or ():
            if widget is None:
                continue
            key = id(widget)
            if key not in self._control_states:
                self._control_states[key] = (widget, widget_state(widget))
            set_widget_state(widget, "disabled")
        if status is not None:
            try:
                status.configure(text=busy_text, foreground=GRAY)
            except Exception:
                pass
        self._work.put((token, function, on_success, error, tuple(controls),
                        status, busy_text))
        return True

    def _drain_events(self):
        if self._closed:
            return
        while True:
            try:
                event = self._events.get_nowait()
            except queue.Empty:
                break
            (_, token, result, failure, success, error, controls, status,
             busy_text) = event
            if token != self._pending and self._pending != 0:
                continue
            self._pending = 0
            for key, (widget, state) in list(self._control_states.items()):
                try:
                    if widget.winfo_exists():
                        set_widget_state(widget, state)
                except Exception:
                    pass
                self._control_states.pop(key, None)
            try:
                if failure is not None:
                    if error:
                        error(failure)
                    else:
                        mb.showerror("操作失败", str(failure), parent=self.root)
                elif success:
                    success(result)
                elif status is not None:
                    status.configure(text=busy_text, foreground=GREEN)
            except Exception as exc:
                try:
                    mb.showerror("界面更新失败", str(exc), parent=self.root)
                except Exception:
                    pass
        self.root.after(80, self._drain_events)

    @staticmethod
    def _local_base(base):
        try:
            parsed = urlsplit(base)
            return (parsed.hostname or "").lower() in (
                "127.0.0.1", "localhost", "::1", "0.0.0.0")
        except Exception:
            return False

    def _ensure_server_sync(self, base):
        """在后台线程中探活；本机服务未运行则拉起并最多等待 15 秒。"""
        self.api.base = base
        if not self._local_base(base):
            return self.api.ping(timeout=5)
        result = self.api.ping(timeout=1)
        if result.get("ok"):
            return result

        root = PROJECT_ROOT
        server_script = os.path.join(PACKAGE_DIR, "server", "app.py")
        if not os.path.isfile(server_script):
            return {"ok": False,
                    "msg": f"本机服务未启动，且找不到服务端文件：{server_script}"}
        pythonw = os.path.join(root, "runtime", "pythonw.exe")
        python = os.path.join(root, "runtime", "python.exe")
        if os.path.isfile(pythonw):
            interpreter = pythonw
        elif os.path.isfile(python):
            interpreter = python
        else:
            interpreter = sys.executable
        if not interpreter or not os.path.isfile(interpreter):
            return {"ok": False,
                    "msg": "本机服务未启动，且找不到可用的 Python 运行时"}

        env = os.environ.copy()
        try:
            parsed = urlsplit(base)
            port = parsed.port or 9000
            host = parsed.hostname or "127.0.0.1"
            env["EXAM_HTTP_PORT"] = str(port)
        except Exception:
            port, host = 9000, "127.0.0.1"
        try:
            process = subprocess.Popen(
                [interpreter, server_script, "--host", "0.0.0.0",
                 "--port", str(port), "--lan-ip", host],
                cwd=os.path.dirname(server_script), env=env,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception as exc:
            return {"ok": False, "msg": f"自动启动本机服务失败：{exc}"}

        deadline = time.monotonic() + 15
        last = result
        while time.monotonic() < deadline:
            last = self.api.ping(timeout=1)
            if last.get("ok"):
                return last
            code = process.poll()
            if code is not None:
                return {"ok": False,
                        "msg": f"本机服务进程提前退出（代码 {code}）。"
                               "请检查端口是否被占用或安装包是否完整。"
                               f" 原始错误：{last.get('msg', '')}"}
            time.sleep(0.35)
        return {"ok": False,
                "msg": "已尝试自动启动本机服务，但等待约 15 秒后端口仍未就绪。"
                       f" 原始错误：{last.get('msg', '')}"}

    def startup(self, login):
        if self.busy:
            return
        self._starting = True

        def work():
            return self._ensure_server_sync(norm_base(login.e_server.get()))

        def success(result):
            self._starting = False
            login.checked_base = norm_base(login.e_server.get())
            if self._current_login() is not login:
                return
            if result.get("ok"):
                login.btn.configure(state="normal")
                login.local_btn.configure(state="normal")
                login.passwordless_enabled = bool(result.get("passwordless_local"))
                if result.get("passwordless_local"):
                    login.local_btn.configure(text="本机免密码登录")
                    login.tip.configure(text="本机服务已就绪，可使用免密码登录。",
                                        foreground=GREEN)
                else:
                    login.local_btn.configure(text="启用本机免密码登录")
                    login.tip.configure(text="本机服务已就绪，请输入密码或启用本机免密码。",
                                        foreground=GREEN)
            else:
                login.tip.configure(text=result.get("msg", "服务启动失败"),
                                    foreground=RED)
                title = "无法连接本机服务" if self._local_base(self.api.base) \
                    else "无法连接服务器"
                mb.showerror(title, result.get("msg", "服务启动失败"),
                             parent=self.root)

        def failure(exc):
            self._starting = False
            login.tip.configure(text=f"服务检查失败：{exc}", foreground=RED)

        self.api.base = norm_base(login.e_server.get())
        self.task(work, success, failure,
                  controls=(login.btn,), status=login.tip,
                  busy_text="正在检查并启动本机服务…")

    def _current_login(self):
        return getattr(self, "login_window", None)

    def _build_login(self):
        self._starting = False
        self.login_window = LoginWindow(self)
        ui_theme.apply_to_tk(self.root, self.ui_tokens)
        self.startup(self.login_window)

    def enter_main(self):
        self.main_window = MainWindow(self)
        ui_theme.apply_to_tk(self.root, self.ui_tokens)

    def logout(self):
        self.api.clear_session()
        self._build_login()

    def close(self):
        if self.busy:
            if not mb.askyesno("退出", "仍有操作正在进行，确定退出吗？",
                               parent=self.root):
                return
        self._closed = True
        try:
            self.root.destroy()
        except Exception:
            pass

    def run(self):
        self.root.mainloop()


class LoginWindow:
    """连接、首次设密和登录；所有网络步骤均在后台执行。"""

    def __init__(self, app):
        self.app = app
        self.root = app.root
        self.api = app.api
        self._build()

    def _build(self):
        for widget in self.root.winfo_children():
            widget.destroy()
        self.root.title(f"{APP_NAME} · 老师端登录")
        self.root.minsize(680, 520)
        ui_theme.maximize_window(self.root)
        self.root.configure(bg=BG)

        card = tk.Frame(self.root, bg=CARD, padx=56, pady=48,
                        highlightbackground=LINE, highlightthickness=1)
        card.place(relx=0.5, rely=0.5, anchor="center")
        tk.Label(card, text=APP_NAME, bg=CARD, font=(FONT, 24, "bold"),
                 fg=INK).pack(pady=(0, 22))

        tk.Label(card, text="服务器地址", bg=CARD, fg=INK,
                 font=(FONT, 11)).pack(anchor="w")
        self.e_server = tk.Entry(card, font=(FONT, 12), width=32)
        self.e_server.pack(fill="x", ipady=7, pady=(6, 14))
        self.e_server.insert(0, load_server())

        tk.Label(card, text="管理密码", bg=CARD, fg=INK,
                 font=(FONT, 11)).pack(anchor="w")
        self.e_pwd = tk.Entry(card, font=(FONT, 12), width=32, show="•")
        self.e_pwd.pack(fill="x", ipady=7, pady=(6, 12))

        self.tip = tk.Label(card, text="正在检查本机服务…", bg=CARD, fg=GRAY,
                            font=(FONT, 9), wraplength=340, justify="left")
        self.checked_base = None
        self.passwordless_enabled = False
        self.tip.pack(pady=(0, 8))
        self.btn = tk.Button(card, text="连 接 并 登 录", command=self._connect,
                             bg=BLUE, fg="white", font=(FONT, 11, "bold"),
                             relief="flat", cursor="hand2",
                             activebackground=BLUE_D, pady=10, state="disabled")
        self.btn.pack(fill="x")
        self.local_btn = tk.Button(
            card, text="本机免密码登录", command=self._local_login,
            bg=CARD, fg=BLUE, font=(FONT, 10), relief="flat",
            cursor="hand2", activebackground=CARD, state="disabled")
        self.local_btn.pack(pady=(10, 0))

        bar = tk.Frame(card, bg=CARD)
        bar.pack(pady=(10, 0))
        tk.Button(bar, text="关于本软件", command=lambda: show_about(self.root),
                  bg=CARD, fg=GRAY, font=(FONT, 9), relief="flat",
                  cursor="hand2", activebackground=CARD).pack()
        self.e_pwd.bind("<Return>", lambda event: self._connect())
        self.e_server.bind("<Return>", lambda event: self._connect())
        self.e_server.bind("<FocusOut>", lambda event: self.app._schedule_login_startup())
        self.signature = signature_label(self.root, BG)
        self.signature.pack(side="bottom", anchor="e", padx=12, pady=5)
        self.signature.lift()

    def _connect(self):
        if self.app.busy:
            self.tip.configure(text="请等待当前操作完成…", foreground=ORANGE)
            return
        base = norm_base(self.e_server.get())
        if not base:
            self.tip.configure(text="请填写服务器地址", foreground=RED)
            return
        password = self.e_pwd.get().strip()
        self.api.base = base
        save_server(self.e_server.get().strip())

        def work():
            result = self.app._ensure_server_sync(base)
            if not result.get("ok"):
                return result
            if result.get("setup"):
                if len(password) < 6:
                    return {"ok": False,
                            "msg": "首次运行：请设置至少 6 位的初始密码"}
                result = self.api.setup(password)
                if not result.get("ok"):
                    return result
            if not password:
                return {"ok": False, "msg": "请输入管理密码"}
            return self.api.login(password)

        def success(result):
            if self.app._current_login() is not self:
                return
            if result.get("ok"):
                self.app.enter_main()
            else:
                self.tip.configure(
                    text=f"连接或登录失败：{result.get('msg', '未知错误')}",
                    foreground=RED)

        self.app.task(
            work, success,
            error=lambda exc: self.tip.configure(
                text=f"连接或登录失败：{exc}", foreground=RED),
            controls=(self.btn,), status=self.tip,
            busy_text="正在连接服务器并登录…")

    def _local_login(self):
        if self.app.busy:
            return
        base = norm_base(self.e_server.get())
        if not base:
            self.tip.configure(text="请填写服务器地址", foreground=RED)
            return
        self.api.base = base
        save_server(self.e_server.get().strip())
        enable = not self.passwordless_enabled
        if enable and not mb.askyesno(
                "启用本机免密码",
                "启用后，这台电脑打开老师端将无需管理密码。\n"
                "远程电脑仍需要密码。确定启用吗？",
                parent=self.root):
            return

        def work():
            result = self.app._ensure_server_sync(base)
            if not result.get("ok"):
                return result
            if enable or not result.get("passwordless_local"):
                result = self.api.set_passwordless(True)
                if not result.get("ok"):
                    return result
            return self.api.login("")

        def success(result):
            if self.app._current_login() is not self:
                return
            if result.get("ok"):
                self.passwordless_enabled = True
                self.app.enter_main()
            else:
                self.tip.configure(
                    text=f"免密码登录失败：{result.get('msg', '未知错误')}",
                    foreground=RED)

        self.app.task(
            work, success,
            error=lambda exc: self.tip.configure(
                text=f"免密码登录失败：{exc}", foreground=RED),
            controls=(self.btn, self.local_btn), status=self.tip,
            busy_text="正在启用并登录…")


class MainWindow:
    """四页签主界面。"""

    def __init__(self, app):
        self.app = app
        self.root = app.root
        self.api = app.api
        self.data = {}
        self._first_load = True
        self._build()

    def _build(self):
        for widget in self.root.winfo_children():
            widget.destroy()
        self.root.title(f"{APP_NAME} · 老师端")
        self.root.minsize(860, 560)
        ui_theme.maximize_window(self.root)
        self.root.configure(bg=BG)

        top = tk.Frame(self.root, bg=BLUE)
        top.pack(fill="x")
        tk.Label(top, text=APP_NAME, bg=BLUE, fg="white",
                 font=(FONT, 15, "bold")).pack(side="left", padx=16, pady=9)
        self.server_label = tk.Label(
            top, text="", bg=BLUE, fg=BLUE_L, font=(FONT, 9))
        self.server_label.pack(side="left", padx=8)
        try:
            host = urlsplit(self.api.base).netloc or self.api.base
        except Exception:
            host = self.api.base
        self.server_label.configure(text=f"已连接 · {host}")
        bar = tk.Frame(top, bg=BLUE)
        bar.pack(side="right", padx=8)
        self.about_btn = tk.Button(
            bar, text="关于", command=lambda: show_about(self.root), bg=BLUE,
            fg="white", font=(FONT, 9), relief="flat", cursor="hand2",
            activebackground=BLUE_D)
        self.about_btn.pack(side="left", padx=2)
        self.logout_btn = tk.Button(
            bar, text="退出登录", command=self.logout, bg=BLUE, fg="white",
            font=(FONT, 9), relief="flat", cursor="hand2",
            activebackground=BLUE_D)
        self.logout_btn.pack(side="left", padx=2)

        self.notebook = ttk.Notebook(self.root, style="Main.TNotebook")
        self.notebook.enable_traversal()
        self.notebook.pack(fill="both", expand=True, padx=12, pady=(12, 6))
        self.exam_page = ExamManager(self)
        self.question_page = QuestionBank(self)
        self.practice_page = PracticeBank(self)
        self.student_page = StudentManager(self)
        self.pages = (self.exam_page, self.question_page,
                      self.practice_page, self.student_page)
        self.notebook.add(self.exam_page, text="考试管理")
        self.notebook.add(self.question_page, text="理论题库")
        self.notebook.add(self.practice_page, text="实操题库")
        self.notebook.add(self.student_page, text="学生机")

        bottom = tk.Frame(self.root, bg=CARD, highlightbackground=LINE,
                          highlightthickness=1)
        bottom.pack(fill="x", side="bottom")
        self.status = tk.Label(bottom, text="就绪", anchor="w", bg=CARD,
                               fg=GRAY, font=(FONT, 9), padx=12, pady=6)
        self.status.pack(side="left", fill="x", expand=True)
        self.signature = signature_label(bottom, CARD)
        self.signature.pack(side="right", padx=12, pady=6)
        self.refresh_all()

    def all_controls(self):
        controls = [self.logout_btn]
        for page in self.pages:
            controls.extend(page.action_controls())
        return controls

    def task(self, function, success=None, controls=None,
             busy_text="正在处理…", error=None):
        if self.app.busy:
            self.status.configure(text="请等待当前操作完成…", foreground=ORANGE)
            return False
        return self.app.task(function, success, error,
                             controls=controls if controls is not None
                             else self.all_controls(),
                             status=self.status, busy_text=busy_text)

    def refresh(self):
        """兼容旧调用入口；实际加载仍在后台线程执行。"""
        self.refresh_all()

    def refresh_all(self):
        def success(result):
            if result.get("ok"):
                self.data = result
                for page in self.pages:
                    page.set_data(result)
                summary = (f"数据已更新 · 考试 {len(result.get('exams') or [])} 场 · "
                           f"理论题 {len(result.get('questions') or [])} 道 · "
                           f"实操题 {len(result.get('practice') or [])} 道 · "
                           f"学生机 {len(result.get('students') or [])} 台")
                self.status.configure(text=summary, foreground=GREEN)
                if self._first_load and not (result.get("exams") or []):
                    self.status.configure(
                        text=summary + " · 可从「考试管理」新建考试",
                        foreground=GRAY)
                self._first_load = False
            else:
                self.status.configure(
                    text=f"加载失败：{result.get('msg', '未知错误')}",
                    foreground=RED)
                if self._first_load:
                    mb.showerror("加载失败", result.get("msg", "未知错误"),
                                 parent=self.root)
                    self._first_load = False

        self.task(self.api.dashboard, success,
                  busy_text="正在加载考试、题库和学生机…")

    def after_change(self, message):
        self.status.configure(text=message, foreground=GREEN)
        self.refresh_all()

    def show_failure(self, result, title="操作失败"):
        message = result.get("msg", "未知错误")
        self.status.configure(text=f"{title}：{message}", foreground=RED)
        mb.showerror(title, message, parent=self.root)

    def export_scores(self, eid, fmt, title=""):
        fmt = str(fmt or "json").lower()
        suffix = {"json": ".json", "csv": ".csv", "xlsx": ".xlsx",
                  "zip": ".zip"}.get(fmt, ".bin")
        initial = safe_filename(title or f"exam{eid}") + suffix
        desktop = os.path.join(os.path.expanduser("~"), "Desktop")
        initial_dir = desktop if os.path.isdir(desktop) else os.getcwd()
        path = filedialog.asksaveasfilename(
            parent=self.root, title="选择导出文件保存位置",
            initialdir=initial_dir, initialfile=initial,
            defaultextension=suffix,
            filetypes=[("导出文件", "*" + suffix), ("所有文件", "*.*")])
        if not path:
            return

        def work():
            result = self.api.export_exam(eid, fmt)
            if not result.get("ok"):
                return result
            try:
                with open(path, "wb") as fileobj:
                    fileobj.write(result.get("data") or result.get("content") or b"")
            except OSError as exc:
                return {"ok": False, "msg": f"保存导出文件失败：{exc}"}
            return {"ok": True, "path": path,
                    "size": result.get("size", 0)}

        def success(result):
            if result.get("ok"):
                self.status.configure(text=f"已导出：{result['path']}",
                                      foreground=GREEN)
                mb.showinfo("导出完成", f"文件已保存：\n{result['path']}",
                            parent=self.root)
            else:
                self.show_failure(result, "导出失败")

        self.task(work, success, busy_text=f"正在导出 {fmt.upper()} 文件…")

    def logout(self):
        if self.app.busy:
            self.status.configure(text="请等待当前操作完成…", foreground=ORANGE)
            return
        if mb.askyesno("退出登录", "确定退出老师端登录吗？", parent=self.root):
            self.api.clear_session()
            self.app._build_login()


class PageBase(ttk.Frame):
    def __init__(self, main, title):
        super().__init__(main.notebook)
        self.main = main
        self.app = main.app
        self.api = main.api
        self.title = title
        self.action_widgets = []
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

    def add_action(self, widget):
        self.action_widgets.append(widget)
        return widget

    def action_controls(self):
        return list(self.action_widgets)

    def set_data(self, data):
        pass


class ExamManager(PageBase):
    def __init__(self, main):
        super().__init__(main, "考试管理")
        self.exams = []
        self._build()
        self.tree.bind("<<TreeviewSelect>>", lambda event: self._update_buttons())
        self.tree.bind("<Double-1>", lambda event: self._edit())

    def _build(self):
        info = ttk.Frame(self)
        info.grid(row=0, column=0, sticky="ew", pady=(2, 6))
        ttk.Label(info, text="考试列表", font=(FONT, 14, "bold")).pack(
            side="left")
        self.summary = ttk.Label(info, text="", style="Muted.TLabel")
        self.summary.pack(side="right")

        row1 = ttk.Frame(self)
        row1.grid(row=1, column=0, sticky="ew", pady=(0, 5))
        self.btn_new = self.add_action(make_button(
            row1, "新建考试", self._new, accent=True))
        self.btn_new.pack(side="left", padx=(0, 4))
        self.btn_edit = self.add_action(make_button(row1, "编辑配置", self._edit))
        self.btn_edit.pack(side="left", padx=4)
        self.btn_delete = self.add_action(make_button(row1, "删除", self._delete))
        self.btn_delete.pack(side="left", padx=4)
        self.btn_scores = self.add_action(make_button(row1, "查看成绩", self._scores))
        self.btn_scores.pack(side="left", padx=4)
        self.btn_review = self.add_action(make_button(row1, "试卷评讲", self._review))
        self.btn_review.pack(side="left", padx=4)
        self.btn_charts = self.add_action(make_button(row1, "成绩图表", self._charts))
        self.btn_charts.pack(side="left", padx=4)
        self.export_var = tk.StringVar(value="csv")
        self.export_box = ttk.Combobox(row1, textvariable=self.export_var,
                                       values=("json", "csv", "xlsx", "zip"),
                                       width=7, state="readonly")
        self.export_box.pack(side="right", padx=(4, 0))
        self.btn_export = self.add_action(make_button(
            row1, "导出", lambda: self._export()))
        self.btn_export.pack(side="right", padx=4)
        self.btn_refresh = self.add_action(make_button(
            row1, "刷新", self.main.refresh_all))
        self.btn_refresh.pack(side="right", padx=4)

        row2 = ttk.Frame(self)
        self.rowconfigure(2, weight=0)
        row2.grid(row=2, column=0, sticky="ew", pady=(0, 7))
        commands = (
            ("start", "开始考试", self._start), ("pause", "暂停", self._pause),
            ("resume", "恢复", self._resume), ("theory", "切理论", self._theory),
            ("practice", "切实操", self._practice), ("stop", "结束考试", self._stop),
            ("regrade", "重新评分", self._regrade),
            ("rejudge", "重判程序填空", self._rejudge),
        )
        self.buttons = {}
        for key, text, command in commands:
            self.buttons[key] = self.add_action(make_button(row2, text, command))
            self.buttons[key].pack(side="left", padx=(0, 3))
        self.rowconfigure(3, weight=1)
        holder, self.tree = make_tree(
            self, ("id", "code", "title", "klass", "duration", "status", "phase"),
            ("ID", "考试编号", "考试名称", "班级", "时长", "状态", "阶段"),
            (42, 148, 245, 90, 78, 72, 76), stretch="title", height=12)
        holder.grid(row=3, column=0, sticky="nsew")

    def set_data(self, data):
        self.exams = list(data.get("exams") or [])
        selected = self.tree.selection()
        selected_id = selected[0] if selected else None
        self.tree.delete(*self.tree.get_children())
        for exam in self.exams:
            eid = str(exam.get("id"))
            duration = exam.get("duration")
            self.tree.insert(
                "", "end", iid=eid,
                values=(eid, exam.get("code") or "—", exam.get("title") or "",
                        exam.get("klass") or "未分班",
                        f"{duration} 分钟" if duration is not None else "—",
                        STATUS_NAMES.get(exam.get("status"), exam.get("status") or "—"),
                        PHASE_NAMES.get(exam.get("phase"), exam.get("phase") or "—")))
        if selected_id and self.tree.exists(selected_id):
            self.tree.selection_set(selected_id)
        active = sum(1 for ex in self.exams
                     if ex.get("status") in ("running", "paused"))
        self.summary.configure(text=f"共 {len(self.exams)} 场 · 进行中 {active} 场")
        self._update_buttons()

    def _selected(self):
        selected = self.tree.selection()
        if not selected:
            return None
        try:
            eid = int(selected[0])
        except ValueError:
            return None
        return next((exam for exam in self.exams if exam.get("id") == eid), None)

    def _require_selected(self):
        exam = self._selected()
        if exam is None:
            mb.showinfo("提示", "请先在列表中选择一场考试", parent=self.main.root)
        return exam

    def _update_buttons(self):
        exam = self._selected()
        active = bool(exam and exam.get("status") in ("running", "paused"))
        status = exam.get("status") if exam else None
        states = {
            "new": True, "edit": exam is not None, "scores": exam is not None,
            "review": exam is not None, "charts": exam is not None,
            "delete": exam is not None and not active,
            "export": exam is not None, "refresh": True,
            "start": exam is not None and status not in ("running", "paused"),
            "pause": status == "running", "resume": status == "paused",
            "theory": active, "practice": active, "stop": active,
            "regrade": exam is not None, "rejudge": exam is not None,
        }
        for key, widget in self.buttons.items():
            widget.configure(state="normal" if states[key] else "disabled")
        self.btn_edit.configure(state="normal" if exam else "disabled")
        self.btn_scores.configure(state="normal" if exam else "disabled")
        self.btn_review.configure(state="normal" if exam else "disabled")
        self.btn_charts.configure(state="normal" if exam else "disabled")
        self.btn_delete.configure(state="normal" if states["delete"] else "disabled")
        self.btn_export.configure(state="normal" if exam else "disabled")

    def _new(self):
        if self.app.busy:
            return
        ExamEditor(self, None)

    def _edit(self):
        exam = self._require_selected()
        if not exam or self.app.busy:
            return
        ExamEditor(self, exam["id"])

    def _delete(self):
        exam = self._require_selected()
        if not exam or self.app.busy:
            return
        if not mb.askyesno(
                "删除考试", f"确定删除「{exam.get('title', '')}」吗？\n"
                "考试配置、答卷和成绩将一并删除，且无法恢复。",
                parent=self.main.root):
            return
        eid = exam["id"]

        def success(result):
            if result.get("ok"):
                self.main.after_change("考试已删除")
            else:
                self.main.show_failure(result, "删除失败")

        self.main.task(lambda: self.api.delete_exam(eid), success,
                       controls=self.action_controls(), busy_text="正在删除考试…")

    def _start(self):
        exam = self._require_selected()
        if not exam or self.app.busy:
            return
        klass = ClassPrompt(self.main.root, exam.get("klass") or "").result
        if klass is None:
            return
        eid = exam["id"]

        def success(result):
            if result.get("ok"):
                mb.showinfo("考试已开始", "开始命令已发送到全部学生机。",
                            parent=self.main.root)
                self.main.after_change("考试已开始")
            else:
                self.main.show_failure(result, "开始失败")

        self.main.task(lambda: self.api.start_exam(eid, klass), success,
                       controls=self.action_controls(), busy_text="正在开始考试…")

    def _simple_action(self, title, api_call, success_text, confirm=None):
        exam = self._require_selected()
        if not exam or self.app.busy:
            return
        if confirm and not mb.askyesno(title, confirm, parent=self.main.root):
            return
        eid = exam["id"]

        def success(result):
            if result.get("ok"):
                self.main.after_change(success_text)
            else:
                self.main.show_failure(result, title + "失败")

        self.main.task(lambda: api_call(eid), success,
                       controls=self.action_controls(),
                       busy_text=f"正在{title}…")

    def _pause(self):
        self._simple_action("暂停考试", self.api.pause_exam,
                            "考试已暂停，倒计时已停止")

    def _resume(self):
        self._simple_action("恢复考试", self.api.resume_exam,
                            "考试已恢复")

    def _theory(self):
        exam = self._require_selected()
        if exam and exam.get("phase") == "theory":
            self.main.status.configure(text="当前已经是理论阶段", foreground=GRAY)
            return
        if not exam or self.app.busy:
            return
        eid = exam["id"]

        def success(result):
            if result.get("ok"):
                self.main.after_change("已切换到理论阶段")
            else:
                self.main.show_failure(result, "阶段切换失败")

        self.main.task(lambda: self.api.set_phase(eid, "theory"), success,
                       controls=self.action_controls(),
                       busy_text="正在切换到理论阶段…")

    def _practice(self):
        exam = self._require_selected()
        if exam and exam.get("phase") == "practice":
            self.main.status.configure(text="当前已经是实操阶段", foreground=GRAY)
            return
        if not exam or self.app.busy:
            return
        eid = exam["id"]

        def success(result):
            if result.get("ok"):
                self.main.after_change("已切换到实操阶段")
            else:
                self.main.show_failure(result, "阶段切换失败")

        self.main.task(lambda: self.api.set_phase(eid, "practice"), success,
                       controls=self.action_controls(),
                       busy_text="正在切换到实操阶段…")

    def _stop(self):
        exam = self._require_selected()
        if not exam or self.app.busy:
            return
        clear_login = StopPrompt(self.main.root).result
        if clear_login is None:
            return
        eid = exam["id"]

        def success(result):
            if result.get("ok"):
                mb.showinfo("考试已结束", "结束命令已发送到全部学生机。",
                            parent=self.main.root)
                self.main.after_change("考试已结束")
            else:
                self.main.show_failure(result, "结束失败")

        self.main.task(lambda: self.api.stop_exam(eid, clear_login), success,
                       controls=self.action_controls(), busy_text="正在结束考试…")

    def _scores(self):
        exam = self._require_selected()
        if exam and not self.app.busy:
            ScoresWindow(self.main, exam)

    def _review(self):
        exam = self._require_selected()
        if exam and not self.app.busy:
            ReviewWindow(self.main, exam)

    def _charts(self):
        exam = self._require_selected()
        if exam and not self.app.busy:
            ChartsWindow(self.main, exam)

    def _export(self):
        exam = self._require_selected()
        if exam and not self.app.busy:
            self.main.export_scores(exam["id"], self.export_var.get(),
                                    exam.get("title") or exam.get("code") or "")

    def _regrade(self):
        exam = self._require_selected()
        if not exam or self.app.busy:
            return
        if not mb.askyesno("重新评分", "将本场已提交的实操答卷重新加入评分队列，"
                           "确定继续吗？", parent=self.main.root):
            return
        eid = exam["id"]

        def success(result):
            if result.get("ok"):
                mb.showinfo("已加入评分队列",
                            f"共重新评分 {result.get('count', 0)} 份答卷。",
                            parent=self.main.root)
                self.main.refresh_all()
            else:
                self.main.show_failure(result, "重新评分失败")

        self.main.task(lambda: self.api.regrade(eid), success,
                       controls=self.action_controls(),
                       busy_text="正在提交重新评分任务…")

    def _rejudge(self):
        exam = self._require_selected()
        if not exam or self.app.busy:
            return
        if not mb.askyesno("重判程序填空", "确定重新运行并判定本场程序填空答案吗？",
                           parent=self.main.root):
            return
        eid = exam["id"]

        def success(result):
            if result.get("ok"):
                mb.showinfo("重判完成",
                            f"重判 {result.get('count', 0)} 份，"
                            f"答对 {result.get('correct', 0)} 份。",
                            parent=self.main.root)
                self.main.refresh_all()
            else:
                self.main.show_failure(result, "重判失败")

        self.main.task(lambda: self.api.rejudge_code(eid), success,
                       controls=self.action_controls(),
                       busy_text="正在重判程序填空…")


class ClassPrompt:
    def __init__(self, parent, klass=""):
        self.result = None
        self.top = tk.Toplevel(parent)
        self.top.title("开始考试")
        self.top.transient(parent)
        self.top.resizable(False, False)
        self.top.configure(bg=BG)
        frame = ttk.Frame(self.top, padding=18)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="请填写本次考试的班级 / 考场：").pack(anchor="w")
        self.var = tk.StringVar(value=klass)
        entry = ttk.Entry(frame, textvariable=self.var, width=30)
        entry.pack(fill="x", pady=10)
        entry.focus_set()
        buttons = ttk.Frame(frame)
        buttons.pack(fill="x")
        make_button(buttons, "取消", self.top.destroy).pack(side="right")
        make_button(buttons, "开始考试", self._ok, accent=True).pack(
            side="right", padx=(0, 8))
        entry.bind("<Return>", lambda event: self._ok())
        self.top.bind("<Escape>", lambda event: self.top.destroy())
        self.top.grab_set()
        parent.wait_window(self.top)

    def _ok(self):
        value = self.var.get().strip()
        if not value:
            mb.showwarning("提示", "班级不能为空", parent=self.top)
            return
        self.result = value
        self.top.destroy()


class StopPrompt:
    def __init__(self, parent):
        self.result = None
        self.top = tk.Toplevel(parent)
        self.top.title("结束考试")
        self.top.transient(parent)
        self.top.resizable(False, False)
        self.top.configure(bg=BG)
        frame = ttk.Frame(self.top, padding=18)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="确定结束当前考试并通知全部学生机退出吗？",
                  wraplength=390).pack(anchor="w")
        ttk.Label(frame, text="请选择是否同时清除学生端的登录状态：",
                  style="Muted.TLabel").pack(anchor="w", pady=(10, 12))
        buttons = ttk.Frame(frame)
        buttons.pack(fill="x")
        make_button(buttons, "取消", self.top.destroy).pack(side="left")
        make_button(buttons, "保留登录", lambda: self._choose(False)).pack(
            side="right", padx=(8, 0))
        make_button(buttons, "清除登录", lambda: self._choose(True),
                    accent=True).pack(side="right", padx=(8, 0))
        self.top.bind("<Escape>", lambda event: self.top.destroy())
        self.top.grab_set()
        parent.wait_window(self.top)

    def _choose(self, value):
        self.result = value
        self.top.destroy()


class ExamEditor:
    """考试配置编辑器：基本、选题、名单素材和 JSON 配置。"""

    def __init__(self, page, eid=None):
        self.page = page
        self.main = page.main
        self.app = page.app
        self.api = page.api
        self.eid = eid
        self.exam = None
        self.locked = False
        self.controls = []
        self.selected_qids = []
        self.selected_pids = []
        self.materials = []
        self.pending_uploads = []
        self._baseline = None
        self._saved_qids = []
        self._saved_pids = []
        self._active_tab = None
        self._tab_guard = False
        self._selection_frames = set()
        self.office_types = ["word"]
        self.top = tk.Toplevel(self.main.root)
        self.top.title("编辑考试" if eid else "新建考试")
        self.top.transient(self.main.root)
        self.top.geometry("1180x760")
        self.top.minsize(1040, 680)
        self.top.configure(bg=BG)
        icon = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.ico")
        if os.path.isfile(icon):
            try:
                self.top.iconbitmap(icon)
            except tk.TclError:
                pass
        self.top.protocol("WM_DELETE_WINDOW", self._close)
        self._build()
        ui_theme.apply_to_tk(self.top, getattr(self.app, "ui_tokens", None))
        self.top.bind("<Control-s>", lambda event: self._save_active())
        self.top.bind("<Escape>", lambda event: self._close())
        if eid is not None:
            self._load()
        else:
            self._set_defaults()
            self._render_all_questions()
            self._render_practice()
            self._render_materials()
            self._sync_type_tabs()
            self.tip.configure(text="请填写考试配置，保存后生效。", foreground=GRAY)

    def _control(self, widget):
        self.controls.append(widget)
        return widget

    def _note_dirty(self):
        if self._baseline is not None:
            self.footer_tip.configure(
                text="有未保存修改 · Ctrl+S 保存 · Esc 关闭", foreground=ORANGE)

    def _state_signature(self):
        text = lambda widget: widget.get("1.0", "end").strip()
        return (
            self.name_var.get(), self.duration_var.get(), self.klass_var.get(),
            bool(self.require_var.get()), bool(self.passwordless_var.get()),
            tuple(sorted(self.type_vars)), tuple(self.type_vars[k].get()
                                                 for k in sorted(self.type_vars)),
            text(self.requirement_text), text(self.finish_text),
            self.checks_editor.get_rules(), self.typing_editor.get_config(),
            self.drawing_editor.get_config(), text(self.roster_text),
            tuple(self.materials), tuple(self.pending_uploads),
        )

    def _mark_clean(self):
        self._baseline = self._state_signature()
        self._saved_qids = list(self.selected_qids)
        self._saved_pids = list(self.selected_pids)
        if not self.locked:
            self.footer_tip.configure(text="Ctrl+S 保存 · Esc 关闭", foreground=GRAY)

    def _selection_dirty(self):
        return (tuple(self.selected_qids) != tuple(self._saved_qids) or
                tuple(self.selected_pids) != tuple(self._saved_pids))

    def _has_unsaved_changes(self):
        return (self._selection_dirty() or
                (self._baseline is not None and
                 self._state_signature() != self._baseline))

    def _entry(self, parent, **kwargs):
        return self._control(ttk.Entry(parent, **kwargs))

    def _button(self, parent, text, command, accent=False):
        return self._control(make_button(parent, text, command, accent=accent))

    def _check(self, parent, text, variable):
        return self._control(ttk.Checkbutton(
            parent, text=text, variable=variable, style="Choice.TCheckbutton"))

    def _text(self, parent, height=4, width=80):
        return self._control(scrolledtext.ScrolledText(
            parent, height=height, width=width, wrap="word", font=(FONT, 9),
            background=CARD, foreground=INK, relief="solid", borderwidth=1))

    def _build(self):
        outer = ttk.Frame(self.top, padding=10)
        outer.pack(fill="both", expand=True)
        outer.rowconfigure(1, weight=1)
        outer.columnconfigure(0, weight=1)
        header = ttk.Frame(outer)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        self.title_label = ttk.Label(header, text="考试配置",
                                     font=(FONT, 13, "bold"))
        self.title_label.pack(side="left")
        self.tip = ttk.Label(header, text="正在加载考试配置…",
                             style="Muted.TLabel")
        self.tip.pack(side="right")
        notebook_style = ttk.Style(self.top)
        notebook_style.configure("Exam.TNotebook", background=BG,
                                 bordercolor=LINE, borderwidth=0)
        notebook_style.configure(
            "Exam.TNotebook.Tab", background=_UI["tab_bg"], foreground=GRAY,
            padding=(10, 8), font=(FONT, 9), borderwidth=0,
            bordercolor=_UI["tab_bg"], lightcolor=_UI["tab_bg"],
            darkcolor=_UI["tab_bg"])
        notebook_style.map(
            "Exam.TNotebook.Tab",
            background=[("selected", CARD), ("active", _UI["tab_active"]),
                        ("disabled", BG)],
            foreground=[("selected", BLUE), ("active", INK),
                        ("disabled", _UI["disabled_fg"])])
        self.notebook = ttk.Notebook(outer, style="Exam.TNotebook")
        self.notebook.enable_traversal()
        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)
        self.notebook.grid(row=1, column=0, sticky="nsew")
        self._build_basic(ttk.Frame(self.notebook, padding=10))
        self._build_question_pages()
        self._build_practice(ttk.Frame(self.notebook, padding=8))
        self._build_typing(ttk.Frame(self.notebook, padding=10))
        self._build_drawing(ttk.Frame(self.notebook, padding=10))
        self._build_roster(ttk.Frame(self.notebook, padding=8))
        self._build_json(ttk.Frame(self.notebook, padding=10))
        for widget in (self.requirement_text, self.finish_text, self.roster_text):
            widget.bind("<KeyRelease>", lambda event: self._note_dirty())
            ui_theme.bind_mousewheel(widget, widget)
        self._sync_type_tabs()
        self._sync_practice_types()
        footer = ttk.Frame(outer)
        footer.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        footer.columnconfigure(0, weight=1)
        self.footer_tip = ttk.Label(footer, text="", style="Muted.TLabel")
        self.footer_tip.grid(row=0, column=0, sticky="w")
        self.btn_back_basic = self._button(footer, "返回基本设置",
                                           lambda: self.notebook.select(self.basic_frame))
        self.btn_save_selection = self._button(footer, "保存选题",
                                               self._save_active, accent=True)
        self.btn_cancel = self._button(footer, "取消", self._close)
        self.btn_save = self._button(footer, "保存考试", self._save, accent=True)
        self._active_tab = self.notebook.select()
        self._update_footer()

    def _build_basic(self, frame):
        self.basic_frame = frame
        self.notebook.add(frame, text="基本设置")
        frame.columnconfigure(1, weight=1)
        self.name_var = tk.StringVar()
        self.duration_var = tk.StringVar(value="60")
        self.klass_var = tk.StringVar()
        self.require_var = tk.BooleanVar(value=False)
        self.passwordless_var = tk.BooleanVar(value=False)
        self.type_vars = {key: tk.BooleanVar(value=False)
                          for key, _label in EXAM_TYPES}
        for variable in self.type_vars.values():
            variable.trace_add("write", lambda *args: (
                self._on_type_changed(), self._note_dirty()))
        for variable in (self.name_var, self.duration_var, self.klass_var,
                         self.require_var, self.passwordless_var):
            variable.trace_add("write", lambda *args: self._note_dirty())

        self.name_entry = self._entry(frame, textvariable=self.name_var)
        ttk.Label(frame, text="考试名称").grid(row=0, column=0, sticky="w",
                                               padx=(0, 6), pady=4)
        self.name_entry.grid(row=0, column=1, columnspan=3, sticky="ew", pady=4)
        ttk.Label(frame, text="总时长（分钟）").grid(row=1, column=0, sticky="w",
                                                     pady=4)
        self.duration_entry = self._entry(
            frame, textvariable=self.duration_var, width=10)
        self.duration_entry.grid(row=1, column=1, sticky="w", pady=4)
        ttk.Label(frame, text="理论题、实操题共用，从开考起统一倒计时",
                  style="Muted.TLabel").grid(row=1, column=2, columnspan=2,
                                             sticky="w", padx=(12, 0))
        ttk.Label(frame, text="班级 / 考场").grid(row=2, column=0, sticky="w",
                                                  pady=4)
        self.klass_entry = self._entry(frame, textvariable=self.klass_var)
        self.klass_entry.grid(row=2, column=1, columnspan=3, sticky="ew", pady=4)
        self.require_check = self._check(frame, "未做完禁止提交", self.require_var)
        self.require_check.grid(row=3, column=0, columnspan=2, sticky="w", pady=4)
        self._check(frame, "免密码考试（学生不填姓名/座位，按 IP 识别）",
                    self.passwordless_var).grid(
                        row=3, column=2, columnspan=2, sticky="w", pady=4)

        types = ttk.LabelFrame(frame, text="本场考试题型（全部页签常驻）")
        types.grid(row=4, column=0, columnspan=4, sticky="ew", pady=(6, 4))
        for column in range(3):
            types.columnconfigure(column, weight=1)
        for index, (key, label) in enumerate(EXAM_TYPES):
            check = self._check(types, label, self.type_vars[key])
            check.configure(width=13)
            check.grid(row=index // 3, column=index % 3, sticky="ew",
                       padx=8, pady=5)

        ttk.Label(frame, text="实操要求 / 说明").grid(row=5, column=0,
                                                      sticky="w", pady=(6, 2))
        self.requirement_text = self._text(frame, 3)
        self.requirement_text.grid(row=6, column=0, columnspan=4, sticky="ew")
        ttk.Label(frame, text="交卷结束语").grid(row=7, column=0,
                                                  sticky="w", pady=(6, 2))
        self.finish_text = self._text(frame, 3)
        self.finish_text.grid(row=8, column=0, columnspan=4, sticky="ew")
        ttk.Label(frame, text="提示：所有题型页签都会显示，带 ✓ 的才发给学生；"
                  "取消勾选不会删除已选题目。答卷文件名由系统按座位号/姓名自动生成。",
                  style="Muted.TLabel", wraplength=1000).grid(
                      row=9, column=0, columnspan=4, sticky="w", pady=(6, 0))

    def _build_question_pages(self):
        self.q_pages = {}
        sort_values = ("选题顺序", "ID 升序", "分类名称", "分值降序", "分值升序")
        for key, label in QTYPES:
            frame = ttk.Frame(self.notebook, padding=8)
            self.notebook.add(frame, text=label)
            self._selection_frames.add(str(frame))
            frame.rowconfigure(2, weight=1)
            frame.columnconfigure(0, weight=1)

            bar = ttk.Frame(frame)
            bar.grid(row=0, column=0, sticky="ew", pady=(0, 4))
            filter_var = tk.StringVar()
            self._control(ttk.Entry(bar, textvariable=filter_var, width=18)).pack(
                side="left")
            ttk.Label(bar, text="分类", style="Muted.TLabel").pack(
                side="left", padx=(8, 3))
            cat_var = tk.StringVar(value="全部分类")
            cat_combo = self._control(ttk.Combobox(
                bar, textvariable=cat_var, width=14, state="readonly"))
            cat_combo.pack(side="left")
            ttk.Label(bar, text="排序", style="Muted.TLabel").pack(
                side="left", padx=(8, 3))
            sort_var = tk.StringVar(value="选题顺序")
            self._control(ttk.Combobox(
                bar, textvariable=sort_var, width=12, state="readonly",
                values=sort_values)).pack(side="left")
            ttk.Label(bar, text="随机数量", style="Muted.TLabel").pack(
                side="left", padx=(8, 3))
            random_var = tk.StringVar(value="10")
            self._control(ttk.Spinbox(
                bar, textvariable=random_var, width=5, from_=1, to=200)).pack(
                    side="left")
            self._button(bar, "随机抽题",
                         lambda k=key: self._random_questions(k)).pack(
                             side="left", padx=(6, 0))

            tools = ttk.Frame(frame)
            tools.grid(row=1, column=0, sticky="ew", pady=(0, 4))
            self._button(tools, "全选当前",
                         lambda k=key: self._select_visible(k, True)).pack(
                             side="left")
            self._button(tools, "清空当前",
                         lambda k=key: self._select_visible(k, False)).pack(
                             side="left", padx=4)
            self._button(tools, "重排已选",
                         lambda k=key: self._sort_selected_questions(k)).pack(
                             side="left", padx=4)
            self._button(tools, "上移",
                         lambda k=key: self._move_selected_question(k, -1)).pack(
                             side="left", padx=4)
            self._button(tools, "下移",
                         lambda k=key: self._move_selected_question(k, 1)).pack(
                             side="left", padx=4)
            count = ttk.Label(tools, text="", style="Muted.TLabel")
            count.pack(side="right", padx=(0, 10))

            holder, tree = make_tree(
                frame, ("sel", "order", "id", "category", "score", "stem"),
                ("选择", "顺序", "ID", "分类", "分值", "题干"),
                (42, 44, 42, 100, 48, 390), stretch="stem", height=12)
            holder.grid(row=2, column=0, sticky="nsew")
            tree.bind("<Button-1>", lambda event, k=key: self._toggle_question(event, k))
            tree.bind("<Double-1>", lambda event, k=key: self._toggle_question(event, k))
            tree.bind("<space>", lambda event, k=key: self._toggle_question_keyboard(event, k))
            filter_var.trace_add("write", lambda *args, k=key: self._render_question_page(k))
            cat_var.trace_add("write", lambda *args, k=key: self._render_question_page(k))
            sort_var.trace_add("write", lambda *args, k=key: self._render_question_page(k))
            self.q_pages[key] = {
                "frame": frame, "filter": filter_var, "category": cat_var,
                "category_combo": cat_combo, "sort": sort_var, "random": random_var,
                "tree": tree, "count": count,
            }

    def _build_practice(self, frame):
        self.practice_frame = frame
        self.notebook.add(frame, text="实操题")
        self._selection_frames.add(frame)
        frame.rowconfigure(2, weight=1)
        frame.columnconfigure(0, weight=1)
        bar = ttk.Frame(frame)
        bar.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        self.p_filter_var = tk.StringVar()
        self._control(ttk.Entry(bar, textvariable=self.p_filter_var, width=20)).pack(
            side="left")
        ttk.Label(bar, text="类型", style="Muted.TLabel").pack(
            side="left", padx=(8, 3))
        self.p_type_var = tk.StringVar(value="全部类型")
        self.p_type_combo = self._control(ttk.Combobox(
            bar, textvariable=self.p_type_var, width=12, state="readonly"))
        self.p_type_combo.pack(side="left")
        ttk.Label(bar, text="排序", style="Muted.TLabel").pack(
            side="left", padx=(8, 3))
        self.p_sort_var = tk.StringVar(value="难度 简单→难")
        self._control(ttk.Combobox(
            bar, textvariable=self.p_sort_var, width=14, state="readonly",
            values=("难度 简单→难", "难度 难→简单", "题目名称", "ID 升序"))).pack(
                side="left")

        tools = ttk.Frame(frame)
        tools.grid(row=1, column=0, sticky="ew", pady=(0, 4))
        self._button(tools, "全选当前",
                     lambda: self._select_visible_practice(True)).pack(side="left")
        self._button(tools, "清空当前",
                     lambda: self._select_visible_practice(False)).pack(
                         side="left", padx=4)
        self._button(tools, "上移",
                     lambda: self._move_selected_practice(-1)).pack(
                         side="left", padx=4)
        self._button(tools, "下移",
                     lambda: self._move_selected_practice(1)).pack(
                         side="left", padx=4)
        self.p_count = ttk.Label(tools, text="", style="Muted.TLabel")
        self.p_count.pack(side="right", padx=(0, 10))

        holder, self.p_tree = make_tree(
            frame, ("sel", "order", "id", "type", "level", "category", "name"),
            ("选择", "顺序", "ID", "类型", "难度", "分类", "题目名称"),
            (42, 44, 42, 72, 62, 100, 330), stretch="name", height=12)
        holder.grid(row=2, column=0, sticky="nsew")
        self.p_tree.bind("<Button-1>", self._toggle_practice)
        self.p_tree.bind("<Double-1>", self._toggle_practice)
        self.p_tree.bind("<space>", self._toggle_practice_keyboard)
        self.p_filter_var.trace_add("write", lambda *args: self._render_practice())
        self.p_type_var.trace_add("write", lambda *args: self._render_practice())
        self.p_sort_var.trace_add("write", lambda *args: self._render_practice())

    def _build_typing(self, frame):
        self.typing_frame = frame
        self.notebook.add(frame, text="打字练习")
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(1, weight=1)
        ttk.Label(frame, text="打字练习配置", font=(FONT, 11, "bold")).grid(
            row=0, column=0, sticky="w", pady=(0, 6))
        self.typing_editor = TypingConfigEditor(frame, on_change=self._note_dirty)
        self.typing_editor.grid(row=1, column=0, sticky="nsew")
        self.controls.extend(self.typing_editor.controls)

    def _build_drawing(self, frame):
        self.drawing_frame = frame
        self.notebook.add(frame, text="画板题")
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(1, weight=1)
        ttk.Label(frame, text="画板题配置", font=(FONT, 11, "bold")).grid(
            row=0, column=0, sticky="w", pady=(0, 6))
        self.drawing_editor = DrawingConfigEditor(frame, on_change=self._note_dirty)
        self.drawing_editor.grid(row=1, column=0, sticky="nsew", padx=8, pady=8)
        self.controls.extend(self.drawing_editor.controls)

    def _build_roster(self, frame):
        self.notebook.add(frame, text="名单与素材")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        frame.columnconfigure(1, weight=1)
        left = ttk.LabelFrame(frame, text="本场学生名单", padding=8)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        left.rowconfigure(1, weight=1)
        left.columnconfigure(0, weight=1)
        ttk.Label(left, text="一行一个学生：姓名,座位,IP；免密码考试可只填 IP。"
                  "也支持空格或 | 分隔。",
                  style="Muted.TLabel", wraplength=360).grid(
                      row=0, column=0, sticky="w")
        self.roster_text = self._text(left, 11, width=40)
        self.roster_text.grid(row=1, column=0, sticky="nsew", pady=6)
        buttons = ttk.Frame(left)
        buttons.grid(row=2, column=0, sticky="ew")
        self._button(buttons, "替换名单",
                     lambda: self._save_roster("replace")).pack(side="left")
        self._button(buttons, "追加名单",
                     lambda: self._save_roster("append")).pack(
                         side="left", padx=4)
        self._button(buttons, "清空", self._clear_roster).pack(side="left")
        self._button(buttons, "从在线学生导入 IP", self._import_student_ips).pack(
            side="left", padx=4)
        self.roster_count = ttk.Label(left, text="", style="Muted.TLabel")
        self.roster_count.grid(row=3, column=0, sticky="w", pady=(5, 0))

        right = ttk.LabelFrame(frame, text="考试素材", padding=8)
        right.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        right.rowconfigure(1, weight=1)
        right.columnconfigure(0, weight=1)
        ttk.Label(right, text="当前考试素材（上传后保存到服务端）").grid(
            row=0, column=0, sticky="w")
        holder, self.material_tree = make_tree(
            right, ("name", "state"), ("文件名", "状态"), (270, 75),
            stretch="name", height=5)
        holder.grid(row=1, column=0, sticky="nsew", pady=4)
        buttons = ttk.Frame(right)
        buttons.grid(row=2, column=0, sticky="ew")
        self._button(buttons, "上传素材", self._upload).pack(side="left")
        self._button(buttons, "删除所选", self._delete_material).pack(
            side="left", padx=4)
        ttk.Label(right, text="服务端已有素材（选择后加入当前考试）").grid(
            row=3, column=0, sticky="w", pady=(8, 0))
        holder, self.available_tree = make_tree(
            right, ("name",), ("文件名",), (345,), stretch="name", height=4)
        holder.grid(row=4, column=0, sticky="nsew", pady=4)
        self._button(right, "加入所选素材", self._add_available).grid(
            row=5, column=0, sticky="w")

    def _build_json(self, frame):
        self.notebook.add(frame, text="评分规则")
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(1, weight=1)
        ttk.Label(frame, text="实操评分规则", font=(FONT, 11, "bold")).grid(
            row=0, column=0, sticky="w")
        self.checks_editor = RuleEditor(frame, on_change=self._note_dirty)
        self.checks_editor.grid(row=1, column=0, sticky="nsew", pady=(6, 0))

    def _on_type_changed(self):
        if hasattr(self, "q_pages"):
            self._sync_type_tabs()

    def _sync_type_tabs(self):
        for key, label in QTYPES:
            page = self.q_pages.get(key)
            if page:
                enabled = bool(self.type_vars[key].get())
                self.notebook.tab(page["frame"],
                                  text=("✓ " if enabled else "") + label)
        for key, frame, label in (
                ("practice", getattr(self, "practice_frame", None), "实操题"),
                ("typing", getattr(self, "typing_frame", None), "打字练习"),
                ("drawing", getattr(self, "drawing_frame", None), "画板题")):
            if frame is not None:
                enabled = bool(self.type_vars[key].get())
                self.notebook.tab(frame, text=("✓ " if enabled else "") + label)

    def _on_tab_changed(self, event=None):
        if self._tab_guard or not hasattr(self, "notebook"):
            return
        new_tab = self.notebook.select()
        old_tab = self._active_tab
        if (old_tab and new_tab and old_tab != new_tab and
                old_tab in self._selection_frames and
                new_tab in self._selection_frames and self._selection_dirty()):
            self._tab_guard = True
            self.notebook.select(old_tab)
            self._tab_guard = False
            mb.showwarning("选题尚未保存",
                           "请先点击底部「保存选题」，再切换其他题型。",
                           parent=self.top)
            return
        self._active_tab = new_tab
        self._update_footer()

    def _update_footer(self):
        if not hasattr(self, "btn_save"):
            return
        for button in (self.btn_back_basic, self.btn_save_selection,
                       self.btn_cancel, self.btn_save):
            button.grid_remove()
        selection_page = self._active_tab in self._selection_frames
        if selection_page:
            self.btn_back_basic.grid(row=0, column=1, padx=(8, 0))
            self.btn_save_selection.grid(row=0, column=2)
        else:
            self.btn_cancel.grid(row=0, column=2)
            self.btn_save.grid(row=0, column=1, padx=(0, 8))

    def _save_active(self):
        if self._active_tab == str(getattr(self, "practice_frame", "")):
            self._apply_practice()
        elif self._active_tab in self._selection_frames:
            self._apply_questions()
        else:
            self._save()

    def _sync_practice_types(self):
        if not hasattr(self, "p_type_combo"):
            return
        values = ("全部类型", "Word", "Excel", "PowerPoint")
        self.p_type_combo.configure(values=values)
        if self.p_type_var.get() not in values:
            self.p_type_var.set("全部类型")
        if hasattr(self, "p_tree"):
            self._render_practice()

    def _set_defaults(self):
        self.name_var.set("")
        self.duration_var.set("60")
        self.office_types = ["word"]
        for key in ("single", "multi", "judge", "blank", "code", "drag",
                    "practice"):
            self.type_vars[key].set(True)
        self.checks_editor.set_rules([])
        self.typing_editor.set_config({})
        self.drawing_editor.set_config(
            {"brief": "", "styles": ["draw"], "grid": 28})
        self._render_all_questions()
        self._render_practice()
        self._sync_type_tabs()
        self._sync_practice_types()
        self._mark_clean()

    def _load(self):
        eid = self.eid

        def success(result):
            if result.get("ok"):
                self._populate(result)
                self.tip.configure(text="配置已加载", foreground=GREEN)
                self.footer_tip.configure(text="Ctrl+S 保存 · Esc 关闭 · 空格切换选题")
            else:
                self.tip.configure(text=f"加载失败：{result.get('msg', '')}",
                                   foreground=RED)
                mb.showerror("加载失败", result.get("msg", "未知错误"),
                             parent=self.top)
                self.top.destroy()

        self.app.task(lambda: self.api.exam(eid), success,
                      error=lambda exc: mb.showerror("加载失败", str(exc),
                                                     parent=self.top),
                      controls=self.controls, status=self.tip,
                      busy_text="正在加载考试配置…")

    def _populate(self, result):
        exam = result.get("exam") or {}
        self.exam = exam
        self.title_label.configure(
            text=f"{exam.get('title', '')} · {exam.get('code') or exam.get('id')}")
        self.name_var.set(exam.get("title") or "")
        self.duration_var.set(str(exam.get("duration") or 60))
        self.klass_var.set(exam.get("klass") or "")
        office_types = as_list(exam.get("etype"))
        if not office_types:
            office_types = [exam.get("etype")] if exam.get("etype") else []
        self.office_types = [key for key in ("word", "excel", "ppt")
                             if key in office_types] or ["word"]
        self.require_var.set(bool(exam.get("require_complete")))
        self.passwordless_var.set(bool(exam.get("passwordless")))
        selected = set(as_list(exam.get("question_types")))
        if not selected:
            selected = {key for key, _label in EXAM_TYPES if key != "basic"}
        for key, variable in self.type_vars.items():
            variable.set(key in selected)
        self.requirement_text.insert("1.0", exam.get("requirement") or "")
        self.finish_text.insert("1.0", exam.get("finish_note") or "")
        self.selected_qids = [int(qid) for qid in result.get("question_ids") or []]
        self.selected_pids = [int(pid) for pid in result.get("practice_ids") or []]
        self.roster_text.insert("1.0", result.get("roster") or "")
        self.materials = [str(name) for name in as_list(exam.get("materials"))]
        self.checks_editor.set_rules(as_list(exam.get("checks")))
        self.typing_editor.set_config(result.get("typing_config") or {})
        self.drawing_editor.set_config(result.get("drawing_config") or {})
        self.controls.extend(self.checks_editor.controls)
        self._render_all_questions()
        self._render_practice()
        self._render_materials()
        self._update_roster_count()
        self._sync_type_tabs()
        self._sync_practice_types()
        self.locked = exam.get("status") in ("running", "paused")
        if self.locked:
            for widget in self.controls:
                if widget is not self.btn_cancel:
                    set_widget_state(widget, "disabled")
            self.tip.configure(text="考试进行中，配置已锁定", foreground=ORANGE)
            self.footer_tip.configure(text="请先结束考试再修改配置")
        else:
            self.tip.configure(text="配置已加载", foreground=GREEN)
        self._mark_clean()

    def _questions(self):
        return list(self.main.data.get("questions") or [])

    def _practices(self):
        return list(self.main.data.get("practice") or [])

    def _questions_of_type(self, key):
        return [question for question in self._questions()
                if question.get("qtype") == key]

    def _question_categories(self, key):
        return sorted({str(question.get("category") or "").strip()
                       for question in self._questions_of_type(key)
                       if str(question.get("category") or "").strip()})

    def _render_all_questions(self):
        for key in self.q_pages:
            self._render_question_page(key)

    def _render_question_page(self, key):
        page = self.q_pages[key]
        questions = self._questions_of_type(key)
        categories = self._question_categories(key)
        page["category_combo"].configure(
            values=("全部分类",) + tuple(categories))
        if page["category"].get() not in ("全部分类",) + tuple(categories):
            page["category"].set("全部分类")
        keyword = page["filter"].get().strip().lower()
        category = page["category"].get()
        if category == "全部分类":
            category = ""
        mode = page["sort"].get()
        selected_pos = {qid: index for index, qid in enumerate(self.selected_qids)}
        rows = []
        for question in questions:
            haystack = " ".join(str(question.get(field) or "") for field in
                                ("stem", "category", "source")).lower()
            if keyword and keyword not in haystack:
                continue
            if category and str(question.get("category") or "").strip() != category:
                continue
            rows.append(question)
        if mode == "ID 升序":
            rows.sort(key=lambda question: int(question["id"]))
        elif mode == "分类名称":
            rows.sort(key=lambda question: (str(question.get("category") or ""),
                                            int(question["id"])))
        elif mode == "分值降序":
            rows.sort(key=lambda question: (-float(question.get("score") or 0),
                                            int(question["id"])))
        elif mode == "分值升序":
            rows.sort(key=lambda question: (float(question.get("score") or 0),
                                            int(question["id"])))
        else:
            rows.sort(key=lambda question: (
                0, selected_pos[int(question["id"])])
                if int(question["id"]) in selected_pos else
                (1, int(question["id"])))
        tree = page["tree"]
        tree.delete(*tree.get_children())
        for index, question in enumerate(rows):
            qid = int(question["id"])
            chosen = qid in selected_pos
            tags = ("selected",) if chosen else (("odd",) if index % 2 else ())
            tree.insert("", "end", iid=str(qid), values=(
                "✓" if chosen else "○",
                selected_pos[qid] + 1 if chosen else "—", qid,
                question.get("category") or "未分类",
                question.get("score") or 0, question.get("stem") or ""), tags=tags)
        question_ids = {int(question["id"]) for question in questions}
        type_selected = sum(1 for qid in self.selected_qids if qid in question_ids)
        page["count"].configure(
            text=f"显示 {len(rows)} 道 · 本题型已选 {type_selected} 道 · "
                 f"全卷已选 {len(self.selected_qids)} 道")

    def _toggle_question(self, event, key):
        row = self.q_pages[key]["tree"].identify_row(event.y)
        if row:
            self._toggle_question_id(key, int(row))

    def _toggle_question_keyboard(self, event, key):
        tree = self.q_pages[key]["tree"]
        row = tree.focus() or (tree.selection()[0] if tree.selection() else "")
        if row:
            self._toggle_question_id(key, int(row))
        return "break"

    def _toggle_question_id(self, key, qid):
        if self.locked:
            mb.showinfo("配置已锁定", "考试进行中不能修改选题", parent=self.top)
            return
        if qid in self.selected_qids:
            self.selected_qids.remove(qid)
        else:
            self.selected_qids.append(qid)
        self._render_question_page(key)
        self._note_dirty()

    def _select_visible(self, key, choose):
        if self.locked:
            return
        visible_ids = [int(row) for row in self.q_pages[key]["tree"].get_children()]
        if choose:
            for qid in visible_ids:
                if qid not in self.selected_qids:
                    self.selected_qids.append(qid)
        else:
            self.selected_qids = [qid for qid in self.selected_qids
                                  if qid not in set(visible_ids)]
        self._render_question_page(key)
        self._note_dirty()

    def _random_questions(self, key):
        if self.locked:
            return
        page = self.q_pages[key]
        try:
            count = max(1, min(int(float(page["random"].get())), 200))
        except (TypeError, ValueError):
            count = 10
        category = page["category"].get()
        if category == "全部分类":
            category = ""
        candidates = [question for question in self._questions_of_type(key)
                      if int(question["id"]) not in self.selected_qids
                      and (not category or
                           str(question.get("category") or "").strip() == category)]
        picked = random.sample(candidates, min(count, len(candidates)))
        if not picked:
            mb.showinfo("随机抽题", "没有符合分类条件的未选题目。", parent=self.top)
            return
        self.selected_qids.extend(int(question["id"]) for question in picked)
        self._note_dirty()
        page["sort"].set("选题顺序")
        self._render_question_page(key)
        self.footer_tip.configure(
            text=f"已随机抽取 {len(picked)} 道{QTYPE_NAMES[key]}，请点击「保存选题」",
            foreground=GREEN)

    def _sort_selected_questions(self, key):
        if self.locked:
            return
        page = self.q_pages[key]
        mode = page["sort"].get()
        if mode == "选题顺序":
            self.footer_tip.configure(text="当前已是选题顺序", foreground=GRAY)
            return
        questions = {int(question["id"]): question
                     for question in self._questions_of_type(key)}
        ordered = [qid for qid in self.selected_qids if qid in questions]
        if mode == "ID 升序":
            ordered.sort()
        elif mode == "分类名称":
            ordered.sort(key=lambda qid: (str(questions[qid].get("category") or ""),
                                          qid))
        elif mode == "分值降序":
            ordered.sort(key=lambda qid: (-float(questions[qid].get("score") or 0), qid))
        else:
            ordered.sort(key=lambda qid: (float(questions[qid].get("score") or 0), qid))
        positions = [index for index, qid in enumerate(self.selected_qids)
                     if qid in questions]
        for index, qid in zip(positions, ordered):
            self.selected_qids[index] = qid
        page["sort"].set("选题顺序")
        self._render_question_page(key)
        self._note_dirty()
        self.footer_tip.configure(text="已按当前规则重排本题型", foreground=GREEN)

    def _move_selected_question(self, key, delta):
        if self.locked:
            return
        page = self.q_pages[key]
        selected = page["tree"].selection()
        if not selected:
            mb.showinfo("调整顺序", "请先选择一道已选题目。", parent=self.top)
            return
        qid = int(selected[0])
        if qid not in self.selected_qids:
            mb.showinfo("调整顺序", "请先勾选要调整顺序的题目。", parent=self.top)
            return
        questions = {int(question["id"]): question
                     for question in self._questions_of_type(key)}
        same_type = [item for item in self.selected_qids if item in questions]
        index = same_type.index(qid)
        target = index + delta
        if target < 0 or target >= len(same_type):
            return
        same_type[index], same_type[target] = same_type[target], same_type[index]
        positions = [pos for pos, item in enumerate(self.selected_qids)
                     if item in questions]
        for pos, item in zip(positions, same_type):
            self.selected_qids[pos] = item
        page["sort"].set("选题顺序")
        self._render_question_page(key)
        page["tree"].selection_set(str(qid))
        self._note_dirty()

    @staticmethod
    def _practice_level_rank(value):
        return {"入门": 0, "基础": 1, "进阶": 2, "挑战": 3}.get(value or "", 9)

    def _render_practice(self):
        keyword = self.p_filter_var.get().strip().lower()
        selected_label = self.p_type_var.get()
        if selected_label == "全部类型":
            allowed_types = {"word", "excel", "ppt"}
        else:
            key = next((key for key, label in OFFICE_NAMES.items()
                        if label == selected_label), "")
            allowed_types = {key} if key else set()
        mode = self.p_sort_var.get()
        selected_pos = {pid: index for index, pid in enumerate(self.selected_pids)}
        rows = []
        for task in self._practices():
            if task.get("etype") not in allowed_types:
                continue
            haystack = " ".join(str(task.get(field) or "") for field in
                                ("name", "category", "requirement")).lower()
            if keyword and keyword not in haystack:
                continue
            rows.append(task)
        if mode == "难度 难→简单":
            rows.sort(key=lambda task: (-self._practice_level_rank(task.get("level")),
                                        str(task.get("name") or "")))
        elif mode == "题目名称":
            rows.sort(key=lambda task: str(task.get("name") or ""))
        elif mode == "ID 升序":
            rows.sort(key=lambda task: int(task["id"]))
        else:
            rows.sort(key=lambda task: (self._practice_level_rank(task.get("level")),
                                        str(task.get("name") or "")))
        self.p_tree.delete(*self.p_tree.get_children())
        for index, task in enumerate(rows):
            pid = int(task["id"])
            chosen = pid in selected_pos
            tags = ("selected",) if chosen else (("odd",) if index % 2 else ())
            self.p_tree.insert("", "end", iid=str(pid), values=(
                "✓" if chosen else "○",
                selected_pos[pid] + 1 if chosen else "—", pid,
                OFFICE_NAMES.get(task.get("etype"), task.get("etype")),
                task.get("level") or "—", task.get("category") or "未分类",
                task.get("name") or ""), tags=tags)
        self.p_count.configure(
            text=f"显示 {len(rows)} 道 · 已选 {len(self.selected_pids)} 道 · "
                 "上移 / 下移可调整学生端作答顺序")

    def _toggle_practice(self, event):
        row = self.p_tree.identify_row(event.y)
        if row:
            self._toggle_practice_id(int(row))

    def _toggle_practice_keyboard(self, event):
        row = self.p_tree.focus() or (
            self.p_tree.selection()[0] if self.p_tree.selection() else "")
        if row:
            self._toggle_practice_id(int(row))
        return "break"

    def _toggle_practice_id(self, pid):
        if self.locked:
            mb.showinfo("配置已锁定", "考试进行中不能修改选题", parent=self.top)
            return
        if pid in self.selected_pids:
            self.selected_pids.remove(pid)
        else:
            self.selected_pids.append(pid)
        self._render_practice()
        self._note_dirty()

    def _select_visible_practice(self, choose):
        if self.locked:
            return
        visible_ids = [int(row) for row in self.p_tree.get_children()]
        if choose:
            for pid in visible_ids:
                if pid not in self.selected_pids:
                    self.selected_pids.append(pid)
        else:
            self.selected_pids = [pid for pid in self.selected_pids
                                  if pid not in set(visible_ids)]
        self._render_practice()
        self._note_dirty()

    def _move_selected_practice(self, delta):
        if self.locked:
            return
        selected = self.p_tree.selection()
        if not selected:
            mb.showinfo("调整顺序", "请先选择一道已选实操题。", parent=self.top)
            return
        pid = int(selected[0])
        if pid not in self.selected_pids:
            mb.showinfo("调整顺序", "请先勾选要调整顺序的实操题。", parent=self.top)
            return
        index = self.selected_pids.index(pid)
        target = index + delta
        if target < 0 or target >= len(self.selected_pids):
            return
        self.selected_pids[index], self.selected_pids[target] = \
            self.selected_pids[target], self.selected_pids[index]
        self._render_practice()
        self.p_tree.selection_set(str(pid))
        self._note_dirty()
        self.footer_tip.configure(text="实操题顺序已调整", foreground=GREEN)

    def _apply_questions(self):
        if self.locked or self.app.busy:
            return
        if self.eid is None:
            mb.showinfo("保存选题", "首次保存会同时保存考试配置，完成后可继续选题。",
                        parent=self.top)
            self._save(close_after=False)
            return
        eid = self.eid
        ids = list(self.selected_qids)

        def success(result):
            if result.get("ok"):
                self._saved_qids = ids
                self.footer_tip.configure(
                    text="理论选题已保存" if not self._has_unsaved_changes()
                    else "理论选题已保存 · 其他设置仍有未保存修改",
                    foreground=GREEN if not self._has_unsaved_changes() else ORANGE)
            else:
                self.main.show_failure(result, "保存选题失败")

        self.main.task(lambda: self.api.set_exam_questions(eid, ids), success,
                       controls=self.controls,
                       busy_text="正在保存理论选题…")

    def _apply_practice(self):
        if self.locked or self.app.busy:
            return
        if self.eid is None:
            mb.showinfo("保存选题", "首次保存会同时保存考试配置，完成后可继续选题。",
                        parent=self.top)
            self._save(close_after=False)
            return
        eid = self.eid
        ids = list(self.selected_pids)

        def success(result):
            if result.get("ok"):
                self._saved_pids = ids
                self.footer_tip.configure(
                    text="实操选题已保存" if not self._has_unsaved_changes()
                    else "实操选题已保存 · 其他设置仍有未保存修改",
                    foreground=GREEN if not self._has_unsaved_changes() else ORANGE)
            else:
                self.main.show_failure(result, "保存选题失败")

        self.main.task(lambda: self.api.set_exam_practice(eid, ids), success,
                       controls=self.controls,
                       busy_text="正在保存实操选题…")

    def _import_student_ips(self):
        if self.locked:
            return
        existing = {line.strip() for line in
                    self.roster_text.get("1.0", "end").splitlines() if line.strip()}
        added = 0
        for student in self.main.data.get("students") or []:
            ip = str(student.get("ip") or "").strip()
            if not ip:
                continue
            name = str(student.get("name") or "IP学生").strip()
            seat = str(student.get("seat") or "").strip()
            line = ",".join(part for part in (name, seat, ip) if part)
            if line not in existing:
                self.roster_text.insert("end", ("\n" if existing else "") + line)
                existing.add(line)
                added += 1
        self._update_roster_count()
        self._note_dirty()
        self.footer_tip.configure(
            text=f"已从在线学生导入 {added} 个 IP，保存考试后生效",
            foreground=GREEN if added else GRAY)

    def _update_roster_count(self):
        rows = [line for line in self.roster_text.get("1.0", "end").splitlines()
                if line.strip()]
        self.roster_count.configure(
            text=f"当前文本约 {len(rows)} 行 · 保存后由服务端去重并规范化")

    def _save_roster(self, mode):
        if self.locked:
            return
        if self.eid is None:
            mb.showinfo("提示", "请先保存考试，再单独保存名单。", parent=self.top)
            return
        text = self.roster_text.get("1.0", "end")
        eid = self.eid

        def success(result):
            if result.get("ok"):
                self.roster_text.delete("1.0", "end")
                self.roster_text.insert("1.0", result.get("roster") or "")
                self._update_roster_count()
                self.footer_tip.configure(
                    text=f"名单已保存（{result.get('count', 0)} 人）",
                    foreground=GREEN)
            else:
                self.main.show_failure(result, "保存名单失败")

        self.main.task(lambda: self.api.set_roster(eid, text, mode), success,
                       controls=self.controls, busy_text="正在保存学生名单…")

    def _clear_roster(self):
        if self.locked:
            return
        if not mb.askyesno("清空名单", "确定清空本场学生名单吗？",
                           parent=self.top):
            return
        self.roster_text.delete("1.0", "end")
        self._update_roster_count()
        self._save_roster("replace")

    def _render_materials(self):
        self.material_tree.delete(*self.material_tree.get_children())
        for name in self.materials:
            self.material_tree.insert("", "end", iid="server:" + name,
                                      values=(name, "已上传"))
        for path in self.pending_uploads:
            name = os.path.basename(path)
            self.material_tree.insert("", "end", iid="pending:" + name,
                                      values=(name, "待上传"))
        self.available_tree.delete(*self.available_tree.get_children())
        for name in sorted(self.main.data.get("materials") or []):
            self.available_tree.insert("", "end", iid=str(name), values=(name,))

    def _upload(self):
        if self.locked:
            return
        path = filedialog.askopenfilename(
            parent=self.top, title="选择考试素材",
            filetypes=[("支持的素材", "*.docx *.xlsx *.pptx *.pdf *.txt *.csv "
                        "*.png *.jpg *.jpeg"), ("所有文件", "*.*")])
        if not path:
            return
        name = os.path.basename(path)
        if name in self.materials or any(os.path.basename(p) == name
                                         for p in self.pending_uploads):
            mb.showinfo("提示", "该素材已经在当前考试列表中。", parent=self.top)
            return
        if self.eid is None:
            self.pending_uploads.append(path)
            self._render_materials()
            self.footer_tip.configure(text="素材将在保存考试时上传", foreground=GRAY)
            return
        eid = self.eid

        def success(result):
            if result.get("ok"):
                self.materials = list(result.get("materials") or self.materials)
                self._render_materials()
                self.footer_tip.configure(text="素材上传完成", foreground=GREEN)
            else:
                self.main.show_failure(result, "素材上传失败")

        self.main.task(lambda: self.api.upload_material(eid, path), success,
                       controls=self.controls, busy_text="正在上传素材…")

    def _delete_material(self):
        if self.locked:
            return
        selected = self.material_tree.selection()
        if not selected:
            mb.showinfo("提示", "请先选择要删除的素材。", parent=self.top)
            return
        row = selected[0]
        if row.startswith("pending:"):
            name = row[len("pending:"):]
            self.pending_uploads = [path for path in self.pending_uploads
                                    if os.path.basename(path) != name]
            self._render_materials()
            return
        name = row[len("server:"):]
        if not mb.askyesno("删除素材", f"从当前考试移除「{name}」吗？",
                           parent=self.top):
            return
        if self.eid is None:
            self.materials = [item for item in self.materials if item != name]
            self._render_materials()
            return
        eid = self.eid

        def success(result):
            if result.get("ok"):
                self.materials = list(result.get("materials") or [])
                self._render_materials()
                self.footer_tip.configure(text="素材已移除", foreground=GREEN)
            else:
                self.main.show_failure(result, "删除素材失败")

        self.main.task(lambda: self.api.delete_material(eid, name), success,
                       controls=self.controls, busy_text="正在删除素材…")

    def _add_available(self):
        if self.locked:
            return
        selected = self.available_tree.selection()
        if not selected:
            mb.showinfo("提示", "请先在已有素材列表中选择文件。", parent=self.top)
            return
        for name in selected:
            if name not in self.materials:
                self.materials.append(name)
        self._render_materials()
        self.footer_tip.configure(text="已加入列表，点击「保存考试」后生效",
                                  foreground=GRAY)

    def _payload(self):
        title = self.name_var.get().strip()
        if not title:
            raise ValueError("考试名称不能为空")
        duration = positive_int(self.duration_var.get(), "总时长")
        checks = self.checks_editor.get_rules()
        typing = self.typing_editor.get_config()
        drawing = self.drawing_editor.get_config()
        question_types = [key for key, _label in EXAM_TYPES
                          if self.type_vars[key].get()]
        if not question_types:
            raise ValueError("请至少选择一种考试题型")
        practice_types = []
        if "practice" in question_types:
            tasks = {int(task["id"]): task for task in self._practices()}
            for pid in self.selected_pids:
                etype = str((tasks.get(pid) or {}).get("etype") or "")
                if etype in ("word", "excel", "ppt") and etype not in practice_types:
                    practice_types.append(etype)
            if not practice_types:
                practice_types = list(self.office_types or ["word"])
        return {
            "title": title,
            "etype": practice_types,
            "duration": duration,
            "theory_duration": duration,
            "klass": self.klass_var.get().strip(),
            "save_as": "",
            "requirement": self.requirement_text.get("1.0", "end").strip(),
            "finish_note": self.finish_text.get("1.0", "end").strip(),
            "require_complete": bool(self.require_var.get()),
            "passwordless": bool(self.passwordless_var.get()),
            "question_types": question_types,
            "question_ids": list(self.selected_qids),
            "practice_ids": list(self.selected_pids),
            "roster": self.roster_text.get("1.0", "end"),
            "materials": list(self.materials),
            "checks": checks,
            "typing_config": typing,
            "drawing_config": drawing,
        }

    def _save(self, close_after=True):
        if self.locked or self.app.busy:
            return
        try:
            payload = self._payload()
        except ValueError as exc:
            mb.showwarning("配置有误", str(exc), parent=self.top)
            return
        pending = list(self.pending_uploads)
        eid = self.eid

        def work():
            if eid is None:
                result = self.api.create_exam(payload)
                if not result.get("ok"):
                    return result
                created_id = int(result["exam"]["id"])
                uploaded = 0
                uploaded_materials = list(payload.get("materials") or [])
                for path in pending:
                    result = self.api.upload_material(created_id, path)
                    if not result.get("ok"):
                        return {"ok": False, "eid": created_id,
                                "uploaded": uploaded,
                                "materials": uploaded_materials,
                                "msg": f"考试已创建，但素材「{os.path.basename(path)}」"
                                       f"上传失败：{result.get('msg', '')}"}
                    uploaded += 1
                    uploaded_materials = list(result.get("materials") or
                                              uploaded_materials)
                result = self.api.exam(created_id)
                if not result.get("ok"):
                    result["eid"] = created_id
                    result["uploaded"] = uploaded
                    result["materials"] = uploaded_materials
                return result
            return self.api.update_exam(eid, payload)

        def success(result):
            if result.get("ok"):
                if close_after:
                    mb.showinfo("保存完成", "考试配置已保存。", parent=self.top)
                    self.top.destroy()
                    self.main.refresh_all()
                    return
                self.eid = int(result["exam"]["id"])
                self.top.title("编辑考试")
                self.pending_uploads = []
                self._mark_clean()
                self.footer_tip.configure(
                    text="考试与选题已保存，可继续选择题型", foreground=GREEN)
                mb.showinfo("保存完成", "考试与选题已保存，可以继续选题。",
                            parent=self.top)
                self.main.refresh_all()
                return
            if result.get("eid"):
                self.eid = int(result["eid"])
                self.top.title("编辑考试")
                self.pending_uploads = self.pending_uploads[
                    int(result.get("uploaded", 0)):]
                self.materials = list(result.get("materials") or self.materials)
                self._render_materials()
                self.footer_tip.configure(text=result.get("msg", "素材上传失败"),
                                          foreground=RED)
                mb.showerror("部分保存失败", result.get("msg", "素材上传失败"),
                             parent=self.top)
                return
            self.main.show_failure(result, "保存考试失败")

        self.main.task(work, success, controls=self.controls,
                       busy_text="正在保存考试配置…")

    def _close(self):
        if self.app.busy:
            mb.showinfo("请稍候", "当前操作尚未完成。", parent=self.top)
            return
        if self._has_unsaved_changes():
            if not mb.askyesno(
                    "尚未保存", "当前考试配置有未保存修改，确定放弃并关闭吗？",
                    parent=self.top):
                return
        self.top.destroy()


class QuestionBank(PageBase):
    def __init__(self, main):
        super().__init__(main, "理论题库")
        self.questions = []
        self._build()
        self.tree.bind("<Double-1>", lambda event: self._edit())

    def _build(self):
        self.rowconfigure(2, weight=1)
        bar = ttk.Frame(self)
        bar.grid(row=0, column=0, sticky="ew", pady=(2, 5))
        self.filter_var = tk.StringVar()
        ttk.Entry(bar, textvariable=self.filter_var, width=24).pack(side="left")
        self.type_var = tk.StringVar(value="全部题型")
        ttk.Combobox(bar, textvariable=self.type_var, width=10,
                     state="readonly", values=("全部题型",) + tuple(
                         label for _key, label in QTYPES)).pack(
                             side="left", padx=5)
        self.category_var = tk.StringVar()
        ttk.Entry(bar, textvariable=self.category_var, width=15).pack(side="left")
        self.filter_var.trace_add("write", lambda *args: self._render())
        self.type_var.trace_add("write", lambda *args: self._render())
        self.category_var.trace_add("write", lambda *args: self._render())
        self.btn_new = self.add_action(make_button(
            bar, "新增题目", lambda: QuestionEditor(self, None), accent=True))
        self.btn_new.pack(side="right")
        self.btn_tidy = self.add_action(make_button(bar, "整理分类", self._tidy))
        self.btn_tidy.pack(side="right", padx=4)
        self.btn_import = self.add_action(make_button(bar, "JSON 导入", self._import))
        self.btn_import.pack(side="right", padx=4)
        self.btn_refresh = self.add_action(make_button(
            bar, "刷新", self.main.refresh_all))
        self.btn_refresh.pack(side="right", padx=4)
        self.summary = ttk.Label(self, text="", style="Muted.TLabel")
        self.summary.grid(row=1, column=0, sticky="w", pady=(0, 4))
        holder, self.tree = make_tree(
            self, ("id", "type", "category", "difficulty", "source", "score", "stem"),
            ("ID", "题型", "分类", "难度", "来源", "分值", "题干"),
            (42, 74, 105, 60, 110, 48, 335), stretch="stem", height=13)
        holder.grid(row=2, column=0, sticky="nsew")
        bottom = ttk.Frame(self)
        bottom.grid(row=3, column=0, sticky="ew", pady=(6, 0))
        self.btn_edit = self.add_action(make_button(bottom, "编辑所选", self._edit))
        self.btn_edit.pack(side="left")
        self.btn_delete = self.add_action(make_button(bottom, "删除所选", self._delete))
        self.btn_delete.pack(side="left", padx=5)
        ttk.Label(bottom, text="双击列表中的题目可直接编辑。",
                  style="Muted.TLabel").pack(side="right")

    def set_data(self, data):
        self.questions = list(data.get("questions") or [])
        self._render()

    def _render(self):
        selected = self.tree.selection()
        selected_id = selected[0] if selected else None
        self.tree.delete(*self.tree.get_children())
        keyword = self.filter_var.get().strip().lower()
        selected_type = self.type_var.get()
        type_key = next((key for key, label in QTYPES if label == selected_type),
                        "")
        category = self.category_var.get().strip().lower()
        shown = 0
        for question in self.questions:
            if keyword and keyword not in str(question.get("stem") or "").lower():
                continue
            if type_key and question.get("qtype") != type_key:
                continue
            if category and category not in str(question.get("category") or "").lower():
                continue
            qid = str(question.get("id"))
            self.tree.insert("", "end", iid=qid, values=(
                qid, QTYPE_NAMES.get(question.get("qtype"), question.get("qtype")),
                question.get("category") or "未分类",
                question.get("difficulty") or "未整理",
                question.get("source") or "未整理",
                question.get("score") or 0, question.get("stem") or ""))
            shown += 1
        if selected_id and self.tree.exists(selected_id):
            self.tree.selection_set(selected_id)
        self.summary.configure(text=f"共 {len(self.questions)} 道 · 当前显示 {shown} 道")

    def _selected(self):
        selected = self.tree.selection()
        if not selected:
            mb.showinfo("提示", "请先选择一道题", parent=self.main.root)
            return None
        qid = int(selected[0])
        return next((q for q in self.questions if q.get("id") == qid), None)

    def _edit(self):
        question = self._selected()
        if question and not self.app.busy:
            QuestionEditor(self, question)

    def _delete(self):
        question = self._selected()
        if not question or self.app.busy:
            return
        if not mb.askyesno("删除题目", "确定删除所选理论题吗？"
                           "已组入考试的关联也会一并移除。", parent=self.main.root):
            return
        qid = question["id"]

        def success(result):
            if result.get("ok"):
                self.main.after_change("理论题已删除")
            else:
                self.main.show_failure(result, "删除失败")

        self.main.task(lambda: self.api.delete_question(qid), success,
                       controls=self.action_controls(), busy_text="正在删除题目…")

    def _import(self):
        if self.app.busy:
            return
        path = filedialog.askopenfilename(
            parent=self.main.root, title="选择理论题 JSON 文件",
            filetypes=[("JSON 文件", "*.json"), ("所有文件", "*.*")])
        if not path:
            return

        def work():
            try:
                with open(path, "r", encoding="utf-8-sig") as fileobj:
                    payload = json.load(fileobj)
            except Exception as exc:
                return {"ok": False, "msg": f"读取 JSON 失败：{exc}"}
            if isinstance(payload, dict):
                payload = payload.get("items", payload.get("questions"))
            if not isinstance(payload, list):
                return {"ok": False,
                        "msg": "JSON 顶层必须是题目数组，或包含 items/questions 数组"}
            return self.api.import_questions(payload)

        def success(result):
            if result.get("ok"):
                mb.showinfo("导入完成",
                            f"新增 {result.get('added', 0)} 题，"
                            f"跳过 {result.get('skipped', 0)} 题。",
                            parent=self.main.root)
                self.main.refresh_all()
            else:
                self.main.show_failure(result, "导入失败")

        self.main.task(work, success, controls=self.action_controls(),
                       busy_text="正在导入理论题…")

    def _tidy(self):
        if self.app.busy:
            return
        if not mb.askyesno("整理分类", "将空白难度和来源按规则自动补齐，"
                           "已有分类不会被修改。继续吗？", parent=self.main.root):
            return

        def success(result):
            if result.get("ok"):
                mb.showinfo("整理完成",
                            f"补充难度 {result.get('difficulty', 0)} 条，"
                            f"补充来源 {result.get('source', 0)} 条。",
                            parent=self.main.root)
                self.main.refresh_all()
            else:
                self.main.show_failure(result, "整理失败")

        self.main.task(self.api.tidy_questions, success,
                       controls=self.action_controls(),
                       busy_text="正在整理题库分类…")


class QuestionEditor:
    def __init__(self, page, question=None):
        self.page = page
        self.main = page.main
        self.app = page.app
        self.api = page.api
        self.question = question
        self.qid = question.get("id") if question else None
        self.controls = []
        self.top = tk.Toplevel(self.main.root)
        self.top.title("编辑理论题" if question else "新增理论题")
        self.top.transient(self.main.root)
        self.top.geometry("920x760")
        self.top.minsize(840, 680)
        self.top.configure(bg=BG)
        icon = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.ico")
        if os.path.isfile(icon):
            try:
                self.top.iconbitmap(icon)
            except tk.TclError:
                pass
        self.top.protocol("WM_DELETE_WINDOW", self._close)
        self._build()
        if question:
            self._populate(question)

    def _text(self, parent, height):
        widget = scrolledtext.ScrolledText(parent, height=height, width=44,
                                           wrap="word", font=(FONT, 9), background=CARD,
                                           foreground=INK, relief="solid",
                                           borderwidth=1)
        self.controls.append(widget)
        ui_theme.bind_mousewheel(widget, widget)
        return widget

    def _entry(self, parent, **kwargs):
        widget = ttk.Entry(parent, **kwargs)
        self.controls.append(widget)
        return widget

    def _build(self):
        outer = ttk.Frame(self.top, padding=12)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(0, weight=1)
        body = ScrollableFrame(outer)
        body.grid(row=0, column=0, sticky="nsew")
        form = body.inner
        form.columnconfigure(0, weight=1)

        row = ttk.Frame(form)
        row.grid(row=0, column=0, sticky="ew")
        ttk.Label(row, text="题型").pack(side="left")
        self.type_var = tk.StringVar(value="single")
        self.type_box = ttk.Combobox(row, textvariable=self.type_var, width=10,
                                     state="readonly", values=tuple(
                                         label for _key, label in QTYPES))
        self.type_box.pack(side="left", padx=5)
        self.controls.append(self.type_box)
        self.type_var.trace_add("write", lambda *args: self._update_hint())
        ttk.Label(row, text="分值").pack(side="left", padx=(18, 5))
        self.score_var = tk.StringVar(value="2")
        self._entry(row, textvariable=self.score_var, width=8).pack(side="left")
        ttk.Label(row, text="难度").pack(side="left", padx=(18, 5))
        self.difficulty_var = tk.StringVar(value="基础")
        self.difficulty_box = ttk.Combobox(row, textvariable=self.difficulty_var,
                                           width=8, state="readonly",
                                           values=("基础", "进阶", "挑战", ""))
        self.difficulty_box.pack(side="left")
        self.controls.append(self.difficulty_box)

        ttk.Label(form, text="题干").grid(row=1, column=0, sticky="w",
                                          pady=(8, 2))
        self.stem_text = self._text(form, 3)
        self.stem_text.grid(row=2, column=0, sticky="nsew")

        pair = ttk.Frame(form)
        pair.grid(row=3, column=0, sticky="nsew", pady=8)
        pair.columnconfigure(0, weight=1)
        pair.columnconfigure(1, weight=1)
        self.options_label = ttk.Label(pair, text="选项（每行一个，可带 A. 前缀）")
        self.options_label.grid(row=0, column=0, sticky="w")
        self.answer_label = ttk.Label(pair, text="正确答案")
        self.answer_label.grid(row=0, column=1, sticky="w", padx=(8, 0))
        self.options_text = self._text(pair, 4)
        self.options_text.grid(row=1, column=0, sticky="nsew")
        self.answer_text = self._text(pair, 4)
        self.answer_text.grid(row=1, column=1, sticky="nsew", padx=(8, 0))
        self.hint = ttk.Label(form, text="", style="Muted.TLabel",
                              wraplength=850)
        self.hint.grid(row=4, column=0, sticky="w")

        meta = ttk.Frame(form)
        meta.grid(row=5, column=0, sticky="ew", pady=6)
        ttk.Label(meta, text="分类").pack(side="left")
        self.category_var = tk.StringVar()
        self._entry(meta, textvariable=self.category_var, width=20).pack(
            side="left", padx=5)
        ttk.Label(meta, text="来源").pack(side="left", padx=(12, 5))
        self.source_var = tk.StringVar(value="自创题")
        self._entry(meta, textvariable=self.source_var, width=20).pack(side="left")

        self.code_frame = ttk.LabelFrame(form, text="程序填空专用", padding=7)
        self.code_frame.grid(row=6, column=0, sticky="nsew", pady=4)
        self.code_frame.columnconfigure(0, weight=1)
        self.code_frame.columnconfigure(1, weight=1)
        ttk.Label(self.code_frame, text="语言").grid(row=0, column=0, sticky="w")
        self.language_var = tk.StringVar(value="python")
        self.language_box = ttk.Combobox(self.code_frame, textvariable=self.language_var,
                                         width=10, state="readonly",
                                         values=("python", "cpp"))
        self.language_box.grid(row=0, column=1, sticky="w", pady=(0, 4))
        self.controls.append(self.language_box)
        ttk.Label(self.code_frame, text="代码模板（用 ____ 标记空位）").grid(
            row=1, column=0, columnspan=2, sticky="w")
        self.code_text = self._text(self.code_frame, 4)
        self.code_text.grid(row=2, column=0, columnspan=2, sticky="nsew")
        ttk.Label(self.code_frame, text="运行输入（可选）").grid(
            row=3, column=0, sticky="w", pady=(5, 0))
        ttk.Label(self.code_frame, text="期望输出（可选）").grid(
            row=3, column=1, sticky="w", padx=(8, 0), pady=(5, 0))
        self.stdin_text = self._text(self.code_frame, 1)
        self.stdin_text.grid(row=4, column=0, sticky="nsew")
        self.expected_text = self._text(self.code_frame, 1)
        self.expected_text.grid(row=4, column=1, sticky="nsew", padx=(8, 0))

        self.drag_frame = ttk.LabelFrame(form, text="拖拽题专用", padding=7)
        self.drag_frame.grid(row=7, column=0, sticky="nsew", pady=4)
        self.drag_frame.columnconfigure(0, weight=1)
        ttk.Label(self.drag_frame, text="拖拽槽位（每行一个）").grid(
            row=0, column=0, sticky="w")
        self.slots_text = self._text(self.drag_frame, 4)
        self.slots_text.grid(row=1, column=0, sticky="nsew")

        footer = ttk.Frame(outer)
        footer.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        footer.columnconfigure(0, weight=1)
        self.tip = ttk.Label(footer, text="", style="Muted.TLabel")
        self.tip.grid(row=0, column=0, sticky="w")
        cancel = make_button(footer, "取消", self._close, width=12)
        self.controls.append(cancel)
        cancel.grid(row=0, column=1, padx=(8, 0))
        save = make_button(footer, "保存题目", self._save, accent=True, width=14)
        self.controls.append(save)
        save.grid(row=0, column=2)
        self.top.bind("<Control-s>", lambda event: self._save())
        body._bind_wheel()
        self._update_hint()

    def _type_key(self):
        return next((key for key, label in QTYPES
                     if label == self.type_var.get()), "single")

    def _update_hint(self):
        hints = {
            "single": "单选题答案填一个字母，如 A。",
            "multi": "多选题答案填多个字母，如 ABC。",
            "judge": "判断题答案填：正确 / 错误（或 T / F）。",
            "blank": "填空题答案可填写文字；多个空由服务端按题目规则判定。",
            "code": "程序填空：代码模板用 ____ 标空；各空答案用 || 分隔。",
            "drag": "拖拽题答案如 1:A|2:B；槽位数量必须与答案段数一致。",
        }
        key = self._type_key()
        self.hint.configure(text=hints.get(key, ""))
        show_options = key in ("single", "multi", "judge", "drag")
        if show_options:
            self.options_label.grid(row=0, column=0, sticky="w")
            self.options_text.grid(row=1, column=0, sticky="nsew")
            self.answer_label.grid(row=0, column=1, sticky="w", padx=(8, 0))
            self.answer_text.grid(row=1, column=1, sticky="nsew", padx=(8, 0))
        else:
            self.options_label.grid_remove()
            self.options_text.grid_remove()
            self.answer_label.grid(row=0, column=0, columnspan=2, sticky="w")
            self.answer_text.grid(row=1, column=0, columnspan=2, sticky="nsew")
        self.options_label.configure(
            text="拖拽项（每行一个）" if key == "drag" else
            "判断选项" if key == "judge" else "选项（每行一个，可带 A. 前缀）")
        if key == "code":
            self.code_frame.grid(row=6, column=0, sticky="nsew", pady=4)
        else:
            self.code_frame.grid_remove()
        if key == "drag":
            self.drag_frame.grid(row=7, column=0, sticky="nsew", pady=4)
        else:
            self.drag_frame.grid_remove()

    def _populate(self, question):
        self.type_var.set(QTYPE_NAMES.get(question.get("qtype"), "单选题"))
        self.score_var.set(str(question.get("score") or 2))
        self.difficulty_var.set(question.get("difficulty") or "")
        self.stem_text.insert("1.0", question.get("stem") or "")
        options = question.get("options") or []
        self.options_text.insert("1.0", "\n".join(str(item) for item in options))
        self.answer_text.insert("1.0", question.get("answer") or "")
        self.category_var.set(question.get("category") or "")
        self.source_var.set(question.get("source") or "")
        self.language_var.set(question.get("language") or "python")
        self.code_text.insert("1.0", question.get("code") or "")
        self.stdin_text.insert("1.0", question.get("stdin_data") or "")
        self.expected_text.insert("1.0", question.get("expected_output") or "")
        payload = json_object(question.get("payload"), {})
        slots = payload.get("slots") or question.get("slots") or []
        self.slots_text.insert("1.0", "\n".join(str(item) for item in slots))

    def _slots(self):
        text = self.slots_text.get("1.0", "end").strip()
        if not text:
            return []
        try:
            value = json.loads(text)
            if isinstance(value, list):
                return [str(item) for item in value]
        except Exception:
            pass
        return [line.strip() for line in text.splitlines() if line.strip()]

    def _payload(self):
        stem = self.stem_text.get("1.0", "end").strip()
        answer = self.answer_text.get("1.0", "end").strip()
        if not stem:
            raise ValueError("题干不能为空")
        if not answer:
            raise ValueError("答案不能为空")
        try:
            score = float(self.score_var.get().strip())
        except (TypeError, ValueError):
            raise ValueError("分值格式不对")
        if score <= 0:
            raise ValueError("分值必须大于 0")
        qtype = self._type_key()
        options = [line.strip() for line in
                   self.options_text.get("1.0", "end").splitlines()
                   if line.strip()] if qtype in ("single", "multi", "drag") else []
        if qtype in ("single", "multi") and len(options) < 2:
            raise ValueError("选择题至少需要两个选项")
        if qtype == "drag" and len(self._slots()) < 2:
            raise ValueError("拖拽题至少需要两个槽位")
        return {
            "qtype": qtype, "stem": stem, "options": options,
            "answer": answer, "score": score,
            "category": self.category_var.get().strip(),
            "difficulty": self.difficulty_var.get().strip(),
            "source": self.source_var.get().strip(),
            "language": self.language_var.get() if qtype == "code" else "",
            "code": self.code_text.get("1.0", "end").strip() if qtype == "code" else "",
            "stdin_data": self.stdin_text.get("1.0", "end").strip()
                          if qtype == "code" else "",
            "expected_output": self.expected_text.get("1.0", "end").strip()
                               if qtype == "code" else "",
            "slots": self._slots() if qtype == "drag" else [],
        }

    def _save(self):
        if self.app.busy:
            return
        try:
            payload = self._payload()
        except ValueError as exc:
            mb.showwarning("题目有误", str(exc), parent=self.top)
            return

        def work():
            if self.qid is None:
                return self.api.create_question(payload)
            return self.api.update_question(self.qid, payload)

        def success(result):
            if result.get("ok"):
                self.top.destroy()
                self.main.refresh_all()
            else:
                self.main.show_failure(result, "保存题目失败")

        self.main.task(work, success, controls=self.controls,
                       busy_text="正在保存理论题…")

    def _close(self):
        if self.app.busy:
            mb.showinfo("请稍候", "当前操作尚未完成。", parent=self.top)
            return
        self.top.destroy()


class PracticeBank(PageBase):
    def __init__(self, main):
        super().__init__(main, "实操题库")
        self.tasks = []
        self._build()
        self.tree.bind("<Double-1>", lambda event: self._edit())

    def _build(self):
        self.rowconfigure(2, weight=1)
        bar = ttk.Frame(self)
        bar.grid(row=0, column=0, sticky="ew", pady=(2, 5))
        self.filter_var = tk.StringVar()
        ttk.Entry(bar, textvariable=self.filter_var, width=22).pack(side="left")
        self.type_var = tk.StringVar(value="全部类型")
        ttk.Combobox(bar, textvariable=self.type_var, width=10,
                     state="readonly", values=("全部类型", "Word", "Excel",
                                               "PowerPoint")).pack(
                           side="left", padx=5)
        self.level_var = tk.StringVar()
        ttk.Entry(bar, textvariable=self.level_var, width=12).pack(side="left")
        self.filter_var.trace_add("write", lambda *args: self._render())
        self.type_var.trace_add("write", lambda *args: self._render())
        self.level_var.trace_add("write", lambda *args: self._render())
        self.btn_new = self.add_action(make_button(
            bar, "新增实操题", lambda: PracticeEditor(self, None), accent=True))
        self.btn_new.pack(side="right")
        self.btn_refresh = self.add_action(make_button(
            bar, "刷新", self.main.refresh_all))
        self.btn_refresh.pack(side="right", padx=4)
        self.summary = ttk.Label(self, text="", style="Muted.TLabel")
        self.summary.grid(row=1, column=0, sticky="w", pady=(0, 4))
        holder, self.tree = make_tree(
            self, ("id", "type", "category", "level", "name", "materials", "requirement"),
            ("ID", "类型", "分类", "难度", "题目名称", "素材", "考试要求"),
            (42, 75, 105, 65, 240, 65, 260), stretch="name", height=13)
        holder.grid(row=2, column=0, sticky="nsew")
        bottom = ttk.Frame(self)
        bottom.grid(row=3, column=0, sticky="ew", pady=(6, 0))
        self.btn_edit = self.add_action(make_button(bottom, "编辑所选", self._edit))
        self.btn_edit.pack(side="left")
        self.btn_delete = self.add_action(make_button(bottom, "删除所选", self._delete))
        self.btn_delete.pack(side="left", padx=5)
        ttk.Label(bottom, text="双击列表中的题目可直接编辑。",
                  style="Muted.TLabel").pack(side="right")

    def set_data(self, data):
        self.tasks = list(data.get("practice") or [])
        self._render()

    def _render(self):
        selected = self.tree.selection()
        selected_id = selected[0] if selected else None
        self.tree.delete(*self.tree.get_children())
        keyword = self.filter_var.get().strip().lower()
        level = self.level_var.get().strip().lower()
        selected_type = self.type_var.get()
        type_key = next((key for key, label in OFFICE_NAMES.items()
                         if label == selected_type), "")
        shown = 0
        for task in self.tasks:
            haystack = " ".join(str(task.get(key) or "") for key in
                                ("name", "category", "requirement")).lower()
            if keyword and keyword not in haystack:
                continue
            if type_key and task.get("etype") != type_key:
                continue
            if level and level not in str(task.get("level") or "").lower():
                continue
            pid = str(task.get("id"))
            self.tree.insert("", "end", iid=pid, values=(
                pid, OFFICE_NAMES.get(task.get("etype"), task.get("etype")),
                task.get("category") or "未分类", task.get("level") or "—",
                task.get("name") or "", len(task.get("materials") or []),
                task.get("requirement") or ""))
            shown += 1
        if selected_id and self.tree.exists(selected_id):
            self.tree.selection_set(selected_id)
        self.summary.configure(text=f"共 {len(self.tasks)} 道 · 当前显示 {shown} 道")

    def _selected(self):
        selected = self.tree.selection()
        if not selected:
            mb.showinfo("提示", "请先选择一道实操题", parent=self.main.root)
            return None
        pid = int(selected[0])
        return next((task for task in self.tasks if task.get("id") == pid), None)

    def _edit(self):
        task = self._selected()
        if task and not self.app.busy:
            PracticeEditor(self, task)

    def _delete(self):
        task = self._selected()
        if not task or self.app.busy:
            return
        if not mb.askyesno("删除实操题", f"确定删除「{task.get('name', '')}」吗？",
                           parent=self.main.root):
            return
        pid = task["id"]

        def success(result):
            if result.get("ok"):
                self.main.after_change("实操题已删除")
            else:
                self.main.show_failure(result, "删除失败")

        self.main.task(lambda: self.api.delete_practice(pid), success,
                       controls=self.action_controls(), busy_text="正在删除实操题…")


class PracticeEditor:
    def __init__(self, page, task=None):
        self.page = page
        self.main = page.main
        self.app = page.app
        self.api = page.api
        self.task = task
        self.pid = task.get("id") if task else None
        self.controls = []
        self.top = tk.Toplevel(self.main.root)
        self.top.title("编辑实操题" if task else "新增实操题")
        self.top.transient(self.main.root)
        self.top.geometry("900x680")
        self.top.minsize(760, 600)
        self.top.configure(bg=BG)
        self.top.protocol("WM_DELETE_WINDOW", self._close)
        self._build()
        if task:
            self._populate(task)

    def _entry(self, parent, **kwargs):
        widget = ttk.Entry(parent, **kwargs)
        self.controls.append(widget)
        return widget

    def _build(self):
        outer = ttk.Frame(self.top, padding=12)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(7, weight=1)
        row = ttk.Frame(outer)
        row.grid(row=0, column=0, sticky="ew")
        ttk.Label(row, text="题目名称").pack(side="left")
        self.name_var = tk.StringVar()
        self._entry(row, textvariable=self.name_var, width=32).pack(
            side="left", fill="x", expand=True, padx=5)
        ttk.Label(row, text="类型").pack(side="left", padx=(10, 5))
        self.type_var = tk.StringVar(value="word")
        self.type_box = ttk.Combobox(row, textvariable=self.type_var, width=10,
                                     state="readonly", values=("word", "excel", "ppt"))
        self.type_box.pack(side="left")
        self.controls.append(self.type_box)
        meta = ttk.Frame(outer)
        meta.grid(row=1, column=0, sticky="ew", pady=8)
        ttk.Label(meta, text="分类").pack(side="left")
        self.category_var = tk.StringVar()
        self._entry(meta, textvariable=self.category_var, width=20).pack(
            side="left", padx=5)
        ttk.Label(meta, text="难度").pack(side="left", padx=(12, 5))
        self.level_var = tk.StringVar(value="基础")
        self.level_box = ttk.Combobox(meta, textvariable=self.level_var, width=10,
                                      state="readonly", values=("基础", "进阶", "挑战", ""))
        self.level_box.pack(side="left")
        self.controls.append(self.level_box)
        ttk.Label(meta, text="保存名").pack(side="left", padx=(12, 5))
        self.save_var = tk.StringVar()
        self._entry(meta, textvariable=self.save_var, width=22).pack(side="left")

        ttk.Label(outer, text="考试要求").grid(row=2, column=0, sticky="w")
        self.requirement = scrolledtext.ScrolledText(
            outer, height=3, wrap="word", font=(FONT, 9), background=CARD,
            foreground=INK, relief="solid", borderwidth=1)
        self.requirement.grid(row=3, column=0, sticky="nsew", pady=(3, 6))
        self.controls.append(self.requirement)
        ttk.Label(outer, text="素材文件名（每行一个）").grid(
            row=4, column=0, sticky="w")
        self.materials = scrolledtext.ScrolledText(
            outer, height=2, wrap="word", font=(FONT, 9), background=CARD,
            foreground=INK, relief="solid", borderwidth=1)
        self.materials.grid(row=5, column=0, sticky="nsew", pady=(3, 6))
        self.controls.append(self.materials)
        ttk.Label(outer, text="评分规则").grid(row=6, column=0, sticky="w")
        self.checks_editor = RuleEditor(outer)
        self.checks_editor.grid(row=7, column=0, sticky="nsew", pady=(3, 4))
        self.controls.extend(self.checks_editor.controls)
        footer = ttk.Frame(outer)
        footer.grid(row=9, column=0, sticky="ew", pady=(8, 0))
        self.tip = ttk.Label(footer, text="", style="Muted.TLabel")
        self.tip.pack(side="left")
        cancel = ttk.Button(footer, text="取消", command=self._close)
        self.controls.append(cancel)
        cancel.pack(side="right")
        save = make_button(footer, "保存实操题", self._save, accent=True)
        self.controls.append(save)
        save.pack(side="right", padx=(0, 8))

    def _populate(self, task):
        self.name_var.set(task.get("name") or "")
        self.type_var.set(task.get("etype") or "word")
        self.category_var.set(task.get("category") or "")
        self.level_var.set(task.get("level") or "")
        self.save_var.set(task.get("save_as") or "")
        self.requirement.insert("1.0", task.get("requirement") or "")
        materials = task.get("materials") or []
        self.materials.insert("1.0", "\n".join(str(item) for item in materials))
        self.checks_editor.set_rules(task.get("checks") or [])
        self.controls.extend(self.checks_editor.controls)

    def _materials(self):
        text = self.materials.get("1.0", "end").strip()
        if not text:
            return []
        try:
            value = json.loads(text)
            if isinstance(value, list):
                return [str(item) for item in value]
            raise ValueError("素材必须是 JSON 数组")
        except json.JSONDecodeError:
            return [line.strip() for line in text.splitlines() if line.strip()]

    def _save(self):
        if self.app.busy:
            return
        name = self.name_var.get().strip()
        if not name:
            mb.showwarning("提示", "题目名称不能为空", parent=self.top)
            return
        checks = self.checks_editor.get_rules()
        try:
            materials = self._materials()
        except ValueError as exc:
            mb.showwarning("素材列表有误", str(exc), parent=self.top)
            return
        payload = {
            "name": name, "etype": self.type_var.get(),
            "category": self.category_var.get().strip(),
            "level": self.level_var.get().strip(),
            "materials": materials,
            "requirement": self.requirement.get("1.0", "end").strip(),
            "save_as": self.save_var.get().strip(), "checks": checks,
        }

        def work():
            if self.pid is None:
                return self.api.create_practice(payload)
            return self.api.update_practice(self.pid, payload)

        def success(result):
            if result.get("ok"):
                self.top.destroy()
                self.main.refresh_all()
            else:
                self.main.show_failure(result, "保存实操题失败")

        self.main.task(work, success, controls=self.controls,
                       busy_text="正在保存实操题…")

    def _close(self):
        if self.app.busy:
            mb.showinfo("请稍候", "当前操作尚未完成。", parent=self.top)
            return
        self.top.destroy()


class StudentManager(PageBase):
    def __init__(self, main):
        super().__init__(main, "学生机")
        self.students = []
        self.auto_var = tk.BooleanVar(value=True)
        self._build()
        self.tree.bind("<Double-1>", lambda event: self._details())
        self._schedule_auto_refresh()

    def _build(self):
        self.rowconfigure(2, weight=1)
        bar = ttk.Frame(self)
        bar.grid(row=0, column=0, sticky="ew", pady=(2, 5))
        self.filter_var = tk.StringVar()
        ttk.Entry(bar, textvariable=self.filter_var, width=24).pack(side="left")
        self.filter_var.trace_add("write", lambda *args: self._render())
        self.auto_check = self.add_action(ttk.Checkbutton(
            bar, text="每 2 秒自动刷新", variable=self.auto_var))
        self.auto_check.pack(side="left", padx=12)
        self.btn_reset = self.add_action(make_button(
            bar, "重置机器凭证", self._reset))
        self.btn_reset.pack(side="right")
        self.btn_refresh = self.add_action(make_button(
            bar, "刷新", self.main.refresh_all))
        self.btn_refresh.pack(side="right", padx=4)
        self.summary = ttk.Label(self, text="", style="Muted.TLabel")
        self.summary.grid(row=1, column=0, sticky="w", pady=(0, 4))
        holder, self.tree = make_tree(
            self, ("online", "hostname", "name", "seat", "ip",
                   "status", "progress", "exam", "token", "seen"),
            ("在线", "机器标识", "姓名", "座位", "IP", "状态",
             "当前进度", "考试ID", "机器凭证", "最后在线"),
            (44, 135, 65, 48, 95, 62, 190, 52, 65, 100),
            stretch="hostname", height=13)
        holder.grid(row=2, column=0, sticky="nsew")
        ttk.Label(self, text="学生机凭证由服务端保存哈希；重置后旧凭证立即失效，"
                  "学生机下次联网会重新登记。", style="Muted.TLabel",
                  wraplength=850).grid(row=3, column=0, sticky="w", pady=(6, 0))

    def set_data(self, data):
        self.students = list(data.get("students") or [])
        self._render()

    @staticmethod
    def _progress_text(student):
        progress = student.get("progress") or {}
        current = int(progress.get("current_question") or 0)
        total = int(progress.get("question_total") or 0)
        answered = int(progress.get("theory_answered") or 0)
        if current and total:
            return f"第 {current}/{total} 题 · 已答 {answered}/{total}"
        practice_index = int(progress.get("practice_index") or 0)
        practice_total = int(progress.get("practice_total") or 0)
        if practice_index and practice_total:
            return f"实操第 {practice_index}/{practice_total} 题"
        section = progress.get("section") or ""
        if section in ("typing", "drawing"):
            return "打字练习中" if section == "typing" else "画板创作中"
        return STUDENT_STATUS.get(student.get("status"),
                                   student.get("status") or "待考")

    def _render(self):
        selected = self.tree.selection()
        selected_id = selected[0] if selected else None
        self.tree.delete(*self.tree.get_children())
        keyword = self.filter_var.get().strip().lower()
        shown = 0
        for student in self.students:
            haystack = " ".join(str(student.get(key) or "") for key in
                                ("hostname", "name", "seat", "ip", "mac")).lower()
            if keyword and keyword not in haystack:
                continue
            hostname = student.get("hostname") or ""
            self.tree.insert("", "end", iid=hostname, values=(
                "在线" if student.get("online") else "离线", hostname,
                student.get("name") or "—", student.get("seat") or "—",
                student.get("ip") or "—",
                STUDENT_STATUS.get(student.get("status"), student.get("status") or "—"),
                self._progress_text(student), student.get("exam_id") or "—",
                "已配置" if student.get("has_token") else "未配置",
                format_time(student.get("last_seen"))))
            shown += 1
        if selected_id and self.tree.exists(selected_id):
            self.tree.selection_set(selected_id)
        online = sum(1 for student in self.students if student.get("online"))
        self.summary.configure(text=f"共 {len(self.students)} 台 · 在线 {online} 台 · "
                                   f"当前显示 {shown} 台")

    def _selected(self):
        selected = self.tree.selection()
        if not selected:
            mb.showinfo("提示", "请先选择一台学生机", parent=self.main.root)
            return None
        hostname = selected[0]
        return next((student for student in self.students
                     if student.get("hostname") == hostname), None)

    def _reset(self):
        student = self._selected()
        if not student or self.app.busy:
            return
        hostname = student.get("hostname") or ""
        if not mb.askyesno("重置机器凭证",
                           f"确定重置「{hostname}」的机器凭证吗？\n"
                           "重置后该机现有凭证会立即失效。",
                           parent=self.main.root):
            return

        def success(result):
            if result.get("ok"):
                self.main.after_change(f"已重置 {hostname} 的机器凭证")
            else:
                self.main.show_failure(result, "重置失败")

        self.main.task(lambda: self.api.reset_student_token(hostname), success,
                       controls=self.action_controls(),
                       busy_text="正在重置学生机凭证…")

    def _details(self):
        student = self._selected()
        if not student:
            return
        mb.showinfo(
            "学生机信息",
            f"机器标识：{student.get('hostname', '')}\n"
            f"姓名 / 座位：{student.get('name') or '—'} / {student.get('seat') or '—'}\n"
            f"IP / MAC：{student.get('ip') or '—'} / {student.get('mac') or '—'}\n"
            f"状态：{'在线' if student.get('online') else '离线'} · "
            f"{STUDENT_STATUS.get(student.get('status'), student.get('status') or '—')}\n"
            f"当前进度：{self._progress_text(student)}\n"
            f"最后在线：{format_time(student.get('last_seen'))}",
            parent=self.main.root)

    def _schedule_auto_refresh(self):
        def tick():
            try:
                if not self.winfo_exists():
                    return
                if (self.auto_var.get() and not self.app.busy and
                        self.main.notebook.index("current") ==
                        self.main.notebook.index(self)):
                    def success(result):
                        if result.get("ok") and self.winfo_exists():
                            self.students = list(result.get("students") or [])
                            self._render()

                    self.main.task(
                        lambda: self.api.dashboard_students(), success,
                        error=lambda exc: None, controls=(), busy_text="")
            except Exception:
                pass
            self.after(2000, tick)

        self.after(2000, tick)


class ScoresWindow:
    def __init__(self, main, exam):
        self.main = main
        self.app = main.app
        self.api = main.api
        self.exam = exam
        self.eid = exam["id"]
        self.controls = []
        self.top = tk.Toplevel(main.root)
        self.top.title(f"考试成绩 · {exam.get('title', '')}")
        self.top.transient(main.root)
        self.top.geometry("900x580")
        self.top.minsize(820, 540)
        self.top.configure(bg=BG)
        self.top.protocol("WM_DELETE_WINDOW", self._close)
        self._build()
        self._load()

    def _build(self):
        outer = ttk.Frame(self.top, padding=10)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(2, weight=1)
        title = ttk.Frame(outer)
        title.grid(row=0, column=0, sticky="ew")
        ttk.Label(title, text=self.exam.get("title") or "考试成绩",
                  font=(FONT, 14, "bold")).pack(side="left")
        self.tip = ttk.Label(title, text="正在加载成绩…", style="Muted.TLabel")
        self.tip.pack(side="right")
        self.summary = ttk.Label(outer, text="", style="Muted.TLabel")
        self.summary.grid(row=1, column=0, sticky="w", pady=6)
        holder, self.tree = make_tree(
            outer, ("seat", "name", "host", "ip", "theory", "practice",
                   "total", "submitted", "online", "filename"),
            ("座位", "姓名", "机器标识", "IP", "理论", "实操", "总分",
             "交卷", "在线", "答卷文件"),
            (50, 70, 125, 90, 62, 62, 62, 52, 48, 190),
            stretch="filename", height=13)
        holder.grid(row=2, column=0, sticky="nsew")
        bar = ttk.Frame(outer)
        bar.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        self.export_var = tk.StringVar(value="csv")
        ttk.Label(bar, text="导出格式").pack(side="left")
        self.export_box = ttk.Combobox(bar, textvariable=self.export_var,
                                       values=("json", "csv", "xlsx", "zip"),
                                       width=8, state="readonly")
        self.export_box.pack(side="left", padx=5)
        self.export_btn = make_button(bar, "导出文件", self._export)
        self.export_btn.pack(side="left")
        self.controls.extend((self.export_box, self.export_btn))
        refresh = make_button(bar, "刷新成绩", self._load)
        self.controls.append(refresh)
        refresh.pack(side="left", padx=5)
        close = make_button(bar, "关闭", self._close)
        close.pack(side="right")

    def _load(self):
        if self.app.busy:
            return

        def success(result):
            if result.get("ok"):
                self._populate(result)
                self.tip.configure(text="成绩已更新", foreground=GREEN)
            else:
                self.tip.configure(text=f"加载失败：{result.get('msg', '')}",
                                   foreground=RED)

        self.app.task(lambda: self.api.scores(self.eid), success,
                      error=lambda exc: self.tip.configure(
                          text=f"加载失败：{exc}", foreground=RED),
                      controls=self.controls, status=self.tip,
                      busy_text="正在加载成绩…")

    def _populate(self, result):
        self.tree.delete(*self.tree.get_children())
        for row in result.get("rows") or []:
            self.tree.insert("", "end", iid=str(row.get("participant_id") or
                                                 row.get("hostname") or len(
                                                     self.tree.get_children())),
                             values=(
                                 row.get("seat") or "—", row.get("name") or "—",
                                 row.get("hostname") or "—", row.get("ip") or "—",
                                 row.get("theory") if row.get("theory") is not None else "—",
                                 row.get("practice") if row.get("practice") is not None else "—",
                                 row.get("total") or 0,
                                 "已交" if row.get("submitted") else "未交",
                                 "在线" if row.get("online") else "离线",
                                 row.get("filename") or ""))
        summary = result.get("summary") or {}
        stats = result.get("stats") or {}
        self.summary.configure(
            text=f"应到 {summary.get('count', 0)} 人 · 已交 {summary.get('submitted', 0)} 人 · "
                 f"平均分 {summary.get('avg_pct', 0)}% · 最高 {summary.get('max', 0)} 分 · "
                 f"及格率 {summary.get('pass_rate', 0)}% · "
                 f"当前在线 {stats.get('online', 0)} 人")

    def _export(self):
        if not self.app.busy:
            self.main.export_scores(self.eid, self.export_var.get(),
                                    self.exam.get("title") or "")

    def _close(self):
        if self.app.busy:
            mb.showinfo("请稍候", "当前操作尚未完成。", parent=self.top)
            return
        self.top.destroy()


def main():
    app = TeacherApp()
    app.run()


if __name__ == "__main__":
    main()
