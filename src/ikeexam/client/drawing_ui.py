# -*- coding: utf-8 -*-
"""
画板创作（drawing）—— 学生端

考试最后一题：在画板上画图（电路图 / 网络拓扑 / 实验装置…），
最后导出**一张压缩图片**上传给老师。

工具：选择/拖动、画笔、橡皮、直线、矩形、圆、三角形、箭头、文字
元件库：电学（电阻/灯泡/开关/电源/电流表/电压表）
        网络（路由器/交换机/PC/服务器/防火墙/无线AP）
        化学（烧杯/试管/锥形瓶/酒精灯/量筒/试剂瓶）
画布内容存在 client.drawing_state() 里，切标签页不丢画。
导出走对象模型重画（PIL），和屏幕显示同一套图元描述，所见即所得。
"""
import io
import os
import sys
import time
import tkinter as tk
from tkinter import messagebox, simpledialog

SRC_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)
from ikeexam.shared import ui_theme

_UI = ui_theme.LIGHT
FONT = ui_theme.pick_family()
BG = _UI["bg"]
CARD = _UI["surface"]
BLUE = _UI["primary"]
BLUE_D = _UI["primary_hover"]
GREEN = _UI["success"]
RED = _UI["danger"]
GRAY = _UI["text_muted"]
INK = _UI["text_strong"]
LINE = _UI["border"]

CANVAS_W, CANVAS_H = 1180, 640
COMP_SIZE = 56                 # 元件单位尺寸（px）
PALETTE = ["#0f172a", "#dc2626", "#2563eb", "#15803d",
           "#c2410c", "#7c3aed", "#0e7490", "#a16207"]

