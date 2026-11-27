# -*- coding: utf-8 -*-
"""老师端打字配置交互回归。"""
import os
import sys
import tkinter as tk
from tkinter import ttk

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

from ikeexam.shared import ui_theme
from ikeexam.teacher.visual_config import TypingConfigEditor


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
        picker._open()
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

        collect(tops[-1])
        assert {"Esc", "Tab", "Del", "↑", "↓", "←", "→"} <= set(labels), labels
        tops[-1].destroy()
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
