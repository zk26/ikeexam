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
    "bg": "#F5F7FA", "surface": "#FFFFFF", "surface_alt": "#EEF2F7",
    "border": "#DCE3EC", "text": "#1F2937", "text_strong": "#0F172A",
    "text_muted": "#6B7280", "text_on_accent": "#FFFFFF",
    "primary": "#2563EB", "primary_hover": "#1D4ED8",
    "primary_active": "#1E40AF", "primary_soft": "#DBEAFE",
    "success": "#16A34A", "success_soft": "#F0FDF4",
    "warning": "#F59E0B", "warning_soft": "#FFF7ED", "danger": "#DC2626",
    "danger_soft": "#FEF2F2", "danger_border": "#FECACA",
    "info": "#0EA5E9", "row_alt": "#F8FAFC", "heading": "#EDF2F9",
    "heading_active": "#DCE7F5", "tab_bg": "#E9EFF7",
    "tab_active": "#DBE7F6", "focus_ring": "#93C5FD",
    "disabled_bg": "#F1F5F9", "disabled_fg": "#94A3B8",
    "scroll_thumb": "#CBD5E1", "scroll_hover": "#94A3B8",
    "arrow": "#64748B",
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
    "arrow": "#94A3B8",
}
TOKENS = {"light": LIGHT, "dark": DARK}
SPACING = {"xs": 4, "sm": 8, "md": 12, "lg": 16, "xl": 24, "xxl": 32}
FONT_CANDIDATES = (
    "Segoe UI", "Microsoft YaHei UI", "Microsoft YaHei", "PingFang SC",
    "Hiragino Sans GB", "Noto Sans CJK SC", "Noto Sans SC",
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
        return "DejaVu Sans"


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
                    borderwidth=1, bordercolor=tokens["border"])
    style.configure("TLabel", background=tokens["surface"], foreground=tokens["text"],
                    font=body, padding=0)
    style.configure("OnBg.TLabel", background=tokens["bg"], foreground=tokens["text"])
    style.configure("Muted.TLabel", foreground=tokens["text_muted"], font=small)
    style.configure("Subtitle.TLabel", foreground=tokens["text_strong"], font=sub)
    style.configure("Title.TLabel", foreground=tokens["text_strong"], font=title)
    style.configure("H1.TLabel", foreground=tokens["text_strong"], font=h1)
    style.configure("Success.TLabel", foreground=tokens["success"])
    style.configure("Danger.TLabel", foreground=tokens["danger"])
    style.configure("Info.TLabel", foreground=tokens["info"])

    style.configure("Choice.TCheckbutton", relief="flat", borderwidth=1,
                    padding=(12, 8), font=body, background=tokens["surface"],
                    foreground=tokens["text"], bordercolor=tokens["border"])
    style.map("Choice.TCheckbutton",
              background=[("selected", tokens["primary_soft"]),
                          ("active", tokens["primary_soft"]),
                          ("disabled", tokens["disabled_bg"])],
              foreground=[("selected", tokens["primary"]),
                          ("disabled", tokens["disabled_fg"])],
              bordercolor=[("selected", tokens["primary"]),
                           ("active", tokens["primary"])])
    style.configure("TButton", relief="flat", borderwidth=0, padding=(14, 8),
                    anchor="center", background=tokens["surface"],
                    foreground=tokens["text"], bordercolor=tokens["border"], font=body)
    style.map("TButton",
              background=[("pressed", tokens["surface_alt"]),
                          ("active", tokens["surface_alt"]),
                          ("disabled", tokens["disabled_bg"])],
              foreground=[("disabled", tokens["disabled_fg"])],
              bordercolor=[("focus", tokens["focus_ring"])])
    style.configure("Accent.TButton", relief="flat", borderwidth=0,
                    padding=(16, 9), background=tokens["primary"],
                    foreground=tokens["text_on_accent"], font=bold)
    style.map("Accent.TButton",
              background=[("pressed", tokens["primary_active"]),
                          ("active", tokens["primary_hover"]),
                          ("disabled", tokens["scroll_thumb"])],
              foreground=[("disabled", tokens["surface"])])
    style.configure("Danger.TButton", relief="flat", borderwidth=0,
                    padding=(14, 8), background=tokens["danger_soft"],
                    foreground=tokens["danger"], bordercolor=tokens["danger_border"])
    style.map("Danger.TButton",
              background=[("pressed", tokens["danger"]),
                          ("active", tokens["danger_border"])],
              foreground=[("pressed", tokens["text_on_accent"])])
    style.configure("Ghost.TButton", relief="flat", borderwidth=0,
                    padding=(10, 5), background=tokens["bg"],
                    foreground=tokens["primary"], font=body)
    style.map("Ghost.TButton",
              background=[("pressed", tokens["primary_soft"]),
                          ("active", tokens["primary_soft"])])
    style.configure("Toolbutton", relief="flat", borderwidth=0, padding=(10, 6),
                    background=tokens["bg"], foreground=tokens["text"])
    style.map("Toolbutton",
              background=[("selected", tokens["primary"]),
                          ("active", tokens["surface_alt"])],
              foreground=[("selected", tokens["text_on_accent"])])

    style.configure("TEntry", fieldbackground=tokens["surface"],
                    foreground=tokens["text"], bordercolor=tokens["border"],
                    lightcolor=tokens["border"], darkcolor=tokens["border"],
                    borderwidth=1, relief="flat", padding=(10, 8), insertwidth=2)
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
                    arrowcolor=tokens["arrow"], arrowsize=13, padding=(10, 8))
    style.map("TSpinbox", bordercolor=[("focus", tokens["primary"])],
              arrowcolor=[("active", tokens["primary"]),
                          ("disabled", tokens["disabled_fg"])])
    style.configure("TCombobox", fieldbackground=tokens["surface"],
                    foreground=tokens["text"], bordercolor=tokens["border"],
                    lightcolor=tokens["border"], darkcolor=tokens["border"],
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
                        foreground=tokens["text_muted"], padding=(22, 10),
                        font=body, borderwidth=0, bordercolor=tokens["tab_bg"],
                        lightcolor=tokens["tab_bg"], darkcolor=tokens["tab_bg"])
        style.map(tab_style,
                  background=[("selected", tokens["surface"]),
                              ("active", tokens["tab_active"]),
                              ("disabled", tokens["bg"])],
                  foreground=[("selected", tokens["primary"]),
                              ("active", tokens["text"]),
                              ("disabled", tokens["disabled_fg"])],
                  bordercolor=[("selected", tokens["surface"]),
                               ("active", tokens["tab_active"])],
                  expand=[("selected", (1, 1, 1, 0))])
        try:
            style.layout(tab_style, tab_layout)
        except tk.TclError:
            pass

    for tv in ("Treeview", "Data.Treeview"):
        style.configure(tv, background=tokens["surface"],
                        fieldbackground=tokens["surface"], foreground=tokens["text"],
                        rowheight=32, bordercolor=tokens["border"], borderwidth=0,
                        font=body)
        style.map(tv, background=[("selected", tokens["primary_soft"])],
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
                        bordercolor=tokens["border"], borderwidth=1,
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
                    view.xview_scroll(delta)
                else:
                    view.yview_scroll(delta)
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
                safe(child, relief="flat", bd=0, highlightthickness=0,
                     font=fonts["body"], padx=12, pady=6, cursor="hand2")
            elif cls in ("Entry", "Spinbox"):
                safe(child, bg=tokens["surface"], fg=tokens["text"],
                     insertbackground=tokens["text"],
                     selectbackground=tokens["primary_soft"],
                     selectforeground=tokens["text_strong"],
                     highlightthickness=1, highlightbackground=tokens["border"],
                     highlightcolor=tokens["primary"], relief="flat", bd=0,
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
                         highlightthickness=1, highlightbackground=tokens["border"],
                         relief="flat", bd=0, font=fonts["body"])
                else:
                    safe(child, highlightthickness=0, relief="flat", bd=0)
            elif cls == "Canvas":
                safe(child, highlightthickness=0)
            walk(child)

    safe(root, bg=tokens["bg"])
    walk(root)


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
