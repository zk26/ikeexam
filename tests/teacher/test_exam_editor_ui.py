# -*- coding: utf-8 -*-
"""老师端考试编辑器与理论题编辑器交互回归。"""
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


def main():
    root = tk.Tk()
    root.withdraw()
    ui_theme.apply_theme(root)
    original_warning = mod.mb.showwarning
    original_info = mod.mb.showinfo
    mod.mb.showwarning = lambda *args, **kwargs: None
    mod.mb.showinfo = lambda *args, **kwargs: None
    try:
        app = SimpleNamespace(ui_tokens=ui_theme.LIGHT, busy=False)
        notebook = ttk.Notebook(root)
        manager_main = SimpleNamespace(
            notebook=notebook, app=app, api=SimpleNamespace(), data={},
            refresh_all=lambda: None)
        manager = mod.ExamManager(manager_main)
        assert hasattr(manager, "btn_new")
        assert set(manager.buttons) == {"start", "pause", "resume", "stop",
                                        "regrade", "rejudge"}
        for name in ("btn_edit", "btn_delete", "btn_scores", "btn_review",
                     "btn_charts"):
            assert not hasattr(manager, name)
        manager.set_data({"exams": [
            {"id": 1, "code": "E1", "title": "草稿", "duration": 60,
             "status": "draft", "phase": "idle"},
            {"id": 2, "code": "E2", "title": "开考", "duration": 60,
             "status": "running", "phase": "theory"},
        ]})
        assert manager.tree.set("1", "status") == "○ 未开始"
        assert manager.tree.set("2", "status") == "▶ 进行中"
        assert manager.tree.column("status", "width") >= 100
        assert str(manager.tree.column("status", "anchor")) == "center"
        assert "status-running" in manager.tree.item("2", "tags")
        assert str(manager.tree.tag_configure("status-running", "foreground")).lower() == "#168a4a"
        menus = []

        class FakeMenu:
            def __init__(self, *args, **kwargs):
                self.labels = []
                menus.append(self)

            def add_command(self, label, **kwargs):
                self.labels.append(label)

            def add_separator(self):
                self.labels.append(None)

            def tk_popup(self, x, y):
                pass

        with patch.object(mod.tk, "Menu", FakeMenu):
            manager._context_menu(SimpleNamespace(y=999, x_root=0, y_root=0))
        assert menus[-1].labels == ["新建考试"]

        student = {"hostname": "PC-TEST", "name": "错误姓名", "seat": "99"}
        student_manager = object.__new__(mod.StudentManager)
        student_manager.main = SimpleNamespace(refresh_all=lambda: None)
        student_manager.students = [student]
        student_manager.tree = SimpleNamespace(
            identify_row=lambda y: "PC-TEST", selection_set=lambda row: None)
        student_manager._selected = lambda: student
        with patch.object(mod.tk, "Menu", FakeMenu):
            student_manager._context_menu(SimpleNamespace(y=10, x_root=0, y_root=0))
        assert "修改学生信息" in menus[-1].labels
        prompt = object.__new__(mod.StudentEditPrompt)
        prompt.name_var = tk.StringVar(value="修正姓名")
        prompt.seat_var = tk.StringVar(value="03")
        prompt.top = SimpleNamespace(destroy=lambda: None)
        prompt.result = None
        prompt._ok()
        assert prompt.result == ("修正姓名", "03")
        assert mod.StudentManager._progress_text({"progress": {
            "current_question": 2, "question_total": 5,
            "theory_answered": 1, "current_task": "单选题",
        }}) == "第 2/5 题 · 已答 1/5 · 单选题"
        assert mod.StudentManager._progress_text({"progress": {
            "section": "typing_zh", "current_task": "打地鼠",
        }}) == "打地鼠"

        class FakeDragTree:
            def __init__(self):
                self.rows = ["1", "2", "3", "4"]
                self.row_at = {10: "1", 20: "2", 30: "3", 40: "4"}
                self.selected = []

            def identify_row(self, y):
                return self.row_at.get(y, "")

            def get_children(self):
                return tuple(self.rows)

            def selection_set(self, rows):
                self.selected = list(rows)

            def selection(self):
                return tuple(self.selected)

            def focus(self, row):
                pass

        manager.tree = FakeDragTree()
        manager._begin_drag_select(SimpleNamespace(y=20, state=0))
        assert manager.tree.selected == ["2"]
        manager._drag_select_rows(SimpleNamespace(y=30, state=0))
        assert manager.tree.selected == ["2", "3"]
        manager._drag_select_rows(SimpleNamespace(y=40, state=0))
        assert manager.tree.selected == ["2", "3", "4"]
        manager._end_drag_select()
        assert manager._drag_select is None
        main_window = SimpleNamespace(root=root, data={
            "questions": [
                {"id": 1, "qtype": "single", "stem": "单选一", "score": 2,
                 "category": "基础"},
                {"id": 2, "qtype": "single", "stem": "单选二", "score": 3,
                 "category": "基础"},
                {"id": 3, "qtype": "multi", "stem": "多选一", "score": 2,
                 "category": "基础"},
            ],
            "practice": [
                {"id": 1, "etype": "word", "name": "Word题", "level": "入门",
                 "category": "基础"},
            ],
            "materials": [],
        })
        page = SimpleNamespace(main=main_window, app=app, api=SimpleNamespace())
        editor = mod.ExamEditor(page, None)
        root.update()
        assert editor.basic_frame.cget("style") == "Surface.TFrame"
        assert editor.basic_frame.master is editor.basic_scroll.canvas
        assert str(editor.basic_scroll.scroll.cget("orient")).lower() == "vertical"
        assert editor.basic_scroll.canvas.cget("yscrollcommand")
        assert editor.basic_scroll.inner.bind("<MouseWheel>")
        assert editor.name_entry.bind("<MouseWheel>")
        assert editor.drawing_editor.cget("style") == "Card.TFrame"
        assert editor.drawing_editor.grid_info()["sticky"] == "nesw"
        assert editor.drawing_editor.master.columnconfigure(0, "weight") == 1
        assert editor.drawing_editor.master.columnconfigure(1, "weight") == 1
        assert editor.drawing_editor.brief.grid_info()["sticky"] == "nesw"
        assert int(editor.drawing_editor.brief.cget("width")) <= 40
        assert editor.drawing_editor.rowconfigure(3, "weight") == 1
        assert editor.drawing_preview.winfo_exists()

        labels = [editor.notebook.tab(tab, "text") for tab in editor.notebook.tabs()]
        assert len(labels) == 12, labels
        assert any("单选题" in label for label in labels)
        assert any("画板题" in label for label in labels)
        assert not hasattr(editor, "office_vars")
        assert not hasattr(editor, "save_as_var")
        assert not hasattr(editor, "theory_duration_var")
        assert not hasattr(editor, "passwordless_var")
        assert not hasattr(editor, "btn_back_basic")
        assert not hasattr(editor, "btn_save_selection")
        assert not ttk.Style(editor.top).map("Exam.TNotebook.Tab", "expand")

        prompt = object.__new__(mod.ClassPrompt)
        prompt.var = tk.StringVar(value="验收班")
        prompt.passwordless_var = tk.BooleanVar(value=True)
        prompt.top = SimpleNamespace(destroy=lambda: None)
        prompt.result = None
        prompt._ok()
        assert prompt.result == ("验收班", True)

        saved_calls = []
        main_window.task = lambda work, success, **kwargs: success(work())
        main_window.show_failure = lambda result, title: None
        editor.api.set_exam_questions = lambda eid, ids: saved_calls.append((eid, list(ids))) or {"ok": True}
        editor.eid = 10

        editor.notebook.select(editor.q_pages["single"]["frame"])
        root.update()
        editor._toggle_question_id("single", 1)
        editor.notebook.select(editor.q_pages["multi"]["frame"])
        root.update()
        assert editor.notebook.select() == str(editor.q_pages["multi"]["frame"])
        editor._cancel_selection_save()
        editor._auto_save_selection("questions")
        assert saved_calls == [(10, [1])]
        assert editor.footer_tip.cget("text") == "选题已自动保存"
        editor.notebook.select(editor.q_pages["single"]["frame"])
        root.update()
        assert editor.btn_save.winfo_manager() == ""
        assert editor.q_pages["single"]["tree"].bind("<MouseWheel>")
        assert editor.q_pages["single"]["tree"].bind("<Double-Button-1>")
        assert any("单选题 (1)" in label for label in
                   [editor.notebook.tab(tab, "text") for tab in editor.notebook.tabs()])
        assert editor.overview_tree.exists("q:1")
        assert editor.overview_total.cget("text") == "总分 2"
        editor.overview_tree.selection_set("q:1")
        editor._remove_overview_selected()
        editor._cancel_selection_save()
        assert not editor.overview_tree.exists("q:1")

        question = mod.QuestionEditor(page, {
            "id": 1, "qtype": "single", "stem": "题", "options": ["甲", "乙"],
            "answer": "A", "score": 2,
        })
        root.update()
        assert question.code_frame.grid_info() == {}
        question.type_var.set("程序填空")
        root.update()
        assert question.code_frame.grid_info()
        question.type_var.set("拖拽题")
        root.update()
        assert question.drag_frame.grid_info()
        assert question.code_frame.grid_info() == {}
        print("[OK] 老师端编辑器交互回归通过")
    finally:
        mod.mb.showwarning = original_warning
        mod.mb.showinfo = original_info
        root.destroy()


