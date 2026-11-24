# -*- coding: utf-8 -*-
"""
拖拽题画板（学生端）—— 把「拖拽项」拖到「槽位」里

交互（小学生也好懂）：
    · 按住项目拖动，松手时落在哪个格子上就放进哪个格子
    · 落在格子外面 = 退回下面的备选区
    · 点一下已经放好的项目 = 取回来重新拖
答案格式："1:A|2:B|3:C"（第几格:哪个项目），与服务端 db.judge 约定一致。
"""
import tkinter as tk

BG = "#f8fafc"
CARD = "#ffffff"
BLUE = "#2563eb"
BLUE_D = "#1d4ed8"
GREEN = "#15803d"
GREEN_L = "#f0fdf4"
RED_L = "#fef2f2"
GRAY = "#5f6b7d"
INK = "#0f172a"
LINE = "#cbd5e1"
SLOT_BG = "#f1f5f9"
SLOT_FILL = "#e0e7ff"

FONT = "Microsoft YaHei"
LETTERS = "ABCDEFGH"


class DragBoard(tk.Frame):
    """
    :param items:   拖拽项文字列表（支持 "img:文件名.png 说明"）
    :param slots:   槽位文字列表（从左到右、从上到下）
    :param on_change: 变化回调 fn(answer_str)
    :param image_getter: fn(图片名, 回调) —— 由主界面异步取图；回调收到 PhotoImage
    """

    def __init__(self, parent, items, slots, on_change=None,
                 image_getter=None, initial=""):
        super().__init__(parent, bg=BG)
        self.items = [str(x) for x in (items or [])]
        self.slots = [str(x) for x in (slots or [])]
        self.on_change = on_change
        self.image_getter = image_getter
        self.photos = {}                 # 字母 -> PhotoImage（异步到货）
        self.images = {}                 # 字母 -> canvas image id
        self.placed = {}                 # 槽位序号(1起) -> 字母
        self.item_state = {}             # 字母 -> ("tray", 序号) / ("slot", 槽位序号)
        self.selected = None
        self.drag_letter = None
        self._drag_offset = (0, 0)
        self._laid_out = False
        self._last_w = 0          # ⚠️ 不能叫 _w：那是 tkinter 内部记录控件路径的属性

        self.slot_boxes = {}
        self.item_boxes = {}
        self.canvas = tk.Canvas(self, bg=BG, highlightthickness=0,
                                height=420)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", self._on_configure)
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_motion)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Destroy>", self._on_destroy)

        # 载入已有的作答（切页回来时恢复）
        for part in str(initial or "").split("|"):
            if ":" not in part:
                continue
            try:
                slot, letter = part.split(":", 1)
                slot = int(slot.strip())
                letter = letter.strip().upper()
            except ValueError:
                continue
            if 1 <= slot <= len(self.slots) and letter in LETTERS[:len(self.items)]:
                self.placed[slot] = letter
        self._sync_state()

    # ---------- 布局 ----------
    def _slot_cols(self):
        return 2 if len(self.slots) <= 4 else 3

    def _item_cols(self):
        return 4 if len(self.items) <= 8 else 4

    def _on_configure(self, e=None):
        w = self.canvas.winfo_width()
        if w < 200:
            return
        # 首次一定要布局一次（_laid_out 之前一直是 False，防抖条件形同虚设）；
        # 之后宽度小幅变化（<8px，比如 redraw 里改高度触发的 Configure）就跳过。
        if self._laid_out and abs(w - self._last_w) < 8:
            return
        self._last_w = w
        self._laid_out = True
        self.redraw()

    def redraw(self):
        """按当前画布宽度重画（窗口缩放、图片到货都会调它）"""
        c = self.canvas
        c.delete("all")
        self.images = {}
        w = max(360, c.winfo_width())
        pad, gap = 12, 12

        # ---- 槽位区（上）----
        cols = self._slot_cols()
        sw = (w - pad * 2 - gap * (cols - 1)) / cols
        sh = 78
        self.slot_boxes = {}
        for i in range(len(self.slots)):
            r, col = divmod(i, cols)
            x0 = pad + col * (sw + gap)
            y0 = pad + r * (sh + gap)
            box = (x0, y0, x0 + sw, y0 + sh)
            self.slot_boxes[i + 1] = box
            c.create_rectangle(*box, fill=SLOT_BG, outline=LINE, width=2,
                               tags=("slot", f"slot{i + 1}"))
            c.create_text(x0 + 8, y0 + 10, anchor="nw", width=sw - 16,
                          text=f"{i + 1}. {self.slots[i]}", fill=GRAY,
                          font=(FONT, 11), tags=("slot_label",))
        slots_h = pad + ((len(self.slots) + cols - 1) // cols) * (sh + gap)

        # ---- 备选区（下）----
        c.create_text(pad, slots_h + 2, anchor="nw", fill=INK,
                      text="↓ 把下面的项目拖到上面的格子里（拖错了点一下就能取回来）",
                      font=(FONT, 11))
        tray_y = slots_h + 26
        icols = self._item_cols()
        iw = (w - pad * 2 - gap * (icols - 1)) / icols
        ih = 62
        self.item_boxes = {}
        for i in range(len(self.items)):
            r, col = divmod(i, icols)
            x0 = pad + col * (iw + gap)
            y0 = tray_y + r * (ih + gap)
            self.item_boxes[LETTERS[i]] = (x0, y0, x0 + iw, y0 + ih)
        tray_rows = (len(self.items) + icols - 1) // icols
        total_h = tray_y + tray_rows * (ih + gap) + pad
        c.config(height=int(total_h))

        # ---- 画每个项目（按当前状态放到槽位或托盘）----
        for i, text in enumerate(self.items):
            letter = LETTERS[i]
            self._draw_item(letter, text)
        for slot, letter in self.placed.items():
            self._place_in_slot(letter, slot)
        self.canvas.update_idletasks()

    def _draw_item(self, letter, text):
        c = self.canvas
        x0, y0, x1, y1 = self.item_boxes[letter]
        label, img_name = _split_img(text)
        tag = f"item{letter}"
        # ⚠️ 矩形单独打 rect 标签：Canvas 的 itemconfig 是按标签**整组**生效的，
        #    直接对 "itemA" 设 -outline 会连里面的文字/图片一起设，
        #    而文字项没有 outline 选项 → 一拖就崩（实测踩过）。
        c.create_rectangle(x0, y0, x1, y1, fill=CARD, outline=BLUE, width=2,
                           tags=("item", tag, f"rect{letter}"))
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        photo = self.photos.get(letter)
        if photo:
            c.create_image(x0 + 34, cy, image=photo, tags=("item", tag))
            c.create_text(x0 + 62, cy, anchor="w", text=f"{letter}. {label}",
                          fill=INK, font=(FONT, 12), width=max(40, x1 - x0 - 70),
                          tags=("item", tag))
        else:
            c.create_text(cx, cy, text=f"{letter}. {label}", fill=INK,
                          font=(FONT, 12), width=max(40, x1 - x0 - 16),
                          justify="center", tags=("item", tag))
            if img_name and self.image_getter:
                self._request_image(letter, img_name)

    def _request_image(self, letter, name):
        """异步取图；到货后重画一次（不阻塞界面）"""
        def ready(photo):
            if photo:
                self.photos[letter] = photo
                try:
                    self.redraw()
                except Exception:
                    pass
        try:
            self.image_getter(name, ready)
        except Exception:
            pass

    # ---------- 交互 ----------
    def _hit_item(self, x, y):
        for letter in reversed(list(self.item_boxes.keys())):
            box = self.canvas.bbox(f"item{letter}") or self.item_boxes[letter]
            if box and box[0] <= x <= box[2] and box[1] <= y <= box[3]:
                return letter
        return None

    def _hit_slot(self, x, y):
        for slot, box in self.slot_boxes.items():
            if box[0] <= x <= box[2] and box[1] <= y <= box[3]:
                return slot
        return None

    def _on_press(self, e):
        letter = self._hit_item(e.x, e.y)
        if not letter:
            return
        self.drag_letter = letter
        # ⚠️ 偏移要用项目**当前**位置算：
        #    已经放进格子的项目，_item_boxes 里还是托盘的原始坐标，
        #    用它会算出错误偏移 —— 表现为"能拖但跟着鼠标乱跑"。
        cur = self.canvas.bbox(f"item{letter}") or self.item_boxes.get(letter)
        if cur:
            self._drag_offset = (e.x - cur[0], e.y - cur[1])
        else:
            self._drag_offset = (0, 0)
        self.canvas.tag_raise(f"item{letter}")

    def _on_motion(self, e):
        if not self.drag_letter:
            return
        letter = self.drag_letter
        tag = f"item{letter}"
        box = self.canvas.bbox(tag)
        if not box:
            return
        dx = e.x - self._drag_offset[0] - box[0]
        dy = e.y - self._drag_offset[1] - box[1]
        self.canvas.move(tag, dx, dy)
        slot = self._hit_slot(e.x, e.y)
        # 悬停高亮，让学生看得清要放到哪一格
        for s, b in self.slot_boxes.items():
            self.canvas.itemconfig(f"slot{s}",
                                   fill=SLOT_FILL if s == slot else SLOT_BG)

    def _on_release(self, e):
        letter = self.drag_letter
        self.drag_letter = None
        if not letter:
            return
        for s in self.slot_boxes:
            self.canvas.itemconfig(f"slot{s}", fill=SLOT_BG)
        slot = self._hit_slot(e.x, e.y)
        if slot:
            self.place(letter, slot)
        else:
            self.unplace(letter)      # 落在格子外 = 取消这次放置
        self._sync_state()

    def _place_in_slot(self, letter, slot):
        box = self.slot_boxes[slot]
        cur = self.canvas.bbox(f"item{letter}")
        if not cur:
            return
        cx = (box[0] + box[2]) / 2 - (cur[0] + cur[2]) / 2
        cy = (box[1] + box[3]) / 2 + 12 - (cur[1] + cur[3]) / 2
        self.canvas.move(f"item{letter}", cx, cy)
        self.canvas.itemconfig(f"rect{letter}", outline=GREEN)
        self.item_state[letter] = ("slot", slot)

    def _snap_to_tray(self, letter):
        box = self.item_boxes.get(letter)
        cur = self.canvas.bbox(f"item{letter}")
        if not box or not cur:
            return
        cx = (box[0] + box[2]) / 2 - (cur[0] + cur[2]) / 2
        cy = (box[1] + box[3]) / 2 - (cur[1] + cur[3]) / 2
        self.canvas.move(f"item{letter}", cx, cy)
        self.canvas.itemconfig(f"rect{letter}", outline=BLUE)
        self.item_state[letter] = ("tray", None)

    def _sync_state(self):
        """从 placed 重建每个项目的位置状态（初始载入 / 拖放后同步）"""
        slot_of = {letter: s for s, letter in self.placed.items()}
        for i, _text in enumerate(self.items):
            letter = LETTERS[i]
            slot = slot_of.get(letter)
            if slot is not None:
                self.item_state[letter] = ("slot", slot)
            elif self.item_state.get(letter, ("tray", None))[0] == "slot":
                self.item_state[letter] = ("tray", None)
            else:
                self.item_state.setdefault(letter, ("tray", None))

    def _notify(self):
        if self.on_change:
            try:
                self.on_change(self.answer())
            except Exception:
                pass

    # ---------- 对外 ----------
    def place(self, letter, slot):
        """
        把项目放进某个槽位（松手时调用）。
        这一格原来有别的项目 → 那个自动退回备选区（一年级学生也看得懂的效果）。
        """
        if letter not in self.item_boxes and self.item_boxes:
            return False
        if not (1 <= int(slot) <= len(self.slots)):
            return False
        slot = int(slot)
        # 同一个项目只应出现在一个格子里：先把它从原来的格子拿掉
        for s in [s for s, l in self.placed.items() if l == letter]:
            self.placed.pop(s, None)
        old = self.placed.get(slot)
        if old and old != letter:
            self.placed.pop(slot, None)
            self.item_state[old] = ("tray", None)
            if old in self.item_boxes:
                self._snap_to_tray(old)
        self.placed[slot] = letter
        self.item_state[letter] = ("slot", slot)
        # 画布还没布局（极少见）时只更新状态，等 redraw 再落位
        if letter in self.item_boxes and slot in getattr(self, "slot_boxes", {}):
            self._place_in_slot(letter, slot)
        self._notify()
        return True

    def unplace(self, letter):
        """把项目取回备选区"""
        if letter not in self.item_state:
            return
        self.placed = {k: v for k, v in self.placed.items() if v != letter}
        self.item_state[letter] = ("tray", None)
        if letter in self.item_boxes:
            self._snap_to_tray(letter)
        self._notify()

    def answer(self):
        """当前作答 → "1:A|2:B"（没放满就只报放了的那几个）"""
        parts = []
        for slot in sorted(self.placed):
            letter = self.placed[slot]
            if letter:
                parts.append(f"{slot}:{letter}")
        return "|".join(parts)

    def clear(self):
        self.placed = {}
        for i, _t in enumerate(self.items):
            self.item_state[LETTERS[i]] = ("tray", None)
        self.redraw()
        self._notify()

    def _on_destroy(self, e=None):
        if e is not None and e.widget is not self:
            return


def _split_img(text):
    """与 gui.py 的选项图标记一致：'img:文件名 文字' → (文字, 文件名)"""
    import re
    s = str(text or "")
    m = re.match(r"^\s*(?:[A-Za-z][.、．]\s*)?img:(\S+)\s*(.*)$", s)
    if m and m.group(1):
        return (m.group(2) or "").strip() or m.group(1), m.group(1)
    return s, None