# ============================================================
# 元件定义：单位坐标（-1..1）图元列表，Canvas 和 PIL 共用同一套
#   ("line", x1, y1, x2, y2)
#   ("rect", x1, y1, x2, y2)      仅描边
#   ("oval", x1, y1, x2, y2)
#   ("poly", [(x, y), ...])
#   ("arc",  x1, y1, x2, y2, start, end)
#   ("text", x, y, "字", 字号倍数)
# ============================================================
COMPONENTS = {
    # ---------- 电学 ----------
    "resistor": {"name": "电阻", "cat": "elec", "shapes": [
        ("rect", -0.45, -0.24, 0.45, 0.24),
        ("line", -1.0, 0, -0.45, 0), ("line", 0.45, 0, 1.0, 0),
    ]},
    "lamp": {"name": "灯泡", "cat": "elec", "shapes": [
        ("oval", -0.38, -0.38, 0.38, 0.38),
        ("line", -0.27, -0.27, 0.27, 0.27), ("line", -0.27, 0.27, 0.27, -0.27),
        ("line", -1.0, 0, -0.38, 0), ("line", 0.38, 0, 1.0, 0),
    ]},
    "switch": {"name": "开关", "cat": "elec", "shapes": [
        ("line", -1.0, 0.18, -0.5, 0.18), ("line", -0.5, 0.18, 0.45, -0.28),
        ("line", 0.5, 0.18, 1.0, 0.18),
        ("oval", -0.58, 0.10, -0.42, 0.26), ("oval", 0.42, 0.10, 0.58, 0.26),
    ]},
    "battery": {"name": "电源", "cat": "elec", "shapes": [
        ("line", -1.0, 0, -0.3, 0), ("line", 0.3, 0, 1.0, 0),
        ("line", -0.3, -0.42, -0.3, 0.42), ("line", 0.12, -0.24, 0.12, 0.24),
        ("line", -0.06, -0.42, -0.06, 0.42), ("line", 0.3, -0.24, 0.3, 0.24),
    ]},
    "ammeter": {"name": "电流表", "cat": "elec", "shapes": [
        ("oval", -0.42, -0.42, 0.42, 0.42),
        ("text", 0, 0, "A", 0.5),
        ("line", -1.0, 0, -0.42, 0), ("line", 0.42, 0, 1.0, 0),
    ]},
    "voltmeter": {"name": "电压表", "cat": "elec", "shapes": [
        ("oval", -0.42, -0.42, 0.42, 0.42),
        ("text", 0, 0, "V", 0.5),
        ("line", -1.0, 0, -0.42, 0), ("line", 0.42, 0, 1.0, 0),
    ]},
    # ---------- 网络 ----------
    "router": {"name": "路由器", "cat": "net", "shapes": [
        ("oval", -0.9, -0.18, 0.9, 0.18),
        ("rect", -0.9, -0.34, 0.9, 0.18),
        ("oval", -0.9, -0.5, 0.9, -0.18),
        ("line", -0.35, -0.06, 0.35, -0.06),
        ("poly", [(-0.12, -0.2), (0.12, -0.2), (0, -0.36)]),
        ("poly", [(-0.12, 0.06), (0.12, 0.06), (0, 0.22)]),
    ]},
    "switch_hub": {"name": "交换机", "cat": "net", "shapes": [
        ("rect", -0.95, -0.32, 0.95, 0.32),
        ("line", -0.6, -0.1, -0.15, -0.1), ("line", -0.3, -0.22, -0.15, -0.1),
        ("line", -0.3, 0.02, -0.15, -0.1),
        ("line", 0.15, 0.1, 0.6, 0.1), ("line", 0.3, -0.02, 0.15, 0.1),
        ("line", 0.3, 0.22, 0.15, 0.1),
    ]},
    "pc": {"name": "电脑", "cat": "net", "shapes": [
        ("rect", -0.62, -0.52, 0.62, 0.22),
        ("line", -0.42, 0.12, 0.42, 0.12),
        ("line", -0.22, 0.22, -0.22, 0.42), ("line", 0.22, 0.22, 0.22, 0.42),
        ("line", -0.5, 0.42, 0.5, 0.42),
    ]},
    "server": {"name": "服务器", "cat": "net", "shapes": [
        ("rect", -0.42, -0.72, 0.42, 0.72),
        ("line", -0.42, -0.38, 0.42, -0.38), ("line", -0.42, -0.04, 0.42, -0.04),
        ("line", -0.42, 0.30, 0.42, 0.30),
        ("oval", -0.28, -0.6, -0.18, -0.5), ("oval", -0.28, -0.26, -0.18, -0.16),
        ("oval", -0.28, 0.08, -0.18, 0.18), ("oval", -0.28, 0.42, -0.18, 0.52),
    ]},
    "firewall": {"name": "防火墙", "cat": "net", "shapes": [
        ("rect", -0.85, -0.55, 0.85, 0.55),
        ("line", -0.85, -0.18, 0.85, -0.18), ("line", -0.85, 0.18, 0.85, 0.18),
        ("line", -0.28, -0.55, -0.28, -0.18), ("line", 0.28, -0.55, 0.28, -0.18),
        ("line", -0.56, -0.18, -0.56, 0.18), ("line", 0.56, -0.18, 0.56, 0.18),
        ("line", -0.28, 0.18, -0.28, 0.55), ("line", 0.28, 0.18, 0.28, 0.55),
    ]},
    "ap": {"name": "无线AP", "cat": "net", "shapes": [
        ("oval", -0.3, -0.02, 0.3, 0.5),
        ("arc", -0.75, -0.85, 0.75, 0.35, 200, 340),
        ("arc", -0.5, -0.6, 0.5, 0.25, 200, 340),
    ]},
    # ---------- 化学 ----------
    "beaker": {"name": "烧杯", "cat": "chem", "shapes": [
        ("line", -0.5, -0.62, -0.5, 0.5), ("line", 0.5, -0.62, 0.5, 0.62),
        ("arc", -0.5, 0.18, 0.5, 0.88, 0, 180),
        ("line", -0.58, -0.62, -0.5, -0.62), ("line", 0.5, -0.62, 0.58, -0.62),
        ("line", -0.5, -0.1, -0.36, -0.1), ("line", 0.5, -0.1, 0.36, -0.1),
        ("line", -0.5, 0.18, 0.5, 0.18),
    ]},
    "test_tube": {"name": "试管", "cat": "chem", "shapes": [
        ("line", -0.22, -0.85, -0.22, 0.55), ("line", 0.22, -0.85, 0.22, 0.62),
        ("arc", -0.22, 0.3, 0.22, 0.9, 0, 180),
        ("line", -0.3, -0.85, 0.3, -0.85),
        ("line", -0.22, 0.05, 0.22, 0.05),
    ]},
    "flask": {"name": "锥形瓶", "cat": "chem", "shapes": [
        ("line", -0.18, -0.75, -0.18, -0.42), ("line", 0.18, -0.75, 0.18, -0.42),
        ("line", -0.18, -0.75, 0.18, -0.75),
        ("line", -0.18, -0.42, -0.52, 0.52), ("line", 0.18, -0.42, 0.52, 0.52),
        ("line", -0.52, 0.52, 0.52, 0.52),
        ("line", -0.35, 0.1, 0.35, 0.1),
    ]},
    "alcohol_lamp": {"name": "酒精灯", "cat": "chem", "shapes": [
        ("arc", -0.42, -0.05, 0.42, 0.85, 0, 180),
        ("line", -0.42, -0.05, -0.42, 0.18), ("line", 0.42, -0.05, 0.42, 0.18),
        ("line", -0.42, 0.18, -0.2, 0.18), ("line", 0.42, 0.18, 0.2, 0.18),
        ("line", -0.2, 0.18, -0.2, -0.12), ("line", 0.2, 0.18, 0.2, -0.12),
        ("line", -0.2, -0.12, 0.2, -0.12),
        ("line", -0.05, -0.12, -0.05, -0.3), ("line", 0.05, -0.12, 0.05, -0.3),
        ("poly", [(0, -0.82), (-0.12, -0.42), (0.12, -0.42)]),
    ]},
    "cylinder": {"name": "量筒", "cat": "chem", "shapes": [
        ("line", -0.28, -0.8, -0.28, 0.62), ("line", 0.28, -0.8, 0.28, 0.68),
        ("arc", -0.28, 0.36, 0.28, 0.94, 0, 180),
        ("line", -0.34, -0.8, -0.28, -0.8), ("line", 0.28, -0.8, 0.34, -0.8),
        ("line", -0.28, -0.45, -0.12, -0.45), ("line", -0.28, -0.15, -0.12, -0.15),
        ("line", -0.28, 0.15, -0.12, 0.15),
    ]},
    "bottle": {"name": "试剂瓶", "cat": "chem", "shapes": [
        ("rect", -0.12, -0.85, 0.12, -0.58),
        ("line", -0.12, -0.58, -0.5, -0.28), ("line", 0.12, -0.58, 0.5, -0.28),
        ("line", -0.5, -0.28, -0.5, 0.7), ("line", 0.5, -0.28, 0.5, 0.7),
        ("line", -0.5, 0.7, 0.5, 0.7),
        ("rect", -0.3, -0.05, 0.3, 0.3),
    ]},
}

