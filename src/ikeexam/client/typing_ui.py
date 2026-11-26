# -*- coding: utf-8 -*-
"""
打字练习（小学模块）—— 学生端

四个子任务各自是**顶部导航的一个标签**（和 选择·填空题 / 判断题 / 程序填空 并列）：
    key_press   看键按键：屏幕上出现一个按键，限时在键盘上按出来
    case_press  小写字母找按键：屏幕上出现小写字母，按对应的字母键
    typing_en   英文打字：照着范文打英文
    typing_zh   中文打字：照着范文打中文

每个子任务都有倒计时（也可以不限时），最后 3 秒会变红放大强调。
速度 / 正确率 / 反应时间实时计算；统计放在 ExamClient.typing_state() 里
跨标签共享（切标签重建面板也不丢进度），由客户端的常驻线程每 3 秒上报。
本模块**不计分**，只统计速度与正确率。
"""
import os
import random
import re
import string
import sys
import time
import tkinter as tk

SRC_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)
from ikeexam.shared import ui_theme

_UI = ui_theme.LIGHT
BG = _UI["bg"]
CARD = _UI["surface"]
BLUE = _UI["primary"]
BLUE_D = _UI["primary_hover"]
GREEN = _UI["success"]
GREEN_L = _UI["success_soft"]
RED = _UI["danger"]
RED_L = _UI["danger_soft"]
ORANGE = _UI["warning"]
GRAY = _UI["text_muted"]
INK = _UI["text_strong"]
LINE = _UI["border"]

FONT = ui_theme.pick_family()

TASK_ORDER = ("whack_mole", "key_press", "case_press", "typing_en",
              "typing_zh", "name_typing")
TASK_DESC = {
    "whack_mole": "地鼠从洞里冒出来啦，快用鼠标点它！"
                  "关卡越高，地鼠越小、冒出来的时间越短",
    "key_press": "屏幕上出现一个按键，请你在键盘上把它按出来（按错没关系，再试一次）",
    "case_press": "屏幕上出现一个小写字母，请你在键盘上按出对应的字母键",
    "typing_en": "照着上面的英文打字，注意指法，又快又准",
    "typing_zh": "照着上面的中文打字，注意坐姿，又快又准",
    "name_typing": "看提示打词语：先看拼音打 → 再看汉字打 → 最后不看提示打",
}

# 打地鼠三档难度兜底参数（正常由服务端配置下发覆盖）：
# 档位越高，地鼠越小、冒出时间越短、间隔越短
WHACK_LEVELS = {
    "easy":   {"name": "简单", "show_ms": 1800, "gap_ms": 650, "size": 56},
    "normal": {"name": "普通", "show_ms": 1300, "gap_ms": 500, "size": 44},
    "hard":   {"name": "困难", "show_ms": 900, "gap_ms": 380, "size": 34},
}

# 键位定义（id 与老师端虚拟键盘约定一致）：id -> (显示文字, 匹配方式, 匹配值)
FUNC_KEYS = {
    "esc": ("退出键 Esc", "keysym", {"Escape"}),
    "tab": ("制表键 Tab", "keysym", {"Tab"}),
    "capslock": ("大写锁定键 CapsLock", "keysym", {"Caps_Lock"}),
    "shift": ("上档键 Shift", "keysym", {"Shift_L", "Shift_R"}),
    "enter": ("回车键 Enter", "keysym", {"Return"}),
    "backspace": ("退格键 ⌫", "keysym", {"BackSpace"}),
    "delete": ("删除键 Delete", "keysym", {"Delete"}),
    "space": ("空格键 Space", "keysym", {"space"}),
    "up": ("方向键 ↑", "keysym", {"Up"}),
    "down": ("方向键 ↓", "keysym", {"Down"}),
    "left": ("方向键 ←", "keysym", {"Left"}),
    "right": ("方向键 →", "keysym", {"Right"}),
}
LETTERS = tuple(string.ascii_lowercase)


def key_target(key_id):
    """
    键 id → 出题目标 (显示文字, "char"/"keysym", 匹配值)
    字母显示大写（看键按键里认字），匹配时大小写都算对
    """
    k = str(key_id or "").lower()
    if k in FUNC_KEYS:
        return FUNC_KEYS[k]
    if k.isalpha() and len(k) == 1:
        return (k.upper(), "char", k)
    if k.isdigit() and len(k) == 1:
        return (k, "char", k)
    return None


def _new_stats(task, cfg):
    if task == "whack_mole":
        # 打地鼠：每关 rounds 只；三关连打 = 3 关
        lv = (cfg.get("level") or "all").strip().lower()
        n_levels = 3 if lv == "all" else 1
        total = int(cfg.get("rounds") or 8) * n_levels
    elif task in ("key_press", "case_press"):
        total = int(cfg.get("rounds") or 10)
    elif task == "name_typing":
        # 打名字测试：每个词 3 个回合（拼音 → 汉字 → 不给提示）
        total = int(cfg.get("rounds") or 5) * int(cfg.get("stages") or 3)
    else:
        total = len(cfg.get("text") or "")
    return {"task": task, "progress": 0, "total": total, "hits": 0,
            "typed": 0, "correct_chars": 0, "elapsed_ms": 0,
            "wpm": 0.0, "accuracy": 0.0, "reaction_ms": 0,
            "finished": 0, "errors": 0}


def _is_cjk(text):
    return any("\u4e00" <= ch <= "\u9fff" for ch in (text or ""))


def ime_off(widget):
    """
    Windows：临时关掉这个窗口的输入法。
    按键题（看键按键 / 小写字母找按键）只按字母，不需要中文输入 ——
    不关的话按一下字母就会弹出输入法候选框（挡在屏幕中间很难看）。
    返回旧的输入法句柄，离开这张页面时用它恢复。
    """
    if os.name != "nt":
        return None
    try:
        import ctypes
        hwnd = ctypes.c_void_p(widget.winfo_id())
        imm32 = ctypes.WinDLL("imm32")
        imm32.ImmAssociateContext.restype = ctypes.c_void_p
        imm32.ImmAssociateContext.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        return imm32.ImmAssociateContext(hwnd, None)
    except Exception:
        return None


def ime_restore(widget, old_ctx):
    """把输入法恢复回去（后面还有中文打字，不能一直关着）"""
    if os.name != "nt" or old_ctx is None:
        return
    try:
        import ctypes
        hwnd = ctypes.c_void_p(widget.winfo_id())
        imm32 = ctypes.WinDLL("imm32")
        imm32.ImmAssociateContext.restype = ctypes.c_void_p
        imm32.ImmAssociateContext.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        imm32.ImmAssociateContext(hwnd, ctypes.c_void_p(old_ctx))
    except Exception:
        pass


def _auto_wrap(label, parent, pad=28):
    """
    让说明文字跟着窗口宽度换行（窄窗口也不会被截断）。
    绑定在父容器上，父容器一变形就重算 wraplength。
    """
    def on_cfg(_e=None):
        try:
            w = parent.winfo_width() - pad
            if w > 120:
                label.config(wraplength=w)
        except Exception:
            pass
    parent.bind("<Configure>", on_cfg, add="+")
    on_cfg()


def _char_width(widget, sample="测"):
    """量一下这个控件当前字体的平均字宽（用真实字体测量，不用猜）"""
    try:
        import tkinter.font as tkfont
        f = tkfont.Font(font=widget.cget("font"))
        w = f.measure(sample)
        return max(6, w)
    except Exception:
        return 20


