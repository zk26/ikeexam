# -*- coding: utf-8 -*-
"""老师端打字配置交互回归。"""
import os
import sys
import tkinter as tk
from tkinter import ttk

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

from ikeexam.shared import ui_theme
from ikeexam.teacher.visual_config import DrawingConfigEditor, TypingConfigEditor


def main():
    root = tk.Tk()
    root.withdraw()
    try:
        ui_theme.apply_theme(root)
        editor = TypingConfigEditor(
            root,
            passages=[{"grade": "四年级", "kind": "生词", "title": "示例课文",
                       "text": "春天来了，花儿开了。"}],
            passages_en=[{"grade": "四年级", "kind": "字母", "title": "字母表",
                          "text": "a b c d e f g"}])
        editor.pack(fill="both", expand=True)
        assert editor.scroll.cget("style") == "Thin.TScrollbar"
        drawing = DrawingConfigEditor(root)
        drawing.pack(fill="x")
        root.update()
        assert drawing.cget("style") == "Card.TFrame"
        assert drawing.brief.grid_info()["sticky"] == "nesw"
        assert drawing.rowconfigure(3, "weight") == 1
        drawing.destroy()
        editor.set_config({
            "key_press": {"enabled": False, "keys": ["q", "w"]},
            "typing_zh": {"enabled": True, "seconds": 120, "text": ""},
        })
        root.update()
        assert editor.bodies["key_press"].winfo_manager() == ""
        assert editor.bodies["typing_zh"].winfo_manager() == "grid"
        picker = next(widget for field, widget, var in editor.fields["key_press"]
                      if field["key"] == "keys")
        picker.set(["q", "w"])
        assert picker.get() == "q, w"
        root.deiconify()
        root.geometry("900x700")
        root.update()
        picker._open()
        root.update()
        tops = [widget for widget in picker.winfo_children()
                if isinstance(widget, tk.Toplevel)]
        assert tops
        labels = []

        def collect(widget):
            for child in widget.winfo_children():
                try:
                    labels.append(str(child.cget("text")))
                except tk.TclError:
                    pass
                collect(child)

        def find_text(widget, text):
            for child in widget.winfo_children():
                try:
                    if str(child.cget("text")) == text:
                        return child
                except tk.TclError:
                    pass
                found = find_text(child, text)
                if found is not None:
                    return found
            return None

        collect(tops[-1])
        assert {"Esc", "Tab", "Del", "↑", "↓", "←", "→"} <= set(labels), labels
        top = tops[-1]
        q_check = find_text(top, "Q")
        top.focus_force()
        root.update()
        top.event_generate("<KeyPress>", keysym="q")
        root.update()
        assert int(top.getvar(q_check.cget("variable"))) == 0
        digit_check = find_text(top, "4")
        top.event_generate("<KeyPress>", keysym="4")
        root.update()
        assert int(top.getvar(digit_check.cget("variable"))) == 1
        esc_check = find_text(top, "Esc")
        top.event_generate("<KeyPress>", keysym="Escape")
        root.update()
        assert top.winfo_exists()
        assert int(top.getvar(esc_check.cget("variable"))) == 1
        top.destroy()
        root.withdraw()
        editor.passage_vars["typing_zh"].set("四年级 · 生词 · 示例课文")
        editor.passage_vars["typing_zh"].set("四年级 · 生词 · 示例课文")
        combo = next(widget for widget in editor.controls
                     if isinstance(widget, ttk.Combobox)
                     and widget.cget("textvariable") == str(editor.passage_vars["typing_zh"]))
        combo.event_generate("<<ComboboxSelected>>")
        text_widget = next(widget for field, widget, var in editor.fields["typing_zh"]
                           if field["key"] == "text")
        assert "春天来了" in text_widget.get("1.0", "end")
        config = editor.get_config()
        assert config["key_press"]["enabled"] is False
        assert config["key_press"]["keys"] == ["q", "w"]
        print("[OK] 打字开关折叠、屏幕按键与范文选择正常")
    finally:
        root.destroy()


if __name__ == "__main__":
    main()
