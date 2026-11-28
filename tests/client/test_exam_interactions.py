# -*- coding: utf-8 -*-
"""学生端自动下一题与横向拖拽回归。"""
import inspect
import os
import sys
import tkinter as tk
from types import SimpleNamespace
from unittest.mock import patch

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "src", "ikeexam", "client"))

import gui
from drag_ui import DragBoard
from drawing_ui import DrawingPanel
from exam_client import Api, ExamClient
from gui import App
from typing_ui import WhackMoleTask


def test_auto_next():
    root = tk.Tk()
    root.withdraw()
    try:
        app = object.__new__(App)
        app.root = root
        app.cfg = SimpleNamespace(auto_next=True)
        app.client = SimpleNamespace(state={}, queue_answers=lambda value: None,
                                     is_submitted=lambda exam_id: False)
        app.exam_id = 9
        question = {"id": 1, "qtype": "single"}
        app.sheet = SimpleNamespace(current=lambda: question, set=lambda *args: None)
        app.refresh_card = lambda: None
        calls = []
        app.next_q = lambda: calls.append("next")
        app._auto_next_job = None
        app._page = "exam"
        app.save_current("A")
        assert app._auto_next_job is not None
        app._auto_next_question()
        assert calls == ["next"]
        app._page = "end"
        app._auto_next_question()
        assert calls == ["next"]
    finally:
        root.destroy()


def test_horizontal_drag():
    root = tk.Tk()
    root.withdraw()
    try:
        board = DragBoard(root, ["甲", "乙"], ["开始", "完成"],
                          layout="horizontal")
        board.pack(fill="both", expand=True)
        board.canvas.configure(width=600)
        board.redraw()
        root.update()
        assert board._slot_cols() == 2
        assert board.slot_boxes[1][1] == board.slot_boxes[2][1]
        texts = [board.canvas.itemcget(item, "text") for item in board.canvas.find_all()
                 if board.canvas.type(item) == "text"]
        assert "1. 开始" in texts and "2. 完成" in texts
        assert not any("拖到上面" in text for text in texts)
    finally:
        root.destroy()


def test_idle_registration_enters_waiting_page():
    api = object.__new__(Api)
    api.cfg = SimpleNamespace(hostname="WAIT-PC", client_id="WAIT-PC", name="小明", seat="01",
                              server="http://127.0.0.1:9000", logged_in=True,
                              passwordless=False, token="", save=lambda: None)
    api.local_ip = lambda: "10.0.0.10"
    api.local_mac = lambda: "00-00-00-00-00-01"
    api._post = lambda path, data: {
        "ok": True, "exam": None, "phase": "idle", "server_name": "QA",
        "student": {"name": "小明", "seat": "01"}, "token": "wait-token",
    }
    hello = api.hello()
    assert hello.get("ok") and hello.get("exam") is None
    assert api.cfg.token == "wait-token"

    client = object.__new__(ExamClient)
    client.state = {"exam": {"id": 1}, "phase": "ended", "hello_error": "旧错误"}
    client._refresh_identity = lambda: False
    client._bind_api = lambda: None
    assert client._accept_hello(hello)
    assert client.state["exam"] is None and client.state["phase"] == "idle"
    assert "hello_error" not in client.state

    root = tk.Tk()
    root.withdraw()
    try:
        app = object.__new__(App)
        app.root = root
        app.cfg = SimpleNamespace(name="小明", seat="01", hostname="WAIT-PC",
                                  client_id="WAIT-PC", server="http://127.0.0.1:9000")
        app.client = SimpleNamespace(state=client.state, cfg=app.cfg)
        app._page = "connecting"
        app._current_page = lambda: app._page
        app.swap = lambda builder: builder(tk.Frame(root))
        app.apply_state = lambda force=False: None
        app._sync_state = lambda: None
        app._resume_submission_outbox = lambda exam_id: None
        app._on_connected()
        root.update()

        def labels(widget):
            found = [widget.cget("text") for widget in widget.winfo_children()
                     if isinstance(widget, tk.Label)]
            for child in widget.winfo_children():
                found.extend(labels(child))
            return found

        text = "\n".join(labels(root))
        assert "等待老师开始考试" in text and "已连接，等待老师开始考试" in text
    finally:
        root.destroy()


