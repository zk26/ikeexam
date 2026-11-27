# -*- coding: utf-8 -*-
"""老师端考试编辑器与理论题编辑器交互回归。"""
import os
import sys
import tkinter as tk
from types import SimpleNamespace

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

        labels = [editor.notebook.tab(tab, "text") for tab in editor.notebook.tabs()]
        assert len(labels) == 12, labels
        assert any("单选题" in label for label in labels)
        assert any("画板题" in label for label in labels)
        assert not hasattr(editor, "office_vars")
        assert not hasattr(editor, "save_as_var")
        assert not hasattr(editor, "theory_duration_var")

        editor.notebook.select(editor.q_pages["single"]["frame"])
        root.update()
        editor._toggle_question_id("single", 1)
        editor.notebook.select(editor.q_pages["multi"]["frame"])
        root.update()
        assert editor.notebook.select() == str(editor.q_pages["single"]["frame"])
        assert editor.btn_save_selection.winfo_manager() == "grid"
        assert editor.btn_save.winfo_manager() == ""
        assert editor.q_pages["single"]["tree"].bind("<MouseWheel>")
        assert editor.q_pages["single"]["tree"].bind("<Double-Button-1>")
        assert any("单选题 (1)" in label for label in
                   [editor.notebook.tab(tab, "text") for tab in editor.notebook.tabs()])
        assert editor.overview_tree.exists("q:1")
        assert editor.overview_total.cget("text") == "总分 2"
        editor.overview_tree.selection_set("q:1")
        editor._remove_overview_selected()
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


if __name__ == "__main__":
    main()