def test_review_window_loads_payload():
    root = tk.Tk()
    root.withdraw()
    ui_theme.apply_theme(root)
    payload = {
        "ok": True, "total_score": 2, "n_attended": 0, "n_submitted": 0,
        "cards": [{
            "no": 1, "q": {"id": 1, "stem": "评讲回归题", "options": ["甲", "乙"]},
            "qtype": "单选题", "category": "基础", "score": 2,
            "right_n": 0, "wrong_n": 0, "blank_n": 0, "rate": None,
            "answer_text": "A. 甲", "right": [], "wrong": [],
        }],
        "task_cards": [], "typing_rows": [], "drawings": [],
    }

    def task(function, on_success=None, **kwargs):
        on_success(function())
        return True

    main = SimpleNamespace(
        root=root, app=SimpleNamespace(busy=False, task=task),
        api=SimpleNamespace(review=lambda eid: payload))
    try:
        window = mod.ReviewWindow(main, {"id": 1, "title": "评讲回归"})
        root.update()
        assert window.tip.cget("text") == "评讲数据已更新"
        assert window.theory_tree.set("1", "stem") == "评讲回归题"
        assert "理论满分 2" in window.summary.cget("text")
        assert window.drawing_area.rowconfigure(0, "weight") == 1
        assert window.drawing_area.columnconfigure(1, "weight") == 1
        window.top.destroy()
    finally:
        root.destroy()


