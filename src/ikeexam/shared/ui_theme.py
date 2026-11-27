# -*- coding: utf-8 -*-
"""ikeexam 双端共用的 Tkinter / ttk 主题与交互基础组件。"""
import ctypes
import sys
import time
import traceback
from pathlib import Path
import tkinter as tk
from tkinter import font as tkfont
from tkinter import messagebox, ttk

LIGHT = {
    "bg": "#FCF8F2", "surface": "#FFFFFF", "surface_alt": "#FFF3E8",
    "border": "#27324A", "text": "#27324A", "text_strong": "#1B2540",
    "text_muted": "#5F6B7D", "text_on_accent": "#27324A",
    "primary": "#20C46A", "primary_hover": "#159B52",
    "primary_active": "#0F7F43", "primary_soft": "#E9FBF1",
    "success": "#15803D", "success_soft": "#E9FBF1",
    "warning": "#B7791F", "warning_soft": "#F1F9D8", "danger": "#C2414B",
    "danger_soft": "#FFB4AA", "danger_border": "#27324A",
    "info": "#0E7490", "info_soft": "#E6F7FB", "row_alt": "#FFF9EE",
    "heading": "#E6E6FA", "heading_active": "#D9D5FA",
    "tab_bg": "#F1F9D8", "tab_active": "#F6C945", "focus_ring": "#38BDF8",
    "disabled_bg": "#E7E3DA", "disabled_fg": "#7A746B",
    "scroll_thumb": "#8B7CF6", "scroll_hover": "#6D5DD3",
    "arrow": "#27324A", "mint": "#E9FBF1", "sky": "#E6F7FB",
    "coral": "#FFB4AA", "lemon": "#F1F9D8", "lavender": "#E6E6FA",
    "yellow": "#F6C945", "purple": "#8B7CF6", "shadow": "#27324A",
}
DARK = {
    "bg": "#0F172A", "surface": "#1E293B", "surface_alt": "#263449",
    "border": "#334155", "text": "#E2E8F0", "text_strong": "#F8FAFC",
    "text_muted": "#94A3B8", "text_on_accent": "#FFFFFF",
    "primary": "#3B82F6", "primary_hover": "#60A5FA",
    "primary_active": "#2563EB", "primary_soft": "#1E3A5F",
    "success": "#4ADE80", "success_soft": "#163526",
    "warning": "#FBBF24", "warning_soft": "#3A2D18", "danger": "#F87171",
    "danger_soft": "#3B1F27", "danger_border": "#7F3B45",
    "info": "#38BDF8", "row_alt": "#1B2739", "heading": "#243247",
    "heading_active": "#2E4057", "tab_bg": "#16213A",
    "tab_active": "#243247", "focus_ring": "#3B82F6",
    "disabled_bg": "#1A2536", "disabled_fg": "#64748B",
    "scroll_thumb": "#475569", "scroll_hover": "#64748B",
    "arrow": "#94A3B8", "mint": "#163526", "sky": "#163247",
    "coral": "#3B1F27", "lemon": "#3A2D18", "lavender": "#2E2952",
    "yellow": "#FBBF24", "purple": "#A78BFA", "shadow": "#0F172A",
}
TOKENS = {"light": LIGHT, "dark": DARK}
SPACING = {"xs": 4, "sm": 8, "md": 12, "lg": 16, "xl": 24, "xxl": 32}
FONT_CANDIDATES = (
    "Microsoft YaHei UI", "Microsoft YaHei", "微软雅黑", "Segoe UI",
    "PingFang SC", "Hiragino Sans GB", "Noto Sans CJK SC", "Noto Sans SC",
    "Source Han Sans SC", "WenQuanYi Micro Hei", "WenQuanYi Zen Hei",
    "DejaVu Sans", "Arial",
)


