# -*- coding: utf-8 -*-
"""Memphis 桌面主题组件回归。"""
import os
import sys
import tkinter as tk

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
        root.update()
        assert banner.find_all()
        print("[OK] Memphis 主题、几何横幅与硬阴影组件正常")
    finally:
        root.destroy()


if __name__ == "__main__":
    main()
