# -*- coding: utf-8 -*-
"""ikee考务中心 —— 老师端桌面软件（Windows）

原生 tkinter 界面，通过 /admin_api/* 接口操作服务端。
双击即用：默认连本机 127.0.0.1:9000，也可在登录窗口改服务器地址。

作者：小可老师 · 网站：https://www.ikee002.top/ · 仓库：https://github.com/zk26/ikeexam
"""
import json
import os
import sys
import tkinter as tk
import tkinter.messagebox as mb
from tkinter import ttk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from api import AdminAPI

APP_NAME = "ikee考务中心"
ABOUT = {
    "name": APP_NAME,
    "author": "小可老师",
    "site": "https://www.ikee002.top/",
    "repo": "https://github.com/zk26/ikeexam",
    "license": "MIT",
    "desc": "机房考试系统：理论题在线作答，WPS 实操题自动评分。",
}

# 配色（与学生端统一）
BG = "#f1f5f9"
CARD = "#ffffff"
INK = "#1e293b"
GRAY = "#64748b"
BLUE = "#2563eb"
BLUE_D = "#1d4ed8"
RED = "#dc2626"
GREEN = "#16a34a"
LINE = "#e2e8f0"

_CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")


def load_server():
    try:
        with open(_CONFIG, "r", encoding="utf-8") as f:
            return (json.load(f) or {}).get("server", "127.0.0.1:9000")
    except Exception:
        return "127.0.0.1:9000"


