# -*- coding: utf-8 -*-
"""
拼豆创作（beads）—— 学生端

画图题第二种样式：在格子板上一格一粒彩豆拼图（拼豆 / 豆豆画）。
左边：选颜色 + 素材图（点一下起稿，再自己改）；中间：拼豆板。
内容存在 client.drawing_state()["beads"]（跨标签共享，切页不丢），
导出一张压缩图和自由画走同一条上传链路（drawing_ui.export_state 分发）。
右上角「上一题 / 保存」+ 保存指示灯 + 改动自动保存（AutoSaveMixin 共用）。
"""
import base64
import os
import sys
import tkinter as tk
from tkinter import messagebox

SRC_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)
from ikeexam.shared import ui_theme

from drawing_ui import AutoSaveMixin

_UI = ui_theme.LIGHT
FONT = ui_theme.pick_family()
BG = _UI["bg"]
CARD = _UI["surface"]
BLUE = _UI["primary"]
GREEN = _UI["success"]
RED = _UI["danger"]
GRAY = _UI["text_muted"]
INK = _UI["text_strong"]
LINE = _UI["border"]

# 彩豆颜色（拼豆常见色，挑对比清晰、小孩喜欢的）
BEAD_COLORS = [
    ("红", "#e11d48"), ("橙", "#ea580c"), ("黄", "#f59e0b"), ("浅绿", "#84cc16"),
    ("绿", "#16a34a"), ("青", "#06b6d4"), ("蓝", "#2563eb"), ("深蓝", "#1e3a8a"),
    ("紫", "#7c3aed"), ("粉", "#ec4899"), ("浅粉", "#f9a8d4"), ("棕", "#92400e"),
    ("肤色", "#fcd9b8"), ("黑", "#1f2937"), ("灰", "#9ca3af"), ("白", "#f8fafc"),
    ("米白", "#fef3c7"), ("浅蓝", "#93c5fd"),
]