CATS = [("elec", "电学元件"), ("net", "网络元件"), ("chem", "化学仪器")]

TOOLS = [
    ("select", "选择/拖动"), ("pen", "画笔"), ("eraser", "橡皮"),
    ("line", "直线"), ("rect", "矩形"), ("oval", "圆"),
    ("triangle", "三角形"), ("arrow", "箭头"), ("text", "文字"),
]


# ============================================================
# 图元 → 统一绘制操作（Canvas / PIL 两套渲染器共用）
# ============================================================
def item_ops(it):
    """把一个画布对象翻译成 (op, 坐标…, 样式) 列表"""
    color = it.get("color") or INK
    width = max(1, int(it.get("width") or 2))
    fill = it.get("fill")
    kind = it.get("kind")
    if kind == "stroke":
        pts = it.get("pts") or []
        ops = []
        for i in range(1, len(pts)):
            ops.append(("line", pts[i - 1][0], pts[i - 1][1],
                        pts[i][0], pts[i][1], {"fill": color, "width": width}))
        if len(pts) == 1:
            x, y = pts[0]
            ops.append(("line", x, y, x + 1, y, {"fill": color, "width": width}))
        return ops
    if kind in ("line", "arrow"):
        x1, y1, x2, y2 = it["x1"], it["y1"], it["x2"], it["y2"]
        ops = [("line", x1, y1, x2, y2, {"fill": color, "width": width})]
        if kind == "arrow":
            import math
            ang = math.atan2(y2 - y1, x2 - x1)
            ah = max(10, width * 4)
            for da in (2.6, -2.6):
                ops.append(("line", x2, y2,
                            x2 - ah * math.cos(ang + da) * 0.9,
                            y2 - ah * math.sin(ang + da) * 0.9,
                            {"fill": color, "width": width}))
        return ops
    if kind in ("rect", "oval"):
        x1, x2 = sorted((it["x1"], it["x2"]))
        y1, y2 = sorted((it["y1"], it["y2"]))
        op = "rect" if kind == "rect" else "oval"
        return [(op, x1, y1, x2, y2,
                 {"outline": color, "width": width, "fill": fill})]
    if kind == "triangle":
        x1, y1, x2, y2 = it["x1"], it["y1"], it["x2"], it["y2"]
        tx = (x1 + x2) / 2
        pts = [(tx, y1), (x1, y2), (x2, y2)]
        return [("poly", pts, {"outline": color, "width": width, "fill": fill})]
    if kind == "text":
        size = int(it.get("size") or 16)
        return [("text", it["x"], it["y"], it.get("text") or "",
                 {"fill": color, "size": size})]
    if kind == "comp":
        return comp_ops(it.get("comp") or "resistor",
                        it.get("x") or 0, it.get("y") or 0,
                        it.get("s") or COMP_SIZE, color)
    return []


