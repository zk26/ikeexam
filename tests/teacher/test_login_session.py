# -*- coding: utf-8 -*-
"""老师端登录页移除免密入口与 24 小时会话回调回归。"""
import os
import sys
import tkinter as tk
from tkinter import ttk
from types import SimpleNamespace
from unittest.mock import patch

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

from ikeexam.shared import ui_theme
from ikeexam.teacher import teacher_app as mod


def test_login_has_no_passwordless_button():
    root = tk.Tk()
    root.withdraw()
    ui_theme.apply_theme(root)
    try:
        app = SimpleNamespace(root=root, api=SimpleNamespace(), ui_tokens=ui_theme.LIGHT,
                              busy=False, _schedule_login_startup=lambda: None)
        login = mod.LoginWindow(app)
        root.update()
        assert not hasattr(login, "local_btn")
        assert not hasattr(login, "_local_login")
        assert int(login.card.place_info()["width"]) == 560
        assert int(login.card.place_info()["height"]) == 680
        assert int(login.tip.cget("height")) == 3
        assert all(root.resizable())
        card_size = (login.card.winfo_width(), login.card.winfo_height())
        tip_size = (login.tip.winfo_width(), login.tip.winfo_height())
        login.tip.configure(text="这是一条很长的状态提示，用于验证提示文字变化时登录框和提示区域都保持固定尺寸，不会自动把窗口撑大或缩小。" * 2)
        root.update()
        assert (login.card.winfo_width(), login.card.winfo_height()) == card_size
        assert (login.tip.winfo_width(), login.tip.winfo_height()) == tip_size
        texts = []
        for child in login.root.winfo_children():
            try:
                texts.extend(str(widget.cget("text")) for widget in child.winfo_children()
                             if widget.winfo_class() in ("Button", "Label", "TLabel"))
            except tk.TclError:
                pass
        assert not any("免密" in text for text in texts), texts
    finally:
        root.destroy()


def test_login_window_can_maximize_and_restore():
    root = tk.Tk()
    root.withdraw()
    ui_theme.apply_theme(root)
    try:
        app = SimpleNamespace(root=root, api=SimpleNamespace(), ui_tokens=ui_theme.LIGHT,
                              busy=False, _schedule_login_startup=lambda: None)
        login = mod.LoginWindow(app)
        root.geometry("900x820")
        root.deiconify()
        root.update()
        card_size = (login.card.winfo_width(), login.card.winfo_height())
        root.state("zoomed")
        root.update()
        assert root.state() == "zoomed"
        assert (login.card.winfo_width(), login.card.winfo_height()) == card_size
        root.state("normal")
        root.update()
        assert root.state() == "normal"
        assert root.winfo_width() < root.winfo_screenwidth()
        assert (login.card.winfo_width(), login.card.winfo_height()) == card_size
    finally:
        root.destroy()


def test_change_password_dialog_requires_original_password():
    root = tk.Tk()
    root.withdraw()
    ui_theme.apply_theme(root)
    calls = []
    notices = []
    api = SimpleNamespace(change_password=lambda *values: calls.append(values) or {
        "ok": True, "msg": "密码已修改"})

    def task(function, on_success=None, **kwargs):
        on_success(function())

    app = SimpleNamespace(root=root, api=api, busy=False, task=task,
                          _build_login=notices.append)
    try:
        dialog = mod.ChangePasswordDialog(root, app)
        dialog.submit()
        assert not calls and "原密码" in dialog.tip.cget("text")
        old, new, confirm = dialog.entries
        old.insert(0, "Admin@123")
        new.insert(0, "short")
        confirm.insert(0, "short")
        dialog.submit()
        assert not calls and "至少 6 位" in dialog.tip.cget("text")
        new.delete(0, "end")
        confirm.delete(0, "end")
        new.insert(0, "New@12345")
        confirm.insert(0, "Different@123")
        dialog.submit()
        assert not calls and "不一致" in dialog.tip.cget("text")
        confirm.delete(0, "end")
        confirm.insert(0, "New@12345")
        with patch.object(mod.mb, "showinfo"):
            dialog.submit()
        assert calls == [("Admin@123", "New@12345", "New@12345")]
        assert notices == ["密码已修改，请使用新密码重新登录。"]
        assert not dialog.win.winfo_exists()
    finally:
        root.destroy()


def test_main_header_password_button_order():
    root = tk.Tk()
    root.withdraw()
    ui_theme.apply_theme(root)
    app = SimpleNamespace(root=root, api=SimpleNamespace(base="http://127.0.0.1:9000"),
                          ui_tokens=ui_theme.LIGHT, busy=False)
    main = object.__new__(mod.MainWindow)
    main.app = app
    main.root = root
    main.api = app.api
    try:
        with patch.object(mod, "ExamManager", side_effect=lambda value: ttk.Frame(value.notebook)), \
                patch.object(mod, "QuestionBank", side_effect=lambda value: ttk.Frame(value.notebook)), \
                patch.object(mod, "PracticeBank", side_effect=lambda value: ttk.Frame(value.notebook)), \
                patch.object(mod, "StudentManager", side_effect=lambda value: ttk.Frame(value.notebook)), \
                patch.object(mod.MainWindow, "refresh_all"):
            main._build()
        labels = [widget.cget("text") for widget in main.password_btn.master.winfo_children()]
        assert labels == ["关于", "修改密码", "退出登录"], labels
    finally:
        root.destroy()


def test_session_timeout_returns_to_login():
    app = object.__new__(mod.TeacherApp)
    app._closed = False
    app._session_job = None
    app.login_window = None
    app.api = SimpleNamespace(discard_session=lambda: None)
    notices = []
    app._build_login = lambda notice="": notices.append(notice)
    app._session_timeout()
    assert notices == ["登录已满 24 小时，请重新登录。"]


def test_session_timer_uses_server_expiry():
    scheduled = []
    cancelled = []
    app = object.__new__(mod.TeacherApp)
    app.root = SimpleNamespace(
        after=lambda milliseconds, callback: scheduled.append((milliseconds, callback)) or "timer",
        after_cancel=cancelled.append,
    )
    app._session_job = None
    app.login_window = object()
    app.ui_tokens = object()
    app.main_window = None
    with patch.object(mod, "MainWindow", return_value=object()), \
            patch.object(ui_theme, "apply_to_tk"):
        app.enter_main(17)
        assert scheduled == [(17000, app._session_timeout)]
        assert app._session_job == "timer"
        app.enter_main()
        assert cancelled == ["timer"]
        assert scheduled[-1] == (24 * 3600 * 1000, app._session_timeout)


if __name__ == "__main__":
    test_login_has_no_passwordless_button()
    test_login_window_can_maximize_and_restore()
    test_change_password_dialog_requires_original_password()
    test_main_header_password_button_order()
    test_session_timeout_returns_to_login()
    test_session_timer_uses_server_expiry()
    print("[OK] 老师端登录与会话失效回归通过")