# ============================================================
# 素材图案（16×16 点阵，一格一豆）：小孩喜欢的常见图案
#   字母 → 颜色；"." = 空格子。点素材图 = 按它起稿，孩子再自由改。
# ============================================================
PATTERNS = [
    {"name": "爱心", "colors": {"R": "#e11d48", "W": "#f9a8d4"}, "grid": [
        "...RRR...RRR....",
        "..RRRRR.RRRRR...",
        ".RRRRRRRRRRRRR..",
        ".RWWRRRRRRRRRR..",
        ".RWWRRRRRRRRRR..",
        ".RRRRRRRRRRRRR..",
        "..RRRRRRRRRRR...",
        "...RRRRRRRRR....",
        "....RRRRRRR.....",
        ".....RRRRR......",
        "......RRR.......",
        ".......R........",
    ]},
    {"name": "星星", "colors": {"Y": "#f59e0b", "W": "#fbbf24"}, "grid": [
        ".......YY.......",
        "......YYYY......",
        ".....WYYYYY.....",
        ".....WYYYYY.....",
        "....YYYYYYYY....",
        "YYYYYYYYYYYYYYYY",
        "YYYYYYYYYYYYYYYY",
        "....YYYYYYYY....",
        "....YYYYYYYY....",
        ".....YYYYYY.....",
        ".....YYYYYY.....",
        "......YYYY......",
        ".......YY.......",
    ]},
    {"name": "小花", "colors": {"P": "#ec4899", "Y": "#f59e0b",
                          "G": "#16a34a", "L": "#84cc16"}, "grid": [
        "......PPPP......",
        ".....PPPPPP.....",
        "....PPPPPPPP....",
        "...PPPPPPPPPP...",
        "...PPPYYYYPPP...",
        "...PPPYYYYPPP...",
        "...PPPYYYYPPP...",
        "...PPPYYYYPPP...",
        "....PPPPPPPP....",
        ".....PPPPPP.....",
        "......PPPP......",
        ".......GG.......",
        ".......GG.......",
        "....LLGGG.......",
        "....LL.GG.......",
        ".......GG.......",
    ]},
    {"name": "小鱼", "colors": {"O": "#ea580c", "Y": "#f59e0b",
                          "W": "#f8fafc", "K": "#1f2937",
                          "B": "#2563eb"}, "grid": [
        "............B...",
        "...........BB...",
        ".....OOOOO......",
        "...OOOOOOOOOO...",
        "..OOOOOOOOOOOO..",
        ".OOYOOOOOOOOOOO.",
        ".OOYKOOOOOOOOOO.",
        ".OOOOOOOOOOOOOO.",
        "..OOOOOOOOOOOO..",
        "...OOOOOOOOOO...",
        ".....OOOOO......",
        "......OOO.......",
        ".......O........",
    ]},
    {"name": "小鸭", "colors": {"Y": "#f59e0b", "W": "#fef3c7",
                          "O": "#ea580c", "K": "#1f2937"}, "grid": [
        "......YYYY......",
        ".....YYYYYY.....",
        ".....YYKYYY.....",
        ".....YYYYYYY....",
        "......OOOO......",
        ".....YYYYYY.....",
        "....YYYYYYYY....",
        "...YYYYYYYYYY...",
        "...YYWYYYYYYY...",
        "...YYWYYYYYYY...",
        "...YYYYYYYYYY...",
        "....YYYYYYYY....",
        ".....YYYYYY.....",
        "....OO..OO......",
        "...OO....OO.....",
    ]},
    {"name": "小猫", "colors": {"O": "#ea580c", "W": "#f8fafc",
                          "P": "#f9a8d4", "K": "#1f2937"}, "grid": [
        "..OO........OO..",
        ".OOOO......OOOO.",
        ".OOOOOOOOOOOOOO.",
        ".OOKOOOOOOOOKOO.",
        ".OOKOOOOOOOOKOO.",
        ".OOOOOOOOOOOOOO.",
        ".OOOOOOWWOOOOOO.",
        ".OOOOOOPKOOOOOO.",
        ".OOOOOOWWOOOOOO.",
        ".OOOOOOOOOOOOOO.",
        "..OOOOOOOOOOOO..",
        "...OOOOOOOOOO...",
        "...K........K...",
    ]},
    {"name": "冰淇淋", "colors": {"P": "#f9a8d4", "R": "#e11d48",
                           "D": "#d97706", "W": "#fef3c7"}, "grid": [
        ".......RR.......",
        "......PPPP......",
        ".....PPPPPP.....",
        "....PPWPPPPP....",
        "...PPPPPPPPPP...",
        "...PPPPPPPPPP...",
        "...PPPPPPPPPP...",
        "....DDDDDDDD....",
        "....DDDDDDDD....",
        ".....DDDDDD.....",
        ".....DDDDDD.....",
        "......DDDD......",
        "......DDDD......",
        ".......DD.......",
    ]},
    {"name": "蘑菇", "colors": {"R": "#e11d48", "W": "#f8fafc",
                          "C": "#fef3c7"}, "grid": [
        ".....RRRRRR.....",
        "...RRRRRRRRRR...",
        "..RRRWRRRRWRRR..",
        ".RRRRRWRRWRRRRR.",
        ".RRRRRRRRRRRRRR.",
        ".RRWRRRRRRRRWRR.",
        ".RRRRRRRRRRRRRR.",
        "..RRRRRWRRRRRR..",
        "...RRRRRRRRRR...",
        "....CCCCCCCC....",
        "....CWCCCCWC....",
        "....CCCCCCCC....",
        "....CCCCCCCC....",
        ".....CCCCCC.....",
    ]},
]