def comp_ops(comp, x, y, s, color):
    """元件定义 → 世界坐标图元"""
    d = COMPONENTS.get(comp)
    if not d:
        return []
    ops = []
    for sh in d["shapes"]:
        t = sh[0]
        if t == "line":
            ops.append(("line", x + sh[1] * s, y + sh[2] * s,
                        x + sh[3] * s, y + sh[4] * s,
                        {"fill": color, "width": 2}))
        elif t == "rect":
            ops.append(("rect", x + sh[1] * s, y + sh[2] * s,
                        x + sh[3] * s, y + sh[4] * s,
                        {"outline": color, "width": 2, "fill": None}))
        elif t == "oval":
            ops.append(("oval", x + sh[1] * s, y + sh[2] * s,
                        x + sh[3] * s, y + sh[4] * s,
                        {"outline": color, "width": 2, "fill": None}))
        elif t == "poly":
            ops.append(("poly", [(x + px * s, y + py * s) for px, py in sh[1]],
                        {"outline": color, "width": 2, "fill": None}))
        elif t == "arc":
            ops.append(("arc", x + sh[1] * s, y + sh[2] * s,
                        x + sh[3] * s, y + sh[4] * s, sh[5], sh[6],
                        {"outline": color, "width": 2}))
        elif t == "text":
            ops.append(("text", x + sh[1] * s, y + sh[2] * s, sh[3],
                        {"fill": color, "size": max(10, int(s * sh[4] * 0.6))}))
    # 元件名标注（小字，画在元件下方）
    ops.append(("text", x, y + s * 1.02, d["name"],
                {"fill": GRAY, "size": 11}))
    return ops


def item_bbox(it):
    """命中检测用的包围盒（放大约 8px 容错）"""
    xs, ys = [], []
    for op in item_ops(it):
        if op[0] in ("line", "rect", "oval"):
            xs += [op[1], op[3]]
            ys += [op[2], op[4]]
        elif op[0] == "poly":
            xs += [p[0] for p in op[1]]
            ys += [p[1] for p in op[1]]
        elif op[0] == "arc":
            xs += [op[1], op[3]]
            ys += [op[2], op[4]]
        elif op[0] == "text":
            xs += [op[1] - 20, op[1] + 20]
            ys += [op[2] - 14, op[2] + 14]
    if not xs:
        return (0, 0, 0, 0)
    return (min(xs) - 8, min(ys) - 8, max(xs) + 8, max(ys) + 8)


def hit_test(items, x, y):
    """从上往下找命中的对象下标（后画的在上面）"""
    for i in range(len(items) - 1, -1, -1):
        x1, y1, x2, y2 = item_bbox(items[i])
        if x1 <= x <= x2 and y1 <= y <= y2:
            return i
    return None


def draw_ops_canvas(cv, ops, tags):
    for op in ops:
        t = op[0]
        if t == "line":
            cv.create_line(op[1], op[2], op[3], op[4],
                           fill=op[5]["fill"], width=op[5]["width"],
                           tags=tags, capstyle="round")
        elif t == "rect":
            cv.create_rectangle(op[1], op[2], op[3], op[4],
                                outline=op[5]["outline"], width=op[5]["width"],
                                fill=op[5].get("fill") or "", tags=tags)
        elif t == "oval":
            cv.create_oval(op[1], op[2], op[3], op[4],
                           outline=op[5]["outline"], width=op[5]["width"],
                           fill=op[5].get("fill") or "", tags=tags)
        elif t == "poly":
            flat = [c for p in op[1] for c in p]
            cv.create_polygon(*flat, outline=op[2]["outline"],
                              width=op[2]["width"],
                              fill=op[2].get("fill") or "", tags=tags)
        elif t == "arc":
            cv.create_arc(op[1], op[2], op[3], op[4],
                          start=op[5], extent=op[6] - op[5],
                          style="arc", outline=op[7]["outline"],
                          width=op[7]["width"], tags=tags)
        elif t == "text":
            cv.create_text(op[1], op[2], text=op[3], fill=op[4]["fill"],
                           font=(FONT, op[4]["size"]), tags=tags)


def _pil_font(size):
    try:
        from PIL import ImageFont
        for name in ("msyh.ttc", "msyhbd.ttc", "simhei.ttf", "arial.ttf"):
            try:
                return ImageFont.truetype(name, size)
            except Exception:
                continue
        return ImageFont.load_default()
    except Exception:
        return None