def pick_family(root=None):
    try:
        available = set(tkfont.families(root))
    except Exception:
        available = set()
    for name in FONT_CANDIDATES:
        if name in available:
            return name
    try:
        return tkfont.nametofont("TkDefaultFont").actual("family")
    except Exception:
        return FONT_CANDIDATES[0] if sys.platform == "win32" else "DejaVu Sans"


def build_fonts(root=None, base_size=10):
    family = pick_family(root)
    return {
        "caption": (family, base_size - 1),
        "body": (family, base_size),
        "body_bold": (family, base_size, "bold"),
        "subtitle": (family, base_size + 2, "bold"),
        "title": (family, base_size + 6, "bold"),
        "h1": (family, base_size + 12, "bold"),
    }


def enable_high_dpi():
    if sys.platform != "win32":
        return False
    try:
        return bool(ctypes.windll.shcore.SetProcessDpiAwarenessContext(-4))
    except Exception:
        pass
    try:
        return bool(ctypes.windll.shcore.SetProcessDpiAwareness(1))
    except Exception:
        pass
    try:
        return bool(ctypes.windll.user32.SetProcessDPIAware())
    except Exception:
        return False


def apply_tk_scaling(root):
    if sys.platform != "win32":
        return
    try:
        scale = ctypes.windll.shcore.GetScaleFactorForDevice(0) / 100.0
        root.tk.call("tk", "scaling", scale)
    except Exception:
        pass