def test_passwordless_auto_entry():
    app = object.__new__(App)
    app.client = SimpleNamespace(state={
        "phase": "theory", "section": None, "confirmed": False,
        "exam": {"id": 9, "passwordless": True},
    })
    assert app.decide_page() == "exam"
    app.client.state["exam"]["passwordless"] = False
    assert app.decide_page() == "login"
    assert "免密码进入" not in inspect.getsource(App.build_login)

    api = object.__new__(Api)
    api.cfg = SimpleNamespace(hostname="QA-PC", client_id="QA-PC", name="", seat="",
                              server="http://127.0.0.1:9000",
                              logged_in=False, passwordless=False, token="",
                              save=lambda: None)
    api.local_ip = lambda: "10.0.0.10"
    api.local_mac = lambda: "00-00-00-00-00-01"
    api._post = lambda path, data: {
        "ok": True, "exam": {"id": 9, "passwordless": True},
        "student": {"name": "小明", "seat": "01"}, "token": "qa-token",
    }
    api.hello()
    assert api.cfg.logged_in and api.cfg.passwordless
    assert api.cfg.name == "小明" and api.cfg.seat == "01"

    client = object.__new__(ExamClient)
    client.state = {"confirmed": False, "exam": None}
    client.cfg = SimpleNamespace(logged_in=False, passwordless=False,
                                 save=lambda: None)
    client.api = SimpleNamespace(hello=lambda: {
        "ok": True, "exam": {"id": 9, "passwordless": True},
        "phase": "theory", "server_name": "QA", "tasks": [], "commands": [],
    })
    client._handle_commands = lambda commands: None
    client._refresh_identity = lambda: False
    client._bind_api = lambda: None
    client._sync_passwordless_state({"passwordless": True, "exam_id": 9})
    assert client.state["confirmed"] is True
    assert client.state["exam"]["id"] == 9
    assert client.cfg.logged_in and client.cfg.passwordless

    client.state = {"confirmed": False, "exam": None}
    client.api = SimpleNamespace(hello=lambda: {"ok": False, "msg": "IP 不在名单"})
    client._sync_passwordless_state({"passwordless": True, "exam_id": 9})
    assert client.state["confirmed"] is False
    assert client.state["hello_error"] == "IP 不在名单"


def test_teacher_profile_update_command():
    saved = []
    acked = []
    client = object.__new__(ExamClient)
    client.cfg = SimpleNamespace(name="错误姓名", seat="99", logged_in=False,
                                 save=lambda: saved.append(True))
    client.state = {"confirmed": False}
    client.api = SimpleNamespace(ack_commands=lambda ids: acked.extend(ids))
    client._seen_commands = set()
    client._log = lambda level, message: None
    client._notify_ui = lambda: None
    client._prepare_identity_change = lambda: None
    client._refresh_identity = lambda: True
    client._handle_commands([{
        "id": 7, "type": "update_profile",
        "payload": {"name": "修正姓名", "seat": "03"},
    }])
    assert client.cfg.name == "修正姓名" and client.cfg.seat == "03"
    assert client.cfg.logged_in and client.state["confirmed"]
    assert saved == [True] and acked == [7]


def test_submit_confirmation_respects_require_complete():
    app = object.__new__(App)
    app.client = SimpleNamespace(state={"exam": {"require_complete": False}})
    app._unfinished_items = lambda: (_ for _ in ()).throw(
        AssertionError("普通交卷不应检查未做题"))
    with patch.object(gui.messagebox, "askyesno", return_value=True) as ask, \
            patch.object(gui.messagebox, "showwarning") as warn:
        assert app._confirm_submission() is True
        assert ask.called and not warn.called

    app.client.state["exam"]["require_complete"] = True
    app._unfinished_items = lambda: ["实操题「测试」还没有答卷"]
    with patch.object(gui.messagebox, "askyesno", return_value=True) as ask, \
            patch.object(gui.messagebox, "showwarning") as warn:
        assert app._confirm_submission() is False
        assert warn.called and not ask.called


def test_whack_mole_adapts_to_window():
    root = tk.Tk()
    root.geometry("1200x800")
    try:
        panel = SimpleNamespace(stats={"whack_mole": {}})
        task = WhackMoleTask(root, panel, "whack_mole", {
            "rounds": 2, "level": "easy", "grid": "3x3",
        })
        task.pack(fill="both", expand=True)
        root.update()
        assert task.canvas.winfo_width() > 560
        assert task.cv_w == task.canvas.winfo_width()
        assert task.cv_h == task.canvas.winfo_height()
        large_width = task.cv_w
        root.geometry("800x600")
        root.update()
        assert task.cv_w < large_width
        assert task.cv_w == task.canvas.winfo_width()
        assert len(task._holes) == 9
    finally:
        root.destroy()