def render_png(items, w=CANVAS_W, h=CANVAS_H, scale=1):
    """把画布对象重画成 PNG 字节（压缩：optimize + 尺寸上限）"""
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (int(w * scale), int(h * scale)), "white")
    d = ImageDraw.Draw(img)
    fcache = {}

    def font(size):
        size = max(9, int(size * scale))
        if size not in fcache:
            fcache[size] = _pil_font(size)
        return fcache[size]

    for it in items:
        for op in item_ops(it):
            t = op[0]
            if t == "line":
                d.line([op[1] * scale, op[2] * scale,
                        op[3] * scale, op[4] * scale],
                       fill=op[5]["fill"], width=max(1, int(op[5]["width"] * scale)))
            elif t == "rect":
                d.rectangle([op[1] * scale, op[2] * scale,
                             op[3] * scale, op[4] * scale],
                            outline=op[5]["outline"],
                            width=max(1, int(op[5]["width"] * scale)),
                            fill=op[5].get("fill"))
            elif t == "oval":
                d.ellipse([op[1] * scale, op[2] * scale,
                           op[3] * scale, op[4] * scale],
                          outline=op[5]["outline"],
                          width=max(1, int(op[5]["width"] * scale)),
                          fill=op[5].get("fill"))
            elif t == "poly":
                pts = [(p[0] * scale, p[1] * scale) for p in op[1]]
                d.polygon(pts, outline=op[2]["outline"],
                          fill=op[2].get("fill"))
                # PIL 的 polygon 不画描边宽度，用线再描一圈
                if op[2].get("fill") is None:
                    d.line(pts + [pts[0]], fill=op[2]["outline"],
                           width=max(1, int(op[2]["width"] * scale)))
            elif t == "arc":
                d.arc([op[1] * scale, op[2] * scale,
                       op[3] * scale, op[4] * scale],
                      op[5], op[6], fill=op[7]["outline"],
                      width=max(1, int(op[7]["width"] * scale)))
            elif t == "text":
                f = font(op[4]["size"])
                d.text((op[1] * scale, op[2] * scale), op[3],
                       fill=op[4]["fill"], font=f, anchor="mm")

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    data = buf.getvalue()
    if len(data) > 900 * 1024:          # 还是太大 → 缩一点再存 JPEG
        img2 = img.resize((int(img.width * 0.8), int(img.height * 0.8)))
        buf = io.BytesIO()
        img2.save(buf, format="JPEG", quality=82)
        return buf.getvalue(), ".jpg"
    return data, ".png"


def state_has_art(state):
    """drawing_state(kind) 里有没有作品（自由画 items 或 拼豆 beads）"""
    if state.get("items"):
        return True
    return any(any(row) for row in (state.get("beads") or []))


def export_state(state):
    """
    把某道画图小题的 drawing_state(kind) 导出成压缩图 → (bytes, 扩展名)。
    按 kind 分发：beads=拼豆（beads 格子），draw=自由画（items）——
    自动保存 / 交卷 / 老师强制收卷都走这一个入口。
    """
    # 导出在后台线程执行（交卷/老师强制收卷时），主线程学生此刻可能还在作画：
    # 直接遍历共享的 items 会因「迭代中改列表」抛 RuntimeError，beads 也会读到
    # 半成品状态。先快照一份再渲染，避免竞态。
    items = list(state.get("items") or [])
    beads = state.get("beads")
    if state.get("kind") == "beads" or (not items and beads):
        from beads_ui import render_beads_png
        return render_beads_png([row[:] for row in (beads or [])])
    return render_png(items)


# ============================================================
# 面板
# ============================================================
class AutoSaveMixin:
    """
    右上角「上一题 / 保存」+ 保存指示灯 + 改动防抖自动保存（自由画 / 拼豆共用）。
    子类需要：tk 组件（有 after）、export_png()、_has_art()、_set_status()，
    并在建工具栏时调 _build_save_cluster(bar)、在 __init__ 里调 _init_autosave()。
    """
    SAVE_DELAY = 900                    # 改动停 0.9 秒就自动存一份

    def _init_autosave(self, on_save, on_prev, on_next=None, next_label=None):
        self.on_save = on_save         # 保存回调 on_save(panel, done)（gui 负责导出上传）
        self.on_prev = on_prev         # 「上一题」回调
        self.on_next = on_next         # 「下一题」回调（None 则不显示）
        self.next_label = next_label or "下一题 ▶"
        self._save_after = None
        self._saving = False
        self._dirty = False
        self._resave = False

    def _cleanup(self, event=None):
        """
        面板销毁时取消未触发的自动保存定时器。
        ⚠️ 不取消的话，切题/交卷销毁面板后，900ms 的 after 仍会触发 _auto_save，
        接着去操作已销毁的 save_dot / status 控件，抛 TclError。
        """
        if event is not None and event.widget is not self:
            return
        if getattr(self, "_save_after", None) is not None:
            try:
                self.after_cancel(self._save_after)
            except Exception:
                pass
            self._save_after = None

    def _build_save_cluster(self, bar):
        """右上角操作区：「上一题 / 下一题 / 保存」+ 保存指示灯"""
        self.save_dot = tk.Label(bar, text="● 空白", bg=BG, fg=GRAY,
                                 font=(FONT, 10), padx=2)
        self.save_dot.pack(side="right", padx=(10, 2))
        tk.Button(bar, text="保存", command=self._save_now, bg=GREEN, fg="white",
                  font=(FONT, 11, "bold"), padx=14, pady=3, relief="flat",
                  cursor="hand2", activebackground="#166534"
                  ).pack(side="right", padx=(0, 6))
        if self.on_next:
            tk.Button(bar, text=self.next_label, command=self._next, bg=BLUE,
                      fg="white", font=(FONT, 11, "bold"), padx=12, pady=3,
                      relief="flat", cursor="hand2", activebackground=BLUE_D
                      ).pack(side="right", padx=(0, 6))
        tk.Button(bar, text="◀ 上一题", command=self._prev, bg="white", fg=INK,
                  font=(FONT, 11), padx=12, pady=3, relief="solid", bd=1,
                  cursor="hand2").pack(side="right", padx=(0, 6))

    def _prev(self):
        """右上角「上一题」"""
        if self.on_prev:
            self.on_prev()

    def _next(self):
        """右上角「下一题」（跳下一个面板 / 去交卷）"""
        if self.on_next:
            self.on_next()

    def _set_save_dot(self, state):
        d = {"dirty": ("● 未保存", "#c2410c"),
             "saving": ("● 保存中…", GRAY),
             "saved": ("● 已保存", GREEN),
             "fail": ("● 保存失败", RED),
             "empty": ("● 空白", GRAY)}.get(state, ("●", GRAY))
        self.save_dot.config(text=d[0], fg=d[1])

    def _touch(self):
        """画布有新操作 → 指示灯转橙，防抖自动保存"""
        self._dirty = True
        self._set_save_dot("dirty")
        if self._save_after is not None:
            try:
                self.after_cancel(self._save_after)
            except Exception:
                pass
        self._save_after = self.after(self.SAVE_DELAY, self._auto_save)

    def _auto_save(self):
        self._save_after = None
        self._do_save()

    def _save_now(self):
        """右上角「保存」：立刻存一份"""
        if self._save_after is not None:
            try:
                self.after_cancel(self._save_after)
            except Exception:
                pass
            self._save_after = None
        self._do_save()

    def _do_save(self):
        if not self._has_art():
            self._dirty = False
            self._set_save_dot("empty")
            return
        if self._saving:
            self._resave = True          # 正在传 → 传完再补一份
            return
        self._saving = True
        self._dirty = False
        self._set_save_dot("saving")

        def done(ok, msg=""):
            self._saving = False
            if ok:
                self._set_save_dot("saved")
                self._set_status("作品已保存（交卷时自动带上最终版，可继续修改）")
            else:
                self._set_save_dot("fail")
                self._set_status(f"保存失败：{msg}；点「保存」或继续修改都会重试")
            if self._resave or self._dirty:
                self._resave = False
                self._touch()
        if self.on_save:
            self.on_save(self, done)
        else:
            self._saving = False
            self._dirty = False
            self._set_save_dot("saved")