def center_window(win, width, height, offset_y=0.5):
    win.update_idletasks()
    sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
    x = max(0, (sw - width) // 2)
    y = max(0, int((sh - height) * offset_y))
    win.geometry(f"{width}x{height}+{x}+{y}")


def maximize_window(win):
    try:
        win.state("zoomed")
        return
    except Exception:
        pass
    try:
        win.attributes("-zoomed", True)
    except Exception:
        pass


def add_hard_shadow(widget, offset=6, color=None):
    """为固定尺寸卡片添加 Memphis 硬边阴影。"""
    color = color or getattr(widget, "_ui_tokens", LIGHT)["shadow"]
    shadow = tk.Frame(widget.master, bg=color, bd=0)
    widget.update_idletasks()
    shadow.place(in_=widget, relx=0, rely=0, x=offset, y=offset, anchor="nw",
                 width=widget.winfo_reqwidth(), height=widget.winfo_reqheight())
    shadow.lower()
    return shadow


class MemphisBanner(tk.Canvas):
    """奶油色几何横幅：太阳、波浪、圆点与硬阴影标题牌。"""

    def __init__(self, master, title="", subtitle="", height=92, tokens=None, **kwargs):
        self.tokens = tokens or getattr(master, "_ui_tokens", LIGHT)
        super().__init__(master, height=height, bg=self.tokens["bg"],
                         highlightthickness=0, bd=0, **kwargs)
        self.title = title
        self.subtitle = subtitle
        family = pick_family(self)
        self._title_font = tkfont.Font(root=self, family=family, size=-20,
                                       weight="bold")
        self._sub_font = tkfont.Font(root=self, family=family, size=-11)
        self.bind("<Configure>", lambda event: self._draw())

    @staticmethod
    def _octagon(x1, y1, x2, y2, cut=12):
        return [
            x1 + cut, y1, x2 - cut, y1, x2, y1 + cut,
            x2, y2 - cut, x2 - cut, y2, x1 + cut, y2,
            x1, y2 - cut, x1, y1 + cut,
        ]

    def _draw(self):
        self.delete("all")
        tokens = self.tokens
        width = max(self.winfo_width(), 360)
        height = max(self.winfo_height(), 72)
        plaque_width = min(max(300, self._title_font.measure(self.title) + 48),
                           max(260, width - 190))
        x, y = 22, 12
        plaque_height = height - 24
        self.create_polygon(self._octagon(x + 5, y + 5, x + plaque_width + 5,
                                           y + plaque_height + 5),
                            fill=tokens["shadow"], outline=tokens["shadow"])
        self.create_polygon(self._octagon(x, y, x + plaque_width,
                                           y + plaque_height),
                            fill=tokens["lavender"], outline=tokens["border"],
                            width=2)
        if self.title:
            self.create_text(x + 18, y + plaque_height / 2 - 8,
                             text=self.title, anchor="w", fill=tokens["text_strong"],
                             font=self._title_font)
        if self.subtitle:
            self.create_text(x + 18, y + plaque_height / 2 + 17,
                             text=self.subtitle, anchor="w", fill=tokens["text_muted"],
                             font=self._sub_font)

        sun_x, sun_y = width - 72, 28
        self.create_oval(sun_x - 17, sun_y - 17, sun_x + 17, sun_y + 17,
                         fill=tokens["yellow"], outline=tokens["border"], width=2)
        for dx, dy in ((0, -27), (0, 27), (-27, 0), (27, 0),
                       (-19, -19), (19, -19), (-19, 19), (19, 19)):
            self.create_line(sun_x + dx * .72, sun_y + dy * .72,
                             sun_x + dx, sun_y + dy, fill=tokens["border"], width=2)
        points = []
        for index in range(9):
            px = width - 176 + index * 15
            py = height - 24 + (6 if index % 2 else -6)
            points.extend((px, py))
        self.create_line(*points, fill=tokens["coral"], width=5,
                         smooth=True, splinesteps=24)
        self.create_polygon(width - 176, 18, width - 150, 52, width - 126, 18,
                            fill=tokens["sky"], outline=tokens["border"], width=2)
        for row in range(3):
            for column in range(3):
                cx = width - 116 + column * 14
                cy = 66 + row * 10
                self.create_oval(cx - 2, cy - 2, cx + 2, cy + 2,
                                 fill=tokens["purple"], outline=tokens["purple"])


def apply_theme(root, mode="light"):
    tokens = TOKENS[mode]
    fonts = build_fonts(root)
    root._ui_tokens = tokens
    root._ui_fonts = fonts
    style = ttk.Style(root)
    style.theme_use("clam" if "clam" in style.theme_names() else style.theme_names()[0])
    body, bold, small = fonts["body"], fonts["body_bold"], fonts["caption"]
    title, h1, sub = fonts["title"], fonts["h1"], fonts["subtitle"]

    style.configure(".", font=body, background=tokens["bg"], foreground=tokens["text"])
    style.configure("TFrame", background=tokens["bg"])
    style.configure("Surface.TFrame", background=tokens["surface"])
    style.configure("Card.TFrame", background=tokens["surface"], relief="solid",
                    borderwidth=2, bordercolor=tokens["border"])
    for name, color in (("Mint", tokens["mint"]), ("Sky", tokens["sky"]),
                        ("Coral", tokens["coral"]), ("Lemon", tokens["lemon"]),
                        ("Lavender", tokens["lavender"])):
        style.configure(f"{name}.TFrame", background=color, relief="solid",
                        borderwidth=2, bordercolor=tokens["border"])
    style.configure("TLabel", background=tokens["surface"], foreground=tokens["text"],
                    font=body, padding=0)
    style.configure("OnBg.TLabel", background=tokens["bg"], foreground=tokens["text"])
    style.configure("Muted.TLabel", foreground=tokens["text_muted"], font=small)
    style.configure("Subtitle.TLabel", foreground=tokens["text_strong"], font=sub)
    style.configure("Title.TLabel", foreground=tokens["text_strong"], font=title)
    style.configure("H1.TLabel", foreground=tokens["text_strong"], font=h1)
    style.configure("Plaque.TLabel", background=tokens["lavender"],
                    foreground=tokens["text_strong"], font=sub, padding=(16, 10),
                    relief="solid", borderwidth=2, bordercolor=tokens["border"])
    style.configure("Success.TLabel", foreground=tokens["success"])
    style.configure("Danger.TLabel", foreground=tokens["danger"])
    style.configure("Info.TLabel", foreground=tokens["info"])

    style.configure("Choice.TCheckbutton", relief="solid", borderwidth=2,
                    padding=(12, 8), font=bold, background=tokens["surface"],
                    foreground=tokens["text"], bordercolor=tokens["border"])
    style.map("Choice.TCheckbutton",
              background=[("selected", tokens["mint"]),
                          ("active", tokens["lemon"]),
                          ("disabled", tokens["disabled_bg"])],
              foreground=[("selected", tokens["text_strong"]),
                          ("disabled", tokens["disabled_fg"])],
              bordercolor=[("selected", tokens["primary"]),
                           ("active", tokens["border"])])
    style.configure("TButton", relief="solid", borderwidth=2, padding=(14, 8),
                    anchor="center", background=tokens["surface"],
                    foreground=tokens["text"], bordercolor=tokens["border"], font=bold)
    style.map("TButton",
              background=[("pressed", tokens["lemon"]),
                          ("active", tokens["sky"]),
                          ("disabled", tokens["disabled_bg"])],
              foreground=[("disabled", tokens["disabled_fg"])],
              bordercolor=[("focus", tokens["focus_ring"])])
    style.configure("Accent.TButton", relief="solid", borderwidth=2,
                    padding=(16, 9), background=tokens["primary"],
                    foreground=tokens["text_on_accent"], font=bold,
                    bordercolor=tokens["border"])
    style.map("Accent.TButton",
              background=[("pressed", tokens["primary_active"]),
                          ("active", tokens["primary_hover"]),
                          ("disabled", tokens["disabled_bg"])],
              foreground=[("disabled", tokens["disabled_fg"])])
    style.configure("Danger.TButton", relief="solid", borderwidth=2,
                    padding=(14, 8), background=tokens["danger_soft"],
                    foreground=tokens["text_strong"], bordercolor=tokens["border"])
    style.map("Danger.TButton",
              background=[("pressed", tokens["danger"]),
                          ("active", tokens["coral"])],
              foreground=[("pressed", "#FFFFFF")])
    style.configure("Ghost.TButton", relief="solid", borderwidth=2,
                    padding=(10, 5), background=tokens["surface"],
                    foreground=tokens["text"], font=bold, bordercolor=tokens["border"])
    style.map("Ghost.TButton",
              background=[("pressed", tokens["lavender"]),
                          ("active", tokens["sky"])])
    style.configure("Toolbutton", relief="flat", borderwidth=0, padding=(10, 6),
                    background=tokens["bg"], foreground=tokens["text"])
    style.map("Toolbutton",
              background=[("selected", tokens["primary"]),
                          ("active", tokens["surface_alt"])],
              foreground=[("selected", tokens["text_on_accent"])])

    style.configure("TEntry", fieldbackground=tokens["surface"],
                    foreground=tokens["text"], bordercolor=tokens["border"],
                    lightcolor=tokens["border"], darkcolor=tokens["border"],
                    borderwidth=2, relief="solid", padding=(10, 8), insertwidth=2)
    style.map("TEntry",
              bordercolor=[("focus", tokens["primary"]),
                           ("invalid", tokens["danger"]),
                           ("disabled", tokens["border"])],
              lightcolor=[("focus", tokens["primary"])],
              darkcolor=[("focus", tokens["primary"])],
              fieldbackground=[("disabled", tokens["disabled_bg"]),
                               ("readonly", tokens["surface_alt"])],
              foreground=[("disabled", tokens["disabled_fg"])])
    style.configure("Invalid.TEntry", fieldbackground=tokens["surface"],
                    bordercolor=tokens["danger"], lightcolor=tokens["danger"],
                    darkcolor=tokens["danger"], padding=(10, 8))
    style.configure("TSpinbox", fieldbackground=tokens["surface"],
                    foreground=tokens["text"], bordercolor=tokens["border"],
                    lightcolor=tokens["border"], darkcolor=tokens["border"],
                    borderwidth=2, relief="solid",
                    arrowcolor=tokens["arrow"], arrowsize=13, padding=(10, 8))
    style.map("TSpinbox", bordercolor=[("focus", tokens["primary"])],
              arrowcolor=[("active", tokens["primary"]),
                          ("disabled", tokens["disabled_fg"])])
    style.configure("TCombobox", fieldbackground=tokens["surface"],
                    foreground=tokens["text"], bordercolor=tokens["border"],
                    lightcolor=tokens["border"], darkcolor=tokens["border"],
                    borderwidth=2, relief="solid",
                    arrowcolor=tokens["arrow"], arrowsize=13, padding=(10, 8))
    style.map("TCombobox", bordercolor=[("focus", tokens["primary"])],
              fieldbackground=[("readonly", tokens["surface"]),
                               ("disabled", tokens["disabled_bg"])],
              arrowcolor=[("active", tokens["primary"])])

    style.configure("TNotebook", background=tokens["bg"], bordercolor=tokens["border"],
                    borderwidth=0, tabmargins=(0, 8, 0, 0))
    style.configure("Main.TNotebook", background=tokens["bg"],
                    bordercolor=tokens["border"], borderwidth=0,
                    tabmargins=(0, 8, 0, 0))
    tab_layout = [
        ("Notebook.tab", {"sticky": "nswe", "bordercolor": tokens["tab_bg"],
                          "lightcolor": tokens["tab_bg"],
                          "darkcolor": tokens["tab_bg"], "children": [
            ("Notebook.padding", {"side": "top", "sticky": "nswe", "children": [
                ("Notebook.label", {"side": "top", "sticky": ""})
            ]})
        ]})
    ]
    for tab_style in ("TNotebook.Tab", "Main.TNotebook.Tab"):
        style.configure(tab_style, background=tokens["tab_bg"],
                        foreground=tokens["text"], padding=(16, 9),
                        font=bold, borderwidth=2, bordercolor=tokens["border"],
                        lightcolor=tokens["border"], darkcolor=tokens["border"])
        style.map(tab_style,
                  background=[("selected", tokens["lavender"]),
                              ("active", tokens["sky"]),
                              ("disabled", tokens["disabled_bg"])],
                  foreground=[("selected", tokens["text_strong"]),
                              ("active", tokens["text"]),
                              ("disabled", tokens["disabled_fg"])],
                  bordercolor=[("selected", tokens["purple"]),
                               ("active", tokens["border"])],
                  expand=[("selected", (1, 1, 1, 0))])
        try:
            style.layout(tab_style, tab_layout)
        except tk.TclError:
            pass

    for tv in ("Treeview", "Data.Treeview"):
        style.configure(tv, background=tokens["surface"],
                        fieldbackground=tokens["surface"], foreground=tokens["text"],
                        rowheight=36, bordercolor=tokens["border"], borderwidth=1,
                        font=body)
        style.map(tv, background=[("selected", tokens["mint"])],
                  foreground=[("selected", tokens["text_strong"])])
    for hd in ("Treeview.Heading", "Data.Treeview.Heading"):
        style.configure(hd, background=tokens["heading"],
                        foreground=tokens["text_strong"], font=bold,
                        padding=(10, 10), relief="flat")
        style.map(hd, background=[("active", tokens["heading_active"])])
    style.configure("TScrollbar", background=tokens["scroll_thumb"],
                    troughcolor=tokens["bg"], bordercolor=tokens["bg"],
                    arrowcolor=tokens["arrow"], arrowsize=12, width=10,
                    relief="flat")
    style.map("TScrollbar",
              background=[("active", tokens["scroll_hover"]),
                          ("pressed", tokens["arrow"])],
              arrowcolor=[("active", tokens["primary"])])
    for name, color in (("Accent", tokens["primary"]),
                        ("Success", tokens["success"]),
                        ("Danger", tokens["danger"])):
        for orient in ("Horizontal", "Vertical"):
            style.configure(f"{name}.{orient}.TProgressbar", background=color,
                            troughcolor=tokens["surface_alt"],
                            bordercolor=tokens["surface_alt"],
                            lightcolor=color, darkcolor=color, thickness=8,
                            pbarrelief="flat")
    style.configure("TScale", background=tokens["surface"],
                    troughcolor=tokens["surface_alt"], borderwidth=0,
                    sliderlength=18, sliderthickness=18)
    style.map("TScale", troughcolor=[("active", tokens["primary_soft"])])
    for name in ("TCheckbutton", "TRadiobutton"):
        style.configure(name, background=tokens["surface"], foreground=tokens["text"],
                        indicatorcolor=tokens["surface"], indicatormargin=6,
                        padding=(4, 6), font=body)
        style.map(name,
                  indicatorcolor=[("selected", tokens["primary"]),
                                  ("active", tokens["primary_soft"]),
                                  ("disabled", tokens["disabled_bg"])],
                  foreground=[("disabled", tokens["disabled_fg"])])
    for name in ("TLabelframe", "Card.TLabelframe"):
        style.configure(name, background=tokens["surface"],
                        bordercolor=tokens["border"], borderwidth=2,
                        labelmargins=(12, 2, 12, 4))
    for name in ("TLabelframe.Label", "Card.TLabelframe.Label"):
        style.configure(name, background=tokens["surface"],
                        foreground=tokens["text_muted"], font=small)
    style.configure("TSeparator", background=tokens["border"])
    style.configure("TPanedwindow", background=tokens["bg"], sashthickness=6,
                    sashrelief="flat")
    try:
        style.configure("Sash", sashthickness=6, gripcount=0)
    except tk.TclError:
        pass
    style.configure("TMenubutton", background=tokens["surface"],
                    foreground=tokens["text"], arrowcolor=tokens["arrow"],
                    arrowsize=12, padding=(12, 7), bordercolor=tokens["border"])
    style.map("TMenubutton", background=[("active", tokens["surface_alt"])],
              arrowcolor=[("active", tokens["primary"])])
    root.configure(bg=tokens["bg"])
    return tokens


def bind_mousewheel(widget, target=None, recursive=False):
    """让 Tk 控件支持鼠标滚轮；recursive 用于滚动表单内的所有子控件。"""
    target = target or widget

    def bind_one(item, view):
        def scroll(event):
            if getattr(event, "num", None) == 4:
                delta = -1
            elif getattr(event, "num", None) == 5:
                delta = 1
            else:
                value = int(getattr(event, "delta", 0) or 0)
                delta = -1 if value > 0 else 1 if value < 0 else 0
            if not delta:
                return None
            try:
                if int(getattr(event, "state", 0) or 0) & 1 and hasattr(view, "xview_scroll"):
                    view.xview_scroll(delta, "units")
                else:
                    view.yview_scroll(delta, "units")
            except tk.TclError:
                return None
            return "break"

        item.bind("<MouseWheel>", scroll)
        item.bind("<Button-4>", scroll)
        item.bind("<Button-5>", scroll)

    bind_one(widget, target)
    if recursive:
        def walk(parent):
            for child in parent.winfo_children():
                cls = child.winfo_class()
                if cls in ("Text", "Listbox", "Treeview", "Canvas"):
                    bind_one(child, child)
                else:
                    bind_one(child, target)
                    walk(child)
        walk(widget)
    return widget


def apply_to_tk(root, tokens=None):
    tokens = tokens or getattr(root, "_ui_tokens", LIGHT)
    fonts = getattr(root, "_ui_fonts", build_fonts(root))
    ttk_base = getattr(ttk, "Widget", None)

    def safe(widget, **options):
        for key, value in options.items():
            try:
                widget.configure(**{key: value})
            except (tk.TclError, AttributeError):
                pass

    def walk(widget):
        for child in widget.winfo_children():
            if ttk_base is not None and isinstance(child, ttk_base):
                continue
            cls = child.winfo_class()
            if cls == "Button":
                try:
                    current = child.cget("bg")
                except tk.TclError:
                    current = ""
                if current in (tokens["primary"], tokens["primary_hover"],
                               tokens["primary_active"]):
                    safe(child, bg=tokens["primary"], fg=tokens["text_on_accent"],
                         activebackground=tokens["primary_hover"],
                         activeforeground=tokens["text_on_accent"])
                elif current in (tokens["danger"], tokens["danger_soft"]):
                    safe(child, bg=tokens["danger_soft"], fg=tokens["danger"],
                         activebackground=tokens["danger_border"],
                         activeforeground=tokens["danger"])
                else:
                    safe(child, bg=tokens["surface"], fg=tokens["text"],
                         activebackground=tokens["surface_alt"],
                         activeforeground=tokens["text"])
                safe(child, relief="solid", bd=2, highlightthickness=0,
                     font=fonts["body"], padx=12, pady=7, cursor="hand2")
            elif cls in ("Entry", "Spinbox"):
                safe(child, bg=tokens["surface"], fg=tokens["text"],
                     insertbackground=tokens["text"],
                     selectbackground=tokens["primary_soft"],
                     selectforeground=tokens["text_strong"],
                     highlightthickness=0, highlightbackground=tokens["border"],
                     highlightcolor=tokens["primary"], relief="solid", bd=2,
                     font=fonts["body"])
            elif cls in ("Text", "Listbox"):
                try:
                    current_bg = child.cget("bg")
                except tk.TclError:
                    current_bg = ""
                if current_bg in ("", "white", "#ffffff", tokens["surface"],
                                  tokens["bg"], tokens["surface_alt"]):
                    safe(child, bg=tokens["surface"], fg=tokens["text"],
                         insertbackground=tokens["text"],
                         selectbackground=tokens["primary_soft"],
                         selectforeground=tokens["text_strong"],
                         highlightthickness=0, highlightbackground=tokens["border"],
                         relief="solid", bd=2, font=fonts["body"])
                else:
                    safe(child, highlightthickness=0, relief="flat", bd=0)
            elif cls == "Canvas":
                safe(child, highlightthickness=0)
            walk(child)

    safe(root, bg=tokens["bg"])
    walk(root)


def autofit_tree_column(tree, column):
    """按表头和可见内容测量列宽；用户仍可拖动分隔线手动调整。"""
    columns = list(tree["columns"])
    if str(column).startswith("#"):
        try:
            column = columns[int(str(column)[1:]) - 1]
        except (ValueError, IndexError):
            return
    if column not in columns:
        return
    font = tkfont.Font(root=tree, family=pick_family(tree), size=10)
    heading = str(tree.heading(column, "text") or "")
    width = font.measure(heading) + 28
    for item in tree.get_children():
        index = columns.index(column)
        values = tree.item(item, "values") or []
        if index < len(values):
            width = max(width, font.measure(str(values[index])) + 28)
    tree.column(column, width=max(44, min(width, 640)))


def style_tree_tags(tree, tokens=None):
    tokens = tokens or LIGHT
    tree.tag_configure("odd", background=tokens["row_alt"])
    tree.tag_configure("selected", background=tokens["primary_soft"],
                       foreground=tokens["text_strong"])


def install_exception_handler(root, app_name, log_path=None):
    log_path = Path(log_path) if log_path else Path.home() / f"{app_name}-error.log"
    last_notice = [0.0]

    def report(exc_type, exc_value, exc_tb):
        detail = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        try:
            with log_path.open("a", encoding="utf-8") as stream:
                stream.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n")
                stream.write(detail)
        except OSError:
            pass
        now = time.monotonic()
        if now - last_notice[0] < 2:
            return
        last_notice[0] = now
        try:
            messagebox.showerror(
                "操作未能完成",
                f"发生了意外错误，程序没有退出。\n\n{exc_value}\n\n"
                f"完整详情已记录到：\n{log_path}",
                parent=root)
        except tk.TclError:
            pass

    root.report_callback_exception = report
    return log_path