def _fit_text_height(text, widget, min_lines=3, max_lines=16, pad=30):
    """
    按「控件实际宽度 + 字体实际字宽」算范文要几行，长课文才不会被截掉。
    """
    try:
        width = widget.winfo_width()
    except Exception:
        width = 0
    if width < 80:
        width = 900                      # 还没布局完 → 先按窗口宽度估一个
    cw = _char_width(widget)
    per_line = max(6, int((width - pad) / cw))
    lines = -(-len(text) // per_line)    # 向上取整
    return max(min_lines, min(max_lines, lines))


# ==================== 任务一/二：按键识别（一轮一个目标，限时按对） ====================
class PressTask(tk.Frame):
    def __init__(self, parent, panel, task, cfg):
        super().__init__(parent, bg=BG)
        self.panel = panel
        self.task = task
        self.cfg = cfg or {}
        self.rounds = max(1, int(self.cfg.get("rounds") or 10))
        # 每轮秒数：0 = 不限时（孩子慢慢找，按对或点「跳过」进入下一轮）
        secs = self.cfg.get("seconds", 5)
        self.seconds = max(0, int(secs or 0))
        self.st = panel.stats[task]
        self.st["total"] = self.rounds
        self.round_no = int(self.st.get("progress") or 0)
        self.target = None            # (显示文字, "char"/"keysym", 匹配值)
        self.round_start = 0.0
        self.finished = bool(self.st.get("finished"))
        self._after_id = None
        self._key_bind_id = None

        desc = tk.Label(self, text=TASK_DESC.get(task, "打字练习"), bg=BG, fg=GRAY,
                        font=(FONT, 13), justify="center")
        desc.pack(pady=(16, 6), padx=16)
        _auto_wrap(desc, self)
        # 卡片加宽：功能键名字长（如「大写锁定键 CapsLock」）也能完整显示
        self.card = tk.Frame(self, bg=CARD, highlightbackground=LINE,
                             highlightthickness=3, width=560, height=240)
        self.card.pack(pady=8, padx=16, fill="x")
        self.card.pack_propagate(False)
        self.target_label = tk.Label(self.card, text="准备…", bg=CARD, fg=INK,
                                     font=(FONT, 56, "bold"), justify="center")
        self.target_label.pack(expand=True, fill="both", padx=10)
        # 倒计时（最后 3 秒变红放大）
        self.cd_label = tk.Label(self, text="", bg=BG, fg=INK,
                                 font=(FONT, 40, "bold"))
        self.cd_label.pack(pady=(4, 0))
        self.prog_label = tk.Label(self, text="", bg=BG, fg=GRAY,
                                   font=(FONT, 12), justify="center")
        self.prog_label.pack(pady=(6, 0), padx=16)
        _auto_wrap(self.prog_label, self)
        # 找不到这个键可以跳过（不计入按对数）
        self.skip_btn = tk.Button(self, text="跳过 ▶", command=self._skip,
                                  bg="white", fg=GRAY, font=(FONT, 11),
                                  padx=16, pady=4, relief="solid", bd=1,
                                  cursor="hand2")
        # 卡片被拉宽/缩窄时，按键名字的换行宽度跟着变
        self.card.bind("<Configure>", lambda e: self.target_label.config(
            wraplength=max(200, e.width - 40)))

        top = self.winfo_toplevel()
        # add="+" 不挤掉主界面自己的按键绑定；销毁时按 funcid 只解自己的
        self._key_bind_id = top.bind("<KeyPress>", self._on_key, add="+")
        # 只按字母，顺便把输入法关掉（否则按一下字母就弹候选框）
        self._old_ime = ime_off(top)
        self.bind("<Destroy>", self._cleanup)

        if self.finished:
            self._show_summary()
        else:
            self._after_id = self.after(700, self._next_round)

    # ---------- 出题 ----------
    def _target_font(self, text):
        """按键名字越长字号越小（「回车键 Enter」这类长名字也不会被卡片切掉）"""
        n = max(1, len(text or ""))
        if n <= 3:
            size = 56
        elif n <= 6:
            size = 40
        elif n <= 10:
            size = 28
        else:
            size = 22
        return (FONT, size, "bold")

    def _set_target_text(self, text, color=INK):
        try:
            w = max(200, self.card.winfo_width() - 40)
        except Exception:
            w = 460
        self.target_label.config(text=text, fg=color, bg=CARD,
                                 font=self._target_font(text), wraplength=w)

    def _pick_target(self):
        """
        按老师选的按键池出题。
        老师用虚拟键盘点选了几个键就只考那几个；没选（空）= 全部随机。
        """
        pool = [k for k in (self.cfg.get("keys") or []) if key_target(k)]
        if self.task == "case_press":
            pool = [k for k in pool if len(k) == 1 and k.isalpha()] or list(LETTERS)
            ch = random.choice(pool)
            return (ch, "char", ch)
        if pool:
            return key_target(random.choice(pool))
        # 没选按键池 → 保持原来的手感：字母 / 数字 / 功能键
        kind = random.random()
        if kind < 0.5:
            ch = random.choice(string.ascii_uppercase)
            return (ch, "char", ch.lower())
        if kind < 0.75:
            ch = random.choice(string.digits)
            return (ch, "char", ch)
        return random.choice(list(FUNC_KEYS.values()))

    # ---------- 一轮的生命周期 ----------
    def _next_round(self):
        if self._after_id:
            try:
                self.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None
        if not self.winfo_exists():
            return
        if self.target is not None:
            return                    # 这一轮还没结束（不限时模式常见），别重复出题
        if self.round_no >= self.rounds:
            self._finish()
            return
        self.target = self._pick_target()
        self.round_no += 1
        self._set_target_text(self.target[0], INK)
        self.card.config(highlightbackground=LINE, highlightthickness=3)
        self.round_start = time.time()
        self.prog_label.config(text=f"第 {self.round_no} / {self.rounds} 轮")
        self.focus_set()          # 焦点收回来，别留在别处的输入框上
        self.skip_btn.pack(pady=(8, 0))
        if self.seconds <= 0:
            # 不限时：不算倒计时，按对或点「跳过」才进入下一轮
            self.cd_label.config(text="不限时", fg=GRAY, font=(FONT, 20))
            return
        self.cd_label.config(text="", fg=INK)
        self._tick_round()

    def _tick_round(self):
        if not self.winfo_exists() or self.target is None:
            return
        left = self.seconds - (time.time() - self.round_start)
        if left <= 0:
            self._miss()
            return
        show = int(left + 0.999)
        if left <= 3.0:
            # 最后 3 秒强调：变红、放大、卡片描红边
            self.cd_label.config(text=str(show), fg=RED, font=(FONT, 60, "bold"))
            self.card.config(highlightbackground=RED, highlightthickness=6)
            self.target_label.config(fg=RED)
        else:
            self.cd_label.config(text=str(show), fg=INK, font=(FONT, 40, "bold"))
            self.card.config(highlightbackground=LINE, highlightthickness=3)
            self.target_label.config(fg=INK)
        self.st["elapsed_ms"] = int(self.st.get("elapsed_ms") or 0) + 100
        self._after_id = self.after(100, self._tick_round)

    def _on_key(self, e):
        if self.target is None or self.finished or not self.winfo_exists():
            return
        _text, kind, value = self.target
        if kind == "char":
            ok = (e.char or "").lower() == value
        else:
            ok = e.keysym in value
        if ok:
            self._hit()
        else:
            # 按错了：橙色描边闪一下（下一轮 tick 会自动恢复），不计进成绩
            self.st["errors"] = int(self.st.get("errors") or 0) + 1
            try:
                self.card.config(highlightbackground=ORANGE,
                                 highlightthickness=6)
            except Exception:
                pass

    def _end_round(self, color):
        if self._after_id:
            try:
                self.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None
        self.target = None
        try:
            self.skip_btn.pack_forget()
        except Exception:
            pass
        self.st["progress"] = self.round_no
        hits = int(self.st.get("hits") or 0)
        self.st["accuracy"] = round(hits / max(1, self.round_no) * 100, 1)
        try:
            self.target_label.config(fg=color)
            self.card.config(highlightbackground=color, highlightthickness=6)
        except Exception:
            pass
        self._after_id = self.after(450, self._next_round)

    def _hit(self):
        reaction = time.time() - self.round_start
        st = self.st
        st["hits"] = int(st.get("hits") or 0) + 1
        st["_react_total"] = float(st.get("_react_total") or 0) + reaction
        st["reaction_ms"] = int(st["_react_total"] / st["hits"] * 1000)
        self._end_round(GREEN)

    def _miss(self):
        self._end_round(RED)

    def _skip(self):
        """不会这个键 → 跳到下一轮（按对数不加，但算进进度）"""
        if self.target is not None:
            self._miss()

    # ---------- 结束 ----------
    def _finish(self):
        self.finished = True
        self.st["finished"] = 1
        self._show_summary()

    def _show_summary(self):
        if not self.winfo_exists():
            return
        self.target = None
        try:
            self.skip_btn.pack_forget()
        except Exception:
            pass
        hits = int(self.st.get("hits") or 0)
        acc = self.st.get("accuracy") or 0
        react = (self.st.get("reaction_ms") or 0) / 1000.0
        self.cd_label.config(text="", font=(FONT, 40, "bold"))
        self.target_label.config(text="完成啦！", fg=GREEN, bg=CARD,
                                 font=(FONT, 44, "bold"))
        self.card.config(highlightbackground=GREEN, highlightthickness=4)
        self.prog_label.config(
            text=f"按对 {hits} / {self.rounds} 轮 · 正确率 {acc:.0f}% · "
                 f"平均反应 {react:.1f} 秒",
            fg=INK, font=(FONT, 14))
        tk.Button(self, text="再练一次", command=self._restart,
                  bg=BLUE, fg="white", font=(FONT, 12, "bold"),
                  padx=22, pady=6, relief="flat", cursor="hand2",
                  activebackground=BLUE_D).pack(pady=10)

    def _restart(self):
        self.panel.reset(self.task, extras={"rounds": self.rounds})

    def _cleanup(self, e=None):
        if e is not None and e.widget is not self:
            return
        if self._after_id:
            try:
                self.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None
        if self._key_bind_id:
            try:
                self.winfo_toplevel().unbind("<KeyPress>", self._key_bind_id)
            except Exception:
                pass
            self._key_bind_id = None
        # 离开按键题：把输入法还回去（中文打字、打名字测试都要用）
        if getattr(self, "_old_ime", None) is not None:
            try:
                ime_restore(self.winfo_toplevel(), self._old_ime)
            except Exception:
                pass
            self._old_ime = None


# ==================== 任务三/四：英文 / 中文打字 ====================
class TypingTask(tk.Frame):
    def __init__(self, parent, panel, task, cfg):
        super().__init__(parent, bg=BG)
        self.panel = panel
        self.task = task
        self.text = (cfg.get("text") or "").strip()
        # 限时秒数：0 = 不限时（打完或学生点「做完了」结束）
        secs = cfg.get("seconds", 180)
        self.seconds = max(0, int(secs or 0))
        self.st = panel.stats[task]
        self.st["total"] = len(self.text)
        self.start_ts = None
        self.finished = bool(self.st.get("finished"))
        self._after_id = None

        top_row = tk.Frame(self, bg=BG)
        top_row.pack(fill="x", padx=14, pady=(14, 4))
        desc = tk.Label(top_row, text=TASK_DESC.get(task, "打字练习"), bg=BG, fg=GRAY,
                        font=(FONT, 13), justify="left")
        desc.pack(side="left")
        # 倒计时（最后 3 秒变红放大）
        self.cd_label = tk.Label(top_row, text="", bg=BG, fg=INK,
                                 font=(FONT, 24, "bold"))
        self.cd_label.pack(side="right", padx=(10, 0))
        _auto_wrap(desc, top_row, pad=140)

        # 范文（只读，正确部分标绿、打错的部分标红）
        # 中文按「字」换行、英文按「词」换行，宽度吃满，长课文才不会被截掉
        wrap_mode = "char" if _is_cjk(self.text) else "word"
        self._lines = 0
        self.passage = tk.Text(self, height=4, wrap=wrap_mode,
                               font=(FONT, 17), bg=CARD, fg=INK,
                               relief="solid", bd=1, padx=12, pady=10,
                               highlightthickness=0)
        self.passage.pack(fill="x", padx=14, pady=(4, 8), expand=False)
        self.passage.insert("1.0", self.text)
        self.passage.tag_config("ok", background=GREEN_L, foreground=GREEN)
        self.passage.tag_config("bad", background=RED_L, foreground=RED,
                                underline=True)
        self.passage.config(state="disabled")

        # 输入区
        self.entry = tk.Text(self, height=4, wrap=wrap_mode,
                             font=(FONT, 17), bg="white", fg=INK,
                             relief="solid", bd=1, padx=12, pady=10,
                             highlightthickness=2,
                             highlightbackground=LINE)
        self.entry.pack(fill="x", padx=14, pady=(0, 8))
        self.entry.bind("<KeyRelease>", lambda e: self._update())
        self.entry.config(state="disabled")

        # 窗口宽度一变就重算该显示几行（小窗口/大屏都不会切成半截）
        self.passage.bind("<Configure>", self._fit_heights)
        self._fit_heights()

        self.live_label = tk.Label(self, text="速度 0 字/分 · 正确率 --%",
                                   bg=BG, fg=INK, font=(FONT, 14, "bold"),
                                   justify="center")
        self.live_label.pack(padx=16)
        _auto_wrap(self.live_label, self)

        btns = tk.Frame(self, bg=BG)
        btns.pack(pady=10)
        self.start_btn = tk.Button(btns, text="开  始", command=self._start,
                                   bg=GREEN, fg="white",
                                   font=(FONT, 13, "bold"), padx=28, pady=8,
                                   relief="flat", cursor="hand2")
        self.start_btn.pack(side="left", padx=6)
        # 不限时（或想提前交）时手动结束本次打字
        self.done_btn = tk.Button(btns, text="做完了 ✓", command=self._finish,
                                  bg=BLUE, fg="white",
                                  font=(FONT, 12, "bold"), padx=18, pady=8,
                                  relief="flat", cursor="hand2",
                                  state="disabled")
        self.done_btn.pack(side="left", padx=6)
        tk.Button(btns, text="再练一次", command=self._restart,
                  bg="white", fg=INK, font=(FONT, 12), padx=18, pady=8,
                  relief="solid", bd=1, cursor="hand2").pack(side="left",
                                                             padx=6)

        self.bind("<Destroy>", self._cleanup)
        if self.finished:
            self._show_summary()

    def _fit_heights(self, e=None):
        """
        范文框 / 输入框占几行，按当前窗口宽度算：
        长课文（比如 190 字的段落）窄窗口下也能完整显示，不会只看到前 4 行。
        """
        lines = _fit_text_height(self.text, self.passage, min_lines=3,
                                 max_lines=10)
        if lines == self._lines:
            return
        self._lines = lines
        try:
            self.passage.config(height=lines)
            self.entry.config(height=max(3, min(5, lines)))
        except Exception:
            pass

    # ---------- 流程 ----------
    def _start(self):
        if self.finished:
            return
        self.start_ts = time.time()
        self.entry.config(state="normal")
        self.entry.focus_set()
        self.start_btn.config(state="disabled", bg=GRAY)
        self.done_btn.config(state="normal")
        if self.seconds <= 0:
            self.cd_label.config(text="不限时", fg=GRAY, font=(FONT, 20))
        self._tick()

    def _tick(self):
        if not self.winfo_exists() or self.finished or self.start_ts is None:
            return
        used = time.time() - self.start_ts
        if self.seconds <= 0:
            # 不限时：正计时（只显示已经用了多久），打完或点「做完了」结束
            m, s = divmod(int(used), 60)
            self.cd_label.config(text=f"已用 {m}:{s:02d}", fg=GRAY,
                                 font=(FONT, 20))
        else:
            left = self.seconds - used
            if left <= 0:
                self._finish()
                return
            m, s = divmod(int(left), 60)
            if left <= 3.0:
                # 最后 3 秒强调：变红放大
                self.cd_label.config(text=f"0:0{int(left + 0.999)}", fg=RED,
                                     font=(FONT, 34, "bold"))
            else:
                self.cd_label.config(text=f"{m}:{s:02d}", fg=INK,
                                     font=(FONT, 24, "bold"))
        self.st["elapsed_ms"] = int(used * 1000)
        self._after_id = self.after(200, self._tick)

    def _update(self):
        if self.finished or self.start_ts is None:
            return
        content = self.entry.get("1.0", "end-1c")
        # 打满范文就不让再多打
        if len(content) > len(self.text):
            content = content[:len(self.text)]
            self.entry.delete("1.0", "end")
            self.entry.insert("1.0", content)

        typed = len(content)
        correct = sum(1 for i, ch in enumerate(content) if ch == self.text[i])
        # 第一个打错的位置（范文高亮用）
        prefix = 0
        for i, ch in enumerate(content):
            if ch != self.text[i]:
                break
            prefix = i + 1

        st = self.st
        st["typed"] = typed
        st["correct_chars"] = correct
        st["accuracy"] = round(correct / typed * 100, 1) if typed else 0.0
        minutes = max((time.time() - self.start_ts) / 60.0, 1 / 60)
        st["wpm"] = round(correct / minutes, 1)

        # 范文高亮：打对的部分绿色，打错的部分红色
        self.passage.config(state="normal")
        self.passage.tag_remove("ok", "1.0", "end")
        self.passage.tag_remove("bad", "1.0", "end")
        if prefix:
            self.passage.tag_add("ok", "1.0", f"1.{prefix}")
        if typed > prefix:
            self.passage.tag_add("bad", f"1.{prefix}", f"1.{typed}")
        self.passage.config(state="disabled")

        self.live_label.config(
            text=f"速度 {st['wpm']:.0f} 字/分 · 正确率 {st['accuracy']:.0f}%")

        if typed >= len(self.text):
            self._finish()

    def _finish(self):
        if self.finished:
            return
        if self._after_id:
            try:
                self.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None
        self.finished = True
        self.st["finished"] = 1
        self.st["elapsed_ms"] = int((time.time() - (self.start_ts or
                                                    time.time())) * 1000)
        try:
            self.entry.config(state="disabled")
            self.done_btn.config(state="disabled")
        except Exception:
            pass
        # 时间到 / 没打完就点「做完了」→ 提示还能回去补；打满范文 → 完成
        if (self.st.get("typed") or 0) >= len(self.text):
            tip = "完成"
        elif self.seconds > 0:
            tip = "时间到"
        else:
            tip = "已结束"
        self.cd_label.config(text=tip, fg=GREEN, font=(FONT, 24, "bold"))
        self._show_summary()

    def _show_summary(self):
        st = self.st
        try:
            self.done_btn.config(state="disabled")
        except Exception:
            pass
        self.live_label.config(
            text=f"完成！速度 {st.get('wpm', 0):.0f} 字/分 · "
                 f"正确率 {st.get('accuracy', 0):.0f}% · "
                 f"打对 {st.get('correct_chars', 0)} / "
                 f"{st.get('typed', 0)} 字",
            fg=GREEN, font=(FONT, 15, "bold"))

    def _restart(self):
        self.panel.reset(self.task)

    def _cleanup(self, e=None):
        if e is not None and e.widget is not self:
            return
        if self._after_id:
            try:
                self.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None


# ==================== 任务五：打名字测试（拼音→汉字→不给提示） ====================
class NameTypingTask(tk.Frame):
    """
    看提示打词语：每个词走三个回合
        第 1 回合：只给拼音（yǎ ān）→ 打出「雅安」
        第 2 回合：给汉字（雅安）→ 照着打
        第 3 回合：不给提示 → 凭记忆打
    次数（打几个词）与词表由老师在「编辑考试」里设置。
    """

    STAGE_HINT = {
        1: "看拼音，打出这个词",
        2: "照着汉字打出来",
        3: "不看提示，凭记忆打出来",
    }
    STAGE_TIP = {1: "拼音提示", 2: "汉字提示", 3: "无提示"}

    def __init__(self, parent, panel, task, cfg):
        super().__init__(parent, bg=BG)
        self.panel = panel
        self.task = task
        self.cfg = cfg or {}
        self.rounds = max(1, int(self.cfg.get("rounds") or 5))
        self.stages = max(1, int(self.cfg.get("stages") or 3))
        secs = self.cfg.get("seconds", 0)
        self.seconds = max(0, int(secs or 0))
        self.st = panel.stats[task]
        self.st["total"] = self.rounds * self.stages

        words = [w for w in (self.cfg.get("words") or [])
                 if isinstance(w, dict) and w.get("word")]
        # 学生本人的姓名由服务端按登录信息生成
        stu = self.cfg.get("student") or {}
        self_word = None
        if stu.get("name"):
            self_word = {"word": stu["name"],
                         "pinyin": stu.get("pinyin") or "",
                         "missing": stu.get("missing") or [],
                         "duoyin": bool(stu.get("duoyin")),
                         "is_self": True}
        # 认出了学生本人 → **只打他自己的名字**（打几遍由老师设的次数决定）；
        # 学生机还没填姓名时才用老师的词表 / 内置词顶着，保证不出空题。
        others = [{"word": w.get("word"), "pinyin": w.get("pinyin") or ""}
                  for w in words
                  if not (self_word and w.get("word") == self_word["word"])]
        random.shuffle(others)
        if self_word:
            seq = [self_word]
        else:
            seq = others or [{"word": "雅安", "pinyin": "yǎ ān"}]
        self.queue = [seq[i % len(seq)] for i in range(self.rounds)]
        self.step = int(self.st.get("progress") or 0)      # 已完成回合数
        self.finished = bool(self.st.get("finished"))
        self.step_start = 0.0
        self._after_id = None
        self._lock_input = False
        self._wrong_n = 0                   # 本回合连错几次（3 次给答案提示）

        head = tk.Frame(self, bg=BG)
        head.pack(fill="x", padx=16, pady=(14, 0))
        self.prog_label = tk.Label(head, text="", bg=BG, fg=GRAY,
                                   font=(FONT, 12))
        self.prog_label.pack(side="left")
        self.cd_label = tk.Label(head, text="", bg=BG, fg=INK,
                                 font=(FONT, 24, "bold"))
        self.cd_label.pack(side="right")

        # 提示卡片
        card = tk.Frame(self, bg=CARD, highlightbackground=LINE,
                        highlightthickness=2)
        card.pack(fill="x", padx=16, pady=(8, 10))
        self.stage_label = tk.Label(card, text="", bg=CARD, fg=BLUE,
                                    font=(FONT, 12, "bold"))
        self.stage_label.pack(anchor="w", padx=14, pady=(12, 0))
        self.prompt_label = tk.Label(card, text="", bg=CARD, fg=INK,
                                     font=(FONT, 40, "bold"))
        self.prompt_label.pack(pady=(2, 4))
        self.hint_label = tk.Label(card, text="", bg=CARD, fg=GRAY,
                                   font=(FONT, 12))
        self.hint_label.pack(pady=(0, 12))

        # 输入区
        row = tk.Frame(self, bg=BG)
        row.pack(padx=16, pady=(0, 6))
        self.entry = tk.Entry(row, font=(FONT, 26), width=12,
                              justify="center")
        self.entry.pack(side="left", ipady=6)
        self.entry.bind("<Return>", lambda e: self._submit())
        self.ok_btn = tk.Button(row, text="确认 ✓", command=self._submit,
                                bg=GREEN, fg="white",
                                font=(FONT, 14, "bold"), padx=20, pady=8,
                                relief="flat", cursor="hand2")
        self.ok_btn.pack(side="left", padx=10)

        self.fb_label = tk.Label(self, text="打完按回车或点「确认」",
                                 bg=BG, fg=GRAY, font=(FONT, 13, "bold"))
        self.fb_label.pack(pady=(4, 0))
        tk.Label(self, text="（每个词要打 3 次：拼音 → 汉字 → 不看提示；"
                            "打错了要重打，打对才进下一题）",
                 bg=BG, fg=GRAY, font=(FONT, 11)).pack()

        self.bind("<Destroy>", self._cleanup)
        if self.finished:
            self._show_summary()
        else:
            self.after(300, self._next_step)

    # ---------- 回合流程 ----------
    def current_word(self):
        i = min(len(self.queue) - 1, self.step // max(1, self.stages))
        return self.queue[i]

    def current_stage(self):
        return (self.step % max(1, self.stages)) + 1

    def _next_step(self):
        if not self.winfo_exists():
            return
        if self.step >= self.rounds * self.stages:
            self._finish()
            return
        w = self.current_word()
        stage = self.current_stage()
        word = w.get("word", "")
        pinyin = w.get("pinyin", "")
        who = "· 你自己的名字" if w.get("is_self") else ""
        self.stage_label.config(
            text=f"第 {self.step // self.stages + 1} / {self.rounds} 个词"
                 f" · 第 {stage} / {self.stages} 回合（{self.STAGE_TIP[stage]}）"
                 f"{who}")
        if stage == 1:
            if pinyin and "?" not in pinyin:
                self.prompt_label.config(text=pinyin, fg=BLUE,
                                         font=(FONT, 34, "bold"))
            else:
                # 多音字/没收录的字：宁可让老师标注，也不瞎给读音
                self.prompt_label.config(text="（拼音待老师标注）", fg=ORANGE,
                                         font=(FONT, 20, "bold"))
        elif stage == 2:
            self.prompt_label.config(text=word, fg=INK, font=(FONT, 40, "bold"))
        else:
            self.prompt_label.config(text="？" * len(word), fg=GRAY,
                                     font=(FONT, 40, "bold"))
        hint = f"{self.STAGE_HINT[stage]}（{len(word)} 个字）"
        if w.get("is_self"):
            hint = "这是你自己的名字，" + hint
            if w.get("duoyin"):
                hint += "　多音字以本人/老师确认为准"
        self.hint_label.config(text=hint)
        self.prog_label.config(
            text=f"进度 {self.step} / {self.rounds * self.stages} 回合")
        self.fb_label.config(text="打完按回车或点「确认」", fg=GRAY)
        self._wrong_n = 0                   # 新回合重新数错

        # ⚠️ 顺序不能反：Entry 在 disabled 状态下 delete 是不生效的，
        #    上一回合打的字会留在框里（学生看到"没清空"）。
        self.entry.config(state="normal")
        self.entry.delete(0, "end")
        self.entry.focus_set()
        self._lock_input = False
        self.step_start = time.time()
        if self.seconds > 0:
            self._tick()
        else:
            self.cd_label.config(text="不限时", fg=GRAY, font=(FONT, 20))

    def _tick(self):
        if (not self.winfo_exists() or self.finished
                or self._lock_input or self.seconds <= 0):
            return
        left = self.seconds - (time.time() - self.step_start)
        if left <= 0:
            self._judge("")
            return
        if left <= 3.0:
            self.cd_label.config(text=f"0:0{int(left + 0.999)}", fg=RED,
                                 font=(FONT, 34, "bold"))
        else:
            self.cd_label.config(text=f"0:{int(left):02d}", fg=INK,
                                 font=(FONT, 24, "bold"))
        self._after_id = self.after(200, self._tick)

    def _submit(self):
        if self._lock_input or self.finished:
            return
        self._judge(self.entry.get())

    def _judge(self, text):
        if self._lock_input:
            return
        self._lock_input = True
        if self._after_id:
            try:
                self.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None
        word = self.current_word().get("word", "")
        elapsed = max(0.1, time.time() - self.step_start)

        def norm(s):
            return re.sub(r"[\s，。、！？!?,.;；:：'\"“”‘’]", "", str(s or ""))

        got, want = norm(text), norm(word)
        ok = bool(got) and got == want
        self.entry.config(state="disabled")

        st = self.st
        typed, correct = len(got), 0
        for i, ch in enumerate(got):
            if i < len(want) and ch == want[i]:
                correct += 1
        st["typed"] = int(st.get("typed") or 0) + typed
        st["correct_chars"] = int(st.get("correct_chars") or 0) + correct
        st["hits"] = int(st.get("hits") or 0) + (1 if ok else 0)
        st["elapsed_ms"] = int(st.get("elapsed_ms") or 0) + int(elapsed * 1000)
        typed_all = max(1, int(st.get("typed") or 0))
        st["accuracy"] = round(int(st.get("correct_chars") or 0) / typed_all * 100, 1)
        minutes = max(0.02, (int(st.get("elapsed_ms") or 0) / 1000) / 60.0)
        st["wpm"] = round(int(st.get("correct_chars") or 0) / minutes, 1)

        if ok:
            # 打对了 → 记进度，进下一回合
            st["progress"] = self.step + 1
            self.fb_label.config(text=f"✅ 打对了！{word}", fg=GREEN)
            self.step += 1
            self._after_id = self.after(800, self._next_step)
            return

        # ❌ 打错了 → **不跳下一题**，清空输入框留在原地再打一遍，
        #    打对为止（错的那次照样算进正确率/速度里）
        st["progress"] = self.step
        self._wrong_n += 1
        if not got:
            self.fb_label.config(text="⏰ 时间到啦，再打一遍试试", fg=ORANGE)
        elif self._wrong_n >= 3:
            # 连错 3 次：把答案亮出来，别让孩子卡死在这一题
            self.fb_label.config(text=f"别着急，照着打一遍：{word}", fg=ORANGE)
        else:
            self.fb_label.config(text="❌ 打错了，再打一遍试试", fg=RED)
        self.entry.config(state="normal")
        self.entry.delete(0, "end")
        self.entry.focus_set()
        self._lock_input = False
        self.step_start = time.time()       # 重新计时（限时任务也一样）
        if self.seconds > 0:
            self._tick()

    def _finish(self):
        if self.finished:
            return
        self.finished = True
        self.st["finished"] = 1
        self._show_summary()

    def _show_summary(self):
        st = self.st
        self._lock_input = True
        try:
            self.entry.config(state="disabled")
            self.cd_label.config(text="")
        except Exception:
            pass
        self.stage_label.config(text="练习完成")
        self.prompt_label.config(text="完成啦！", fg=GREEN,
                                 font=(FONT, 34, "bold"))
        self.hint_label.config(text="")
        self.fb_label.config(
            text=f"打对 {st.get('hits', 0)} / {st.get('total', 0)} 回合 · "
                 f"正确率 {st.get('accuracy', 0):.0f}% · "
                 f"平均 {st.get('wpm', 0):.0f} 字/分",
            fg=GREEN)
        tk.Button(self, text="再练一次", command=self._restart,
                  bg=BLUE, fg="white", font=(FONT, 12, "bold"),
                  padx=22, pady=6, relief="flat", cursor="hand2",
                  activebackground=BLUE_D).pack(pady=10)

    def _restart(self):
        self.panel.reset(self.task)

    def _cleanup(self, e=None):
        if e is not None and e.widget is not self:
            return
        if self._after_id:
            try:
                self.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None


# ==================== 打地鼠：鼠标点击练习（简单→普通→困难） ====================
_HAMMER_PNG = {}            # 角度 → PNG 字节（与 Tk 无关，可跨窗口复用）


def _hammer_photo(angle):
    """
    一帧卡通锤子 PhotoImage（正/斜 28° = 敲下）。
    锤头中心画在图片正中 —— 跟随鼠标时用 anchor="center"，
    鼠标点就是敲击点。画不出来返回 None（退回系统光标）。
    """
    try:
        import base64
        import io
        from PIL import Image, ImageDraw
        if angle not in _HAMMER_PNG:
            s, w = 4, 84                       # 4 倍超采样抗锯齿
            img = Image.new("RGBA", (w * s, w * s), (0, 0, 0, 0))
            d = ImageDraw.Draw(img)
            cx = cy = w * s // 2               # 锤头中心 = 鼠标热点
            # 木柄：从锤头斜伸到右下角
            d.line([(cx + 6 * s, cy + 6 * s), (cx + 22 * s, w * s - 7 * s)],
                   fill=(176, 110, 52, 255), width=9 * s)
            d.ellipse([cx + 22 * s - 7 * s, w * s - 7 * s - 7 * s,
                       cx + 22 * s + 7 * s, w * s - 7 * s + 7 * s],
                      fill=(146, 88, 42, 255))
            # 锤头：圆角横条 + 亮面 + 描边
            d.rounded_rectangle([cx - 30 * s, cy - 15 * s,
                                 cx + 30 * s, cy + 15 * s],
                                radius=11 * s, fill=(148, 163, 184, 255),
                                outline=(71, 85, 105, 255), width=3 * s)
            d.rounded_rectangle([cx - 25 * s, cy - 11 * s,
                                 cx + 2 * s, cy - 1 * s],
                                radius=7 * s, fill=(226, 232, 240, 255))
            img = img.resize((w, w), Image.LANCZOS)
            if angle:
                img = img.rotate(angle, resample=Image.BICUBIC,
                                 center=(w // 2, w // 2))
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            _HAMMER_PNG[angle] = buf.getvalue()
        return tk.PhotoImage(data=base64.b64encode(_HAMMER_PNG[angle]))
    except Exception as e:
        print(f"[打地鼠] 锤子光标生成失败: {e}", flush=True)
        return None


class WhackMoleTask(tk.Frame):
    """
    打地鼠：地鼠从洞里冒出来，快用鼠标点它。
    三档难度：档位越高，地鼠越小、冒出时间越短（简单→普通→困难）。
    老师可配「三关连打」或只打某一关；每关冒出 rounds 只。
    地鼠画在洞口下层，从洞里往上冒，就是「钻出来」的效果。
    """
    COLS = 3
    ROWS = 3

    def __init__(self, parent, panel, task, cfg):
        super().__init__(parent, bg=BG)
        self.panel = panel
        self.task = task
        self.cfg = cfg or {}
        self.rounds = max(1, int(self.cfg.get("rounds") or 8))
        # 三档难度参数：服务端下发的覆盖兜底值
        self.level_params = {}
        for k, v in WHACK_LEVELS.items():
            self.level_params[k] = dict(v)
        for k, v in (self.cfg.get("levels") or {}).items():
            if k in self.level_params and isinstance(v, dict):
                self.level_params[k].update(v)
        # 老师选的关卡序列：all = 三关连打，否则只打某一关
        mode = (self.cfg.get("level") or "all").strip().lower()
        self.level_seq = ([mode] if mode in WHACK_LEVELS
                          else ["easy", "normal", "hard"])
        self.st = panel.stats[task]
        self.st["total"] = self.rounds * len(self.level_seq)
        self.finished = bool(self.st.get("finished"))
        self.li = 0                    # 第几关（level_seq 下标）
        self.pop_no = 0                # 本关第几只
        # 洞口布局：老师可配（3x3 / 3x4 / 4x4 / 4x5 / 5x5），默认 3×3
        self.ROWS, self.COLS = 3, 3
        g = str(self.cfg.get("grid") or "").strip().lower()
        try:
            if "x" in g:
                r, c = g.split("x", 1)
                self.ROWS = max(2, min(6, int(r)))
                self.COLS = max(2, min(6, int(c)))
        except (TypeError, ValueError):
            pass
        # 画布随格子数放大（3×3 保持原尺寸）
        self.cv_w = max(560, 148 * self.COLS + 110)
        self.cv_h = max(320, 104 * self.ROWS + 52)
        self.mole_up = False           # 现在有没有地鼠冒在外面
        self.mole_bbox = None          # 当前地鼠的点击范围
        self.pop_time = 0.0
        self._t0 = 0.0
        self._afters = []
        self.level_stat = {}           # 关名 -> [打中, 冒出]

        desc = tk.Label(self, text=TASK_DESC.get(task, "打字练习"), bg=BG, fg=GRAY,
                        font=(FONT, 13), justify="center")
        desc.pack(pady=(16, 6), padx=16)
        _auto_wrap(desc, self)

        info = tk.Frame(self, bg=BG)
        info.pack(fill="x", padx=16)
        self.level_label = tk.Label(info, text="", bg=BG, fg=BLUE,
                                    font=(FONT, 15, "bold"))
        self.level_label.pack(side="left")
        self.score_label = tk.Label(info, text="", bg=BG, fg=INK,
                                    font=(FONT, 13))
        self.score_label.pack(side="right")
        # 「再玩一次」常驻右上角：画布再高也不会把按钮挤出屏幕
        self.retry_btn = tk.Button(info, text="再玩一次", command=self._restart,
                                   bg=BLUE, fg="white",
                                   font=(FONT, 11, "bold"), padx=14, pady=4,
                                   relief="flat", cursor="hand2",
                                   activebackground=BLUE_D)
        self.retry_btn.pack(side="right", padx=(0, 12))

        # 鼠标变成一把锤子：系统箭头藏起来，锤子图跟着鼠标走
        self.hammer_up = _hammer_photo(0)
        self.hammer_down = _hammer_photo(28) if self.hammer_up else None
        self.canvas = tk.Canvas(self, width=self.cv_w, height=self.cv_h,
                                bg="#e9f5e0",
                                highlightthickness=3,
                                highlightbackground=LINE, cursor="hand2")
        if self.hammer_up:
            try:
                self.canvas.config(cursor="none")
            except Exception:
                pass
        self.canvas.pack(pady=10)
        self.canvas.bind("<Button-1>", self._on_click)
        if self.hammer_up:
            self.hammer_id = self.canvas.create_image(
                0, 0, image=self.hammer_up, anchor="center", tags="hammer")
            self.canvas.itemconfigure(self.hammer_id, state="hidden")
            self.canvas.bind("<Motion>", self._move_hammer)
            self.canvas.bind("<Enter>", self._move_hammer)
            self.canvas.bind("<Leave>", lambda _e: self.canvas.itemconfigure(
                self.hammer_id, state="hidden"))
        self._holes = self._layout_holes()
        self._draw_field()

        self.prog_label = tk.Label(self, text="", bg=BG, fg=GRAY,
                                   font=(FONT, 12), justify="center")
        self.prog_label.pack(pady=(2, 0), padx=16)
        _auto_wrap(self.prog_label, self)
        self.bind("<Destroy>", self._cleanup)

        if self.finished:
            self._show_summary()
        else:
            self._after(self._start_level, 600, 0)

    # ---------- 定时器小工具（一起销毁，防切页后还在跑） ----------
    def _after(self, fn, ms, *args):
        aid = self.after(ms, lambda: fn(*args))
        self._afters.append(aid)
        return aid

    def _clear_afters(self):
        for aid in self._afters:
            try:
                self.after_cancel(aid)
            except Exception:
                pass
        self._afters = []

    # ---------- 场地绘制 ----------
    def _layout_holes(self):
        """洞位中心坐标（行列数跟着老师配的布局走）"""
        w, h = self.cv_w, self.cv_h
        holes = []
        pad_x, pad_y = 46, 30
        self.cell_w = (w - pad_x * 2) / self.COLS
        self.cell_h = (h - pad_y * 2) / self.ROWS
        for r in range(self.ROWS):
            for c in range(self.COLS):
                hx = pad_x + self.cell_w * (c + 0.5)
                hy = pad_y + self.cell_h * (r + 0.62)
                holes.append((hx, hy))
        return holes

    def _draw_field(self):
        """草地 + 土堆 + 洞口（洞口有 tag，随时抬到地鼠上层做遮挡）"""
        cv = self.canvas
        # 土堆/洞口大小跟着格子缩放：格子多了不能挤成一团
        mw = min(78, self.cell_w * 0.44)
        mh = max(15, min(26, self.cell_h * 0.16))
        hw, hh = mw * 0.74, max(10, mh * 0.66)
        for hx, hy in self._holes:
            cv.create_oval(hx - mw, hy - mh, hx + mw, hy + mh * 1.15,
                           fill="#c9a06a", outline="#b98b52", width=2,
                           tags="mound")
            cv.create_oval(hx - hw, hy - hh, hx + hw, hy + hh * 1.25,
                           fill="#2b1d12", outline="#1d130c", width=2,
                           tags="hole")

    # ---------- 关卡流程 ----------
    def _start_level(self, li):
        if not self.winfo_exists() or self.finished:
            return
        self.li = li
        self.pop_no = 0
        key = self.level_seq[li]
        p = self.level_params[key]
        if not self._t0:
            self._t0 = time.time()
        self.level_label.config(
            text=f"第 {li + 1} / {len(self.level_seq)} 关 · {p['name']}")
        self.score_label.config(text=self._score_text())
        self.canvas.delete("banner")
        self.canvas.create_text(
            self.cv_w // 2, self.cv_h // 2,
            text=f"第 {li + 1} 关 · {p['name']}关\n准备，出发！",
            font=(FONT, 26, "bold"), fill=BLUE, justify="center",
            tags="banner")
        self._after(self._pop_next, 1200)

    def _pop_next(self):
        if not self.winfo_exists() or self.finished or self.mole_up:
            return
        self.canvas.delete("banner")
        if self.pop_no >= self.rounds:
            self._end_level()
            return
        self.pop_no += 1
        self.st["progress"] = int(self.st.get("progress") or 0) + 1
        key = self.level_seq[self.li]
        p = self.level_params[key]
        holes = self._holes
        # 尽量别连续同一个洞冒出来
        prev = getattr(self, "_last_hole", -1)
        i = random.randrange(len(holes))
        if len(holes) > 1 and i == prev:
            i = (i + 1 + random.randrange(len(holes) - 1)) % len(holes)
        self._last_hole = i
        hx, hy = holes[i]
        size = max(18, int(p.get("size") or 44))
        # 格子多的时候地鼠别盖住隔壁的洞
        try:
            size = max(18, int(min(size, self.cell_w * 0.52, self.cell_h * 0.95)))
        except Exception:
            pass
        self._draw_mole(hx, hy, size)
        self.mole_up = True
        self.mole_bbox = (hx - size / 2, hy - size * 0.85,
                          hx + size / 2, hy + size * 0.15)
        self.pop_time = time.time()
        self._set_prog()
        self._after(self._escape, int(p.get("show_ms") or 1300))

    def _end_level(self):
        key = self.level_seq[self.li]
        hits, shown = self.level_stat.get(key, [0, 0])
        self.canvas.delete("banner")
        self.canvas.create_text(
            self.cv_w // 2, self.cv_h // 2,
            text=f"{self.level_params[key]['name']}关结束！"
                 f"打中 {hits} / {shown} 只",
            font=(FONT, 22, "bold"), fill=GREEN, justify="center",
            tags="banner")
        if self.li + 1 < len(self.level_seq):
            self._after(self._start_level, 1400, self.li + 1)
        else:
            self._after(self._finish, 1400)

    def _finish(self):
        self.finished = True
        self.st["finished"] = 1
        self.st["elapsed_ms"] = int((time.time() - self._t0) * 1000) \
            if self._t0 else int(self.st.get("elapsed_ms") or 0)
        self._show_summary()

    # ---------- 地鼠的画与动画 ----------
    def _draw_mole(self, hx, hy, size):
        """在洞口下方画一只地鼠，再升到洞口（被洞口黑椭圆挡住下半身）"""
        cv = self.canvas
        cv.delete("mole")
        cv.delete("fx")
        r = size / 2
        x, y = hx, hy + size * 0.9          # 起点：藏在洞底下
        ids = []
        er = max(4, r * 0.32)               # 耳朵
        ids.append(cv.create_oval(x - r * 0.95 - er, y - r * 0.72 - er,
                                  x - r * 0.95 + er, y - r * 0.72 + er,
                                  fill="#a9713d", outline="#7c4f26",
                                  width=2, tags="mole"))
        ids.append(cv.create_oval(x + r * 0.95 - er, y - r * 0.72 - er,
                                  x + r * 0.95 + er, y - r * 0.72 + er,
                                  fill="#a9713d", outline="#7c4f26",
                                  width=2, tags="mole"))
        ids.append(cv.create_oval(x - r, y - r, x + r, y + r,
                                  fill="#b9834b", outline="#8a5a2b",
                                  width=2, tags="mole"))
        er2 = max(2, r * 0.16)              # 眼睛
        for sx in (-0.38, 0.38):
            ids.append(cv.create_oval(x + r * sx - er2, y - r * 0.32 - er2,
                                      x + r * sx + er2, y - r * 0.32 + er2,
                                      fill="white", outline="#5b3c1c",
                                      width=1, tags="mole"))
            ids.append(cv.create_oval(x + r * sx - er2 * 0.45,
                                      y - r * 0.32 - er2 * 0.45,
                                      x + r * sx + er2 * 0.45,
                                      y - r * 0.32 + er2 * 0.45,
                                      fill="#241207", tags="mole"))
        nr = max(2, r * 0.18)               # 鼻子
        ids.append(cv.create_oval(x - nr, y - nr * 0.7, x + nr, y + nr * 0.7,
                                  fill="#e0708a", outline="#b64d66",
                                  width=1, tags="mole"))
        tw = max(2, r * 0.12)               # 门牙
        ids.append(cv.create_rectangle(x - tw, y + r * 0.22, x - 0.5,
                                       y + r * 0.52,
                                       fill="white", outline="#c9c9c9",
                                       tags="mole"))
        ids.append(cv.create_rectangle(x + 0.5, y + r * 0.22, x + tw,
                                       y + r * 0.52,
                                       fill="white", outline="#c9c9c9",
                                       tags="mole"))
        # 洞口黑椭圆抬到地鼠上层，地鼠下半截就藏在「洞」里
        cv.tag_raise("hole")
        # 冒出动画：4 步升到洞口
        target_y = hy - size * 0.32
        steps = 4
        dy = (target_y - y) / steps
        self._mole_ids = ids

        def step(n=0):
            if not self.winfo_exists():
                return
            cv.move("mole", 0, dy)
            if n + 1 < steps:
                self._after(step, 32, n + 1)
        self._after(step, 32, 0)

    def _mole_down(self, done=None):
        """地鼠缩回洞里，然后回调 done"""
        if not self.winfo_exists():
            return
        steps = 3
        try:
            bbox = self.canvas.bbox("mole")
        except Exception:
            bbox = None
        if not bbox:
            self.mole_up = False
            if done:
                self._after(done, 80)
            return
        dy = (bbox[3] - bbox[1]) * 0.75 / steps

        def step(n=0):
            if not self.winfo_exists():
                return
            self.canvas.move("mole", 0, dy)
            if n + 1 < steps:
                self._after(step, 28, n + 1)
            else:
                self.canvas.delete("mole")
                self.mole_up = False
                self.mole_bbox = None
                if done:
                    self._after(done, 80)
        self._after(step, 28, 0)

    # ---------- 一轮的生命周期 ----------
    def _move_hammer(self, e):
        """锤子跟着鼠标走（永远盖在最上层）"""
        if not self.hammer_up:
            return
        self.canvas.coords(self.hammer_id, e.x, e.y)
        self.canvas.itemconfigure(self.hammer_id, state="normal")
        self.canvas.tag_raise("hammer")

    def _swing(self):
        """点下去锤子抡一下（纯动画反馈，不影响判定）"""
        if not self.hammer_up:
            return
        try:
            self.canvas.itemconfigure(self.hammer_id, image=self.hammer_down)
        except Exception:
            return

        def back():
            try:
                if self.winfo_exists():
                    self.canvas.itemconfigure(self.hammer_id,
                                              image=self.hammer_up)
            except Exception:
                pass
        self._after(back, 150)

    def _on_click(self, e):
        self._swing()
        if self.finished or not self.mole_up or not self.mole_bbox:
            return
        x1, y1, x2, y2 = self.mole_bbox
        if x1 <= e.x <= x2 and y1 <= e.y <= y2:
            self._hit()
        else:
            # 点空了：不算漏掉，只记失误，洞口闪一下红提示
            self.st["errors"] = int(self.st.get("errors") or 0) + 1
            self.canvas.create_text(
                e.x, e.y - 18, text="点空啦", font=(FONT, 12, "bold"),
                fill=RED, tags="fx")
            self._after(self.canvas.delete, 350, "fx")

    def _hit(self):
        self.mole_up = False
        reaction = time.time() - self.pop_time
        st = self.st
        st["hits"] = int(st.get("hits") or 0) + 1
        st["_react_total"] = float(st.get("_react_total") or 0) + reaction
        st["reaction_ms"] = int(st["_react_total"] / st["hits"] * 1000)
        key = self.level_seq[self.li]
        h, s = self.level_stat.get(key, [0, 0])
        self.level_stat[key] = [h + 1, s + 1]
        self._update_acc()
        self._touch_elapsed()
        self.score_label.config(text=self._score_text())
        # 打中特效：变红 +「啪！」
        try:
            for it in self.canvas.find_withtag("mole"):
                self.canvas.itemconfig(it, fill="#e2703a")
            self.canvas.create_text(
                self.mole_bbox[0] + (self.mole_bbox[2] - self.mole_bbox[0]) / 2,
                self.mole_bbox[1] - 16, text="啪！", font=(FONT, 16, "bold"),
                fill=RED, tags="fx")
            self._after(self.canvas.delete, 300, "fx")
        except Exception:
            pass
        gap = int(self.level_params[self.level_seq[self.li]].get("gap_ms")
                  or 500)
        self._mole_down(done=lambda: self._after(self._pop_next, gap))

    def _escape(self):
        """超时没点到 → 地鼠跑了"""
        if not self.mole_up or self.finished or not self.winfo_exists():
            return
        self.mole_up = False
        key = self.level_seq[self.li]
        h, s = self.level_stat.get(key, [0, 0])
        self.level_stat[key] = [h, s + 1]
        self.st["escaped"] = int(self.st.get("escaped") or 0) + 1
        self._update_acc()
        self._touch_elapsed()
        self.score_label.config(text=self._score_text())
        gap = int(self.level_params[key].get("gap_ms") or 500)
        self._mole_down(done=lambda: self._after(self._pop_next, gap))

    def _update_acc(self):
        appeared = int(self.st.get("progress") or 0)
        hits = int(self.st.get("hits") or 0)
        self.st["accuracy"] = round(hits / max(1, appeared) * 100, 1)

    def _touch_elapsed(self):
        if self._t0:
            self.st["elapsed_ms"] = int((time.time() - self._t0) * 1000)

    def _score_text(self):
        return (f"打中 {int(self.st.get('hits') or 0)} 只 · "
                f"漏掉 {int(self.st.get('escaped') or 0)} 只")

    def _set_prog(self):
        key = self.level_seq[self.li]
        self.prog_label.config(
            text=f"{self.level_params[key]['name']}关："
                 f"第 {self.pop_no} / {self.rounds} 只"
                 f"（共 {len(self.level_seq)} 关，点中地鼠就算打到）")

    # ---------- 结束 ----------
    def _show_summary(self):
        if not self.winfo_exists():
            return
        self._clear_afters()
        self.mole_up = False
        self.mole_bbox = None
        self.canvas.delete("mole")
        self.canvas.delete("fx")
        self.canvas.delete("banner")
        hits = int(self.st.get("hits") or 0)
        escaped = int(self.st.get("escaped") or 0)
        total = int(self.st.get("total") or 0)
        acc = self.st.get("accuracy") or 0
        react = (self.st.get("reaction_ms") or 0) / 1000.0
        self.level_label.config(text="游戏结束")
        self.score_label.config(text="")
        # 横幅按画布居中、字号收一点 —— 大画布（5×5）上也不挡洞口和按钮
        cx, cy = self.cv_w // 2, self.cv_h // 2
        self.canvas.create_text(
            cx, cy - 34, text="完成啦！", font=(FONT, 28, "bold"),
            fill=GREEN, tags="banner")
        lines = [f"打中 {hits} / {total} 只 · 漏掉 {escaped} 只 · "
                 f"命中率 {acc:.0f}% · 平均反应 {react:.1f} 秒"]
        if self.level_stat:
            lines.append("　".join(
                f"{self.level_params[k]['name']} {h}/{s} 只"
                for k, (h, s) in
                ((k, self.level_stat[k]) for k in self.level_seq
                 if k in self.level_stat)))
        self.canvas.create_text(cx, cy + 26, text="\n".join(lines),
                                font=(FONT, 13), fill=INK, justify="center",
                                tags="banner")
        self.prog_label.config(text="地鼠跑啦，手要快哦！", fg=GRAY,
                               font=(FONT, 12))

    def _restart(self):
        self.panel.reset(self.task,
                         extras={"rounds": self.rounds,
                                 "level": self.cfg.get("level") or "all",
                                 "levels": self.level_params})

    def _cleanup(self, e=None):
        if e is not None and e.widget is not self:
            return
        self._clear_afters()
        self.mole_up = False
class TypingPanel(tk.Frame):
    """
    顶部导航里「看键按键 / 小写字母找按键 / 英文打字 / 中文打字」每个标签一页。
    统计存在 client.typing_state() 里（跨标签共享），上报由客户端常驻线程负责。
    """

    def __init__(self, parent, client, config, task, on_goto=None,
                 tail_next=None):
        super().__init__(parent, bg=BG)
        self.client = client
        self.task = task
        self.on_goto = on_goto              # 切到别的子任务（跳顶部标签）
        self.tail_next = tail_next          # (按钮文字, 回调)：打字全部做完后去画板/拼豆/交卷
        self.cfg = {t.get("task"): t for t in (config or {}).get("tasks", [])
                    if t.get("task")}
        self.current = None
        # 共享统计：切标签重建面板也不会丢
        self.stats = client.typing_state()["stats"]
        for t in TASK_ORDER:
            self.stats.setdefault(t, _new_stats(t, self.cfg.get(t) or {}))
        client.ensure_typing_reporter()

        head = tk.Frame(self, bg=BG)
        head.pack(fill="x", padx=16, pady=(12, 0))
        name = (self.cfg.get(task) or {}).get("name") or task
        tk.Label(head, text=name, bg=BG, fg=INK,
                 font=(FONT, 15, "bold")).pack(side="left")
        hint = tk.Label(head, text="不计分，比一比谁打得又快又准！"
                                   "（进度会自动报告给老师）",
                        bg=BG, fg=GRAY, font=(FONT, 11), justify="left")
        hint.pack(side="left", padx=10)
        _auto_wrap(hint, head, pad=200)

        # 底部翻页条（样式对齐理论题）：先 pack 底部条再放内容区 ——
        # 大画布（4×5/5×5）再高也挤不掉按钮；按钮靠右下角
        self.nav_bar = tk.Frame(self, bg=BG)
        self.nav_bar.pack(side="bottom", fill="x", padx=16, pady=(2, 12))
        tk.Frame(self, bg=LINE, height=1).pack(side="bottom", fill="x",
                                               padx=16, pady=(8, 6))
        self.stage = tk.Frame(self, bg=BG)
        self.stage.pack(fill="both", expand=True, padx=16, pady=(8, 0))

        self.reload()
        self._build_next_bar()

    # ---------- 内容 ----------
    def reload(self):
        """重建当前子任务的内容区（「再练一次」也走这里）"""
        for w in self.stage.winfo_children():
            w.destroy()
        cfg = self.cfg.get(self.task)
        if cfg is None:
            tk.Label(self.stage, text="本场考试没有配置这个打字任务",
                     bg=BG, fg=GRAY, font=(FONT, 13)).pack(expand=True)
            return
        if self.task == "whack_mole":
            cls = WhackMoleTask
        elif self.task in ("key_press", "case_press"):
            cls = PressTask
        elif self.task == "name_typing":
            cls = NameTypingTask
        else:
            cls = TypingTask
        self.current = cls(self.stage, self, self.task, cfg)
        self.current.pack(fill="both", expand=True)

    def reset(self, task, extras=None):
        """把某个子任务的统计归零，重新开始练"""
        cfg = dict(self.cfg.get(task) or {})
        if extras:
            cfg.update(extras)
        self.stats[task] = _new_stats(task, cfg)
        if task == self.task:
            self.reload()

    # ---------- 底部「上一题/下一题」 ----------
    def _enabled_tasks(self):
        return [t for t in TASK_ORDER if t in self.cfg]

    def _build_next_bar(self):
        """上一题/下一题：底部条靠右下角（和理论题一致的底部条样式）"""
        for w in self.nav_bar.winfo_children():
            w.destroy()
        tasks = self._enabled_tasks()
        try:
            i = tasks.index(self.task)
        except ValueError:
            return
        # 先 pack「下一题」再 pack「上一题」→ 上一题在左、下一题在右，整体靠右
        if i + 1 < len(tasks):
            nxt = tasks[i + 1]
            nxt_name = (self.cfg.get(nxt) or {}).get("name") or nxt
            self._nav_btn(f"下一题：{nxt_name} ▶", nxt, primary=True)
        elif self.tail_next:
            # 打字任务已到最后一个：下一题去画板/拼豆创作，没有画图就去交卷
            label, cb = self.tail_next
            tk.Button(self.nav_bar, text=label, command=cb,
                      font=(FONT, 11), padx=16, pady=6, bg=BLUE, fg="white",
                      relief="flat", bd=0, cursor="hand2",
                      activebackground=BLUE_D
                      ).pack(side="right", padx=(8, 0))
        if i > 0:
            prv = tasks[i - 1]
            prv_name = (self.cfg.get(prv) or {}).get("name") or prv
            self._nav_btn(f"◀ 上一题：{prv_name}", prv, primary=False)

    def _nav_btn(self, text, key, primary):
        tk.Button(self.nav_bar, text=text,
                  command=(lambda k=key: self.on_goto(k)) if self.on_goto
                  else (lambda: None),
                  font=(FONT, 11), padx=16, pady=6,
                  bg=BLUE if primary else "SystemButtonFace",
                  fg="white" if primary else INK,
                  relief="flat" if primary else "solid",
                  bd=0 if primary else 1, cursor="hand2",
                  activebackground=BLUE_D if primary else LINE
                  ).pack(side="right", padx=(8, 0))