def test_drawing_toolbar_wraps_without_clipping():
    root = tk.Tk()
    root.geometry("900x600")
    try:
        panel = DrawingPanel.__new__(DrawingPanel)
        tk.Frame.__init__(panel, root, bg="#FCF8F2")
        panel.on_prev = None
        panel.on_next = None
        panel.next_label = "下一题"
        panel._save_now = lambda: None
        panel._prev = lambda: None
        panel._next = lambda: None
        panel._set_tool = lambda key: None
        panel._set_color = lambda color: None
        panel._set_width = lambda width: None
        panel._build_toolbar()
        panel.pack(fill="x")
        root.update()
        toolbar = panel.winfo_children()[0]
        left, actions = toolbar.winfo_children()[:2]
        assert len(left.winfo_children()) == 2
        assert actions.winfo_x() + actions.winfo_width() <= 900
    finally:
        root.destroy()


def test_progress_reports_current_task():
    app = object.__new__(App)
    reported = []
    app.client = SimpleNamespace(
        state={"phase": "theory"}, practice_tasks=lambda: [],
        set_ui_progress=lambda progress: reported.append(progress))
    app._current_page = lambda: "exam"
    app.cur_tab = "choice"
    app.sheet = SimpleNamespace(
        questions=[{"id": 1, "qtype": "single"}], index=0,
        current=lambda: {"id": 1, "qtype": "single"},
        answered_count=lambda: 0)
    app._report_progress()
    assert reported[-1]["current_task"] == "单选题"

    app.cur_tab = "whack_mole"
    app.sheet = None
    app.exam_tabs = lambda: [("whack_mole", "打地鼠", ())]
    app._report_progress()
    assert reported[-1]["section"] == "whack_mole"
    assert reported[-1]["current_task"] == "打地鼠"


def test_back_to_login_clears_previous_student_state():
    calls = []
    saved = []
    stop_event = set()
    stop_event.add("stopped")
    cfg = SimpleNamespace(
        name="旧考生", seat="01", logged_in=True, passwordless=True,
        token="machine-token", save=lambda: saved.append({
            "name": cfg.name, "seat": cfg.seat, "logged_in": cfg.logged_in,
            "passwordless": cfg.passwordless, "token": cfg.token,
        }))
    state = {
        "exam": {"id": 12}, "phase": "ended", "confirmed": True,
        "remaining": 3, "section": "draw", "materials": [{"name": "素材"}],
        "typing": {"items": []}, "drawing": {"items": []},
        "ui_progress": {"section": "draw"}, "exam_started_at": 100,
        "hello_error": "旧错误", "ended_exam_id": 12, "theory_answered": 2,
        "theory_total": 3, "practice_submitted": True, "tasks": [{"id": 1}],
        "submitted": True, "submitted_key": "old", "submitting": True,
    }

    def stop():
        calls.append("stop")
        stop_event.add("stopped")

    client = SimpleNamespace(
        state=state, _stop=stop_event, want_lock=lambda: False,
        flush_answers=lambda: calls.append("flush") or True,
        stop_answer_sync=lambda: calls.append("stop_answer_sync"),
        stop=stop, _refresh_identity=lambda rebind=True:
        calls.append(("refresh_identity", rebind)) or True)
    app = object.__new__(App)
    app.client = client
    app.cfg = cfg
    app.sheet = object()
    app.exam_id = 12
    app._theory_exam_id = 12
    app.cur_tab = "draw"
    app.cur_task_idx = 2
    app.marked = {1}
    app.card_btns = [object()]
    app._img_cache = {"old": object()}
    pages = []
    app.swap = pages.append

    app.back_to_login()

    assert calls == ["flush", "stop_answer_sync", "stop",
                     ("refresh_identity", False)]
    assert not stop_event
    assert len(pages) == 1 and pages[0].__func__ is App.build_login
    assert pages[0].__self__ is app
    assert cfg.name == "" and cfg.seat == ""
    assert not cfg.logged_in and not cfg.passwordless
    assert cfg.token == "machine-token"
    assert saved == [{"name": "", "seat": "", "logged_in": False,
                      "passwordless": False, "token": "machine-token"}]
    assert state["exam"] is None and state["phase"] == "idle"
    assert state["confirmed"] is False and state["tasks"] == []
    assert state["remaining"] is None and state["section"] is None
    assert state["materials"] == [] and state["typing"] is None
    assert state["drawing"] is None and state["ui_progress"] == {}
    assert "hello_error" not in state
    assert app.sheet is None and app.exam_id is None
    assert app._theory_exam_id is None and app.cur_tab is None
    assert app.cur_task_idx == 0 and app.marked == set()
    assert app.card_btns == [] and app._img_cache == {}