# ============================================================
# 导出 / 缩略图
# ============================================================
def render_beads_png(board, cell=24):
    """拼豆板 → 压缩 PNG（白底淡格子 + 一格一粒圆豆）→ (bytes, ".png")"""
    import io
    from PIL import Image, ImageDraw
    rows = len(board)
    cols = len(board[0]) if rows else 0
    if not rows or not cols:
        cols = rows = 1
        board = [[None]]
    pad = max(6, cell // 2)
    w = cols * cell + pad * 2
    h = rows * cell + pad * 2
    img = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(img)
    for c in range(cols + 1):
        x = pad + c * cell
        d.line([x, pad, x, pad + rows * cell], fill="#e2e8f0")
    for r in range(rows + 1):
        y = pad + r * cell
        d.line([pad, y, pad + cols * cell, y], fill="#e2e8f0")
    m = max(1, cell // 9)
    for r in range(rows):
        for c in range(cols):
            col = board[r][c]
            if not col:
                continue
            x0, y0 = pad + c * cell, pad + r * cell
            d.ellipse([x0 + m, y0 + m, x0 + cell - m, y0 + cell - m],
                      fill=col, outline="#94a3b8")
    d.rectangle([pad, pad, pad + cols * cell, pad + rows * cell],
                outline="#cbd5e1", width=2)
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue(), ".png"


def _pattern_board(pat):
    colors = pat.get("colors") or {}
    return [[colors.get(ch) for ch in line] for line in pat.get("grid") or []]


def pattern_thumb_png(pat, cell=7):
    """素材图缩略图（和拼豆板同一套画法，看起来就是拼好的豆）→ PNG bytes"""
    return render_beads_png(_pattern_board(pat), cell=cell)[0]


def _photo(png):
    return tk.PhotoImage(data=base64.b64encode(png))


# ============================================================
# 参考图（AI 生成的可爱拼豆成品图，点开看大图；只看不改）
# ============================================================
def assets_dir():
    """素材图目录（PyInstaller 打包后在临时目录里，源码运行在本文件旁）"""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "beads_assets")


def list_reference_images():
    """beads_assets 里所有 PNG/JPG → [(文件名, 完整路径), ...]，按名字排序"""
    d = assets_dir()
    if not os.path.isdir(d):
        return []
    out = []
    for fn in sorted(os.listdir(d)):
        if fn.lower().endswith((".png", ".jpg", ".jpeg", ".gif")):
            out.append((os.path.splitext(fn)[0], os.path.join(d, fn)))
    return out


def _ref_thumb(path, max_px=88):
    """参考图缩略图 PhotoImage（等比缩到 max_px 内）"""
    from PIL import Image
    img = Image.open(path).convert("RGB")
    img.thumbnail((max_px, max_px))
    import io
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return tk.PhotoImage(data=base64.b64encode(buf.getvalue()))


# ============================================================
# 面板
# ============================================================
class BeadPanel(tk.Frame, AutoSaveMixin):
    # 上传用的小题类型：beads=拼豆题
    kind = "beads"

    def __init__(self, parent, client, cfg, on_save=None, on_prev=None,
                 on_next=None, next_label=None):
        super().__init__(parent, bg=BG)
        self.client = client
        self.cfg = cfg or {}
        self._init_autosave(on_save, on_prev, on_next, next_label)
        self.bind("<Destroy>", self._cleanup)
        self.tool = "fill"                 # fill（放豆）/ eraser（拿走）
        self.color = BEAD_COLORS[0][1]
        self._undo_stack = []
        self._last_cell = None

        # 格子数：老师可配（16~36），默认 28×28
        try:
            self.n = max(16, min(36, int(self.cfg.get("grid") or 28)))
        except (TypeError, ValueError):
            self.n = 28
        st = client.drawing_state(self.kind)
        board = st.get("beads")
        if not board or len(board) != self.n or len(board[0]) != self.n:
            board = [[None] * self.n for _ in range(self.n)]
            st["beads"] = board
        self.board = board

        brief = (self.cfg.get("brief") or "").strip()
        if brief:
            tip = tk.Label(self, text="画题要求：" + brief, bg="#eff6ff",
                           fg="#1d4ed8", font=(FONT, 12), justify="left",
                           wraplength=1100, anchor="w")
            tip.pack(fill="x", padx=12, pady=(10, 2))

        self._build_toolbar()
        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True, padx=12, pady=6)
        self._build_side_panel(body)

        self.cell = 22
        size = self.n * self.cell
        self.canvas = tk.Canvas(body, width=size, height=size, bg="white",
                                highlightthickness=2,
                                highlightbackground=LINE, cursor="crosshair")
        self.canvas.pack(side="left", expand=True)
        self.canvas.bind("<Button-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._drag_paint)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.canvas.bind("<Button-3>", self._erase_at_event)
        self.canvas.bind("<Control-z>", lambda e: self._undo())
        self._draw_grid()

        self.status = tk.Label(self, text="", bg=BG, fg=GRAY, font=(FONT, 11))
        self.status.pack(fill="x", padx=14, pady=(2, 8))
        self._set_status("点格子放豆豆（按住拖可以连着放）；左边点素材图可以起稿；"
                         "改动会自动保存")
        if self._has_art():
            self._touch()                  # 切回来先补存一份
        else:
            self._set_save_dot("empty")

    # ---------- 界面 ----------
    def _build_toolbar(self):
        bar = tk.Frame(self, bg=BG)
        bar.pack(fill="x", padx=12, pady=(8, 2))
        self.tool_btns = {}
        for key, label in (("fill", "放豆豆"), ("eraser", "橡皮（拿走）")):
            b = tk.Button(bar, text=label, command=lambda k=key: self._set_tool(k),
                          bg="white", fg=INK, font=(FONT, 10), padx=8, pady=3,
                          relief="solid", bd=1, cursor="hand2")
            b.pack(side="left", padx=2)
            self.tool_btns[key] = b
        tk.Button(bar, text="撤销一步", command=self._undo, bg="white", fg=INK,
                  font=(FONT, 10), padx=8, pady=3, relief="solid", bd=1,
                  cursor="hand2").pack(side="left", padx=(12, 2))
        tk.Button(bar, text="清空画布", command=self._clear, bg="white", fg=RED,
                  font=(FONT, 10), padx=8, pady=3, relief="solid", bd=1,
                  cursor="hand2").pack(side="left", padx=2)
        self._build_save_cluster(bar)      # 右上角：上一题 / 保存 / 保存指示灯
        self._set_tool("fill")

    def _build_side_panel(self, body):
        panel = tk.Frame(body, bg=CARD, width=200, highlightthickness=1,
                         highlightbackground=LINE)
        panel.pack(side="left", fill="y", padx=(0, 8))
        panel.pack_propagate(False)

        tk.Label(panel, text="选颜色", bg=CARD, fg=INK,
                 font=(FONT, 12, "bold")).pack(anchor="w", padx=10, pady=(10, 4))
        grid = tk.Frame(panel, bg=CARD)
        grid.pack(padx=8)
        self.color_btns = []
        for i, (name, hexv) in enumerate(BEAD_COLORS):
            b = tk.Button(grid, bg=hexv, width=2,
                          command=lambda h=hexv: self._set_color(h),
                          relief="solid", bd=1, cursor="hand2",
                          activebackground=hexv)
            b.grid(row=i // 6, column=i % 6, padx=2, pady=2)
            self.color_btns.append((hexv, b))

        tk.Label(panel, text="素材图（点一下起稿）", bg=CARD, fg=INK,
                 font=(FONT, 12, "bold")).pack(anchor="w", padx=10,
                                               pady=(12, 2))
        tk.Label(panel, text="起稿后接着自己改，拼出自己的样子",
                 bg=CARD, fg=GRAY, font=(FONT, 9), justify="left",
                 wraplength=180).pack(anchor="w", padx=10, pady=(0, 4))
        grid2 = tk.Frame(panel, bg=CARD)
        grid2.pack(padx=6)
        self._thumb_photos = []
        for i, pat in enumerate(PATTERNS):
            try:
                ph = _photo(pattern_thumb_png(pat, cell=5))
                self._thumb_photos.append(ph)
                b = tk.Button(grid2, image=ph, command=lambda p=pat: self._draft(p),
                              relief="solid", bd=1, cursor="hand2", bg="white",
                              activebackground=LINE)
            except Exception as e:
                print(f"[拼豆] 起稿图案生成失败 {pat.get('name')}: {e}", flush=True)
                b = tk.Button(grid2, text=pat.get("name") or "?", width=8,
                              command=lambda p=pat: self._draft(p),
                              relief="solid", bd=1, cursor="hand2")
            b.grid(row=i // 2, column=i % 2, padx=3, pady=3)

        # 参考图：可爱拼豆成品图，点开看大图（只看不改）
        refs = list_reference_images()
        if refs:
            tk.Label(panel, text="参考图（点开看大图）", bg=CARD, fg=INK,
                     font=(FONT, 12, "bold")).pack(anchor="w", padx=10,
                                                   pady=(12, 2))
            grid3 = tk.Frame(panel, bg=CARD)
            grid3.pack(padx=6, pady=(0, 8))
            self._ref_photos = []
            for i, (title, path) in enumerate(refs):
                try:
                    ph = _ref_thumb(path)
                    self._ref_photos.append(ph)
                    b = tk.Button(
                        grid3, image=ph,
                        command=lambda p=path, t=title: self._show_reference(t, p),
                        relief="solid", bd=1, cursor="hand2", bg="white",
                        activebackground=LINE)
                except Exception as e:
                    print(f"[拼豆] 参考图加载失败 {title}: {e}", flush=True)
                    continue
                b.grid(row=i // 2, column=i % 2, padx=3, pady=3)

    def _show_reference(self, title, path):
        """点参考图 → 弹窗看大图"""
        win = tk.Toplevel(self)
        win.title(f"参考图：{title}")
        win.configure(bg=BG)
        win.transient(self.winfo_toplevel())
        try:
            from PIL import Image
            import io
            img = Image.open(path).convert("RGB")
            img.thumbnail((560, 560))
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            ph = tk.PhotoImage(data=base64.b64encode(buf.getvalue()))
            if not hasattr(self, "_ref_big"):
                self._ref_big = []
            self._ref_big.append(ph)               # 防止被回收
            del self._ref_big[:-4]                 # 只留最近 4 张，避免内存只增不减
            tk.Label(win, image=ph, bg=BG).pack(padx=12, pady=(12, 6))
        except Exception as e:
            tk.Label(win, text=f"图片加载失败：{e}", bg=BG, fg=RED,
                     font=(FONT, 11)).pack(padx=12, pady=12)
        tk.Label(win, text="照着它拼一拼，也可以拼自己的样子", bg=BG, fg=GRAY,
                 font=(FONT, 11)).pack(pady=(0, 4))
        tk.Button(win, text="关掉", command=win.destroy, bg=BLUE, fg="white",
                  font=(FONT, 11), padx=18, pady=4, relief="flat",
                  cursor="hand2").pack(pady=(0, 12))

    def _set_status(self, msg):
        if hasattr(self, "status"):      # 建工具栏时状态栏还没创建
            self.status.config(text=msg)

    def _set_tool(self, key):
        self.tool = key
        for k, b in self.tool_btns.items():
            b.config(bg=(BLUE if k == key else "white"),
                     fg=("white" if k == key else INK))
        if hasattr(self, "canvas"):      # 建工具栏时画布还没创建
            self.canvas.config(
                cursor=("circle" if key == "eraser" else "crosshair"))
        self._set_status("橡皮：点格子把豆拿走" if key == "eraser"
                         else "放豆豆：点格子放一粒（按住拖连着放）")

    def _set_color(self, c):
        self.color = c
        self.tool = "fill"
        for k, b in self.tool_btns.items():
            b.config(bg=(BLUE if k == "fill" else "white"),
                     fg=("white" if k == "fill" else INK))
        for cc, b in self.color_btns:
            b.config(relief=("sunken" if cc == c else "solid"))

    # ---------- 画布 ----------
    def _draw_grid(self):
        cv = self.canvas
        cv.delete("all")
        self.cell_items = []
        for r in range(self.n):
            row_items = []
            for c in range(self.n):
                x0, y0 = c * self.cell, r * self.cell
                it = cv.create_oval(x0 + 2, y0 + 2,
                                    x0 + self.cell - 2, y0 + self.cell - 2,
                                    outline="", tags=("cell", f"r{r}c{c}"))
                if self.board[r][c]:
                    self._paint_item(it, self.board[r][c])
                row_items.append(it)
            self.cell_items.append(row_items)

    def _paint_item(self, it, col):
        self.canvas.itemconfig(it, fill=col, outline="#94a3b8")

    def _cell_at(self, e):
        c, r = e.x // self.cell, e.y // self.cell
        if 0 <= r < self.n and 0 <= c < self.n:
            return r, c
        return None

    def _press(self, e):
        self._push_undo()
        self._last_cell = None
        self._paint_at(e)

    def _drag_paint(self, e):
        self._paint_at(e)

    def _paint_at(self, e):
        rc = self._cell_at(e)
        if rc is None or rc == self._last_cell:
            return
        self._last_cell = rc
        r, c = rc
        if self.tool == "eraser":
            if self.board[r][c]:
                self.board[r][c] = None
                self.canvas.itemconfig(self.cell_items[r][c], fill="",
                                       outline="")
                self._touch()
        else:
            if self.board[r][c] != self.color:
                self.board[r][c] = self.color
                self._paint_item(self.cell_items[r][c], self.color)
                self._touch()

    def _erase_at_event(self, e):
        rc = self._cell_at(e)
        if rc is None:
            return
        r, c = rc
        if self.board[r][c]:
            self._push_undo()
            self.board[r][c] = None
            self.canvas.itemconfig(self.cell_items[r][c], fill="", outline="")
            self._touch()

    def _release(self, e):
        self._last_cell = None

    def _push_undo(self):
        self._undo_stack.append([row[:] for row in self.board])
        del self._undo_stack[:-30]         # 只留最近 30 步

    def _undo(self):
        if not self._undo_stack:
            return
        self.board[:] = self._undo_stack.pop()
        self._draw_grid()
        self._touch()

    def _clear(self):
        if not self._has_art():
            return
        if messagebox.askyesno("清空画布", "确定清掉所有豆豆？（可以撤销一步找回）"):
            self._push_undo()
            for row in self.board:
                for c in range(self.n):
                    row[c] = None
            self._draw_grid()
            self._touch()

    def _draft(self, pat):
        """按素材图起稿：图案居中铺到板上（可撤销），孩子接着自己改"""
        self._push_undo()
        grid = _pattern_board(pat)
        ph, pw = len(grid), (len(grid[0]) if grid else 0)
        top = max(0, (self.n - ph) // 2)
        left = max(0, (self.n - pw) // 2)
        for r in range(self.n):
            for c in range(self.n):
                self.board[r][c] = None
        for r in range(ph):
            for c in range(pw):
                col = grid[r][c]
                if col and top + r < self.n and left + c < self.n:
                    self.board[top + r][left + c] = col
        self._draw_grid()
        self._touch()
        self._set_status(f"已按「{pat.get('name')}」起稿，接着自己改一改吧！")

    # ---------- 导出 ----------
    def export_png(self):
        return render_beads_png(self.board)

    def _has_art(self):
        return any(any(row) for row in self.board)
