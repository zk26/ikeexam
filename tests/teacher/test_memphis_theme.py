# -*- coding: utf-8 -*-
"""Memphis 桌面主题组件回归。"""
import os
import sys
import tkinter as tk
from tkinter import ttk

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

from ikeexam.shared import ui_theme


def main():
    root = tk.Tk()
    root.withdraw()
    try:
        tokens = ui_theme.apply_theme(root)
        assert tokens["bg"] == "#FCF8F2"
        assert tokens["border"] == "#27324A"
        banner = ui_theme.MemphisBanner(
            root, title="阳光考试季", subtitle="认真作答，你也可以很棒",
            height=78, tokens=tokens)
        banner.pack(fill="x")
        banner.configure(width=600)
        banner._draw()
        card = tk.Frame(root, bg=tokens["surface"], width=320, height=120,
                        highlightbackground=tokens["border"], highlightthickness=2)
        card.pack()
        ui_theme.add_hard_shadow(card, 6, tokens["shadow"])
        listbox = tk.Listbox(root)
        for index in range(50):
            listbox.insert("end", f"item {index}")
        ui_theme.bind_mousewheel(listbox, listbox)
        listbox.event_generate("<MouseWheel>", delta=-120)
        tree = ttk.Treeview(root, columns=("name",), show="headings")
        tree.heading("name", text="名称")
        tree.column("name", width=50)
        tree.insert("", "end", values=("这是一列需要自动适应的很长试题名称",))
        ui_theme.autofit_tree_column(tree, "#1")
        assert int(tree.column("name", "width")) > 50
        root.update()
        assert banner.find_all()
        print("[OK] Memphis 主题、几何横幅与硬阴影组件正常")
    finally:
        root.destroy()


if __name__ == "__main__":
    main()