def test_content_navigation_does_not_duplicate_submit():
    app = object.__new__(App)
    switched = []
    app._drawing_order = lambda: []
    app.switch_tab = switched.append
    app.submit_all = lambda: (_ for _ in ()).throw(
        AssertionError("内容区不应出现交卷按钮"))
    assert app._typing_tail_nav() is None
    app._drawing_order = lambda: ["draw"]
    assert app._drawing_next_label("draw") is None
    app._drawing_next("draw")
    assert switched == []

    app._drawing_order = lambda: ["draw", "beads"]
    label, callback = app._typing_tail_nav()
    assert label == "下一题：画板创作 ▶"
    callback()
    assert app._drawing_next_label("draw") == "下一题：拼豆创作 ▶"
    app._drawing_next("draw")
    assert switched == ["draw", "beads"]


def test_drawing_save_uses_current_exam_id():
    class InlineThread:
        def __init__(self, target, **kwargs):
            self.target = target

        def start(self):
            self.target()

    uploads = []
    app = object.__new__(App)
    app.client = SimpleNamespace(
        state={"exam": {"id": 12}},
        api=SimpleNamespace(upload_drawing=lambda *args, **kwargs:
                            uploads.append((args, kwargs)) or {"ok": True}))
    app.uiq = SimpleNamespace(put=lambda callback: callback())
    panel = SimpleNamespace(kind="draw", export_png=lambda: (b"png-data", ".png"))
    results = []
    with patch.object(gui.threading, "Thread", InlineThread):
        app._save_drawing(panel, lambda ok, msg: results.append((ok, msg)))
    assert results == [(True, "")]
    assert uploads == [((12, b"png-data", ".png"), {"kind": "draw"})]

    app.client.state["exam"] = None
    results.clear()
    app._save_drawing(panel, lambda ok, msg: results.append((ok, msg)))
    assert results[0][0] is False and "尚未进入考试" in results[0][1]
    assert len(uploads) == 1


def test_drawing_submit_exports_and_avoids_stale_final_marker():
    import queue

    class Outbox:
        def __init__(self):
            self.records = {}

        def put(self, key, identity, kind, data, principal=None):
            self.records[key] = {"kind": kind, "data": data}

        def remove(self, key):
            self.records.pop(key, None)

    class QueueStub:
        def __init__(self, client):
            self.client = client
            self.jobs = []

        def add(self, label, function):
            self.jobs.append((label, function))

        def start(self):
            lines = [function() for _label, function in self.jobs]
            events = queue.Queue()
            events.put(("done", lines, [], self.client.identity, self.client.generation))
            return events

    uploads = []
    outbox = Outbox()
    client = SimpleNamespace(
        state={"exam": {"id": 12}}, identity="student-1", generation=3,
        principal="principal-1", outbox=outbox,
        is_submitted=lambda exam_id: False, practice_tasks=lambda: [],
        typing_config=lambda: None, drawing_styles=lambda: ["draw"],
        drawing_state=lambda kind: {"items": [{"kind": "pen"}]},
        submission_key=lambda kind, exam_id: f"{kind}:{exam_id}",
        pending_submission_records=lambda exam_id: [],
        api=SimpleNamespace(upload_drawing=lambda *args, **kwargs:
                            uploads.append((args, kwargs)) or {"ok": True}))
    app = object.__new__(App)
    app.client = client
    app.sheet = None
    app.root = SimpleNamespace(after=lambda delay, callback: None)
    app._confirm_submission = lambda: True
    app._build_submitting = lambda total: None
    with patch("drawing_ui.state_has_art", return_value=True), \
            patch("drawing_ui.export_state", return_value=(b"png-data", ".png")), \
            patch.object(gui, "SubmitQueue", QueueStub):
        app.submit_all()
    assert uploads == [((12, b"png-data", ".png"), {"kind": "draw"})]
    assert "final:12" in outbox.records

    outbox.records.clear()
    uploads.clear()
    client.state["submitting"] = False
    with patch("drawing_ui.state_has_art", return_value=True), \
            patch("drawing_ui.export_state", side_effect=RuntimeError("导出中断")), \
            patch.object(gui.messagebox, "showerror") as error:
        app.submit_all()
    assert error.called and "导出中断" in error.call_args.args[1]
    assert "final:12" not in outbox.records


if __name__ == "__main__":
    test_auto_next()
    test_horizontal_drag()
    test_idle_registration_enters_waiting_page()
    test_passwordless_auto_entry()
    test_teacher_profile_update_command()
    test_submit_confirmation_respects_require_complete()
    test_whack_mole_adapts_to_window()
    test_drawing_toolbar_wraps_without_clipping()
    test_progress_reports_current_task()
    test_back_to_login_clears_previous_student_state()
    test_content_navigation_does_not_duplicate_submit()
    test_drawing_save_uses_current_exam_id()
    test_drawing_submit_exports_and_avoids_stale_final_marker()
    print("[OK] 学生端交互、题型进度、交卷规则与画板保存提交正常")
