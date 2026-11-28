# -*- coding: utf-8 -*-
"""老师端评讲与图表窗口。"""
import ntpath
import os
import tkinter as tk
from tkinter import messagebox as mb, ttk

from PIL import Image, ImageTk
from ikeexam.shared import ui_theme
from ikeexam.server import runtime

try:
    from .visual_config import ScrollableFrame
except ImportError:
    from visual_config import ScrollableFrame

_UI = ui_theme.LIGHT
FONT = "Microsoft YaHei UI"
BG = _UI["bg"]
CARD = _UI["surface"]
INK = _UI["text"]
GRAY = _UI["text_muted"]
BLUE = _UI["info"]
GREEN = _UI["success"]
ORANGE = _UI["warning"]
RED = _UI["danger"]
LINE = _UI["border"]


def _safe_drawing_path(value):
    if not isinstance(value, str) or not value or "\x00" in value:
        return None
    if value.startswith(("\\\\", "//")) or ntpath.splitdrive(value)[0]:
        return None
    if not ntpath.isabs(value) or ntpath.basename(value).count(":"):
        return None
    if os.path.splitext(value)[1].lower() not in (".png", ".jpg", ".jpeg"):
        return None
    try:
        root = os.path.realpath(os.path.join(runtime.UPLOAD_DIR, "drawing"))
        path = os.path.realpath(value)
        if os.path.commonpath((root, path)) != root:
            return None
    except (OSError, ValueError):
        return None
    return path


def _iconify(win):
    icon = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.ico")
    if os.path.isfile(icon):
        try:
            win.iconbitmap(icon)
        except tk.TclError:
            pass


def _tree(parent, columns, headings, widths, stretch=None, height=12):
    holder = ttk.Frame(parent)
    holder.rowconfigure(0, weight=1)
    holder.columnconfigure(0, weight=1)
    tree = ttk.Treeview(holder, columns=columns, show="headings", height=height)
    vertical = ttk.Scrollbar(holder, orient="vertical", command=tree.yview)
    horizontal = ttk.Scrollbar(holder, orient="horizontal", command=tree.xview)
    tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
    ui_theme.bind_mousewheel(tree, tree)
    tree.grid(row=0, column=0, sticky="nsew")
    vertical.grid(row=0, column=1, sticky="ns")
    horizontal.grid(row=1, column=0, sticky="ew")
    for index, (column, heading, width) in enumerate(zip(columns, headings, widths)):
        tree.heading(column, text=heading)
        tree.column(column, width=width, minwidth=40,
                    anchor="w", stretch=(column == stretch))
    return holder, tree


def _text(parent, height=8):
    widget = tk.Text(parent, height=height, wrap="word", font=(FONT, 10),
                     bg=CARD, fg=INK, relief="solid", borderwidth=1,
                     highlightthickness=0)
    ui_theme.bind_mousewheel(widget, widget)
    return widget