def save_server(addr):
    try:
        with open(_CONFIG, "w", encoding="utf-8") as f:
            json.dump({"server": addr}, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def norm_base(addr):
    a = (addr or "").strip()
    if not a:
        return ""
    if not a.startswith("http://") and not a.startswith("https://"):
        a = "http://" + a
    return a.rstrip("/")


def show_about(parent):
    mb.showinfo(
        f"关于 {APP_NAME}",
        f"{ABOUT['name']}\n\n"
        f"{ABOUT['author']} 自主开发\n\n"
        f"{ABOUT['desc']}\n\n"
        f"网站：{ABOUT['site']}\n"
        f"仓库：{ABOUT['repo']}\n"
        f"协议：{ABOUT['license']} License"
    )


class LoginWindow:
    """连接 + 登录（或首次设密）"""

    def __init__(self, app):
        self.app = app
        self.root = app.root
        self.api = app.api
        self._build()

    def _build(self):
        for w in self.root.winfo_children():
            w.destroy()
        self.root.title(f"{APP_NAME} · 老师端登录")
        self.root.geometry("420x360")
        self.root.configure(bg=BG)

        card = tk.Frame(self.root, bg=CARD, padx=34, pady=30,
                        highlightbackground=LINE, highlightthickness=1)
        card.place(relx=0.5, rely=0.5, anchor="center")

        tk.Label(card, text=APP_NAME, bg=CARD, font=("Microsoft YaHei", 20, "bold"),
                 fg=INK).pack(pady=(0, 2))
        tk.Label(card, text="老师端 · 小可老师 自主开发", bg=CARD,
                 fg=GRAY, font=("Microsoft YaHei", 10)).pack(pady=(0, 20))

        # 服务器地址
        tk.Label(card, text="服务器地址", bg=CARD, fg=INK,
                 font=("Microsoft YaHei", 11)).pack(anchor="w")
        self.e_server = tk.Entry(card, font=("Microsoft YaHei", 12), width=28)
        self.e_server.pack(fill="x", ipady=5, pady=(4, 12))
        self.e_server.insert(0, load_server())

        # 密码
        tk.Label(card, text="管理密码", bg=CARD, fg=INK,
                 font=("Microsoft YaHei", 11)).pack(anchor="w")
        self.e_pwd = tk.Entry(card, font=("Microsoft YaHei", 12), width=28,
                              show="•")
        self.e_pwd.pack(fill="x", ipady=5, pady=(4, 12))

        self.tip = tk.Label(card, text="", bg=CARD, fg=RED,
                            font=("Microsoft YaHei", 10))
        self.tip.pack(pady=(0, 8))

        self.btn = tk.Button(card, text="连 接 并 登 录", command=self._connect,
                             bg=BLUE, fg="white",
                             font=("Microsoft YaHei", 12, "bold"),
                             relief="flat", cursor="hand2",
                             activebackground=BLUE_D, pady=8)
        self.btn.pack(fill="x")

        bar = tk.Frame(card, bg=CARD)
        bar.pack(pady=(12, 0))
        tk.Button(bar, text="关于本软件", command=lambda: show_about(self.root),
                  bg=CARD, fg=GRAY, font=("Microsoft YaHei", 9), relief="flat",
                  cursor="hand2", activebackground=CARD).pack()

        self.e_pwd.bind("<Return>", lambda e: self._connect())

    def _connect(self):
        base = norm_base(self.e_server.get())
        if not base:
            self.tip.config(text="请填写服务器地址")
            return
        self.tip.config(text="正在连接…", fg=GRAY)
        self.root.update_idletasks()
        self.api.base = base
        save_server(self.e_server.get().strip())
        r = self.api.ping()
        if not r.get("ok"):
            self.tip.config(text=f"连接失败：{r.get('msg', '未知错误')}", fg=RED)
            return
        # 首次运行：先设密
        if r.get("setup"):
            self._do_setup()
        else:
            self._do_login()

    def _do_setup(self):
        pwd = self.e_pwd.get().strip()
        if len(pwd) < 6:
            self.tip.config(text="首次运行：请设置至少 6 位的初始密码", fg=RED)
            return
        r = self.api.setup(pwd)
        if r.get("ok"):
            self.tip.config(text="初始密码已设置，正在登录…", fg=GREEN)
            self.root.after(300, self._do_login)
        else:
            self.tip.config(text=f"设置失败：{r.get('msg', '')}", fg=RED)

    def _do_login(self):
        pwd = self.e_pwd.get().strip()
        r = self.api.login(pwd)
        if r.get("ok"):
            self.app.enter_main()
        else:
            self.tip.config(text=f"登录失败：{r.get('msg', '密码错误')}", fg=RED)


class MainWindow:
    """主窗口：考试列表 + 控制台（骨架，后续逐步扩展）"""

    def __init__(self, app):
        self.app = app
        self.root = app.root
        self.api = app.api
        self._build()

    def _build(self):
        for w in self.root.winfo_children():
            w.destroy()
        self.root.title(f"{APP_NAME} · 老师端")
        self.root.geometry("920x600")
        self.root.configure(bg=BG)

        # 顶部
        top = tk.Frame(self.root, bg=BLUE)
        top.pack(fill="x")
        tk.Label(top, text=APP_NAME, bg=BLUE, fg="white",
                 font=("Microsoft YaHei", 15, "bold")).pack(side="left",
                                                            padx=16, pady=10)
        bar = tk.Frame(top, bg=BLUE)
        bar.pack(side="right", padx=8)
        tk.Button(bar, text="关于", command=lambda: show_about(self.root),
                  bg=BLUE, fg="white", font=("Microsoft YaHei", 10),
                  relief="flat", cursor="hand2",
                  activebackground=BLUE_D).pack(side="left", padx=2)
        tk.Button(bar, text="退出登录", command=self.app.logout,
                  bg=BLUE, fg="white", font=("Microsoft YaHei", 10),
                  relief="flat", cursor="hand2",
                  activebackground=BLUE_D).pack(side="left", padx=2)

        # 内容
        self.body = tk.Frame(self.root, bg=BG)
        self.body.pack(fill="both", expand=True, padx=16, pady=16)
        self.refresh()

    def refresh(self):
        for w in self.body.winfo_children():
            w.destroy()
        r = self.api.exams()
        tk.Label(self.body, text="考试列表", bg=BG, fg=INK,
                 font=("Microsoft YaHei", 15, "bold")).pack(anchor="w",
                                                            pady=(0, 10))
        if not r.get("ok"):
            tk.Label(self.body, text=f"加载失败：{r.get('msg', '')}", bg=BG,
                     fg=RED).pack(anchor="w")
            return
        exams = r.get("exams") or []
        if not exams:
            tk.Label(self.body, text="还没有考试（请在网页端或后续版本创建）",
                     bg=BG, fg=GRAY).pack(anchor="w")
            return
        for ex in exams:
            status = ex.get("status", "")
            fg = {"running": GREEN, "paused": "#d97706",
                  "stopped": GRAY}.get(status, INK)
            row = tk.Frame(self.body, bg=CARD, padx=14, pady=10,
                           highlightbackground=LINE, highlightthickness=1)
            row.pack(fill="x", pady=4)
            tk.Label(row, text=ex.get("title", "（无标题）"), bg=CARD, fg=INK,
                     font=("Microsoft YaHei", 12, "bold")).pack(side="left")
            tk.Label(row, text=f"[{ex.get('code', '')}]", bg=CARD,
                     fg=GRAY).pack(side="left", padx=8)
            tk.Label(row, text=status, bg=CARD, fg=fg).pack(side="right")


class TeacherApp:
    def __init__(self):
        self.root = tk.Tk()
        self.api = AdminAPI(norm_base(load_server()))
        self._ensure_server()
        self._build_login()

    def _ensure_server(self):
        """Windows 版「双击即用」：本机服务端没跑时，后台自动拉起"""
        try:
            if self.api.ping().get("ok"):
                return
            here = os.path.dirname(os.path.abspath(__file__))
            root = os.path.dirname(here)
            pythonw = os.path.join(root, "runtime", "pythonw.exe")
            server_script = os.path.join(root, "server", "app.py")
            if not os.path.isfile(pythonw) or not os.path.isfile(server_script):
                return
            import subprocess
            subprocess.Popen(
                [pythonw, server_script],
                cwd=os.path.join(root, "server"),
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception:
            pass

    def _build_login(self):
        LoginWindow(self)

    def enter_main(self):
        MainWindow(self)

    def logout(self):
        self._build_login()

    def run(self):
        self.root.mainloop()


def main():
    app = TeacherApp()
    app.run()


if __name__ == "__main__":
    main()
