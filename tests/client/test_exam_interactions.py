# -*- coding: utf-8 -*-
"""学生端自动下一题与横向拖拽回归。"""
import os
import sys
import tkinter as tk
from types import SimpleNamespace

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "src", "ikeexam", "client"))

from drag_ui import DragBoard
from gui import App


def test_auto_next():
    root = tk.Tk()
    root.withdraw()
    try:
        app = object.__new__(App)
        app.root = root
        app.cfg = SimpleNamespace(auto_next=True)
        app.client = SimpleNamespace(state={}, queue_answers=lambda value: None)
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


if __name__ == "__main__":
    test_auto_next()
    test_horizontal_drag()
    print("[OK] 自动下一题与横向流程拖拽正常")
