# -*- coding: utf-8 -*-
"""老师端登录页移除免密入口与 24 小时会话回调回归。"""
import os
import sys
import tkinter as tk
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
    test_session_timeout_returns_to_login()
    test_session_timer_uses_server_expiry()
    print("[OK] 老师端登录与会话失效回归通过")
