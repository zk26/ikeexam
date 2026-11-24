# -*- coding: utf-8 -*-
"""
生成题目配图（WPS 工具栏按钮 / 文件图标 等）

题目里用  img:文件名.png 说明文字  引用，学生端会把图显示在选项或题干里。
图片输出到项目根目录的 qimg/（打包 EXE 时会一起打进去，首次运行复制到 exe 旁边）。

跑法：python 题库/生成题目图片.py
依赖：Pillow（pip install pillow）
"""
import os
import sys

from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "qimg")

BG = (255, 255, 255)
BTN_BG = (243, 244, 246)
BTN_LINE = (203, 213, 225)
INK = (15, 23, 42)
BLUE = (37, 99, 235)
RED = (220, 38, 38)
GREEN = (21, 128, 61)


def _font(size, bold=False):
    """找一个能显示中文的字体（Windows 自带微软雅黑/黑体）"""
    cands = []
    windir = os.environ.get("WINDIR", r"C:\Windows")
    if bold:
        cands += [os.path.join(windir, "Fonts", "msyhbd.ttc"),
                  os.path.join(windir, "Fonts", "simhei.ttf")]
    cands += [os.path.join(windir, "Fonts", "msyh.ttc"),
              os.path.join(windir, "Fonts", "simhei.ttf"),
              os.path.join(windir, "Fonts", "simsun.ttc"),
              os.path.join(windir, "Fonts", "arial.ttf")]
    for p in cands:
        if os.path.isfile(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                continue
    return ImageFont.load_default()


def _rounded(draw, box, radius, fill, outline=None, width=1):
    draw.rounded_rectangle(box, radius=radius, fill=fill,
                           outline=outline, width=width)


def button(name, text, w=120, h=44, fg=INK, bold=True, size=24, bg=BTN_BG):
    """普通工具栏按钮：圆角矩形 + 文字"""
    img = Image.new("RGB", (w, h), BG)
    d = ImageDraw.Draw(img)
    _rounded(d, (1, 1, w - 2, h - 2), 8, bg, BTN_LINE, 2)
    f = _font(size, bold)
    tw = d.textlength(text, font=f)
    d.text(((w - tw) / 2, (h - size * 1.35) / 2), text, font=f, fill=fg)
    img.save(os.path.join(OUT, name))
    print("  ", name)


def icon_line(name, lines, align="center", w=64, h=64, color=INK, lw=4):
    """
    用几条横线画对齐图标（左/中/右对齐、加粗等）
    align: left / center / right / justify
    """
    img = Image.new("RGB", (w, h), BG)
    d = ImageDraw.Draw(img)
    _rounded(d, (1, 1, w - 2, h - 2), 8, BTN_BG, BTN_LINE, 2)
    ys = [16, 30, 44]
    widths = [40, 28, 36]
    if len(lines) == 1:                      # 只有一条时画粗线（加粗用）
        ys, widths = [30], [lines[0]]
    for i, y in enumerate(ys[:len(lines)]):
        L = lines[i]
        if align == "left":
            x0 = 12
        elif align == "right":
            x0 = w - 12 - L
        elif align == "justify":
            x0 = 12
            L = w - 24
        else:
            x0 = (w - L) / 2
        d.rounded_rectangle((x0, y, x0 + L, y + 5), radius=2, fill=color)
    img.save(os.path.join(OUT, name))
    print("  ", name)


def icon_bold():
    """加粗按钮：粗体的 B"""
    w, h = 64, 64
    img = Image.new("RGB", (w, h), BG)
    d = ImageDraw.Draw(img)
    _rounded(d, (1, 1, w - 2, h - 2), 8, BTN_BG, BTN_LINE, 2)
    f = _font(38, True)
    tw = d.textlength("B", font=f)
    d.text(((w - tw) / 2, 8), "B", font=f, fill=INK)
    img.save(os.path.join(OUT, "btn_bold.png"))
    print("   btn_bold.png")


def icon_italic():
    w, h = 64, 64
    img = Image.new("RGB", (w, h), BG)
    d = ImageDraw.Draw(img)
    _rounded(d, (1, 1, w - 2, h - 2), 8, BTN_BG, BTN_LINE, 2)
    f = _font(38, True)
    tw = d.textlength("I", font=f)
    d.text(((w - tw) / 2, 8), "I", font=f, fill=INK)
    d.line((38, 14, 26, 50), fill=INK, width=3)     # 斜线
    img.save(os.path.join(OUT, "btn_italic.png"))
    print("   btn_italic.png")


def icon_underline():
    w, h = 64, 64
    img = Image.new("RGB", (w, h), BG)
    d = ImageDraw.Draw(img)
    _rounded(d, (1, 1, w - 2, h - 2), 8, BTN_BG, BTN_LINE, 2)
    f = _font(38, True)
    tw = d.textlength("U", font=f)
    d.text(((w - tw) / 2, 6), "U", font=f, fill=INK)
    d.line((16, 52, 48, 52), fill=INK, width=4)
    img.save(os.path.join(OUT, "btn_underline.png"))
    print("   btn_underline.png")


def icon_color():
    """字体颜色：A + 红色下划线"""
    w, h = 64, 64
    img = Image.new("RGB", (w, h), BG)
    d = ImageDraw.Draw(img)
    _rounded(d, (1, 1, w - 2, h - 2), 8, BTN_BG, BTN_LINE, 2)
    f = _font(36, True)
    tw = d.textlength("A", font=f)
    d.text(((w - tw) / 2, 8), "A", font=f, fill=INK)
    d.rounded_rectangle((14, 48, 50, 56), radius=2, fill=RED)
    img.save(os.path.join(OUT, "btn_color.png"))
    print("   btn_color.png")


def icon_sum():
    """自动求和：Σ"""
    w, h = 64, 64
    img = Image.new("RGB", (w, h), BG)
    d = ImageDraw.Draw(img)
    _rounded(d, (1, 1, w - 2, h - 2), 8, BTN_BG, BTN_LINE, 2)
    f = _font(40, True)
    tw = d.textlength("Σ", font=f)
    d.text(((w - tw) / 2, 6), "Σ", font=f, fill=GREEN)
    img.save(os.path.join(OUT, "btn_sum.png"))
    print("   btn_sum.png")


def icon_grid(name, rows, cols, w=64, h=64, fill_cells=(), color=INK):
    """表格图标：画 rows×cols 的格子；fill_cells 里填色的格子高亮"""
    img = Image.new("RGB", (w, h), BG)
    d = ImageDraw.Draw(img)
    _rounded(d, (1, 1, w - 2, h - 2), 8, BTN_BG, BTN_LINE, 2)
    x0, y0, x1, y1 = 14, 14, w - 14, h - 14
    cw = (x1 - x0) / cols
    ch = (y1 - y0) / rows
    for r in range(rows):
        for c in range(cols):
            box = (x0 + c * cw, y0 + r * ch, x0 + (c + 1) * cw, y0 + (r + 1) * ch)
            bg = BLUE if (r, c) in fill_cells else None
            d.rectangle(box, outline=color, width=2, fill=bg)
    img.save(os.path.join(OUT, name))
    print("  ", name)


def icon_arrow_sort(name, up=True, w=64, h=64):
    """升/降序排序：箭头 + 短横线"""
    img = Image.new("RGB", (w, h), BG)
    d = ImageDraw.Draw(img)
    _rounded(d, (1, 1, w - 2, h - 2), 8, BTN_BG, BTN_LINE, 2)
    d.line((44, 18, 44, 46), fill=BLUE, width=5)
    if up:
        d.polygon([(44, 12), (36, 24), (52, 24)], fill=BLUE)
    else:
        d.polygon([(44, 52), (36, 40), (52, 40)], fill=BLUE)
    for y, ln in ((22, 20), (32, 14), (42, 17)):
        d.rounded_rectangle((14, y, 14 + ln, y + 4), radius=2, fill=INK)
    img.save(os.path.join(OUT, name))
    print("  ", name)


def icon_doc(name, kind="doc"):
    """文档 / 文件夹 / 表格 图标（拖拽题用）"""
    w, h = 64, 64
    img = Image.new("RGB", (w, h), BG)
    d = ImageDraw.Draw(img)
    if kind == "folder":
        d.rounded_rectangle((8, 18, 56, 50), radius=5, fill=(250, 204, 21),
                            outline=(202, 138, 4), width=2)
        d.rounded_rectangle((8, 12, 28, 24), radius=4, fill=(253, 224, 71),
                            outline=(202, 138, 4), width=2)
    else:
        d.rounded_rectangle((14, 8, 50, 56), radius=4, fill=(248, 250, 252),
                            outline=(100, 116, 139), width=2)
        d.line((22, 20, 42, 20), fill=(100, 116, 139), width=2)
        d.line((22, 30, 42, 30), fill=(100, 116, 139), width=2)
        d.line((22, 40, 36, 40), fill=(100, 116, 139), width=2)
        if kind == "excel":
            d.rectangle((22, 44, 42, 52), outline=GREEN, width=2)
    img.save(os.path.join(OUT, name))
    print("  ", name)


def main():
    os.makedirs(OUT, exist_ok=True)
    print("生成题目配图 ->", OUT)
    # 工具栏按钮（带文字）
    button("btn_save.png", "保存", size=22)
    button("btn_new.png", "新建", size=22)
    button("btn_open.png", "打开", size=22)
    button("btn_font.png", "宋体 字", size=22)
    button("btn_size.png", "字号 二号", size=20)
    button("btn_merge.png", "合并单元格", size=18)
    button("btn_center_text.png", "居中", size=22)
    button("btn_wrap.png", "自动换行", size=20)
    button("btn_chart.png", "插入图表", size=20)
    button("btn_sum_text.png", "自动求和", size=20)
    button("btn_page.png", "纸张大小", size=20)
    button("btn_header.png", "页眉", size=22)
    # 图标类
    icon_bold()
    icon_italic()
    icon_underline()
    icon_color()
    icon_sum()
    icon_line("btn_align_left.png", [24], align="left")
    icon_line("btn_align_center.png", [28], align="center")
    icon_line("btn_align_right.png", [24], align="right")
    icon_line("btn_align_both.png", [None], align="justify")
    icon_grid("btn_merge_cells.png", 2, 3, fill_cells={(0, 0), (0, 1), (0, 2)})
    icon_grid("btn_table.png", 3, 3)
    icon_arrow_sort("btn_sort_up.png", up=True)
    icon_arrow_sort("btn_sort_down.png", up=False)
    icon_doc("icon_folder.png", "folder")
    icon_doc("icon_doc.png", "doc")
    icon_doc("icon_excel.png", "excel")
    print("完成，共", len(os.listdir(OUT)), "个文件")


if __name__ == "__main__":
    sys.exit(main())