class ReviewWindow:
    def __init__(self, main, exam):
        self.main = main
        self.app = main.app
        self.api = main.api
        self.exam = exam
        self.eid = exam["id"]
        self.controls = []
        self.cards = []
        self.visible_answers = set()
        self.practice_details = {}
        self.typing_details = {}
        self.drawing_photos = []
        self.top = tk.Toplevel(main.root)
        self.top.title(f"试卷评讲 · {exam.get('title', '')}")
        self.top.transient(main.root)
        self.top.geometry("1200x780")
        self.top.minsize(1000, 660)
        self.top.configure(bg=BG)
        _iconify(self.top)
        self.top.protocol("WM_DELETE_WINDOW", self._close)
        self._build()
        self._load()

    def _build(self):
        outer = ttk.Frame(self.top, padding=16)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(2, weight=1)
        head = ttk.Frame(outer)
        head.grid(row=0, column=0, sticky="ew")
        ttk.Label(head, text=self.exam.get("title") or "试卷评讲",
                  font=(FONT, 16, "bold")).pack(side="left")
        self.tip = ttk.Label(head, text="正在加载评讲数据…", style="Muted.TLabel")
        self.tip.pack(side="right")
        self.summary = ttk.Label(outer, text="", style="Muted.TLabel")
        self.summary.grid(row=1, column=0, sticky="w", pady=6)

        bar = ttk.Frame(outer)
        bar.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        ttk.Label(bar, text="理论题排序").pack(side="left")
        self.sort_var = tk.StringVar(value="卷面顺序")
        sort_box = ttk.Combobox(bar, textvariable=self.sort_var, width=12,
                                state="readonly", values=("卷面顺序", "正确率从低到高"))
        sort_box.pack(side="left", padx=4)
        self.only_wrong = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text="只看有错的题", variable=self.only_wrong,
                        command=self._render_theory).pack(side="left", padx=8)
        self.project_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text="投影大字", variable=self.project_var,
                        command=self._update_detail_font).pack(side="left", padx=8)
        self.controls.extend((sort_box, self.only_wrong, self.project_var))
        ttk.Button(bar, text="全部亮答案", command=lambda: self._all_answers(True)).pack(
            side="left", padx=4)
        ttk.Button(bar, text="全部收起", command=lambda: self._all_answers(False)).pack(
            side="left", padx=4)
        refresh = ttk.Button(bar, text="刷新", command=self._load)
        refresh.pack(side="left", padx=4)
        close = ttk.Button(bar, text="关闭", command=self._close)
        close.pack(side="right")
        self.controls.extend((refresh, close))

        self.notebook = ttk.Notebook(outer, style="Main.TNotebook")
        self.notebook.enable_traversal()
        self.notebook.grid(row=2, column=0, sticky="nsew")
        self._build_theory(ttk.Frame(self.notebook, padding=8))
        self._build_practice(ttk.Frame(self.notebook, padding=8))
        self._build_typing(ttk.Frame(self.notebook, padding=8))
        self._build_drawings(ttk.Frame(self.notebook, padding=8))

    def _build_theory(self, frame):
        self.notebook.add(frame, text="理论题")
        frame.rowconfigure(2, weight=1)
        frame.columnconfigure(0, weight=1)
        holder, self.theory_tree = _tree(
            frame, ("no", "type", "category", "stem", "right", "wrong", "blank",
                    "rate", "answer"),
            ("题号", "题型", "分类", "题干", "做对", "做错", "没答", "正确率", "答案"),
            (52, 76, 96, 430, 58, 58, 58, 80, 260), stretch="stem")
        holder.grid(row=0, column=0, sticky="nsew")
        self.theory_tree.bind("<<TreeviewSelect>>", lambda event: self._theory_detail())
        self.theory_tree.bind("<Double-1>", lambda event: self._toggle_answer())
        ttk.Label(frame, text="双击题目或“答案”列可显示/收起正确答案。",
                  style="Muted.TLabel").grid(row=1, column=0, sticky="w", pady=4)
        self.theory_detail = _text(frame, 8)
        self.theory_detail.grid(row=2, column=0, sticky="nsew", pady=(4, 0))

    def _build_practice(self, frame):
        self.notebook.add(frame, text="实操题")
        frame.rowconfigure(2, weight=1)
        frame.columnconfigure(0, weight=1)
        holder, self.practice_tree = _tree(
            frame, ("task", "seat", "name", "score", "full", "state", "file"),
            ("实操题", "座位", "姓名", "得分", "满分", "状态", "答卷文件"),
            (190, 52, 76, 56, 56, 70, 240), stretch="task")
        holder.grid(row=0, column=0, sticky="nsew")
        self.practice_tree.bind("<<TreeviewSelect>>", lambda event: self._practice_detail())
        ttk.Label(frame, text="选择一行查看该生实操评分点明细。",
                  style="Muted.TLabel").grid(row=1, column=0, sticky="w", pady=4)
        self.practice_detail = _text(frame, 8)
        self.practice_detail.grid(row=2, column=0, sticky="nsew", pady=(4, 0))

    def _build_typing(self, frame):
        self.notebook.add(frame, text="打字排名")
        frame.rowconfigure(2, weight=1)
        frame.columnconfigure(0, weight=1)
        holder, self.typing_tree = _tree(
            frame, ("rank", "seat", "name", "overall", "wpm", "acc", "done"),
            ("名次", "座位", "姓名", "综合评分", "最快速度", "平均正确率", "完成项目"),
            (54, 54, 90, 88, 90, 100, 90), stretch="name")
        holder.grid(row=0, column=0, sticky="nsew")
        self.typing_tree.bind("<<TreeviewSelect>>", lambda event: self._typing_detail())
        ttk.Label(frame, text="综合评分 = 正确率 60% + 速度 40%。",
                  style="Muted.TLabel").grid(row=1, column=0, sticky="w", pady=4)
        self.typing_detail = _text(frame, 8)
        self.typing_detail.grid(row=2, column=0, sticky="nsew", pady=(4, 0))

    def _build_drawings(self, frame):
        self.notebook.add(frame, text="画图作品")
        scroll = ScrollableFrame(frame)
        scroll.pack(fill="both", expand=True)
        self.drawing_area = scroll.inner
        for column in range(3):
            self.drawing_area.columnconfigure(column, weight=1)
        self.drawing_area.rowconfigure(0, weight=1)

    def _load(self):
        if self.app.busy:
            return

        def success(result):
            if not self.top.winfo_exists():
                return
            if result.get("ok"):
                self._populate(result)
                self.tip.configure(text="评讲数据已更新", foreground=GREEN)
            else:
                self.tip.configure(text=f"加载失败：{result.get('msg', '')}",
                                   foreground=RED)

        self.app.task(lambda: self.api.review(self.eid), success,
                      error=lambda exc: self.tip.configure(
                          text=f"加载失败：{exc}", foreground=RED),
                      controls=self.controls, status=self.tip,
                      busy_text="正在加载评讲数据…")

    def _populate(self, result):
        self.cards = list(result.get("cards") or [])
        self.summary.configure(
            text=f"理论满分 {result.get('total_score', 0)} · "
                 f"参考 {result.get('n_attended', 0)} 人 · "
                 f"已交卷 {result.get('n_submitted', 0)} 人 · "
                 f"答案默认隐藏，双击题目可显示")
        self._render_theory()
        self._render_practice(result.get("task_cards") or [])
        self._render_typing(result.get("typing_rows") or [])
        self._render_drawings(result.get("drawings") or [])

    def _render_theory(self):
        self.theory_tree.delete(*self.theory_tree.get_children())
        cards = list(self.cards)
        if self.sort_var.get() == "正确率从低到高":
            cards.sort(key=lambda card: card.get("rate") if card.get("rate") is not None else 101)
        if self.only_wrong.get():
            cards = [card for card in cards
                     if card.get("rate") is not None and card.get("rate") < 100]
        for card in cards:
            qid = card["q"]["id"]
            shown = qid in self.visible_answers
            self.theory_tree.insert("", "end", iid=str(qid), values=(
                card.get("no"), card.get("qtype"), card.get("category") or "—",
                (card["q"].get("stem") or "")[:160], card.get("right_n", 0),
                card.get("wrong_n", 0), card.get("blank_n", 0),
                f"{card.get('rate')}%" if card.get("rate") is not None else "—",
                card.get("answer_text") if shown else "双击显示"))
        if not cards:
            self.theory_tree.insert("", "end", values=("—", "—", "—",
                                                       "没有符合条件的题目", "—", "—", "—", "—", "—"))

    def _selected_card(self):
        selected = self.theory_tree.selection()
        if not selected:
            return None
        return next((card for card in self.cards
                     if str(card["q"]["id"]) == selected[0]), None)

    def _toggle_answer(self):
        card = self._selected_card()
        if not card:
            return
        qid = card["q"]["id"]
        if qid in self.visible_answers:
            self.visible_answers.remove(qid)
        else:
            self.visible_answers.add(qid)
        self._render_theory()
        self.theory_tree.selection_set(str(qid))
        self._theory_detail()

    def _all_answers(self, show):
        self.visible_answers = {card["q"]["id"] for card in self.cards} if show else set()
        self._render_theory()
        self._theory_detail()

    def _theory_detail(self):
        card = self._selected_card()
        if not card:
            self.theory_detail.delete("1.0", "end")
            return
        q = card["q"]
        lines = [f"第 {card.get('no')} 题 · {card.get('qtype')} · {card.get('score')} 分",
                 q.get("stem") or ""]
        for option in q.get("options") or []:
            lines.append(str(option))
        if q["id"] in self.visible_answers:
            lines.extend(["", f"正确答案：{card.get('answer_text')}"])
        else:
            lines.extend(["", "正确答案已隐藏，双击题目可显示。"])
        lines.extend(["", f"做对 {card.get('right_n', 0)} 人：",
                      "、".join(f"{w.get('seat') or '—'} {w.get('name') or w.get('host')}"
                                for w in card.get("right") or []) or "无",
                      f"做错 {card.get('wrong_n', 0)} 人："])
        for w in card.get("wrong") or []:
            lines.append(f"{w.get('seat') or '—'} {w.get('name') or w.get('host')}：{w.get('answer') or '（空）'}")
        if not card.get("wrong"):
            lines.append("无")
        lines.append(f"交卷但没答 {card.get('blank_n', 0)} 人")
        self.theory_detail.delete("1.0", "end")
        self.theory_detail.insert("1.0", "\n".join(lines))

    def _update_detail_font(self):
        size = 14 if self.project_var.get() else 10
        for widget in (self.theory_detail, self.practice_detail, self.typing_detail):
            widget.configure(font=(FONT, size))

    def _render_practice(self, cards):
        self.practice_tree.delete(*self.practice_tree.get_children())
        self.practice_details = {}
        for card in cards:
            task = card.get("t") or {}
            for row in card.get("rows") or []:
                sid = str(row.get("sid"))
                self.practice_details[sid] = row
                self.practice_tree.insert("", "end", iid=sid, values=(
                    task.get("name") or "—", row.get("seat") or "—",
                    row.get("name") or "—",
                    row.get("score") if row.get("score") is not None else "—",
                    row.get("full") or card.get("full") or "—",
                    "缺卷" if row.get("empty") else ("评分中" if row.get("score") is None else "已评分"),
                    row.get("file") or "—"))
        if not self.practice_tree.get_children():
            self.practice_tree.insert("", "end", values=("—", "—", "暂无实操提交", "—", "—", "—", "—"))

    def _practice_detail(self):
        selected = self.practice_tree.selection()
        row = self.practice_details.get(selected[0]) if selected else None
        self.practice_detail.delete("1.0", "end")
        if not row:
            return
        lines = [f"{row.get('seat') or '—'} {row.get('name') or '—'}",
                 f"得分：{row.get('score')} / {row.get('full')}", ""]
        for item in row.get("detail") or []:
            lines.append(f"{item.get('id')}：{item.get('got')} / {item.get('full')} · {item.get('msg')}")
        if not row.get("detail"):
            lines.append("暂无评分点明细")
        self.practice_detail.insert("1.0", "\n".join(lines))

    def _render_typing(self, rows):
        self.typing_tree.delete(*self.typing_tree.get_children())
        self.typing_details = {}
        for row in rows:
            key = f"{row.get('participant_id') or 0}:{row.get('host')}"
            self.typing_details[key] = row
            self.typing_tree.insert("", "end", iid=key, values=(
                row.get("rank"), row.get("seat") or "—", row.get("name") or "—",
                row.get("overall"), row.get("wpm"), f"{row.get('acc')}%",
                f"{row.get('done_n')} 项"))
        if not rows:
            self.typing_tree.insert("", "end", values=("—", "—", "暂无打字记录", "—", "—", "—", "—"))

    def _typing_detail(self):
        selected = self.typing_tree.selection()
        row = self.typing_details.get(selected[0]) if selected else None
        self.typing_detail.delete("1.0", "end")
        if not row:
            return
        lines = [f"第 {row.get('rank')} 名 · {row.get('seat') or '—'} {row.get('name') or '—'}",
                 f"综合评分 {row.get('overall')} · 最快 {row.get('wpm')} 字/分 · "
                 f"平均正确率 {row.get('acc')}%", ""]
        for task in row.get("task_rows") or []:
            lines.append(f"{task.get('name')}：{task.get('line')}")
        if not row.get("task_rows"):
            lines.append("还没有练习数据")
        self.typing_detail.insert("1.0", "\n".join(lines))

    def _render_drawings(self, drawings):
        for widget in self.drawing_area.winfo_children():
            widget.destroy()
        self.drawing_photos = []
        if not drawings:
            ttk.Label(self.drawing_area, text="还没有同学提交画图作品。\n学生完成画板或拼豆后，作品会显示在这里。",
                      style="Muted.TLabel", justify="center").grid(
                          row=0, column=0, columnspan=3, sticky="nsew", pady=48)
            return
        for index, drawing in enumerate(drawings):
            card = ttk.Frame(self.drawing_area, padding=8)
            card.grid(row=index // 3, column=index % 3, sticky="nsew", padx=8, pady=8)
            self.drawing_area.rowconfigure(index // 3, weight=1)
            path = _safe_drawing_path(drawing.get("saved_path"))
            photo = None
            if path:
                try:
                    with Image.open(path) as source:
                        image = source.convert("RGB")
                    image.thumbnail((240, 160), Image.Resampling.LANCZOS)
                    photo = ImageTk.PhotoImage(image)
                except Exception:
                    photo = None
            if photo:
                self.drawing_photos.append(photo)
                label = tk.Label(card, image=photo, bg=CARD, cursor="hand2")
                label.pack()
                label.bind("<Double-1>", lambda event, item=drawing: self._open_drawing(item))
            else:
                tk.Label(card, text="图片无法预览", bg=CARD, fg=GRAY,
                         width=28, height=10).pack()
            name = f"{drawing.get('seat') or '—'} {drawing.get('name') or drawing.get('hostname') or '—'}"
            if drawing.get("kind") == "beads":
                name += " · 拼豆"
            tk.Label(card, text=name, bg=CARD, fg=INK, font=(FONT, 10)).pack(pady=4)

    def _open_drawing(self, drawing):
        win = tk.Toplevel(self.top)
        win.title(f"作品 · {drawing.get('name') or drawing.get('hostname') or ''}")
        win.configure(bg=BG)
        path = _safe_drawing_path(drawing.get("saved_path"))
        if path:
            try:
                with Image.open(path) as source:
                    image = source.convert("RGB")
                image.thumbnail((1000, 700), Image.Resampling.LANCZOS)
                photo = ImageTk.PhotoImage(image)
                win._photo = photo
                tk.Label(win, image=photo, bg=CARD).pack(padx=16, pady=16)
            except Exception:
                ttk.Label(win, text="原图不存在或无法读取。").pack(padx=24, pady=24)
        else:
            ttk.Label(win, text="原图不存在或无法读取。").pack(padx=24, pady=24)
        ttk.Button(win, text="关闭", command=win.destroy).pack(pady=(0, 14))

    def _close(self):
        if self.app.busy:
            mb.showinfo("请稍候", "当前操作尚未完成。", parent=self.top)
            return
        self.top.destroy()


class BarChart(tk.Canvas):
    def __init__(self, master, title, items, horizontal=False, suffix="", **kwargs):
        super().__init__(master, bg=CARD, highlightthickness=0, height=220, **kwargs)
        self.title = title
        self.items = list(items or [])
        self.horizontal = horizontal
        self.suffix = suffix
        self.bind("<Configure>", lambda event: self.draw())

    def draw(self):
        self.delete("all")
        width = max(self.winfo_width(), 320)
        height = max(self.winfo_height(), 220)
        if self.title:
            self.create_text(16, 18, text=self.title, anchor="w", fill=INK,
                             font=(FONT, 11, "bold"))
        if not self.items:
            self.create_text(width / 2, height / 2, text="暂无数据", fill=GRAY,
                             font=(FONT, 10))
            return
        if self.horizontal:
            self._draw_horizontal(width, height)
        else:
            self._draw_vertical(width, height)

    def _draw_horizontal(self, width, height):
        items = self.items[:24]
        left, right = 150, width - 42
        top, bottom = 42, height - 24
        maximum = max([float(value or 0) for _label, value, _color in items] + [1])
        row_height = (bottom - top) / max(len(items), 1)
        bar_height = min(20, max(8, row_height * 0.55))
        for index, (label, value, color) in enumerate(items):
            y = top + row_height * (index + 0.5)
            self.create_text(left - 10, y, text=str(label)[:18], anchor="e",
                             fill=GRAY, font=(FONT, 8))
            bar_width = max(0, (right - left) * float(value or 0) / maximum)
            self.create_rectangle(left, y - bar_height / 2, left + bar_width,
                                  y + bar_height / 2, fill=color or BLUE, outline="")
            self.create_text(left + bar_width + 6, y,
                             text=f"{value:g}{self.suffix}", anchor="w",
                             fill=INK, font=(FONT, 8))

    def _draw_vertical(self, width, height):
        left, right = 44, width - 24
        top, bottom = 48, height - 52
        maximum = max([float(value or 0) for _label, value, _color in self.items] + [1])
        step = (right - left) / max(len(self.items), 1)
        bar_width = min(54, step * 0.55)
        for index, (label, value, color) in enumerate(self.items):
            x = left + step * (index + 0.5)
            bar_height = max(0, (bottom - top) * float(value or 0) / maximum)
            self.create_rectangle(x - bar_width / 2, bottom - bar_height,
                                  x + bar_width / 2, bottom,
                                  fill=color or BLUE, outline="")
            self.create_text(x, bottom - bar_height - 8, text=f"{value:g}{self.suffix}",
                             anchor="s", fill=INK, font=(FONT, 9))
            self.create_text(x, bottom + 14, text=str(label), anchor="n",
                             fill=GRAY, font=(FONT, 8))


class ChartsWindow:
    def __init__(self, main, exam):
        self.main = main
        self.app = main.app
        self.api = main.api
        self.exam = exam
        self.eid = exam["id"]
        self.controls = []
        self.auto_var = tk.BooleanVar(value=True)
        self.top = tk.Toplevel(main.root)
        self.top.title(f"成绩图表 · {exam.get('title', '')}")
        self.top.transient(main.root)
        self.top.geometry("1200x780")
        self.top.minsize(1000, 660)
        self.top.configure(bg=BG)
        _iconify(self.top)
        self.top.protocol("WM_DELETE_WINDOW", self._close)
        self._build()
        self._load()
        self._schedule_refresh()

    def _build(self):
        outer = ttk.Frame(self.top, padding=16)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(2, weight=1)
        head = ttk.Frame(outer)
        head.grid(row=0, column=0, sticky="ew")
        ttk.Label(head, text=self.exam.get("title") or "成绩图表",
                  font=(FONT, 16, "bold")).pack(side="left")
        self.tip = ttk.Label(head, text="正在加载图表…", style="Muted.TLabel")
        self.tip.pack(side="right")
        bar = ttk.Frame(outer)
        bar.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        ttk.Checkbutton(bar, text="每 5 秒自动刷新", variable=self.auto_var).pack(side="left")
        refresh = ttk.Button(bar, text="立即刷新", command=self._load)
        refresh.pack(side="left", padx=8)
        close = ttk.Button(bar, text="关闭", command=self._close)
        close.pack(side="right")
        self.controls.extend((self.auto_var, refresh, close))
        self.summary = ttk.Frame(outer)
        self.summary.grid(row=1, column=0, sticky="ew", pady=6)
        scroll = ScrollableFrame(outer)
        scroll.grid(row=2, column=0, sticky="nsew")
        self.chart_area = scroll.inner
        self.chart_area.columnconfigure(0, weight=1)
        self.chart_area.columnconfigure(1, weight=1)

    def _load(self):
        if self.app.busy:
            return

        def success(result):
            if not self.top.winfo_exists():
                return
            if result.get("ok"):
                self._populate(result)
                self.tip.configure(text="图表已更新", foreground=GREEN)
            else:
                self.tip.configure(text=f"加载失败：{result.get('msg', '')}",
                                   foreground=RED)

        self.app.task(lambda: self.api.stats(self.eid), success,
                      error=lambda exc: self.tip.configure(
                          text=f"加载失败：{exc}", foreground=RED),
                      controls=self.controls, status=self.tip,
                      busy_text="正在加载成绩图表…")

    def _populate(self, result):
        for widget in self.summary.winfo_children():
            widget.destroy()
        summary = result.get("summary") or {}
        stats = [
            ("学生数", summary.get("count", 0), BLUE),
            ("已交卷", summary.get("submitted", 0), GREEN),
            ("平均得分率", f"{summary.get('avg_pct', 0)}%", BLUE),
            ("及格率", f"{summary.get('pass_rate', 0)}%", GREEN),
            ("优秀率", f"{summary.get('good_rate', 0)}%", ORANGE),
            ("最高分", summary.get("max", 0), BLUE),
        ]
        card_styles = ("Mint.TFrame", "Sky.TFrame", "Lemon.TFrame",
                       "Mint.TFrame", "Coral.TFrame", "Lavender.TFrame")
        for index, (label, value, color) in enumerate(stats):
            card = ttk.Frame(self.summary, style=card_styles[index], padding=10)
            card.grid(row=0, column=index, sticky="ew", padx=5)
            ttk.Label(card, text=str(value), font=(FONT, 16, "bold"),
                      foreground=color).pack()
            ttk.Label(card, text=label, style="Muted.TLabel").pack()
            self.summary.columnconfigure(index, weight=1)

        for widget in self.chart_area.winfo_children():
            widget.destroy()
        dist = result.get("dist") or {}
        dist_items = list(zip(dist.get("labels") or [], dist.get("values") or [],
                              [RED, ORANGE, BLUE, "#0891B2", GREEN]))
        question_items = []
        for item in sorted(result.get("questions") or [], key=lambda row: row.get("rate", 0)):
            question_items.append((f"第{item.get('no')}题 {item.get('qtype')}",
                                   item.get("rate") or 0,
                                   GREEN if (item.get("rate") or 0) >= 85 else
                                   BLUE if (item.get("rate") or 0) >= 70 else
                                   ORANGE if (item.get("rate") or 0) >= 60 else RED))
        check_items = [(item.get("name") or "评分点", item.get("rate") or 0,
                        GREEN if (item.get("rate") or 0) >= 85 else
                        BLUE if (item.get("rate") or 0) >= 70 else
                        ORANGE if (item.get("rate") or 0) >= 60 else RED)
                       for item in (result.get("checks") or [])[:14]]
        ranking = sorted(result.get("rows") or [], key=lambda row: row.get("total") or 0,
                         reverse=True)[:20]
        rank_items = [((row.get("seat") or "—") + " " + (row.get("name") or row.get("hostname") or "—"),
                       row.get("total") or 0, BLUE) for row in ranking]

        self._chart_card(0, 0, "分数段分布", dist_items)
        self._chart_card(0, 1, "及格率 / 优秀率", [
            ("及格率", summary.get("pass_rate") or 0, GREEN),
            ("优秀率", summary.get("good_rate") or 0, ORANGE),
        ], suffix="%")
        self._chart_card(1, 0, "各题得分率（得分率低的排前面）", question_items, horizontal=True, suffix="%")
        self._chart_card(1, 1, "实操评分点得分率", check_items, horizontal=True, suffix="%")
        self._chart_card(2, 0, "成绩排名（前 20 名）", list(reversed(rank_items)), horizontal=True)
        live = result.get("live") or {}
        self._chart_card(2, 1, "在线与交卷", [
            ("在线", live.get("online", 0), BLUE),
            ("已交卷", summary.get("submitted", 0), GREEN),
            ("未交卷", max(0, (summary.get("count") or 0) - (summary.get("submitted") or 0)), ORANGE),
        ])

    def _chart_card(self, row, column, title, items, horizontal=False, suffix=""):
        frame = ttk.LabelFrame(self.chart_area, text=title, style="Card.TLabelframe",
                               padding=10)
        frame.grid(row=row, column=column, sticky="nsew", padx=8, pady=8)
        self.chart_area.rowconfigure(row, weight=1)
        chart = BarChart(frame, "", items, horizontal=horizontal, suffix=suffix)
        chart.pack(fill="both", expand=True)

    def _schedule_refresh(self):
        def tick():
            if not self.top.winfo_exists():
                return
            if self.auto_var.get() and not self.app.busy:
                self._load()
            self.top.after(5000, tick)

        self.top.after(5000, tick)

    def _close(self):
        if self.app.busy:
            mb.showinfo("请稍候", "当前操作尚未完成。", parent=self.top)
            return
        self.top.destroy()