class DrawingPanel(tk.Frame, AutoSaveMixin):
    # 上传用的小题类型：draw=画板创作（自由画）
    kind = "draw"

    def __init__(self, parent, client, cfg, on_save=None, on_prev=None,
                 on_next=None, next_label=None):
        super().__init__(parent, bg=BG)
        self.client = client
        self.cfg = cfg or {}
        self._init_autosave(on_save, on_prev, on_next, next_label)
        self.tool = "pen"
        self.color = PALETTE[0]
        self.width = 3
        self.pending_comp = None           # 元件模式：点了元件库还没落画布
        self.draft = None                  # 正在拖的预览对象
        self.sel = None                    # 选中对象下标
        self._drag = None
        self._pen_pts = None
        self.items = client.drawing_state(self.kind)["items"]
        self._moved = False
        self._backup = None              # 清空画布时的备份，Ctrl+Z 可恢复
        self.bind("<Destroy>", self._cleanup)

        brief = (self.cfg.get("brief") or "").strip()
        if brief:
            tip = tk.Label(self, text="画题要求：" + brief, bg="#eff6ff",
                           fg="#1d4ed8", font=(FONT, 12), justify="left",
                           wraplength=1100, anchor="w")
            tip.pack(fill="x", padx=12, pady=(10, 2))

        self._build_toolbar()
        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True, padx=12, pady=6)
        self._build_comp_panel(body)

        wrap = tk.Frame(body, bg=BG)
        wrap.pack(side="left", fill="both", expand=True)
        self.canvas = tk.Canvas(wrap, width=CANVAS_W, height=CANVAS_H,
                                bg="white", highlightthickness=2,
                                highlightbackground=LINE, cursor="crosshair")
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Button-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._motion)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.canvas.bind("<Delete>", lambda e: self._delete_sel())
        self.canvas.bind("<Control-z>", lambda e: self._undo())

        self.status = tk.Label(self, text="", bg=BG, fg=GRAY, font=(FONT, 11))
        self.status.pack(fill="x", padx=14, pady=(2, 8))
        self._set_status("选画笔直接画；选元件后点画布放置；「选择/拖动」可以移动和删除"
                         "（Delete）；Ctrl+Z 撤销；改动会自动保存")
        self.redraw()
        if self.items:
            self._touch()                  # 切回画板先补存一份
        else:
            self._set_save_dot("empty")

    # ---------- 界面 ----------
    def _build_toolbar(self):
        toolbar = tk.Frame(self, bg=BG)
        toolbar.pack(fill="x", padx=12, pady=(8, 2))
        left = tk.Frame(toolbar, bg=BG)
        left.pack(side="left", fill="x", expand=True)
        tools = tk.Frame(left, bg=BG)
        tools.pack(fill="x")
        self.tool_btns = {}
        for key, label in TOOLS:
            b = tk.Button(tools, text=label, command=lambda k=key: self._set_tool(k),
                          bg="white", fg=INK, font=(FONT, 10), padx=7, pady=3,
                          relief="solid", bd=1, cursor="hand2")
            b.pack(side="left", padx=2)
            self.tool_btns[key] = b
        styles = tk.Frame(left, bg=BG)
        styles.pack(fill="x", pady=(4, 0))
        tk.Label(styles, text="颜色", bg=BG, fg=GRAY,
                 font=(FONT, 10)).pack(side="left", padx=(0, 4))
        self.color_btns = []
        for c in PALETTE:
            b = tk.Button(styles, bg=c, width=2, command=lambda cc=c: self._set_color(cc),
                          relief="solid", bd=1, cursor="hand2",
                          activebackground=c)
            b.pack(side="left", padx=1)
            self.color_btns.append((c, b))
        tk.Label(styles, text="线宽", bg=BG, fg=GRAY,
                 font=(FONT, 10)).pack(side="left", padx=(12, 4))
        for w in (2, 3, 5):
            tk.Button(styles, text=str(w), command=lambda ww=w: self._set_width(ww),
                      bg="white", font=(FONT, 10), width=2, relief="solid",
                      bd=1, cursor="hand2").pack(side="left", padx=1)
        actions = tk.Frame(toolbar, bg=BG)
        actions.pack(side="right", padx=(8, 0))
        self._build_save_cluster(actions)

    def _build_comp_panel(self, body):
        panel = tk.Frame(body, bg=CARD, width=150, highlightthickness=1,
                         highlightbackground=LINE)
        panel.pack(side="left", fill="y", padx=(0, 8))
        panel.pack_propagate(False)
        tk.Label(panel, text="元件库", bg=CARD, fg=INK,
                 font=(FONT, 12, "bold")).pack(pady=(10, 4))
        self.comp_btns = []
        for cat, cat_name in CATS:
            tk.Label(panel, text=cat_name, bg=CARD, fg=GRAY,
                     font=(FONT, 10)).pack(anchor="w", padx=10, pady=(8, 2))
            for key, d in COMPONENTS.items():
                if d["cat"] != cat:
                    continue
                b = tk.Button(panel, text=d["name"],
                              command=lambda k=key: self._pick_comp(k),
                              bg="white", fg=INK, font=(FONT, 10), width=10,
                              relief="solid", bd=1, cursor="hand2")
                b.pack(padx=10, pady=1)
                self.comp_btns.append(b)

    def _set_status(self, msg):
        self.status.config(text=msg)

    def _set_tool(self, key):
        self.tool = key
        self.pending_comp = None
        for k, b in self.tool_btns.items():
            b.config(bg=(BLUE if k == key else "white"),
                     fg=("white" if k == key else INK))
        cur = {"pen": "画笔：按住左键画", "eraser": "橡皮：点一下删掉碰到的图形",
               "select": "选择：点中图形拖动移动，按 Delete 删除",
               "text": "文字：点画布，输入要写的字"}.get(key)
        if cur:
            self._set_status(cur)
        else:
            self._set_status("按住拖出图形，松手完成")
        self.canvas.config(cursor=("hand2" if key == "select"
                                   else ("circle" if key == "eraser" else "crosshair")))

    def _set_color(self, c):
        self.color = c
        for cc, b in self.color_btns:
            b.config(relief=("sunken" if cc == c else "solid"))

    def _set_width(self, w):
        self.width = w

    def _pick_comp(self, key):
        self.tool = "comp"
        self.pending_comp = key
        for k, b in self.tool_btns.items():
            b.config(bg="white", fg=INK)
        self._set_status(f"已选「{COMPONENTS[key]['name']}」—— 点画布任意位置放置")

    # ---------- 画布交互 ----------
    def _press(self, e):
        if self.tool == "comp" and self.pending_comp:
            self.items.append({"kind": "comp", "comp": self.pending_comp,
                               "x": e.x, "y": e.y, "s": COMP_SIZE,
                               "color": self.color})
            self.redraw()
            self._touch()
            return
        if self.tool == "text":
            txt = simpledialog.askstring("文字", "要写的字：", parent=self)
            if txt and txt.strip():
                self.items.append({"kind": "text", "x": e.x, "y": e.y,
                                   "text": txt.strip()[:40], "color": self.color,
                                   "size": 16})
                self.redraw()
                self._touch()
            return
        if self.tool == "pen":
            self._pen_pts = [(e.x, e.y)]
            return
        if self.tool == "eraser":
            self._erase_at(e.x, e.y)
            return
        if self.tool == "select":
            self.sel = hit_test(self.items, e.x, e.y)
            self._moved = False
            if self.sel is not None:
                self._drag = (e.x, e.y)
                self._set_status("拖动移动，按 Delete 删除")
            else:
                self._set_status("没点中图形")
            self.redraw()
            return
        # 形状工具：起点
        self._drag = (e.x, e.y)

    def _motion(self, e):
        if self.tool == "pen" and self._pen_pts is not None:
            self._pen_pts.append((e.x, e.y))
            pts = self._pen_pts
            if len(pts) >= 2:
                x1, y1 = pts[-2]
                self.canvas.create_line(x1, y1, e.x, e.y, fill=self.color,
                                        width=self.width, capstyle="round",
                                        tags="draft")
            return
        if self.tool == "eraser":
            self._erase_at(e.x, e.y)
            return
        if self.tool == "select" and self.sel is not None and self._drag:
            dx = e.x - self._drag[0]
            dy = e.y - self._drag[1]
            self._drag = (e.x, e.y)
            self._move_item(self.items[self.sel], dx, dy)
            self._moved = True
            self.redraw()
            return
        if self.tool in ("line", "rect", "oval", "triangle", "arrow") and self._drag:
            x0, y0 = self._drag
            self.canvas.delete("draft")
            draft = {"kind": self.tool, "x1": x0, "y1": y0,
                     "x2": e.x, "y2": e.y, "color": self.color,
                     "width": self.width}
            draw_ops_canvas(self.canvas, item_ops(draft), "draft")

    def _release(self, e):
        if self.tool == "pen" and self._pen_pts is not None:
            pts = self._pen_pts
            self._pen_pts = None
            if len(pts) >= 2:
                self.items.append({"kind": "stroke", "pts": pts,
                                   "color": self.color, "width": self.width})
                self._touch()
            self.canvas.delete("draft")
            self.redraw()
            return
        if self.tool == "select":
            self._drag = None
            if self._moved:
                self._moved = False
                self._touch()
            return
        if self.tool in ("line", "rect", "oval", "triangle", "arrow") and self._drag:
            x0, y0 = self._drag
            self._drag = None
            self.canvas.delete("draft")
            if abs(e.x - x0) + abs(e.y - y0) >= 4:
                self.items.append({"kind": self.tool, "x1": x0, "y1": y0,
                                   "x2": e.x, "y2": e.y, "color": self.color,
                                   "width": self.width})
                self._touch()
            self.redraw()

    def _move_item(self, it, dx, dy):
        k = it.get("kind")
        if k == "stroke":
            it["pts"] = [(x + dx, y + dy) for x, y in it["pts"]]
        elif k == "text":
            it["x"] += dx
            it["y"] += dy
        elif k == "comp":
            it["x"] += dx
            it["y"] += dy
        else:
            for f in ("x1", "x2"):
                if f in it:
                    it[f] += dx
            for f in ("y1", "y2"):
                if f in it:
                    it[f] += dy

    def _erase_at(self, x, y):
        i = hit_test(self.items, x, y)
        if i is not None:
            del self.items[i]
            self.redraw()
            self._touch()

    def _delete_sel(self):
        if self.sel is not None and 0 <= self.sel < len(self.items):
            del self.items[self.sel]
            self.sel = None
            self.redraw()
            self._touch()

    def _undo(self):
        if self._backup is not None:
            # 清空后的撤销：把备份恢复回来（弹窗承诺过"可以撤销一步找回"）
            self.items[:] = self._backup
            self._backup = None
            self.sel = None
            self.redraw()
            self._touch()
            return
        if self.items:
            self.items.pop()
            self.sel = None
            self.redraw()
            self._touch()

    def _clear(self):
        if not self.items:
            return
        if messagebox.askyesno("清空画布", "确定清掉全部内容？（可以撤销一步找回）"):
            self._backup = list(self.items)
            self.items.clear()
            self.sel = None
            self.redraw()
            self._touch()

    # ---------- 渲染 ----------
    def redraw(self):
        cv = self.canvas
        cv.delete("all")
        for it in self.items:
            draw_ops_canvas(cv, item_ops(it), "obj")
        if self.sel is not None and 0 <= self.sel < len(self.items):
            x1, y1, x2, y2 = item_bbox(self.items[self.sel])
            cv.create_rectangle(x1, y1, x2, y2, outline=BLUE, dash=(4, 3),
                                width=2, tags="selbox")
        n = len(self.items)
        self._set_status(f"画布上共 {n} 个图形"
                         + ("，「选择/拖动」点中图形后按 Delete 可删除" if n else ""))

    # ---------- 导出 ----------
    def export_png(self):
        """导出压缩图片 → (bytes, 扩展名)"""
        return render_png(self.items)

    def _has_art(self):
        return bool(self.items)