def test_practice_editor_can_add_scoring_rule():
    root = tk.Tk()
    root.withdraw()
    ui_theme.apply_theme(root)
    app = SimpleNamespace(busy=False)
    main = SimpleNamespace(root=root, app=app, api=SimpleNamespace())
    page = SimpleNamespace(main=main, app=app, api=main.api)
    try:
        editor = mod.PracticeEditor(page, None)
        root.update()
        assert editor.checks_editor.empty_label.winfo_manager() == "grid"
        editor.checks_editor.add_button.invoke()
        root.update()
        assert len(editor.checks_editor.rows) == 1
        assert editor.checks_editor.empty_label.winfo_manager() == ""
        assert len(editor.checks_editor.get_rules()) == 1
        editor.top.destroy()
    finally:
        root.destroy()


def test_charts_window_uses_card_grid():
    root = tk.Tk()
    root.withdraw()
    ui_theme.apply_theme(root)
    payload = {
        "ok": True,
        "summary": {"count": 1, "submitted": 1, "avg_pct": 76,
                    "pass_rate": 100, "good_rate": 0, "max": 19.5},
        "dist": {"labels": ["不及格", "及格"], "values": [0, 1]},
        "questions": [], "checks": [], "rows": [], "live": {"online": 1},
    }

    def task(function, on_success=None, **kwargs):
        on_success(function())
        return True

    main = SimpleNamespace(
        root=root, app=SimpleNamespace(busy=False, task=task),
        api=SimpleNamespace(stats=lambda eid: payload))
    try:
        window = mod.ChartsWindow(main, {"id": 1, "title": "图表回归"})
        root.update()
        assert window.tip.cget("text") == "图表已更新"
        assert len(window.summary.winfo_children()) == 6
        cards = window.chart_area.winfo_children()
        assert len(cards) == 6
        assert all(card.cget("style") == "Card.TLabelframe" for card in cards)
        assert all(card.winfo_children()[0].title == "" for card in cards)
        assert window.chart_area.rowconfigure(0, "weight") == 1
        window.top.destroy()
    finally:
        root.destroy()


if __name__ == "__main__":
    main()
    test_review_window_loads_payload()
    test_practice_editor_can_add_scoring_rule()
    test_charts_window_uses_card_grid()
    print("[OK] 老师端编辑器、评讲与图表窗口回归通过")
