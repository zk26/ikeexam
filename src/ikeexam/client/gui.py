"""
学生端图形界面（tkinter）
三个界面：等待 → 理论题 → 实操题
"""
import inspect
import os
import queue
import sys
import threading
import time
import webbrowser

CLIENT_DIR = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.dirname(os.path.dirname(CLIENT_DIR))
if CLIENT_DIR not in sys.path:
    sys.path.insert(0, CLIENT_DIR)
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)
from ikeexam.shared import ui_theme
ui_theme.enable_high_dpi()
import tkinter as tk
from tkinter import messagebox, ttk

from exam_client import ExamClient, Config, TYPE_LABEL, LockedRoot
import wps


class SubmitQueue:
    """
    交卷等待队列：理论 / 打字 / 实操 / 画板逐项排队上传。
    一场考试要传的东西比较多、比较大的时候，不会一次卡死界面 ——
    每传完一项就刷新一次进度，全部传完才显示交卷成功页。

    ⚠️ 上传线程**绝不直接碰 Tk**（after/控件都不行）：在部分环境下
    工作线程调 Tk 会抛「main thread is not in main loop」，第一项还没开传
    就全线失败、界面卡在提交页不动。线程只把事件放进 queue，由主线程
    定时来取（_pump_submit_events）。
    """

    def __init__(self):
        self.items = []
        self.events = queue.Queue()
        # 事件：("progress", 已完成, 总数, 当前项名) / ("done", 成功行, 失败行)

    def add(self, label, fn):
        self.items.append((label, fn))

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()
        return self.events

    def _run(self):
        ok_lines, fail_lines = [], []
        total = len(self.items)
        for i, (label, fn) in enumerate(self.items):
            self.events.put(("progress", i, total, label))
            try:
                ok_lines.append(f"· {label}：{fn() or '已提交'}")
            except Exception as e:
                fail_lines.append(f"· {label}：{e}")
        self.events.put(("progress", total, total, "完成"))
        self.events.put(("done", ok_lines, fail_lines))

# 双端共用设计令牌。
_UI = ui_theme.LIGHT
BG = _UI["bg"]
CARD = _UI["surface"]
BLUE = _UI["primary"]
BLUE_D = _UI["primary_hover"]
BLUE_L = _UI["primary_soft"]
GREEN = _UI["success"]
GREEN_D = _UI["success"]
GREEN_L = _UI["success_soft"]
RED = _UI["danger"]
RED_D = _UI["danger"]
RED_L = _UI["danger_soft"]
ORANGE = _UI["warning"]
ORANGE_D = _UI["warning"]
ORANGE_L = _UI["warning_soft"]
GRAY = _UI["text_muted"]
GRAY_DIM = _UI["disabled_fg"]
GRAY_D = _UI["text"]
INK = _UI["text_strong"]
LINE = _UI["border"]
LINE_2 = _UI["surface_alt"]
SITE = "https://www.ikee002.top/"
SIGNATURE = f"大茂小可 | {SITE}"
FONT = "Microsoft YaHei"


def fmt_time(sec):
    if sec is None:
        return "--:--"
    sec = max(0, int(sec))
    return f"{sec // 60:02d}:{sec % 60:02d}"


def _icon_path():
    """
    图标文件路径。
    PyInstaller 打包后资源在 sys._MEIPASS 临时目录里，
    源码运行时就在本文件旁边。
    """
    import sys
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    p = os.path.join(base, "app.ico")
    return p if os.path.isfile(p) else None


def _logo_path():
    import sys
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "logo.png")


def _about_qr_path():
    import sys
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "about_qr.png")


def open_site(event=None):
    webbrowser.open(SITE)


class App:
    """
    界面流程：

        登录（姓名 + 座位号）
          ↓
        自动寻找服务器（广播，实时显示进度）
          ↓ 找不到
        手动输入服务器地址
          ↓ 连上
        等待 → 理论题 → 实操题 → 结束

    全程断线自动重连，顶部横幅提示状态。
    """
    def __init__(self, cfg):
        global FONT
        self.cfg = cfg
        self.client = ExamClient(cfg)
        self.client.on_phase = self.on_phase      # 阶段变化回调
        self.client.on_status = self.on_status    # 文字进度
        self.client.on_conn = self.on_conn        # 连接状态变化
        self.client.on_state = self.on_state      # 心跳状态（界面低成本刷新）
        # 老师点「结束考试」→ 强制交卷（在 GUI 里做，最清楚交了哪些）
        self.client.on_force_submit = self.force_submit_all

        self.locked_root = LockedRoot(tk, "ikee考务中心",
                                      window_mode=cfg.window_mode)
        self.root = self.locked_root.root
        FONT = ui_theme.pick_family(self.root)
        self.ui_tokens = ui_theme.apply_theme(self.root)
        ui_theme.apply_tk_scaling(self.root)
        ui_theme.install_exception_handler(self.root, "ikeexam-student")

        # 把 root 交给 client，阶段切换时它要同步锁定状态
        self.client.root = self.locked_root
        # 退出请求统一走这里（Esc / 关闭窗口 / 退出按钮）
        self.locked_root.on_exit = self.request_exit

        # 窗口标题栏图标（exe 图标由 PyInstaller 设置，这是窗口自己的）
        ip = _icon_path()
        if ip:
            try:
                self.root.iconbitmap(ip)
            except Exception:
                pass
        self.frame = None
        self.uiq = queue.Queue()          # 后台线程 -> UI 的安全通道
        self.banner = None                # 顶部断线横幅
        self._build_signature()

        # 理论题状态
        self.sheet = None
        self.vars = {}
        self._connected_once = False      # 是否曾成功连上（决定横幅措辞）
        self.marked = set()               # 标记待复查的题号（0 起）
        self.card_btns = []               # 答题卡按钮：只建一次，之后只改颜色
        self._theory_exam_id = None       # 已加载的试卷属于哪场考试
        self._user_tab = False            # 学生自己点过顶部标签吗

        self.root.after(200, self.drain_queue)
        self.root.after(500, self.tick)
        self.root.bind_all("<F5>", lambda event: self.refresh())

        # 已经登过录就直接找服务器，否则先弹登录框
        if self.cfg.logged_in and (self.cfg.name or getattr(self.cfg, "passwordless", False)):
            self.swap(self.build_connecting)
            self._go_connect()
        else:
            self.swap(self.build_login)

    # ---------- 通用 ----------
    def drain_queue(self):
        """处理后台线程发来的 UI 请求"""
        try:
            while True:
                fn = self.uiq.get_nowait()
                try:
                    fn()
                except Exception as e:
                    print("UI 回调出错:", e)
        except queue.Empty:
            pass
        self.root.after(200, self.drain_queue)

    def tick(self):
        """每秒刷新倒计时（只改一个 Label，极轻量）"""
        try:
            r = self.client.state.get("remaining")
            if hasattr(self, "time_label") and self.time_label.winfo_exists():
                left = r
                if hasattr(self, "_last_sync"):
                    delta = int(time.time() - self._last_sync)
                    if left is not None:
                        left = max(0, left - delta)
                self.time_label.config(text=fmt_time(left))
                # 最后 5 分钟变红提醒
                want_red = left is not None and left <= 300
                fg = RED if want_red else "white"
                if self.time_label.cget("fg") != fg:
                    self.time_label.config(fg=fg)
                if left is not None and left <= 0:
                    # 只触发一次：tick 每秒跑，不加标志的话倒计时归零后
                    # 会每秒重复自动交卷 / 重复弹「时间到」
                    if not getattr(self, "_timeout_fired", False):
                        self._timeout_fired = True
                        self.on_timeout()
                elif left is not None and left > 0:
                    # 下一场考试开始、remaining 重新变正时复位标志
                    self._timeout_fired = False
        except Exception:
            pass
        self.root.after(1000, self.tick)

    def swap(self, builder):
        """销毁旧页面，创建新页面。同时更新页面追踪。"""
        # 根据 builder 函数名推断页面类型
        name = getattr(builder, "__name__", "")
        page_map = {"build_wait": "wait", "build_connecting": "connecting",
                    "build_login": "login",             "build_exam": "exam",
                    "build_practice": "practice", "build_end": "end",
                    "build_paused": "paused"}
        self._page = page_map.get(name, "unknown")

        if self.frame:
            try:
                self.frame.destroy()
            except Exception:
                pass
        self.frame = tk.Frame(self.root, bg=BG)
        self.frame.pack(fill="both", expand=True)
        self._call_builder(builder, self.frame)
        ui_theme.apply_to_tk(self.root, self.ui_tokens)

    def _call_builder(self, builder, parent):
        """
        调用页面构建函数。

        ⚠️ 有些构建函数带 parent 参数、有些不带。以前是写死的
        `builder(self.frame)`，于是 `swap(self.build_wait)` 直接抛
        TypeError —— 而 swap 此时已经把旧页面销毁、只留下一个空的
        Frame，学生看到的就是"整片白屏、没有等待页"，异常还被
        drain_queue 吞掉，很难查。这里按签名适配，杜绝这类白屏。
        """
        try:
            n = len(inspect.signature(builder).parameters)
        except (TypeError, ValueError):
            n = 1
        if n >= 1:
            return builder(parent)
        return builder()

    def _build_signature(self):
        self.signature = tk.Label(
            self.root, text=SIGNATURE, bg=BG, fg=GRAY,
            font=(FONT, 9), anchor="e", cursor="hand2")
        self.signature.pack(side="bottom", fill="x", anchor="e",
                            padx=14, pady=5)
        self.signature.bind("<Button-1>", open_site)

    def _current_page(self):
        """当前显示的是哪个页面"""
        return getattr(self, "_page", "unknown")

    def _set_page(self, name):
        self._page = name

    # ---------- 页面调度 ----------
    def _current_exam_id(self):
        ex = self.client.state.get("exam") or {}
        return ex.get("id")

    def decide_page(self):
        """
        该显示哪一页 = 服务端阶段(phase) + 学生所在板块(section) 共同决定。

        ⚠️ 以前只有 phase 一个维度：心跳回的是 running，学生正在答题时
        本地存的是 theory，两者一比就不相等，于是触发"阶段变了"→ 重建页面，
        学生做几道题就被踢回主菜单。现在两个维度分开，答题中不会被打扰。
        """
        st = self.client.state
        phase = st.get("phase") or "idle"
        section = st.get("section")

        if phase == "ended":
            # 只有"刚考完这场"的本机才显示结束页（让他知道考完了）。
            # 其他人（下一批学生在这台机器上登录）应该看到待考界面，
            # 等老师重新开始考试 —— 因为没法判断现在坐的是不是上一个同学。
            ex = st.get("exam") or {}
            if ex.get("id") and st.get("ended_exam_id") == ex.get("id"):
                return "end"
            return "wait"
        if phase == "paused":
            return "paused"
        if phase == "idle":
            return "wait"

        # 考试已经在跑，但这台机器还没确认坐的是谁
        # （上一场刚结束/刚开机）→ 先让学生填姓名座位号，别直接拉进考试
        if not st.get("confirmed", True) and not section:
            return "login"

        # 理论题和实操题是**同一个界面**的两个导航标签：开考直接进考试界面，
        # 不再先跳一个「请选择要完成的部分」中间页（顶部导航已经能切了）。
        return "exam"

    def apply_state(self, force=False):
        """幂等：已经在正确的页面就什么都不做（不重建 = 不丢题号、不卡顿）"""
        # 登录页/连接页是"人还没定下来"的状态：
        # 这时老师开考也不该把这台机器拽进考试（坐着的可能是另一个同学），
        # 等本人填完姓名座位号（走的是 force=True）再切页
        if not force and self._current_page() in ("login", "connecting"):
            return
        want = self.decide_page()
        if not force and want == self._current_page():
            return
        self._show_page(want)

    def _show_page(self, page, force=False):
        """
        统一的页面切换入口。

        ⚠️ 注意：页面名要**最后**才设置。
        很多 build_xxx 内部会调 self.swap(b)，而 swap 会按构造函数名猜页面名
        （内部函数叫 b → 猜成 unknown），如果先设名字就会被它覆盖掉。
        结果 decide_page() 永远不等于当前页 → 每 3 秒心跳都把页面重建一次，
        学生就看到白屏/闪烁。
        """
        self._set_page(page)          # 提前设一次：early return 的路径也要正确
        try:
            if page == "exam":
                # 已经在考试界面就不重建（保住题号和答案）
                if (not force and getattr(self, "content", None)
                        and self.content.winfo_exists()):
                    self.render_tab()
                    return
                if not getattr(self, "cur_tab", None):
                    self.cur_tab = ("practice" if self.client.state.get("section")
                                    == "practice" else "choice")
                self.build_exam()
            elif page == "end":
                self._leave_theory()
                self.build_end()
            elif page == "paused":
                self.build_paused()
            elif page == "login":
                self.swap(self.build_login)
            else:
                self._leave_theory()
                self.build_wait()
        except Exception as e:
            print(f"[页面切换] {page} 失败: {e}", flush=True)
            self._show_error_page(f"页面加载失败: {e}")
        self._set_page(page)          # 见上面的说明：必须放在最后
        # 页面换了就同步窗口形态：
        # 理论题 → 全屏置顶；实操 → 最大化、不置顶（不挡 WPS）
        try:
            self.client.sync_lock()
        except Exception:
            pass

    def _leave_theory(self):
        """离开理论题页：先把攒下的答案交上去，别丢答案"""
        if not self.sheet:
            return
        try:
            self.client.flush_answers()
        except Exception:
            pass

    def refresh(self):
        """保留给「刷新状态」按钮：重新握手一次并刷新页面"""
        def run():
            try:
                h = self.client.api.hello()
                if h and h.get("ok"):
                    self.client.state["exam"] = h.get("exam")
                    self.client.state["phase"] = h.get("phase")
                    if h.get("exam"):
                        ex = h["exam"]
                        self.client.state["materials"] = ex.get("materials") or []
                        self.client.state["theory_answered"] = ex.get("theory_answered", 0)
                        self.client.state["theory_total"] = ex.get("theory_total", 0)
                        self.client.state["practice_submitted"] = ex.get("practice_submitted", False)
            except Exception as e:
                print(f"[刷新] 网络失败: {e}", flush=True)
            self.uiq.put(lambda: self.apply_state(force=True))
        threading.Thread(target=run, daemon=True).start()

    def on_phase(self, phase=None):
        """阶段/命令变化：交给 apply_state 幂等处理，不强行重建页面"""
        self.uiq.put(lambda: self.apply_state())

    def on_state(self, st=None):
        """心跳带来的状态更新（后台线程 → 主线程）"""
        self.uiq.put(self._sync_state)

    def _report_progress(self):
        progress = {
            "section": getattr(self, "cur_tab", None) or self._current_page(),
            "status": ("working" if self.client.state.get("phase") in
                       ("theory", "practice", "paused") else "idle"),
        }
        sheet = getattr(self, "sheet", None)
        if sheet is not None and getattr(sheet, "questions", None):
            progress.update({
                "current_question": int(sheet.index) + 1,
                "question_total": len(sheet.questions),
                "theory_answered": sheet.answered_count(),
            })
        tasks = self.client.practice_tasks()
        if tasks:
            progress.update({
                "practice_index": int(getattr(self, "cur_task_idx", 0)) + 1,
                "practice_total": len(tasks),
            })
        self.client.set_ui_progress(progress)

    def _sync_state(self):
        """心跳刷新：只做必要的低成本更新（不在答题时重建任何东西）"""
        self._last_sync = time.time()
        self._report_progress()
        if self._current_page() != "exam":
            return
        # 老师刚装了/删了操作题 → 顶部导航要跟着多出/少掉「实操题」标签
        want = [k for k, _l, _t in self.exam_tabs()]
        if want != getattr(self, "_drawn_tabs", None):
            self.build_exam()
            return
        # 这道实操题刚提交 → 内容区状态跟着更新，不用重连就能看到
        if getattr(self, "cur_tab", None) == "practice":
            key = tuple((t.get("task_id"), bool(t.get("submitted")))
                        for t in self.client.practice_tasks())
            if key != getattr(self, "_prac_key", None):
                self.render_tab()

    def _show_error_page(self, msg):
        """兜底错误页"""
        def b(p):
            card = tk.Frame(p, bg=CARD, padx=40, pady=30,
                            highlightbackground=LINE, highlightthickness=2)
            card.place(relx=0.5, rely=0.5, anchor="center")
            tk.Label(card, text="加载出错", bg=CARD, fg=RED,
                     font=(FONT, 16, "bold")).pack(pady=(0, 10))
            tk.Label(card, text=msg, bg=CARD, fg=GRAY,
                     font=(FONT, 11), wraplength=400,
                     justify="center").pack()
            tk.Button(card, text="重试", command=self.refresh,
                      bg=BLUE, fg=INK, font=(FONT, 11),
                      padx=20, pady=6, relief="flat").pack(pady=(16, 0))
        self.swap(b)

    def _upload_drawing_quiet(self, eid):
        """静默上传画图最新版（到时自动交卷 / 老师强制收卷用，不弹窗）"""
        try:
            import drawing_ui
            if not (eid and self.client.has_drawing()):
                return
            for kind in self.client.drawing_styles():
                st = self.client.drawing_state(kind)
                if not drawing_ui.state_has_art(st):
                    continue
                data, ext = drawing_ui.export_state(st)
                name = "拼豆作品" if kind == "beads" else "画板作品"
                r = self.client.api.upload_drawing(eid, data, ext, kind=kind)
                self._log_only(f"{name}已交" if r.get("ok")
                               else f"{name}提交失败：{r.get('msg')}")
        except Exception as e:
            self._log_only(f"画图作品提交失败：{e}")

    def on_timeout(self):
        """
        时间到。
        理论题和实操题现在是同一个考试界面的两个标签（页面名是 exam），
        所以要看当前停在哪个标签，而不是看页面名 ——
        以前判 cur == "theory"/"practice"，合并界面后两个都不匹配，
        时间到了等于什么都不做。
        """
        threading.Thread(target=self._upload_drawing_quiet,
                         args=(self.exam_id,), daemon=True).start()
        cur = self._current_page()
        if cur == "exam":
            if getattr(self, "cur_tab", None) == "practice":
                self.auto_submit()
            elif self.sheet and self.sheet.total():
                self.submit_theory(force=True, quiet=True)
                if self.client.practice_tasks():
                    self.switch_tab("practice")
                    self.auto_submit()
        elif cur == "theory" and self.sheet:
            self.submit_theory(force=True, quiet=True)
        elif cur == "practice":
            self.auto_submit()

    def on_status(self, msg):
        """自动发现/重连的进度"""
        self.uiq.put(lambda: self._show_status(msg))

    def _show_status(self, msg):
        for attr in ("conn_status", "wait_status"):
            try:
                w = getattr(self, attr, None)
                if w is not None and w.winfo_exists():
                    w.config(text=msg)
            except Exception:
                pass

    # ---------- 退出 ----------
    def request_exit(self):
        """
        统一的退出入口（点 X / Esc / 退出按钮都走这里）。

        规则：
          · 普通窗口模式（默认）：随时能退出，只弹一次确认，
            并先把攒着的答案交上去（重新打开客户端还能接着做）。
          · 严格模式（config.json: window_mode=fullscreen）：考试中拒绝退出。
        """
        # 退出原因记一笔：学生偶尔会遇到"客户端自己关了"，
        # 有日志才能判断是学生自己退的、老师结束的、还是崩了
        try:
            self.client.log("info", f"请求退出（阶段="
                             f"{self.client.state.get('phase')}）")
        except Exception:
            pass
        # 退出前把攒下的答案交上去，别丢答案
        try:
            self.client.flush_answers()
        except Exception:
            pass

        if self.client.want_lock():
            # 严格模式：考试中不许退（双重保险，即使窗口按钮 leaked 出来）
            try:
                messagebox.showwarning(
                    "考试进行中",
                    "考试已经开始，不能退出客户端。\n\n"
                    "请等待老师结束考试。")
            except Exception:
                pass
            return

        # 考试进行中退出时提醒一句（答案已自动保存，不是"白考了"）
        # 只看阶段，不看当前在哪一页 —— 页面名在重建过程中会短暂变化，
        # 拿它判断很容易误判
        in_exam = (self.client.state.get("phase")
                   in ("theory", "practice", "paused"))
        tip = ("考试还没结束！\n\n"
               "已作答的内容会自动保存，重新打开客户端可以接着做。\n\n"
               "确定要退出吗？") if in_exam else (
            "确定要退出考试客户端吗？\n\n"
            "退出后需要重新打开程序才能参加考试。")
        try:
            ok = messagebox.askyesno("退出确认", tip)
        except Exception:
            ok = True
        if not ok:
            return

        try:
            self.client.stop()
        except Exception:
            pass
        self.locked_root.destroy()

    def _exit_bar(self, parent, text="退出"):
        """
        右下角退出按钮。只在非考试阶段显示。
        返回 Frame（调用方决定是否 pack）。
        """
        bar = tk.Frame(parent, bg=CARD)
        tk.Button(bar, text=text, command=self.request_exit,
                  font=(FONT, 10), padx=14, pady=4,
                  relief="flat", fg=GRAY, cursor="hand2").pack()
        return bar

    # ---------- 界面 0：登录 ----------
    def build_login(self, f=None):
        """填姓名 + 座位号才能进"""
        wrap = tk.Frame(f if f else self.root, bg=BG)
        wrap.pack(fill="both", expand=True)
        banner = ui_theme.MemphisBanner(
            wrap, title="阳光考试季", subtitle="认真作答，你也可以很棒", height=78,
            tokens=self.ui_tokens)
        banner.pack(fill="x")

        card = tk.Frame(wrap, bg=CARD, padx=52, pady=38,
                        highlightbackground=LINE, highlightthickness=2)
        card.place(relx=0.5, rely=0.58, anchor="center")

        # Logo / 标题
        logo_frame = tk.Frame(card, bg=CARD)
        logo_frame.pack(pady=(0, 6))
        logo_path = _logo_path()
        if os.path.isfile(logo_path):
            from PIL import Image, ImageTk
            image = Image.open(logo_path)
            image.thumbnail((112, 112), Image.Resampling.LANCZOS)
            self.login_logo = ImageTk.PhotoImage(image)
            tk.Label(logo_frame, image=self.login_logo, bg=CARD).pack()
        else:
            logo = tk.Label(logo_frame, text="考", bg=BLUE, fg=INK,
                            font=(FONT, 18, "bold"),
                            width=3, height=1, relief="flat")
            logo.pack()
        tk.Label(card, text="ikee考务中心", bg=CARD,
                 font=(FONT, 28, "bold"), fg=INK).pack(pady=(16, 6))
        tk.Label(card, text="请先填写你的信息，再进入考试", bg=CARD,
                 fg=GRAY_D, font=(FONT, 12)).pack(pady=(0, 30))

        # 姓名
        row1 = tk.Frame(card, bg=CARD)
        row1.pack(fill="x", pady=(0, 12))
        tk.Label(row1, text="姓  名", bg=CARD, width=7, anchor="w",
                 font=(FONT, 12), fg=INK).pack(side="left")
        self.e_name = tk.Entry(row1, font=(FONT, 14), width=28,
                               relief="solid", bd=1, highlightcolor=BLUE,
                               highlightthickness=2)
        self.e_name.pack(side="left", ipady=8)
        self.e_name.insert(0, self.cfg.name)

        # 座位号
        row2 = tk.Frame(card, bg=CARD)
        row2.pack(fill="x", pady=(0, 8))
        tk.Label(row2, text="座位号", bg=CARD, width=7, anchor="w",
                 font=(FONT, 12), fg=INK).pack(side="left")
        self.e_seat = tk.Entry(row2, font=(FONT, 14), width=28,
                               relief="solid", bd=1, highlightcolor=BLUE,
                               highlightthickness=2)
        self.e_seat.pack(side="left", ipady=8)
        self.e_seat.insert(0, self.cfg.seat)

        self.login_tip = tk.Label(card, text="", bg=CARD, fg=RED,
                                  font=(FONT, 10))
        self.login_tip.pack(pady=(8, 0))
        # 老师端配了「本场学生名单」时，姓名必须和名单一致才进得去
        tk.Label(card, text="姓名要和老师端的本场名单一致（没配名单就不限制）",
                 bg=CARD, fg=GRAY, font=(FONT, 9)).pack(pady=(2, 0))

        tk.Button(card, text="进  入", command=self.do_login,
                  bg=BLUE, fg=INK, font=(FONT, 14, "bold"),
                  width=24, pady=12, relief="flat", cursor="hand2",
                  activebackground=BLUE_D).pack(pady=(18, 8))
        tk.Button(card, text="免密码进入（按 IP 识别）", command=self.do_passwordless,
                  bg=CARD, fg=BLUE, font=(FONT, 11), width=24, pady=8,
                  relief="flat", cursor="hand2").pack(pady=(0, 8))

        tk.Label(card, text=f"本机: {self.cfg.hostname}（标识 {self.cfg.client_id}）",
                 bg=CARD, fg=GRAY, font=(FONT, 9)).pack()

        self._exit_bar(card, "退出程序").pack(pady=(16, 0))

        tk.Button(card, text="关于本软件", command=self.show_about,
                  bg=CARD, fg=GRAY, font=(FONT, 9), relief="flat",
                  cursor="hand2", activebackground=CARD,
                  activeforeground=BLUE).pack(pady=(6, 0))
        ui_theme.add_hard_shadow(card, 6, LINE)

        # 回车快捷提交
        self.e_name.bind("<Return>", lambda e: self.e_seat.focus())
        self.e_seat.bind("<Return>", lambda e: self.do_login())
        self.root.after(120, lambda: self.e_name.focus())

    def show_about(self):
        from PIL import Image, ImageTk

        win = tk.Toplevel(self.root)
        win.title("关于 ikee考务中心")
        win.configure(bg=CARD)
        win.resizable(False, False)
        win.transient(self.root)
        win.bind("<Escape>", lambda event: win.destroy())
        icon = _icon_path()
        if icon:
            win.iconbitmap(icon)

        body = tk.Frame(win, bg=CARD, padx=24, pady=22)
        body.pack(fill="both", expand=True)
        info = tk.Frame(body, bg=CARD)
        info.pack(side="left", fill="both", expand=True, padx=(0, 22))

        tk.Label(info, text="ikee考务中心", bg=CARD, fg=INK,
                 font=(FONT, 18, "bold")).pack(anchor="w")
        tk.Label(info, text="小可属于大茂小可", bg=CARD, fg=BLUE,
                 font=(FONT, 11, "bold")).pack(
                     anchor="w", pady=(6, 10))
        tk.Label(info, text="机房考试系统：理论题在线作答，\nWPS 实操题自动评分。",
                 bg=CARD, fg=GRAY_D, font=(FONT, 10),
                 wraplength=300, justify="left").pack(anchor="w")

        site = tk.Label(info, text=SITE, bg=CARD, fg=BLUE,
                        font=(FONT, 10), cursor="hand2")
        site.pack(anchor="w", pady=(14, 4))
        site.bind("<Button-1>", open_site)
        repo = tk.Label(info, text="https://github.com/zk26/ikeexam", bg=CARD,
                        fg=BLUE, font=(FONT, 9), cursor="hand2")
        repo.pack(anchor="w")
        repo.bind("<Button-1>", lambda event: webbrowser.open(
            "https://github.com/zk26/ikeexam"))
        tk.Label(info, text="MIT License", bg=CARD, fg=GRAY,
                 font=(FONT, 9)).pack(anchor="w", pady=(10, 0))

        image = Image.open(_about_qr_path())
        image.thumbnail((250, 250), Image.Resampling.LANCZOS)
        photo = ImageTk.PhotoImage(image)
        win._about_qr = photo
        tk.Label(body, image=photo, bg=CARD).pack(side="right")
        tk.Label(body, text="赞助作者", bg=CARD, fg=GRAY,
                 font=(FONT, 9)).pack(side="right", pady=(6, 0))

        tk.Button(win, text="关闭", command=win.destroy,
                  font=(FONT, 10), padx=22, pady=4,
                  relief="flat", cursor="hand2").pack(pady=(0, 18))
        win.update_idletasks()
        x = self.root.winfo_rootx() + max(
            0, (self.root.winfo_width() - win.winfo_width()) // 2)
        y = self.root.winfo_rooty() + max(
            0, (self.root.winfo_height() - win.winfo_height()) // 2)
        win.geometry(f"+{x}+{y}")
        ui_theme.apply_to_tk(win, self.ui_tokens)
        win.grab_set()
        return win

    def do_login(self):
        """校验并保存，然后开始找服务器"""
        name = (self.e_name.get() or "").strip()
        seat = (self.e_seat.get() or "").strip()

        if not name:
            self.login_tip.config(text="请填写姓名")
            self.e_name.focus()
            return
        if len(name) > 30:
            self.login_tip.config(text="姓名过长（最多 30 字）")
            self.e_name.focus()
            return
        if not seat:
            self.login_tip.config(text="请填写座位号（学号）")
            self.e_seat.focus()
            return
        if len(seat) > 20:
            self.login_tip.config(text="座位号过长（最多 20 位）")
            self.e_seat.focus()
            return
        # 座位号只收数字/字母/连字符：防止路径分隔符、控制字符被带进目录名
        if any(ch in seat for ch in r'\/:*?"<>|') or any(ord(c) < 32 for c in seat):
            self.login_tip.config(text="座位号含非法字符")
            self.e_seat.focus()
            return

        self.cfg.name = name
        self.cfg.seat = seat
        self.cfg.logged_in = True
        self.cfg.passwordless = False
        self.cfg.save()
        # 本人确认过身份了，接下来老师开考可以直接把他拉进去
        self.client.state["confirmed"] = True

        self.swap(self.build_connecting)
        self._go_connect()

    def do_passwordless(self):
        self.cfg.name = ""
        self.cfg.seat = ""
        self.cfg.logged_in = True
        self.cfg.passwordless = True
        self.cfg.save()
        self.client.state["confirmed"] = True
        self.swap(self.build_connecting)
        self._go_connect()

    # ---------- 界面 0.5：正在连接 ----------
    def build_connecting(self, f=None):
        wrap = tk.Frame(f if f else self.root, bg=BG)
        wrap.pack(fill="both", expand=True)
        card = tk.Frame(wrap, bg=CARD, padx=48, pady=42,
                        highlightbackground=LINE, highlightthickness=2)
        card.place(relx=0.5, rely=0.5, anchor="center")

        tk.Label(card, text="正在连接考试服务器", bg=CARD,
                 font=(FONT, 17, "bold"), fg=INK).pack(pady=(0, 16))

        self.conn_status = tk.Label(card, text="正在自动寻找…", bg=CARD,
                                    fg=BLUE, font=(FONT, 12),
                                    wraplength=400, justify="center")
        self.conn_status.pack(pady=(0, 14))

        # 转圈提示
        self.conn_dot = tk.Label(card, text="●  ●  ●", bg=CARD, fg=BLUE,
                                 font=(FONT, 10))
        self.conn_dot.pack()

        # 刷新（重新寻找）+ 手动输入：一直显示，学生/老师随时能重试
        bar = tk.Frame(card, bg=CARD)
        bar.pack(pady=(18, 0))
        tk.Button(bar, text="重新寻找服务器", command=self.retry_connect,
                  bg=BLUE, fg=INK, font=(FONT, 11, "bold"),
                  padx=16, pady=8, relief="flat", cursor="hand2",
                  activebackground=BLUE_D).pack(side="left", padx=4)
        self.btn_manual = tk.Button(
            bar, text="手动输入服务器地址", command=self.build_manual,
            font=(FONT, 11), padx=16, pady=8, relief="flat",
            fg=BLUE, cursor="hand2", bg=BLUE_L, activebackground=BLUE)
        self.btn_manual.pack(side="left", padx=4)

        # 被名单拦下时用：回登录页改姓名/座位号（本来就没让进，允许直接回）
        self.btn_relogin = tk.Button(
            bar, text="换个学生登录", command=lambda: self.back_to_login(force=True),
            font=(FONT, 11), padx=16, pady=8, relief="flat",
            fg="white", cursor="hand2", bg=ORANGE, activebackground="#c2410c")

        self._exit_bar(card, "退出程序").pack(pady=(16, 0))

    def _go_connect(self):
        """后台线程找服务器，避免界面卡住"""
        def run():
            ok = self.client.start(on_status=self.on_status)
            if ok:
                self._connected_once = True
                self.uiq.put(self._on_connected)
            else:
                self.uiq.put(self._on_connect_failed)
        threading.Thread(target=run, daemon=True).start()

    def retry_connect(self):
        """刷新：重新寻找服务器（连接页的刷新按钮）"""
        try:
            if hasattr(self, "conn_status") and self.conn_status.winfo_exists():
                self.conn_status.config(text="正在重新寻找服务器…", fg=BLUE)
        except Exception:
            pass

        def run():
            try:
                self.client.stop()
            except Exception:
                pass
            # 清掉旧状态，强制重新握手
            self.client._stop.clear()
            ok = self.client.reconnect(on_status=self.on_status)
            if ok:
                self._connected_once = True
                self.uiq.put(self._on_connected)
            else:
                self.uiq.put(self._on_connect_failed)
        threading.Thread(target=run, daemon=True).start()

    def _on_connected(self):
        """连接成功后：没考试就在等待页，有考试按状态进对应页面"""
        exam = self.client.state.get("exam")
        # 还停在登录页说明本人还没确认身份（可能正准备换考生），
        # 这时老师开考也不能把这台机器拽进去
        if self._current_page() == "login":
            return
        if not exam:
            self.swap(self.build_wait)
            try:
                nm = self.client.state.get("server_name") or ""
                svr = self.client.cfg.server or ""
                if hasattr(self, "lbl_server") and self.lbl_server.winfo_exists():
                    self.lbl_server.config(
                        text=f"服务器: {svr}" + (f"（{nm}）" if nm else ""))
                if hasattr(self, "wait_status") and self.wait_status.winfo_exists():
                    self.wait_status.config(text="已连接，等待老师开始考试", fg=GREEN)
            except Exception as e:
                print(f"[等待页] 更新状态失败: {e}", flush=True)
        self._last_sync = time.time()
        self.apply_state(force=True)
        self._sync_state()

    def _on_connect_failed(self):
        """自动寻找失败 → 给手动输入的入口"""
        # 服务端明确拒了（比如姓名不在本场名单里）：把原因显示出来，
        # 并给一个「改姓名/座位号」的入口，别让学生卡在连接页
        err = (self.client.state or {}).get("hello_error") or ""
        if err:
            try:
                self.conn_status.config(
                    text="无法进入考试：\n\n" + err
                         + "\n\n请点下面「换个学生登录」改正后重试。",
                    fg=RED, wraplength=420, justify="left")
                self.btn_relogin.pack(pady=(18, 0))
                self.btn_manual.pack(pady=(6, 0))
                return
            except Exception:
                pass
        try:
            self.conn_status.config(
                text="没有自动找到服务器。\n可以手动输入地址，或让老师检查服务器。",
                fg=RED)
            self.btn_manual.pack(pady=(18, 0))
        except Exception:
            self.build_manual()

    # ---------- 界面 0.8：手动输入地址 ----------
    def build_manual(self):
        win = tk.Toplevel(self.root)
        win.title("手动输入服务器地址")
        win.geometry("460x260")
        win.configure(bg=BG)
        win.transient(self.root)
        win.grab_set()
        try:
            ip = _icon_path()
            if ip:
                win.iconbitmap(ip)
        except Exception:
            pass

        card = tk.Frame(win, bg=CARD, padx=28, pady=24)
        card.place(relx=0.5, rely=0.5, anchor="center")

        tk.Label(card, text="服务器地址", bg=CARD,
                 font=(FONT, 13, "bold")).pack(anchor="w")
        tk.Label(card, text="形如 http://服务器IP:9000", bg=CARD,
                 fg=GRAY, font=(FONT, 10)).pack(anchor="w", pady=(2, 8))

        e = tk.Entry(card, font=(FONT, 13), width=30, relief="solid", bd=1)
        e.pack(ipady=6)
        e.insert(0, self.cfg.fallback or "")

        tip = tk.Label(card, text="", bg=CARD, fg=RED, font=(FONT, 10))
        tip.pack(pady=(8, 0))

        def ok():
            url = (e.get() or "").strip()
            if not url:
                tip.config(text="请填写服务器地址。", fg=RED)
                e.focus_set()
                return
            if not url.startswith("http"):
                url = "http://" + url
            self.cfg.server = url.rstrip("/")
            self.cfg.fallback = url.rstrip("/")
            self.cfg.save()
            self.client.cfg.server = self.cfg.server
            tip.config(text="正在连接服务器…", fg=BLUE)
            connect_btn.config(state="disabled")

            def work():
                try:
                    api = type(self.client.api)(self.cfg)
                    return {"ok": True, "hello": api.hello(), "api": api}
                except Exception as exc:
                    return {"ok": False, "msg": str(exc)}

            def done(result):
                if not win.winfo_exists():
                    return
                if not result.get("ok"):
                    tip.config(text=f"连接失败：{result.get('msg', '未知原因')}。"
                                    "请检查地址和网络后重试。", fg=RED)
                    connect_btn.config(state="normal")
                    return
                self.client.api = result["api"]
                hello = result.get("hello") or {}
                self.client.state["server_name"] = hello.get("server_name", "")
                self.client.state["exam"] = hello.get("exam")
                self.client.state["phase"] = hello.get("phase")
                self.client.state["materials"] = (
                    hello.get("exam") or {}).get("materials") or []
                win.destroy()
                self._connected_once = True
                self._on_connected()

            def run():
                result = work()
                self.uiq.put(lambda: done(result))

            threading.Thread(target=run, daemon=True).start()

        bar = tk.Frame(card, bg=CARD)
        bar.pack(pady=(14, 0))
        connect_btn = tk.Button(bar, text="连接", command=ok, bg=BLUE, fg=INK,
                                font=(FONT, 11), padx=20, pady=6,
                                relief="flat", cursor="hand2")
        connect_btn.pack(side="left", padx=5)
        tk.Button(bar, text="取消", command=win.destroy,
                  font=(FONT, 11), padx=20, pady=6).pack(side="left", padx=5)
        e.bind("<Return>", lambda ev: ok())
        win.bind("<Escape>", lambda event: win.destroy())
        ui_theme.apply_to_tk(win, self.ui_tokens)
        e.focus()

    # ---------- 断线横幅 ----------
    def on_conn(self, online, msg=""):
        """连接状态变化 → 更新顶部横幅（线程安全）"""
        self.uiq.put(lambda: self._set_banner(online, msg))

    def _set_banner(self, online, msg=""):
        try:
            if self.banner and self.banner.winfo_exists():
                self.banner.destroy()
                self.banner = None
        except Exception:
            pass
        if online:
            return
        # 只在已经进了考试界面后才显示横幅
        if not self._connected_once:
            return
        self.banner = tk.Frame(self.root, bg=_UI["coral"], height=34)
        self.banner.pack(fill="x", side="top")
        self.banner.pack_propagate(False)
        tk.Label(self.banner,
                 text=f"连接已断开，正在自动重连…（{msg}）",
                 bg=_UI["coral"], fg=INK,
                 font=(FONT, 11)).pack(expand=True)

    # ---------- 界面 1：等待 ----------
    def build_wait(self, f=None):
        """
        等待老师开始考试的页面。显示学生信息、服务器状态、操作按钮。

        ⚠️ f 参数不能省：连接成功后是 `swap(self.build_wait)` 调进来的，
        swap 会把新建的 Frame 传进来。签名对不上就会白屏（见 _call_builder）。
        """
        def b(p):
            # 顶部几何标题栏
            top = tk.Frame(p, bg=_UI["lavender"], height=60)
            top.pack(fill="x", side="top")
            top.pack_propagate(False)
            tk.Label(top, text="ikee考务中心", bg=_UI["lavender"], fg=INK,
                     font=(FONT, 18, "bold")).pack(expand=True)

            # 主体区域
            body = tk.Frame(p, bg=BG)
            body.pack(fill="both", expand=True)

            # 居中的卡片
            card = tk.Frame(body, bg=CARD, padx=54, pady=44,
                            highlightbackground=LINE, highlightthickness=2)
            card.place(relx=0.5, rely=0.45, anchor="center")

            # 标题
            tk.Label(card, text="等待老师开始考试", bg=CARD,
                     font=(FONT, 22, "bold")).pack(pady=(0, 16))

            # 身份信息卡片
            info_frame = tk.Frame(card, bg=_UI["mint"], padx=22, pady=14,
                                  highlightbackground=LINE, highlightthickness=2)
            info_frame.pack(fill="x", pady=(0, 12))
            who = f"{self.cfg.name or '免密码考生'}　座位 {self.cfg.seat or '按 IP 识别'}"
            tk.Label(info_frame, text=who, bg=_UI["mint"], fg=INK,
                     font=(FONT, 17, "bold")).pack()
            tk.Label(info_frame, text=f"本机: {self.cfg.hostname}"
                                      f"（标识 {self.cfg.client_id}）",
                     bg=_UI["mint"], fg=GRAY,
                     font=(FONT, 11)).pack()

            # 服务器信息
            server_name = self.client.state.get("server_name") or ""
            self.lbl_server = tk.Label(
                card, text=f"服务器: {server_name or self.cfg.server or '正在寻找…'}",
                bg=CARD, fg=GRAY, font=(FONT, 11))
            self.lbl_server.pack(pady=(4, 2))

            # 状态文字
            self.wait_status = tk.Label(card, text="已连接，等待老师开始考试", bg=CARD,
                                        fg=GREEN, font=(FONT, 13, "bold"),
                                        wraplength=420, justify="center")
            self.wait_status.pack(pady=(8, 20))

            # 操作按钮
            bar = tk.Frame(card, bg=CARD)
            bar.pack()
            tk.Button(bar, text="重新寻找服务器", command=self.rediscover,
                      bg=BLUE, fg=INK, font=(FONT, 11),
                      padx=16, pady=6, relief="flat").pack(side="left", padx=4)
            tk.Button(bar, text="刷新状态", command=self.refresh,
                      font=(FONT, 11),
                      padx=16, pady=6).pack(side="left", padx=4)
            # 姓名/座位号填错了，或者这台机器换人坐了 → 重新登录
            tk.Button(bar, text="重新登录（换考生）", command=self.back_to_login,
                      font=(FONT, 11), padx=16, pady=6,
                      relief="flat", fg=BLUE, bg=BLUE_L,
                      cursor="hand2").pack(side="left", padx=4)
            self._exit_bar(bar, "退出程序").pack(side="left", padx=4)

        if f is not None:
            b(f)            # 是 swap 调进来的：直接画进它建好的 Frame
        else:
            self.swap(b)

    def rediscover(self):
        """手动重新找服务器（换了网段、老师重启服务时用）"""
        self.wait_status.config(text="正在重新寻找服务器…", fg=BLUE)
        self.root.update_idletasks()

        def run():
            ok = self.client.reconnect(on_status=self.on_status)
            self.uiq.put(lambda: self._after_rediscover(ok))
        threading.Thread(target=run, daemon=True).start()

    def _after_rediscover(self, ok):
        if ok:
            self._set_banner(True)
            self.lbl_server.config(text=f"服务器: {self.client.cfg.server}")
            self.wait_status.config(text="已重新连接", fg=GREEN)
            self.root.after(1200, self.refresh)
        else:
            self.wait_status.config(
                text="仍然没找到，请点『手动输入服务器地址』", fg=RED)
            try:
                self.btn_manual.wait_status  # noqa
            except Exception:
                pass

    # ============ 考试主界面：顶部导航 + 内容区（不再整页跳转）============
    #
    # 老师要求：
    #   · 导航栏放「考试名 / 选择填空 / 判断 / 程序填空 / 实操题 / 交卷 / 时间 / 姓名 座位号」
    #   · 点导航只换内容区，所以做完还能回头检查，不再反复跳转
    THEORY_TABS = (
        ("choice", "选择·填空题", ("single", "multi", "blank")),
        ("judge", "判断题", ("judge",)),
        ("drag", "拖拽题", ("drag",)),
        ("code", "程序填空", ("code",)),
    )

    def exam_tabs(self):
        """
        顶部要显示哪些导航。

        ⚠️ 没有内容的板块一律不显示。
        以前固定画「选择·填空题 / 判断题 / 程序填空」三个标签，
        卷子里只有程序填空时，学生点前两个进去就是"这一部分没有题目"，
        看着像系统坏了。
        """
        tabs = []
        if self.sheet is not None:
            # 试卷加载完了 → 按这份卷子里实际有的题型画
            have = set()
            for q in (self.sheet.questions or []):
                have.add(q.get("qtype"))
            for key, label, types in self.THEORY_TABS:
                if have & set(types):
                    tabs.append((key, label, types))
        if self.client.practice_tasks():
            tabs.append(("practice", "实操题", ()))
        # 打字练习（小学模块）：每个子任务各占顶部一个标签
        # （和 选择·填空题 / 判断题 / 程序填空 并列，老师勾了几个就出现几个）
        for task, label in self._typing_task_tabs():
            tabs.append((task, label, ()))
        # 画图小题（画板创作 / 拼豆题，老师独立勾选，可同时考）：
        # 各占顶部一个标签，排在最后（打字测试之后）
        for style, label in (("draw", "画板创作"), ("beads", "拼豆创作")):
            if style in self.client.drawing_styles():
                tabs.append((style, label, ()))
        return tabs

    def _typing_task_tabs(self):
        """老师启用的打字子任务 [(任务id, 显示名), ...]"""
        try:
            cfg = self.client.typing_config()
        except Exception:
            return []
        if not cfg:
            return []
        return [(t.get("task"), t.get("name") or t.get("task"))
                for t in cfg.get("tasks", []) if t.get("task")]

    def build_exam(self):
        """考试界面外壳：顶部导航 + 内容区"""
        exam = self.client.state.get("exam") or {}
        title = exam.get("title") or "考试"

        tabs = self.exam_tabs()
        self._drawn_tabs = [k for k, _l, _t in tabs]

        def b(p):
            nav = tk.Frame(p, bg=_UI["lavender"], height=64)
            nav.pack(fill="x")
            nav.pack_propagate(False)
            tk.Label(nav, text=title, bg=_UI["lavender"], fg=INK,
                     font=(FONT, 14, "bold")).pack(side="left",
                                                                 padx=(16, 14))
            # 导航标签：题型多了放不下时可以横向滑动（左右箭头 + 滚轮）
            tabs_wrap = tk.Frame(nav, bg=_UI["lavender"])
            tabs_wrap.pack(side="left", fill="both", expand=True)
            tk.Button(tabs_wrap, text="◀",
                      command=lambda: self._tabs_xview(-1),
                      bg=_UI["lemon"], fg=INK, relief="solid", bd=2, width=2,
                      cursor="hand2", activebackground=_UI["yellow"]
                      ).pack(side="left", fill="y")
            tk.Button(tabs_wrap, text="▶",
                      command=lambda: self._tabs_xview(1),
                      bg=_UI["lemon"], fg=INK, relief="solid", bd=2, width=2,
                      cursor="hand2", activebackground=_UI["yellow"]
                      ).pack(side="right", fill="y")
            tc = tk.Canvas(tabs_wrap, bg=_UI["lavender"], highlightthickness=0, height=42)
            tc.pack(side="left", fill="both", expand=True)
            inner = tk.Frame(tc, bg=_UI["lavender"])
            tc.create_window((0, 0), window=inner, anchor="nw")
            self.tab_btns = {}
            for key, label, _types in tabs:
                btn = tk.Button(inner, text=label,
                                command=lambda k=key: self.switch_tab(k),
                                bg=_UI["lemon"], fg=INK, relief="solid", bd=2,
                                font=(FONT, 11, "bold"), padx=12, pady=6,
                                cursor="hand2", activebackground=_UI["yellow"],
                                activeforeground=INK)
                btn.pack(side="left", padx=2, pady=9)
                self.tab_btns[key] = btn

            def _tabs_cfg(e=None):
                bb = tc.bbox("all") or (0, 0, 0, 0)
                tc.configure(scrollregion=bb)
                tc.itemconfigure("all", width=max(tc.winfo_width(), bb[2]))

            inner.bind("<Configure>", _tabs_cfg)
            self._tabs_canvas = tc

            def _on_wheel(e):
                tc.xview_scroll(-1 if e.delta > 0 else 1, "units")

            def _bind_wheel(e):
                tc.bind_all("<MouseWheel>", _on_wheel)

            def _unbind_wheel(e):
                tc.unbind_all("<MouseWheel>")

            for w in (tc, inner):
                w.bind("<Enter>", _bind_wheel)
                w.bind("<Leave>", _unbind_wheel)
            # 右侧：交卷 / 倒计时 / 考生信息 / 保存状态
            tk.Button(nav, text="交  卷", command=self.submit_all, bg=RED,
                      fg="white", font=(FONT, 11, "bold"),
                      padx=16, pady=4, relief="flat", cursor="hand2",
                      activebackground=RED_D).pack(side="right", padx=(8, 14))
            clock = tk.Frame(nav, bg=_UI["lavender"], padx=12, pady=2)
            clock.pack(side="right", padx=(0, 10))
            tk.Label(clock, text="剩余时间", bg=_UI["lavender"], fg=GRAY,
                     font=(FONT, 9)).pack()
            self.time_label = tk.Label(clock, text="--:--", bg=_UI["lavender"],
                                       fg=INK, font=("Consolas", 17, "bold"))
            self.time_label.pack()
            who = tk.Frame(nav, bg=_UI["lavender"])
            who.pack(side="right", padx=8)
            tk.Label(who, text=self.cfg.name, bg=_UI["lavender"], fg=INK,
                     font=(FONT, 11, "bold")).pack(anchor="e")
            tk.Label(who, text=f"座位号 {self.cfg.seat}", bg=_UI["lavender"], fg=INK,
                     font=(FONT, 9)).pack(anchor="e")
            self.save_label = tk.Label(nav, text="", bg=_UI["lavender"], fg=GRAY,
                                       font=(FONT, 9))
            self.save_label.pack(side="right", padx=6)

            self.content = tk.Frame(p, bg=BG)
            self.content.pack(fill="both", expand=True)
        self.swap(b)
        self.render_tab()

    def _tabs_xview(self, direction):
        """顶部导航左右滑（题型多的时候看后面的标签）"""
        tc = getattr(self, "_tabs_canvas", None)
        if tc:
            try:
                tc.xview_scroll(4 * int(direction), "units")
            except Exception:
                pass

    def switch_tab(self, key):
        """切导航标签：只重建内容区，不重载试卷、不丢答案"""
        # 提交中 / 已交卷：禁止进入新页面（防止交卷后再改答案）
        if self.client.state.get("submitting") or self.client.state.get("submitted"):
            return
        self._user_tab = True         # 学生自己选的，别再自动跳回理论题
        # 告诉 client 现在在哪一块：严格模式下要用它决定
        # "理论题全屏 / 实操题不挡 WPS"，普通窗口模式下只是记个状态
        self.client.state["section"] = ("practice" if key == "practice"
                                        else "theory")
        if key == getattr(self, "cur_tab", None):
            try:
                self.client.sync_lock()
            except Exception:
                pass
            return
        self.cur_tab = key
        self.render_tab()
        try:
            self.client.sync_lock()
        except Exception:
            pass

    def render_tab(self):
        """渲染当前标签页"""
        key = getattr(self, "cur_tab", None) or "choice"
        # 当前标签可能不存在（这场考试没有这种题型）→ 落到第一个真实存在的
        keys = [k for k, _l, _t in self.exam_tabs()]
        if key not in keys:
            key = keys[0] if keys else "choice"
        self.cur_tab = key
        if not getattr(self, "content", None) or not self.content.winfo_exists():
            return
        for w in self.content.winfo_children():
            w.destroy()
        for k, btn in (getattr(self, "tab_btns", None) or {}).items():
            try:
                on = (k == key)
                btn.config(bg=_UI["mint"] if on else _UI["lemon"],
                           fg=INK,
                           font=(FONT, 11, "bold" if on else "normal"))
            except Exception:
                pass
        # ⚠️ 顺序很重要：只要这场考试有理论题，就必须先把试卷拉回来。
        # 顶部标签是"按卷子里实际有的题型"画的，试卷没到手时理论题标签
        # 还画不出来 —— 如果这时直接判断 key == "practice" 去渲染实操题，
        # 试卷就永远不会被请求，理论题标签也永远不出现，
        # 学生就卡在实操题上、理论题根本进不去。
        if self.sheet is None and self._wants_theory():
            self._load_paper_into(self.content)
            return
        if key == "practice":
            self._render_practice_into(self.content)
            return
        if key in ("draw", "beads"):
            self._render_drawing_into(self.content, key)
            return
        if key in [t for t, _l in self._typing_task_tabs()]:
            self._render_typing_into(self.content, key)
            return
        if self.sheet is None:
            self._load_paper_into(self.content)
            return
        self._render_theory_into(self.content, key)

    def _wants_theory(self):
        """
        这场考试有没有理论题（握手数据里带的 has_theory）。
        拿不准时按"有"处理 —— 多请求一次试卷，好过理论题点不出来。
        """
        ex = self.client.state.get("exam") or {}
        return bool(ex.get("has_theory", True))

    # ---------- 打字练习（小学模块） ----------
    def _render_typing_into(self, parent, task):
        """打字练习标签页：一个子任务一页（看键按键 / 小写找键 / 英文·中文打字）"""
        cfg = self.client.typing_config()
        if not cfg:
            tk.Label(parent, text="本场考试没有打字题", bg=BG, fg=GRAY,
                     font=(FONT, 13)).pack(expand=True)
            return
        try:
            from typing_ui import TypingPanel
            # 统计存在 client 上（跨标签共享），切标签不丢进度；
            # 底部的「下一个任务」按钮直接跳到顶部对应标签，
            # 打字任务都做完后跳画板/拼豆创作（没有画图就去交卷）
            TypingPanel(parent, self.client, cfg, task,
                        on_goto=self.switch_tab,
                        tail_next=self._typing_tail_nav()
                        ).pack(fill="both", expand=True)
        except Exception as e:
            print(f"[打字] 面板加载失败: {e}", flush=True)
            tk.Label(parent, text=f"打字练习加载失败: {e}", bg=BG, fg=RED,
                     font=(FONT, 12)).pack(expand=True)

    # ---------- 画图小题（画板创作 / 拼豆题，排在考试最后） ----------
    def _render_drawing_into(self, parent, kind="draw"):
        """
        画图小题标签页，按 kind 出面板：
        draw=画板创作（画笔 / 形状 / 电学·网络·化学元件 → 交一张压缩图）
        beads=拼豆题（彩豆拼图 → 交一张压缩图）
        """
        cfg = self.client.drawing_config()
        if cfg is None:
            tk.Label(parent, text="本场考试没有画图题", bg=BG, fg=GRAY,
                     font=(FONT, 13)).pack(expand=True)
            return
        try:
            if kind == "beads":
                from beads_ui import BeadPanel
                cls = BeadPanel
            else:
                from drawing_ui import DrawingPanel
                cls = DrawingPanel
            # 画的内容存在 client.drawing_state(kind)（跨标签共享），切页不丢画
            # 右上角「上一题/下一题」在整个考试序列里前后走
            self.drawing_panel = cls(parent, self.client, cfg,
                                     on_save=self._save_drawing,
                                     on_prev=self._drawing_prev,
                                     on_next=lambda k=kind: self._drawing_next(k),
                                     next_label=self._drawing_next_label(kind))
            self.drawing_panel.pack(fill="both", expand=True)
        except Exception as e:
            print(f"[画图] 面板加载失败: {e}", flush=True)
            tk.Label(parent, text=f"画图加载失败: {e}", bg=BG, fg=RED,
                     font=(FONT, 12)).pack(expand=True)

    def _save_drawing(self, panel, done):
        """
        画图保存（改动自动保存 / 右上角「保存」共用）：
        后台导出压缩图并上传（画板 / 拼豆按 panel.kind 各传各的），
        完成后回主线程调 done(ok, msg) 刷新指示灯。
        ⚠️ 上传线程不碰 Tk，回调走 uiq（和 SubmitQueue 同一套约定）。
        """
        try:
            data, ext = panel.export_png()
        except Exception as e:
            done(False, f"生成图片失败：{e}")
            return

        def work():
            try:
                r = self.client.api.upload_drawing(
                    self.exam_id, data, ext,
                    kind=getattr(panel, "kind", "draw"))
                ok = bool(r.get("ok"))
                msg = r.get("msg") or ""
            except Exception as e:
                ok, msg = False, str(e)
            self.uiq.put(lambda ok=ok, msg=msg: done(ok, msg))
        threading.Thread(target=work, daemon=True).start()

    def _drawing_prev(self):
        """画图右上角「上一题」：回上一个题型的最后一题"""
        key = self._prev_tab_key()
        if not key:
            return
        types = dict((k, t) for k, _l, t in self.exam_tabs()).get(key) or ()
        self.switch_tab(key)
        last = self._last_index_of(types)
        if last is not None:
            self.goto_q(last)

    # ---------- 「下一题」跨面板导航（打字 → 画板 → 拼豆 → 交卷） ----------
    def _drawing_order(self):
        """本场启用的画图小题，按考试顺序：draw（画板）→ beads（拼豆）"""
        return [s for s in ("draw", "beads")
                if s in self.client.drawing_styles()]

    def _typing_tail_nav(self):
        """
        打字任务全部做完后，底部「下一题」去哪：
        有画图题 → 跳画板创作（或拼豆创作）；没有 → 「▶ 去交卷」。
        返回 (按钮文字, 回调)，交给 TypingPanel 的尾部导航。
        """
        labels = {"draw": "画板创作", "beads": "拼豆创作"}
        order = self._drawing_order()
        if order:
            s = order[0]
            return (f"下一题：{labels[s]} ▶", lambda s=s: self.switch_tab(s))
        return ("▶ 去交卷", self.submit_all)

    def _drawing_next(self, kind):
        """画图右上角「下一题」：去下一种画图小题，画板/拼豆都做完就去交卷"""
        order = self._drawing_order()
        if kind in order:
            rest = order[order.index(kind) + 1:]
            if rest:
                self.switch_tab(rest[0])
                return
        self.submit_all()

    def _drawing_next_label(self, kind):
        """画图右上角「下一题」按钮文字（跟着下一个目标变）"""
        labels = {"draw": "画板创作", "beads": "拼豆创作"}
        order = self._drawing_order()
        if kind in order:
            rest = order[order.index(kind) + 1:]
            if rest:
                return f"下一题：{labels[rest[0]]} ▶"
        return "▶ 去交卷"

    # ---------- 理论题选项图片（img:文件名.png 选项文字） ----------
    @staticmethod
    def _parse_img_opt(opt):
        """
        解析选项里的图片标记。允许写成：
            img:cap.png 大写锁定键        （不带字母前缀）
            A. img:cap.png 大写锁定键     （老师连着前缀一起写）
        返回 (图片文件名 或 None, 显示的纯文字)
        """
        import re
        s = str(opt or "")
        m = re.match(r"^\s*([A-Za-z][.、．]\s*)?img:(\S+)\s*(.*)$", s)
        if m and m.group(2):
            prefix = (m.group(1) or "").strip()
            text = (prefix + " " + m.group(3)).strip() if m.group(3) else prefix
            return m.group(2), text
        return None, s

    def _opt_photo(self, name, setter):
        """
        从服务器取选项图片（后台线程），成功后在主线程回调 setter(PhotoImage)。
        结果缓存：一张图一场考试只取一次；取失败记 False 不再反复请求。
        """
        cache = getattr(self, "_img_cache", None)
        if cache is None:
            cache = self._img_cache = {}
        hit = cache.get(name)
        if hit:
            setter(hit)
            return
        if hit is False:
            return

        def fetch():
            # 只下载字节：tk.PhotoImage 必须在主线程构造，不能在这里建（跨线程 Tk 会随机失败）
            raw = None
            try:
                raw = self.client.api.get_bytes("/qimg/" + name)
            except Exception as e:
                print(f"[选项图片] {name} 加载失败: {e}", flush=True)

            def done():
                photo = None
                if raw:
                    try:
                        import base64
                        photo = tk.PhotoImage(data=base64.b64encode(raw))
                    except Exception:
                        photo = None
                self._img_cache[name] = photo if photo else False
                if photo:
                    try:
                        setter(photo)
                    except Exception:
                        pass
            self.uiq.put(done)
        threading.Thread(target=fetch, daemon=True).start()

    def _set_opt_img(self, widget, photo):
        """把取到的图片贴到选项按钮上（题早翻走了就扔掉）"""
        try:
            if widget.winfo_exists():
                widget.config(image=photo, compound="left")
        except Exception:
            pass

    def _show_stem_img(self, photo):
        """题干配图到货：贴上去并显示（题干区在滚动容器里，要 before q_stem）"""
        try:
            if not self.stem_img.winfo_exists():
                return
            self.stem_img.config(image=photo)
            if not self.stem_img.winfo_ismapped():
                self.stem_img.pack(anchor="w", pady=(0, 10),
                                   before=self.q_stem)
        except Exception:
            pass

    # ---------- 拖拽题 ----------
    def render_drag_question(self, parent, q, saved):
        """
        拖拽题：把项目拖到格子里。
        答案 "1:A|2:B"（第几格:哪个项目），判分在服务端 db.judge。
        """
        try:
            from drag_ui import DragBoard
        except Exception as e:
            tk.Label(parent, text=f"拖拽题组件加载失败: {e}", bg=CARD, fg=RED,
                     font=(FONT, 12)).pack(anchor="w")
            return
        slots = q.get("slots") or []
        if not slots:
            tk.Label(parent, text="这道拖拽题没有配槽位，请联系老师",
                     bg=CARD, fg=RED,
                     font=(FONT, 12)).pack(anchor="w")
            return
        board = DragBoard(parent, q.get("options") or [], slots,
                          on_change=lambda ans: self.save_current(ans),
                          image_getter=self._opt_photo,
                          initial=saved or "")
        board.pack(fill="both", expand=True)
        self.vars["_drag"] = board
        bar = tk.Frame(parent, bg=CARD)
        bar.pack(anchor="w", pady=(10, 0))
        tk.Button(bar, text="清空重做", command=board.clear,
                  font=(FONT, 11), padx=14, pady=4,
                  bg=LINE_2, relief="flat", cursor="hand2").pack(side="left")
        tk.Label(bar, text="  提示：拖错了点一下项目就能取回来",
                 bg=CARD, fg=GRAY,
                 font=(FONT, 10)).pack(side="left")

    # ---------- 试卷加载 ----------
    def _load_paper_into(self, parent):
        tk.Label(parent, text="正在加载试卷…", bg=BG, fg=BLUE,
                 font=(FONT, 14)).pack(expand=True)
        if getattr(self, "_paper_loading", False):
            return
        self._paper_loading = True

        def fetch():
            r = None
            for attempt in range(3):
                try:
                    r = self.client.api.paper()
                    if r.get("ok"):
                        break
                except Exception as e:
                    print(f"[试卷] 异常(第{attempt+1}次): {e}", flush=True)
                if attempt < 2:
                    time.sleep(1)
            self._paper_loading = False
            if r and r.get("ok"):
                self.uiq.put(lambda: self._got_paper(r))
            else:
                msg = (r or {}).get("msg", "无法获取试卷")
                self.uiq.put(lambda: self._show_paper_error(msg))
        threading.Thread(target=fetch, daemon=True).start()

    def _got_paper(self, r):
        from exam_client import AnswerSheet
        new_exam = (self._theory_exam_id != r["exam_id"])
        self.sheet = AnswerSheet(r["questions"])
        self.sheet.load_saved(r.get("answered") or {})
        self.exam_id = r["exam_id"]
        self._theory_exam_id = r["exam_id"]
        self._last_sync = time.time()
        if not hasattr(self, "marked"):
            self.marked = set()
        # 答案同步器：本地即时保存，每 10 秒批量上传
        sync = self.client.begin_answer_sync(self.exam_id)
        sync.on_saved = self._on_answers_saved
        # 换了一场考试：标签回到默认（理论题优先）。
        # "学生点过哪个标签"是按场次算的 —— 上一场他选过实操题，
        # 不该影响这一场；同一场里重新拉试卷则保持他选的那个。
        if new_exam:
            self._user_tab = False
            self.cur_tab = "choice"
        # 试卷到手才知道有哪些题型 → 重建一次导航，
        # 这样不会出现"点进去才发现这一部分没有题目"的空标签
        if [k for k, _l, _t in self.exam_tabs()] != getattr(
                self, "_drawn_tabs", None):
            self.build_exam()
        else:
            self.render_tab()
        # swap() 是按"构造函数名"猜页面名的（内部函数叫 b → 猜成 unknown），
        # 这里重建过一次，必须把页面名改回 exam ——
        # 否则心跳/超时都会以为"不在考试页"，时间到不会自动交卷
        self._set_page("exam")

    def _show_paper_error(self, msg):
        """试卷加载失败"""
        for w in self.content.winfo_children():
            w.destroy()
        card = tk.Frame(self.content, bg=CARD, padx=40, pady=30,
                        highlightbackground=LINE, highlightthickness=2)
        card.place(relx=0.5, rely=0.5, anchor="center")
        tk.Label(card, text="试卷加载失败", bg=CARD, fg=RED,
                 font=(FONT, 16, "bold")).pack(pady=(0, 8))
        tk.Label(card, text=msg, bg=CARD, fg=GRAY, wraplength=400,
                 justify="center", font=(FONT, 11)).pack()
        tk.Button(card, text="重新加载", command=self._reload_paper,
                  bg=BLUE, fg=INK, font=(FONT, 11),
                  padx=20, pady=6, relief="flat").pack(pady=(16, 0))

    def _reload_paper(self):
        self.sheet = None
        self.render_tab()

    # ---------- 理论题内容 ----------
    def _render_theory_into(self, parent, tab_key):
        types = dict((k, t) for k, _l, t in self.THEORY_TABS).get(tab_key, ())
        self.cur_types = types
        q = self.sheet.current()
        if not q or q["qtype"] not in types:
            idx = self._first_index_of(types)
            if idx is None:
                tk.Label(parent, text="这一部分没有题目", bg=BG, fg=GRAY,
                         font=(FONT, 13)).pack(expand=True)
                return
            self.sheet.index = idx

        body = tk.Frame(parent, bg=BG)
        body.pack(fill="both", expand=True, padx=16, pady=12)

        # 左：答题卡（只显示本页的题）
        side = tk.Frame(body, bg=CARD, width=250, padx=14, pady=14,
                        highlightbackground=LINE, highlightthickness=2)
        side.pack(side="left", fill="y", padx=(0, 12))
        side.pack_propagate(False)
        tk.Label(side, text="答题卡", bg=CARD, fg=INK,
                 font=(FONT, 13, "bold")).pack(anchor="w")
        self.sheet_frame = tk.Frame(side, bg=CARD)
        self.sheet_frame.pack(fill="both", expand=True, pady=8)
        self.progress = tk.Label(side, text="", bg=CARD, fg=GRAY_D,
                                 font=(FONT, 11))
        self.progress.pack(anchor="w")
        legend = tk.Frame(side, bg=CARD)
        legend.pack(anchor="w", pady=(6, 4))
        # 色块用真实的小方块，不用 "■" 字符 ——
        # 方块字符的颜色由字体渲染决定，不同系统里大小/颜色都不一致
        for color, txt in ((GREEN, "已答"), (BLUE, "当前"),
                           (ORANGE, "标记"), (LINE_2, "未答")):
            chip = tk.Frame(legend, bg=color, width=10, height=10)
            chip.pack(side="left", pady=2)
            chip.pack_propagate(False)
            tk.Label(legend, text=txt, bg=CARD, fg=GRAY_D,
                     font=(FONT, 9)).pack(side="left",
                                                        padx=(4, 8))

        # 右：题目区
        main = tk.Frame(body, bg=CARD, padx=24, pady=18,
                        highlightbackground=LINE, highlightthickness=2)
        main.pack(side="left", fill="both", expand=True)
        meta = tk.Frame(main, bg=CARD)
        meta.pack(fill="x")
        self.q_no = tk.Label(meta, text="", bg=CARD, fg=BLUE,
                             font=(FONT, 12, "bold"))
        self.q_no.pack(side="left")
        self.mark_btn = tk.Button(meta, text="☆ 标记本题", command=self.toggle_mark,
                                  font=(FONT, 10), padx=10, pady=2,
                                  relief="flat", bg=LINE_2, cursor="hand2")
        self.mark_btn.pack(side="right")
        # ⚠️ 顺序很关键：底部翻页条**必须先 pack**。
        # 以前先放内容区（fill=both, expand=True）再放按钮条，程序填空题的
        # 代码 + 控制台一长，按钮条就被挤出可视区 —— 学生看不到"下一题"。
        nav = tk.Frame(main, bg=CARD)
        nav.pack(side="bottom", fill="x", pady=(10, 0))
        tk.Frame(main, bg=LINE, height=1).pack(side="bottom", fill="x",
                                               pady=(10, 0))
        self.prev_btn = tk.Button(nav, text="◀ 上一题", command=self.prev_q,
                                  font=(FONT, 11), padx=16, pady=6)
        self.prev_btn.pack(side="left")
        self.next_btn = tk.Button(nav, text="下一题 ▶", command=self.next_q,
                                  font=(FONT, 11), padx=16, pady=6)
        self.next_btn.pack(side="left", padx=8)

        # 中间内容区：放进可滚动的 Canvas —— 题干/代码再长也能滚，
        # 翻页按钮始终留在底部
        wrap = tk.Frame(main, bg=CARD)
        wrap.pack(side="top", fill="both", expand=True, pady=(10, 0))
        self.scroll_canvas = tk.Canvas(wrap, bg=CARD, highlightthickness=0, bd=0)
        vbar = tk.Scrollbar(wrap, orient="vertical",
                            command=self.scroll_canvas.yview)
        self.scroll_canvas.configure(yscrollcommand=vbar.set)
        vbar.pack(side="right", fill="y")
        self.scroll_canvas.pack(side="left", fill="both", expand=True)
        inner = tk.Frame(self.scroll_canvas, bg=CARD)
        self._scroll_inner = inner
        win = self.scroll_canvas.create_window((0, 0), window=inner,
                                              anchor="nw")

        def _sync_scroll(_e=None):
            try:
                self.scroll_canvas.configure(
                    scrollregion=self.scroll_canvas.bbox("all"))
            except Exception:
                pass

        def _sync_width(e):
            try:
                self.scroll_canvas.itemconfigure(win, width=e.width)
            except Exception:
                pass

        def _wheel(e):
            # 只有内容比可视区高时才滚（滚轮在代码框/控制台里要留给它们自己）
            try:
                if (self.scroll_canvas.bbox("all") or (0, 0, 0, 0))[3] \
                        <= self.scroll_canvas.winfo_height():
                    return
                step = -1 if e.delta > 0 else 1
                self.scroll_canvas.yview_scroll(step, "units")
            except Exception:
                pass

        inner.bind("<Configure>", _sync_scroll)
        self.scroll_canvas.bind("<Configure>", _sync_width)
        self._scroll_wheel = _wheel
        for _w in (self.scroll_canvas, inner):
            _w.bind("<MouseWheel>", _wheel)

        # 题干配图（题目里写 img:文件名.png 时显示在题干上方）
        self.stem_img = tk.Label(inner, bg=CARD)
        self.stem_img.bind("<MouseWheel>", _wheel)
        self.q_stem = tk.Label(inner, text="", bg=CARD, wraplength=760,
                               justify="left", font=(FONT, 15))
        self.q_stem.pack(anchor="w", pady=(0, 14))
        self.q_stem.bind("<MouseWheel>", _wheel)
        self.opt_frame = tk.Frame(inner, bg=CARD)
        self.opt_frame.pack(anchor="w", fill="x")

        self.build_card()
        self.render_question()
        self.refresh_card()

    def _next_tab_key(self):
        """当前标签之后还有哪个导航标签（没有就 None）"""
        keys = [k for k, _l, _t in self.exam_tabs()]
        cur = getattr(self, "cur_tab", "choice")
        if cur not in keys:
            return None
        i = keys.index(cur)
        return keys[i + 1] if i + 1 < len(keys) else None

    def _prev_tab_key(self):
        """当前标签之前是哪个导航标签（没有就 None）"""
        keys = [k for k, _l, _t in self.exam_tabs()]
        cur = getattr(self, "cur_tab", "choice")
        if cur not in keys:
            return None
        i = keys.index(cur)
        return keys[i - 1] if i > 0 else None

    def _first_index_of(self, types):
        for i, q in enumerate(self.sheet.questions):
            if q["qtype"] in types:
                return i
        return None

    def _last_index_of(self, types):
        got = None
        for i, q in enumerate(self.sheet.questions):
            if q["qtype"] in types:
                got = i
        return got

    def _step_index(self, cur, step):
        """在**本页题型范围内**前后翻题；到头返回 None"""
        i = cur + step
        while 0 <= i < self.sheet.total():
            if self.sheet.questions[i]["qtype"] in getattr(self, "cur_types", ()):
                return i
            i += step
        return None

    def render_question(self):
        """
        只重建「选项区」。
        答题卡不重建 —— 以前每答一题就把几十个按钮销毁重建，是卡顿主因之一。
        """
        q = self.sheet.current()
        if not q:
            return
        types = getattr(self, "cur_types", ())
        same = [i for i, x in enumerate(self.sheet.questions)
                if x["qtype"] in types]
        pos = same.index(self.sheet.index) + 1 if self.sheet.index in same else 0
        self.q_no.config(
            text=f"本部分第 {pos} / {len(same)} 题（全卷第 {self.sheet.index + 1} 题）"
                 f"    [{TYPE_LABEL.get(q['qtype'], q['qtype'])}]   {q['score']} 分")
        # 题干也可能带图（img:文件名.png 题干文字）
        stem_img, stem_text = self._parse_img_opt(q["stem"])
        self.q_stem.config(text=stem_text or q["stem"])
        if stem_img:
            self._opt_photo(stem_img, self._show_stem_img)
        else:
            try:
                self.stem_img.pack_forget()
            except Exception:
                pass
        try:
            self.mark_btn.config(
                text="★ 已标记" if self.sheet.index in self.marked else "☆ 标记本题")
        except Exception:
            pass
        # 底部按钮：本页还有题就"下一题"；到本页末尾就引导去下一部分
        try:
            if hasattr(self, "prev_btn") and self.prev_btn.winfo_exists():
                if self._step_index(self.sheet.index, -1) is not None:
                    self.prev_btn.config(text="◀ 上一题", command=self.prev_q,
                                         bg="SystemButtonFace", fg=INK)
                elif self._prev_tab_key():
                    label = dict((k, l) for k, l, _t in
                                 self.exam_tabs())[self._prev_tab_key()]
                    self.prev_btn.config(
                        text=f"◀ 上一部分：{label}", command=self.prev_q,
                        bg=BLUE_L, fg=BLUE_D)
                else:
                    self.prev_btn.config(text="◀ 已是第一部分",
                                         bg=LINE_2, fg=GRAY_D,
                                         command=lambda: None)
            if hasattr(self, "next_btn") and self.next_btn.winfo_exists():
                if self._step_index(self.sheet.index, 1) is not None:
                    self.next_btn.config(text="下一题 ▶", command=self.next_q,
                                         bg="SystemButtonFace", fg=INK)
                else:
                    nxt = self._next_tab_key()
                    if nxt:
                        label = dict((k, l) for k, l, _t in self.exam_tabs())[nxt]
                        txt = ("开始实操题 ▶" if nxt == "practice"
                               else f"下一部分：{label} ▶")
                        self.next_btn.config(
                            text=txt, bg=GREEN, fg="white",
                            command=lambda k=nxt: self.switch_tab(k))
                    else:
                        self.next_btn.config(text="已是最后一部分", bg=LINE_2,
                                             fg=GRAY_D, command=lambda: None)
        except Exception:
            pass

        for w in self.opt_frame.winfo_children():
            w.destroy()

        qt = q["qtype"]
        saved = self.sheet.get(q["id"])
        self.vars["_opt_boxes"] = []      # 选项外框（选中态高亮用）

        if qt in ("single", "judge"):
            v = tk.StringVar(value=saved or "")
            for i, opt in enumerate(q["options"] or []):
                tag = "ABCD"[i]
                img_name, opt_text = self._parse_img_opt(opt)
                # 外层卡片框：选中的那项变蓝底 + 蓝边 + 右侧打勾，
                # 老样式只有一个系统小圆点，学生经常看不出自己选了没选
                box, row, mark = self._opt_box()
                rb = tk.Radiobutton(row, text=opt_text, variable=v, value=tag,
                                    bg=CARD, font=(FONT, 13), fg=INK,
                                    anchor="w", selectcolor=CARD,
                                    activebackground=CARD, cursor="hand2",
                                    highlightthickness=0, bd=0,
                                    command=lambda vv=v: (self.save_current(vv.get()),
                                                          self._paint_opt_boxes()))
                rb.pack(side="left", fill="x", expand=True)
                self.vars["_opt_boxes"].append(
                    {"tag": tag, "box": box, "row": row, "mark": mark,
                     "widget": rb, "on": (lambda t=tag: v.get() == t)})
                if img_name:
                    self._opt_photo(img_name,
                                    lambda p, w=rb: self._set_opt_img(w, p))
            self.vars["_cur"] = v
            self._paint_opt_boxes()

        elif qt == "multi":
            vs = []
            picked = set(saved or "")
            for i, opt in enumerate(q["options"] or []):
                tag = "ABCD"[i]
                img_name, opt_text = self._parse_img_opt(opt)
                box, row, mark = self._opt_box()
                bv = tk.BooleanVar(value=(tag in picked))
                cb = tk.Checkbutton(row, text=opt_text, variable=bv,
                                    bg=CARD, font=(FONT, 13), fg=INK,
                                    anchor="w", selectcolor=CARD,
                                    activebackground=CARD, cursor="hand2",
                                    highlightthickness=0, bd=0,
                                    command=lambda: (self.save_multi(),
                                                     self._paint_opt_boxes()))
                cb.pack(side="left", fill="x", expand=True)
                self.vars["_opt_boxes"].append(
                    {"tag": tag, "box": box, "row": row, "mark": mark,
                     "widget": cb, "on": (lambda b=bv: bool(b.get()))})
                if img_name:
                    self._opt_photo(img_name,
                                    lambda p, w=cb: self._set_opt_img(w, p))
                vs.append((tag, bv))
            self.vars["_multi"] = vs
            self._paint_opt_boxes()

        elif qt == "code":
            self.render_code_question(self.opt_frame, q, saved)

        elif qt == "drag":
            self.render_drag_question(self.opt_frame, q, saved)

        else:  # blank
            v = tk.StringVar(value=saved or "")
            e = tk.Entry(self.opt_frame, textvariable=v,
                         font=(FONT, 14), width=44)
            e.pack(anchor="w", pady=6)
            # 输入即存本地（不发网络请求）
            v.trace_add("write", lambda *_: self.save_current(v.get()))
            self.vars["_cur"] = v

        # 换题回到顶部；并给新控件补滚轮（内容长时能滚着看，按钮始终在底部）
        try:
            if getattr(self, "scroll_canvas", None) is not None:
                self.scroll_canvas.yview_moveto(0)
        except Exception:
            pass
        self._bind_wheel_tree(self.opt_frame)

    def _opt_box(self):
        """
        造一个选项外框（返回 外框/内容行/右侧勾标记）。
        选中的选项会变蓝底 + 蓝边 + 打勾，学生一眼就能看出自己选了哪个。
        """
        box = tk.Frame(self.opt_frame, bg=CARD, highlightbackground=LINE_2,
                       highlightthickness=2, bd=0)
        box.pack(anchor="w", pady=4, fill="x", padx=2)
        row = tk.Frame(box, bg=CARD)
        row.pack(fill="x", padx=10, pady=6)
        mark = tk.Label(row, text="", bg=CARD, fg=GREEN, width=3,
                        font=(FONT, 14, "bold"))
        mark.pack(side="right")
        return box, row, mark

    def _paint_opt_boxes(self):
        """按当前选中状态刷新选项外框（换题/改选后都要调一次）"""
        for b in self.vars.get("_opt_boxes") or []:
            try:
                on = bool(b["on"]())
            except Exception:
                on = False
            bg = "#e8f1fe" if on else CARD
            try:
                b["box"].config(bg=bg, highlightbackground=BLUE if on else LINE_2)
                b["row"].config(bg=bg)
                # 选中只用底色+加粗提示，不再显示小勾（考试界面不该出现对错标记）
                b["mark"].config(text="", bg=bg)
                b["widget"].config(
                    bg=bg, activebackground=bg,
                    font=(FONT, 13, "bold" if on else "normal"))
            except Exception:
                pass

    def _bind_wheel_tree(self, widget):
        """给内容区里的控件补滚轮绑定（代码框/控制台自己滚，跳过）"""
        wheel = getattr(self, "_scroll_wheel", None)
        if wheel is None:
            return
        try:
            children = widget.winfo_children()
        except Exception:
            return
        for child in children:
            if isinstance(child, (tk.Text, tk.Listbox, tk.Scrollbar)):
                continue
            try:
                child.bind("<MouseWheel>", wheel, add="+")
            except Exception:
                pass
            self._bind_wheel_tree(child)

    # ---------- 程序填空（直接在原代码上填 + 控制台）----------
    def render_code_question(self, parent, q, saved):
        """
        程序填空界面（按老师要求重做）：
          上方  原代码，空位处就是输入框 —— 学生直接在原题上填，
                不用猜"第 1 空 / 第 2 空"分别对应哪里
          下方  控制台：上面写 input() 要读的数据，点运行，下面看输出
        """
        import re
        code = q.get("code") or ""
        lang = (q.get("language") or "python").lower()
        n_blank = q.get("blanks") or len(re.findall(r"_{4,}", code))
        saved_parts = [x.strip() for x in str(saved or "").split("||")]
        saved_parts += [""] * max(0, n_blank - len(saved_parts))

        head = tk.Frame(parent, bg="#1E293B", padx=14, pady=6)
        head.pack(fill="x", pady=(0, 6))
        tk.Label(head,
                 text=f"  {lang.upper()} 程序填空（{n_blank} 个空）"
                      f" —— 直接在下面代码的空位里输入",
                 bg="#1E293B", fg="#E2E8F0",
                 font=(FONT, 11, "bold")).pack(anchor="w")

        # ---- 代码区：空位用内嵌输入框，在原文上直接填 ----
        # 高度固定（最多 16 行）：题目再长也不会把下面的控制台和翻页按钮顶出去，
        # 长代码在框内自己滚。
        code_box = tk.Frame(parent, bg="#0f172a")
        code_box.pack(fill="x", pady=(0, 6))
        xsb = tk.Scrollbar(code_box, orient="horizontal")
        xsb.pack(side="bottom", fill="x")
        ysb = tk.Scrollbar(code_box, orient="vertical")
        ysb.pack(side="right", fill="y")
        txt = tk.Text(code_box, height=min(16, max(6, code.count("\n") + 3)),
                      bg="#0f172a", fg="#e2e8f0", relief="flat",
                      font=("Consolas", 12), wrap="none",
                      insertbackground="#e2e8f0", padx=14, pady=10)
        txt.pack(side="left", fill="both", expand=True)
        txt.configure(xscrollcommand=xsb.set, yscrollcommand=ysb.set)
        xsb.config(command=txt.xview)
        ysb.config(command=txt.yview)

        def _append(text):
            txt.configure(state="normal")
            txt.insert("end", text)
            txt.configure(state="disabled")

        entries = []
        pat = re.compile(r"_{4,}")
        for line in code.split("\n"):
            pos = 0
            for m in pat.finditer(line):
                if line[pos:m.start()]:
                    _append(line[pos:m.start()])
                i = len(entries)
                var = tk.StringVar(
                    value=saved_parts[i] if i < len(saved_parts) else "")
                width = max(6, min(26, len(var.get()) + 5))
                e = tk.Entry(txt, textvariable=var, width=width,
                             font=("Consolas", 12), justify="center",
                             bg="#1E293B", fg="#fbbf24",
                             insertbackground="#fbbf24", relief="flat",
                             highlightthickness=2,
                             highlightbackground="#fbbf24")
                e.bind("<KeyRelease>", lambda ev: self.save_code_blanks())
                e.bind("<Return>", lambda ev: self.run_code())
                txt.window_create("end", window=e)
                entries.append(var)
                pos = m.end()
            _append(line[pos:] + "\n")

        self.vars["_code_entries"] = entries
        self.vars["_code_widgets"] = []
        self.vars["_code_q"] = q

        # ---- 控制台：输入 + 运行 + 输出 ----
        tk.Label(parent, text="控制台", bg=CARD, fg=INK,
                 font=(FONT, 12, "bold")).pack(anchor="w", pady=(6, 2))
        con = tk.Frame(parent, bg="#0f172a")
        con.pack(fill="x")
        tk.Label(con, text="输入（程序里 input() 按顺序读这些行，一行一个）",
                 bg="#0f172a", fg=GRAY,
                 font=(FONT, 10)).pack(anchor="w", padx=12, pady=(8, 2))
        self.stdin_box = tk.Text(con, height=3, bg="#111c30", fg="#fbbf24",
                                 relief="flat", font=("Consolas", 12),
                                 insertbackground="#fbbf24", padx=12, pady=6)
        self.stdin_box.pack(fill="x", padx=12)
        stdin_pre = (q.get("stdin_data") or "").strip()
        if stdin_pre:
            self.stdin_box.insert("1.0", stdin_pre)

        runbar = tk.Frame(con, bg="#0f172a")
        runbar.pack(fill="x", padx=12, pady=8)
        # 提前检测本机有没有能用的 Python（微软商店占位程序不算）
        try:
            import runner as _runner
            _has_py = bool(_runner.detect().get("python"))
        except Exception:
            _has_py = True      # 检测本身出错时别拦着学生
        self.run_btn = tk.Button(
            runbar, text="▶ 运行代码", command=self.run_code,
            bg=GREEN if _has_py else GRAY, fg="white",
            font=(FONT, 11, "bold"),
            padx=16, pady=5, relief="flat",
            cursor="hand2" if _has_py else "arrow",
            activebackground=GREEN_D, state="normal" if _has_py else "disabled")
        self.run_btn.pack(side="left")
        self.run_status = tk.Label(runbar, text="", bg="#0f172a", fg=GRAY,
                                   font=(FONT, 10))
        self.run_status.pack(side="left", padx=12)
        if not _has_py:
            self.run_status.config(
                text="本机没装 Python，无法运行（不影响作答和评分）",
                fg="#fbbf24")

        tk.Label(con, text="输出", bg="#0f172a", fg=GRAY,
                 font=(FONT, 10)).pack(anchor="w", padx=12)
        self.run_out = tk.Text(con, height=7, bg="#111c30", fg="#4ade80",
                               relief="flat", font=("Consolas", 12),
                               padx=12, pady=6, wrap="word")
        self.run_out.pack(fill="x", padx=12, pady=(2, 10))
        self.run_out.insert("1.0", "点「运行代码」，输出显示在这里")
        self.run_out.configure(state="disabled")

    def _highlight_blanks(self, txt, code):
        """把代码里的 ____ 标黄"""
        import re
        txt.configure(state="normal")
        for m in re.finditer(r"_{4,}", code):
            start = f"1.0+{m.start()}c"
            end = f"1.0+{m.end()}c"
            txt.tag_add("blank", start, end)
        txt.configure(state="disabled")

    def _focus_blank(self, n):
        """回车跳到第 n 个空（0 起）"""
        try:
            ws = self.vars.get("_code_widgets", [])
            if 0 <= n < len(ws):
                ws[n].focus_set()
                ws[n].icursor("end")
        except Exception:
            pass

    def save_code_blanks(self):
        """把各空的答案拼成 '答案1||答案2' 保存"""
        entries = self.vars.get("_code_entries", [])
        val = "||".join(v.get().strip() for v in entries)
        self.save_current(val)

    def run_code(self):
        """运行当前程序填空 / 程序大题（本地执行）"""
        q = self.vars.get("_code_q")
        if not q:
            return
        # 防重复点击：一次只跑一个进程（连点会把机器 CPU 占满）
        if getattr(self, "_running_code", False):
            return
        self._running_code = True

        entries = self.vars.get("_code_entries", [])
        answers = [v.get().strip() for v in entries]
        lang = (q.get("language") or "python").lower()

        missing = sum(1 for a in answers if not a)
        if missing:
            self.run_status.config(
                text=f"还有 {missing} 个空没填（可以先运行看效果）", fg="#e67e22")
        else:
            self.run_status.config(text="正在运行…", fg=BLUE)
        try:
            self.run_btn.config(state="disabled")
        except Exception:
            pass
        self.root.update_idletasks()

        # 后台线程跑，避免界面卡死
        def work():
            import runner
            try:
                code = runner.fill_blanks(q.get("code") or "", answers)
                # 控制台「输入」框里的内容就是程序 input() 读的数据
                try:
                    stdin_data = self.stdin_box.get("1.0", "end").rstrip("\n")
                except Exception:
                    stdin_data = q.get("stdin_data") or ""
                # 题目里可以带 timeout 字段，程序大题给更长时间
                to = int(q.get("timeout") or 0) or None
                r = runner.run(code, lang, stdin_data, timeout=to)
            finally:
                self._running_code = False
            self.uiq.put(lambda: self._show_run_result(r, lang))

        threading.Thread(target=work, daemon=True).start()

    def _show_run_result(self, r, lang):
        """显示运行结果"""
        try:
            # 跑完了，把按钮放出来
            self.run_btn.config(state="normal")
        except Exception:
            pass
        try:
            self.run_out.configure(state="normal")
            self.run_out.delete("1.0", "end")
            if r.get("ok"):
                out = (r.get("out") or "").strip()
                self.run_out.insert("1.0", out if out else "（程序没有输出）")
                self.run_status.config(text="运行完成", fg=GREEN)
            else:
                err = (r.get("err") or r.get("msg") or "运行失败").strip()
                self.run_out.insert("1.0", err)
                self.run_out.tag_configure("err", foreground="#e74c3c")
                self.run_out.tag_add("err", "1.0", "end")
                self.run_status.config(text="运行出错", fg=RED)
            self.run_out.configure(state="disabled")
        except Exception:
            pass

    def save_multi(self):
        tags = [t for t, bv in self.vars.get("_multi", []) if bv.get()]
        self.save_current("".join(tags))

    def save_current(self, value):
        """
        学生改答案 → 只写本地 + 打脏标记，不联网。
        （以前每敲一个字符就发一次 HTTP；50 台机器一起敲，服务端单连接
          SQLite 直接排队，界面就是这么一卡一卡的）
        """
        q = self.sheet.current()
        if not q:
            return
        self.sheet.set(q["id"], value)
        self.refresh_card()
        self.client.queue_answers({str(q["id"]): value})

    def _on_answers_saved(self, n=0):
        """批量同步成功（后台线程）→ 更新「答案已保存 时:分:秒」"""
        self.uiq.put(self._show_saved_time)

    def _show_saved_time(self):
        try:
            if hasattr(self, "save_label") and self.save_label.winfo_exists():
                self.save_label.config(
                    text="答案已保存 " + time.strftime("%H:%M:%S"))
        except Exception:
            pass

    # ---------- 答题卡 ----------
    def build_card(self):
        """答题卡：只显示**本部分**的题，按钮只建一次"""
        types = getattr(self, "cur_types", ())
        self.card_idx = [i for i, q in enumerate(self.sheet.questions)
                         if q["qtype"] in types]
        self.card_btns = []
        cols = 5
        for n, gi in enumerate(self.card_idx):
            # pady=4：把点按区域撑到约 38×30px，原来太扁容易点错题号
            btn = tk.Button(self.sheet_frame, text=str(n + 1), width=3,
                            relief="flat", font=(FONT, 10),
                            pady=4, cursor="hand2",
                            command=lambda x=gi: self.goto_q(x))
            btn.grid(row=n // cols, column=n % cols, padx=3, pady=3)
            self.card_btns.append(btn)

    def refresh_card(self):
        """只更新颜色（原来每答一题销毁重建全部按钮）"""
        if not getattr(self, "card_btns", None):
            return
        for n, btn in enumerate(self.card_btns):
            gi = self.card_idx[n]
            qid = self.sheet.questions[gi]["id"]
            if gi == self.sheet.index:
                bg, fg = BLUE, "white"
            elif qid in self.sheet.answers:
                bg, fg = GREEN, "white"
            elif gi in self.marked:
                bg, fg = ORANGE, "white"
            else:
                bg, fg = LINE_2, INK
            if btn.cget("bg") != bg:
                btn.config(bg=bg, fg=fg, activebackground=bg, activeforeground=fg)
        # 进度显示本部分的
        types = getattr(self, "cur_types", ())
        tot = len(self.card_idx)
        done = sum(1 for gi in self.card_idx
                   if self.sheet.questions[gi]["id"] in self.sheet.answers)
        try:
            self.progress.config(
                text=f"本部分已答 {done} / {tot} 题" if types
                     else f"已答 {self.sheet.answered_count()} / {self.sheet.total()} 题")
        except Exception:
            pass

    def toggle_mark(self):
        """标记/取消标记本题（NCRE 的「待复查」）"""
        i = self.sheet.index
        if i in self.marked:
            self.marked.discard(i)
        else:
            self.marked.add(i)
        self.render_question()
        self.refresh_card()

    def goto_q(self, i):
        if self.sheet.goto(i):
            self.render_question()
            self.refresh_card()
            self._report_progress()

    def next_q(self):
        """下一题：只在本部分题型里走"""
        i = self._step_index(self.sheet.index, 1)
        if i is not None:
            self.goto_q(i)

    def prev_q(self):
        i = self._step_index(self.sheet.index, -1)
        if i is not None:
            self.goto_q(i)
            return
        # 本部分到头了 → 回上一部分的最后一题
        # （以前这里直接 return，「上一题」在每部分第一题上点了没反应）
        key = self._prev_tab_key()
        if not key:
            return
        types = dict((k, t) for k, _l, t in self.exam_tabs()).get(key) or ()
        last = self._last_index_of(types)
        if key != getattr(self, "cur_tab", None):
            self.switch_tab(key)
        if last is not None:
            self.goto_q(last)

    def _typing_unfinished(self):
        """打字练习里还没做完的子任务名（老师勾了但学生没做完）"""
        try:
            cfg = self.client.typing_config()
        except Exception:
            cfg = None
        if not cfg:
            return []
        stats = (self.client.typing_state().get("stats") or {})
        out = []
        for t in cfg.get("tasks", []):
            key = t.get("task")
            if not (stats.get(key) or {}).get("finished"):
                out.append(t.get("name") or key)
        return out

    def _log_only(self, msg):
        """只在后台写一条日志（不碰界面，心跳线程里也能安全调用）"""
        try:
            self.client.log("info", msg)
        except Exception:
            pass

    def force_submit_all(self, exam_id=None):
        """
        老师点「结束考试」时的强制交卷。
        ⚠️ 跑在心跳后台线程里：不能弹窗、不能动任何控件，
            只做网络/文件操作（交卷 + 写日志）。
        理论题 + 全部实操题（没做的按缺卷 0 分）+ 打字统计。
        """
        try:
            eid = self.exam_id or exam_id
            if self.sheet and self.sheet.total() and eid:
                try:
                    self.client.flush_answers()
                except Exception:
                    pass
                try:
                    self.client.api.submit_theory(eid, self.sheet.payload())
                    self.client.api.finish_theory(eid)
                    self._log_only("强制交卷：理论题已交")
                except Exception as e:
                    self._log_only(f"强制交卷：理论题失败 {e}")
            try:
                self.client.flush_typing()
            except Exception:
                pass
            # 画板作品：强制收卷也带上最新一版
            self._upload_drawing_quiet(eid)
            for t in (self.client.practice_tasks() or []):
                try:
                    ok, info = self.client.auto_submit(t, allow_empty=True)
                    if ok and (info or {}).get("empty"):
                        self._log_only(f"强制交卷：{t.get('name')} 没做，按缺卷 0 分")
                    elif ok:
                        self._log_only(f"强制交卷：{t.get('name')} 已交")
                    else:
                        self._log_only(f"强制交卷：{t.get('name')} 失败 {info}")
                except Exception as e:
                    self._log_only(f"强制交卷：{t.get('name')} 出错 {e}")
        except Exception as e:
            self._log_only(f"强制交卷出错: {e}")

    def submit_all(self):
        """
        导航栏上的「交卷」：不管在哪个页面点，都一次性提交全部内容
        （理论题 + 全部实操题 + 打字练习的统计 + 画板作品）。
        没做完的先列出来提醒；老师设了「未做完禁止提交」就直接拦住。
        提交走等待队列：全部上传完才显示成功页，中途禁止切去别的页面。
        """
        if self.client.state.get("submitting"):
            messagebox.showinfo("正在提交", "正在提交中，请稍等，不要重复点击。")
            return
        if self.client.state.get("submitted"):
            messagebox.showinfo("已交卷",
                                "你已经交过卷了，请等待老师结束考试。")
            return
        tasks = self.client.practice_tasks() or []
        # ---- 0) 先检查做完没有 ----
        missing = []
        if self.sheet and self.sheet.total():
            un = self.sheet.unanswered()
            if un:
                msg = f"理论题还有 {len(un)} 题没有做（第 "
                msg += "、".join(map(str, un[:8]))
                if len(un) > 8:
                    msg += "…"
                missing.append(msg + " 题）")
        for t in tasks:
            name = t.get("name") or f"实操题 {t.get('no', '')}"
            try:
                done = (bool(self.client.collected_answer(self.exam_id, t))
                        or bool(self.client.pending_answer(self.exam_id, t)[0]))
            except Exception:
                done = bool(t.get("submitted"))
            if not done:
                missing.append(f"实操题「{name}」还没有答卷")
        for name in self._typing_unfinished():
            missing.append(f"打字练习「{name}」还没有做完")
        import drawing_ui
        for kind in self.client.drawing_styles():
            if not drawing_ui.state_has_art(self.client.drawing_state(kind)):
                missing.append("拼豆创作：还什么都没有拼" if kind == "beads"
                               else "画板创作：还什么都没有画")
        require = bool((self.client.state.get("exam") or {}).get("require_complete"))
        if missing:
            detail = "\n".join("· " + m for m in missing)
            if require:
                # 老师设置了「未做完禁止提交」→ 直接拦下
                messagebox.showwarning(
                    "不能交卷",
                    "老师设置了「未做完禁止提交」，以下内容还没有做完：\n\n"
                    + detail + "\n\n请完成后再点「交卷」。")
                return
            # 老师没设置 → 提醒后学生可以自己决定交不交
            if not messagebox.askyesno(
                    "确认交卷",
                    "以下内容还没有做完：\n\n" + detail
                    + "\n\n仍要交卷吗？\n"
                      "点「是」就一次性全部交卷：没做的题不会再让你重做，"
                      "直接按 0 分计（老师端的成绩和评讲里会标成「缺卷」）。"):
                return
        elif not messagebox.askyesno(
                "确认交卷",
                "交卷将一次性提交全部成绩（理论题 + 全部实操题 + 打字练习）。\n\n"
                "确定交卷吗？"):
            return
        # ---- 收集要提交的东西（主线程收集，后台队列只负责上传）----
        jobs = []
        has_theory = bool(self.sheet and self.sheet.total())
        if has_theory:
            try:
                self.client.flush_answers()
                payload = self.sheet.payload()
            except Exception as e:
                messagebox.showerror("交卷失败", f"理论题答案打包失败：{e}")
                return

            def up_theory(p=payload):
                self.client.api.submit_theory(self.exam_id, p)
                self.client.api.finish_theory(self.exam_id)
                return "已提交"
            jobs.append(("理论题答案", up_theory))
        if self.client.typing_config():
            def up_typing():
                self.client.flush_typing()
                return "已提交最新成绩"
            jobs.append(("打字练习记录", up_typing))
        for t in tasks:
            name = t.get("name") or f"实操题 {t.get('no', '')}"

            def up_prac(t=t):
                # 学生已确认「没做完也交」→ 没做的按缺卷 0 分提交
                ok, info = self.client.auto_submit(t, allow_empty=True)
                if not ok:
                    raise RuntimeError(info or "提交失败")
                auto = info or {}
                t["submitted"] = True
                if auto.get("empty"):
                    return "没做，缺卷按 0 分处理"
                if auto.get("pending"):
                    return "已提交，正在后台评分"
                s = auto.get("score")
                return f"{s} / {auto.get('full')}" if s is not None else "已提交"
            jobs.append((f"实操「{name}」", up_prac))
        # 画图作品：交卷带最新一版（画板 / 拼豆各传各的，统一导出入口）
        for kind in self.client.drawing_styles():
            st = self.client.drawing_state(kind)
            if not drawing_ui.state_has_art(st):
                continue
            try:
                data, ext = drawing_ui.export_state(st)
            except Exception as e:
                messagebox.showerror(
                    "交卷失败",
                    f"{'拼豆' if kind == 'beads' else '画板'}导出失败：{e}")
                return

            def up_draw(d=data, e=ext, k=kind):
                r = self.client.api.upload_drawing(self.exam_id, d, e, kind=k)
                if not r.get("ok"):
                    raise RuntimeError(r.get("msg") or "上传失败")
                return f"已提交（{len(d) // 1024} KB）"
            jobs.append(("拼豆作品" if kind == "beads" else "画板作品",
                         up_draw))

        if not jobs:
            self.client.state["submitted"] = True
            self._show_submit_success([], [])
            return

        # ---- 锁定界面 → 提交中页面 → 队列逐项上传 → 成功页 ----
        self.client.state["submitting"] = True

        try:
            self._build_submitting(len(jobs))
            q = SubmitQueue()
            for label, fn in jobs:
                q.add(label, fn)
            self._submit_evq = q.start()
            self.root.after(100, self._pump_submit_events)
        except Exception as e:
            self.client.state["submitting"] = False
            messagebox.showerror("交卷失败", f"提交流程出错：{e}\n请再点一次「交卷」重试。")
            # 出错别卡死在「正在提交」页 —— 跳到失败页，那里有「重新提交」
            try:
                self._build_submit_failed([], [f"· 提交流程出错：{e}"])
            except Exception:
                pass

    def _pump_submit_events(self):
        """
        主线程定时取上传队列的事件（进度 / 完成）。
        上传线程只往 queue 里放事件，Tk 更新全在这里做 —— 这样任何环境
        下都不会出现「工作线程碰 Tk 导致整个提交卡死」。
        """
        evq = getattr(self, "_submit_evq", None)
        if evq is None:
            return
        alive = True
        try:
            while True:
                ev = evq.get_nowait()
                if ev[0] == "progress":
                    self._set_submit_progress(ev[1], ev[2], ev[3])
                elif ev[0] == "done":
                    self._submit_evq = None
                    self._submit_finished(ev[1], ev[2])
                    alive = False
                    break
        except queue.Empty:
            pass
        except Exception:
            self._submit_evq = None
            alive = False
        if alive:
            try:
                self.root.after(120, self._pump_submit_events)
            except Exception:
                pass

    # ---------- 交卷：提交中 / 成功 / 失败页面 ----------
    def _build_submitting(self, total):
        """提交中页面：显示上传进度；此时禁止切去别的页面"""
        self._submit_total = total

        def b(p):
            body = tk.Frame(p, bg=BG)
            body.pack(fill="both", expand=True)
            card = tk.Frame(body, bg=CARD, padx=52, pady=44,
                            highlightbackground=LINE, highlightthickness=2)
            card.place(relx=0.5, rely=0.45, anchor="center")
            tk.Label(card, text="正在提交，请稍等…", bg=CARD, fg=BLUE_D,
                     font=(FONT, 22, "bold")).pack(pady=(0, 12))
            tk.Label(card, text="提交期间请不要关闭程序、不要关机，\n"
                                "全部内容上传完成后会显示提交成功。",
                     bg=CARD, fg=GRAY, justify="center",
                     font=(FONT, 12)).pack()
            self.sub_prog = tk.Label(card, text=f"准备上传 0 / {total}",
                                     bg=CARD, fg=INK,
                                     font=(FONT, 13, "bold"))
            self.sub_prog.pack(pady=(18, 0))
        self.swap(b)

    def _set_submit_progress(self, i, total, label):
        w = getattr(self, "sub_prog", None)
        if w:
            try:
                w.config(text=f"正在上传：{label}（{i} / {total}）"
                         if i < total else "上传完成")
            except Exception:
                pass

    def _submit_finished(self, ok_lines, fail_lines):
        self.client.state["submitting"] = False
        if fail_lines:
            self._build_submit_failed(ok_lines, fail_lines)
        else:
            self.client.state["submitted"] = True
            self._show_submit_success(ok_lines, fail_lines)

    def _build_submit_failed(self, ok_lines, fail_lines):
        """部分上传失败：列出失败项，可以点「重新提交」整个重来（服务端去重）"""
        def b(p):
            body = tk.Frame(p, bg=BG)
            body.pack(fill="both", expand=True)
            card = tk.Frame(body, bg=CARD, padx=52, pady=44,
                            highlightbackground=LINE, highlightthickness=2)
            card.place(relx=0.5, rely=0.45, anchor="center")
            tk.Label(card, text="部分提交失败", bg=CARD, fg=RED,
                     font=(FONT, 22, "bold")).pack(pady=(0, 10))
            tk.Label(card, text="以下内容上传失败（可能是网络断了一下）：\n"
                     + "\n".join(fail_lines),
                     bg=CARD, fg=INK, justify="left",
                     font=(FONT, 11)).pack()
            if ok_lines:
                tk.Label(card, text="已经传上去的：\n" + "\n".join(ok_lines),
                         bg=CARD, fg=GRAY, justify="left",
                         font=(FONT, 10)).pack(pady=(10, 0))
            tk.Button(card, text="重新提交", command=self.submit_all,
                      bg=BLUE, fg=INK, font=(FONT, 13, "bold"),
                      padx=26, pady=8, relief="flat", cursor="hand2"
                      ).pack(pady=(18, 0))
        self.swap(b)

    def _show_submit_success(self, ok_lines, fail_lines):
        """交卷成功页：显示提交清单 + 结束语，引导先关闭考试程序再用电脑"""
        note = ((self.client.state.get("exam") or {}).get("finish_note") or "").strip()

        def b(p):
            body = tk.Frame(p, bg=BG)
            body.pack(fill="both", expand=True)
            card = tk.Frame(body, bg=CARD, padx=52, pady=46,
                            highlightbackground=LINE, highlightthickness=2)
            card.place(relx=0.5, rely=0.45, anchor="center")
            tk.Label(card, text="✓ 交卷成功", bg=CARD, fg=GREEN,
                     font=(FONT, 26, "bold")).pack(pady=(0, 12))
            if ok_lines:
                tk.Label(card, text="已提交：\n" + "\n".join(ok_lines),
                         bg=CARD, fg=INK, justify="left",
                         font=(FONT, 11)).pack()
            if note:
                tk.Label(card, text=note, bg="#eff6ff", fg="#1d4ed8",
                         justify="center", wraplength=460,
                         font=(FONT, 12)).pack(pady=(14, 0))
            tk.Label(card, text="━━━━━━━━━━━━━━━━━━\n"
                                "请先点下面的按钮关闭考试程序，\n"
                                "然后再打开其他程序。",
                     bg=CARD, fg=ORANGE, justify="center",
                     font=(FONT, 13, "bold")).pack(pady=(16, 0))
            tk.Button(card, text="关闭考试程序", command=self._quit_app,
                      bg=RED, fg="white", font=(FONT, 13, "bold"),
                      padx=26, pady=8, relief="flat", cursor="hand2",
                      activebackground="#b91c1c").pack(pady=(16, 0))
        self.swap(b)

    def _quit_app(self):
        """交卷成功后关闭考试程序（引导学生关掉本程序再用电脑）"""
        try:
            self.root.destroy()
        except Exception:
            pass

    def submit_theory(self, force=False, quiet=False):
        """
        交理论题：先把攒下的答案全部同步，再通知服务端。
        :return: True 成功（或用户确认）；False 用户取消
        """
        if not self.sheet:
            return True
        un = self.sheet.unanswered()
        if un and not force:
            msg = f"还有 {len(un)} 题未作答：\n第 {', '.join(map(str, un[:10]))} 题"
            if len(un) > 10:
                msg += f" …等共 {len(un)} 题"
            msg += "\n\n确定要交卷吗？（交卷后仍可回来检查）"
            if not messagebox.askyesno("确认交卷", msg):
                return False
        try:
            self.client.flush_answers()
        except Exception:
            pass
        try:
            r = self.client.api.submit_theory(self.exam_id, self.sheet.payload())
            self.client.api.finish_theory(self.exam_id)
            if not quiet:
                messagebox.showinfo(
                    "已交卷",
                    f"理论题已交卷，作答 {r.get('answered', 0)} 题。\n\n"
                    "还可以用上面的导航回去检查修改。")
            return True
        except Exception as e:
            messagebox.showerror("提交失败", str(e))
            return False

    # ---------- 界面 2.8：考试暂停 ----------
    def build_paused(self):
        """老师暂停考试时显示"""
        prev = self.client.state.get("phase") or "theory"
        self.client.state["_prev_phase"] = prev

        def b(p):
            top = tk.Frame(p, bg=_UI["lemon"], height=56)
            top.pack(fill="x")
            top.pack_propagate(False)
            tk.Label(top, text="考试已暂停", bg=_UI["lemon"], fg=INK,
                     font=(FONT, 16, "bold")).pack(expand=True)
            body = tk.Frame(p, bg=BG)
            body.pack(fill="both", expand=True)
            card = tk.Frame(body, bg=CARD, padx=48, pady=44,
                            highlightbackground=LINE, highlightthickness=2)
            card.place(relx=0.5, rely=0.45, anchor="center")
            tk.Label(card, text="考试已暂停", bg=CARD, fg=ORANGE,
                     font=(FONT, 22, "bold")).pack(pady=(0, 10))
            tk.Label(card, text="计时已停止，请等待老师恢复考试", bg=CARD, fg=GRAY,
                     font=(FONT, 12)).pack()
            tk.Label(card, text=("考试暂停中，可以最小化窗口休息一下；"
                                 "已作答内容都已保存"),
                     bg=CARD, fg=GRAY, wraplength=420, justify="center",
                     font=(FONT, 11)).pack(pady=(8, 0))
        self.swap(b)

    # ---------- 界面 3：实操题（导航里的一个标签）----------
    def build_practice(self):
        """进入实操题：切到导航栏的「实操题」标签"""
        self.cur_tab = "practice"
        if self._current_page() != "exam" or not getattr(self, "content", None):
            self.client.state["section"] = "practice"
            self._show_page("exam", force=True)
        else:
            self.render_tab()

    def _render_practice_into(self, parent):
        """
        实操题内容区（一场考试可以有好几道）：
          左边列这道考试的实操题清单，点一下切到那道题；
          右边显示这道题的要求 + 操作按钮。
        """
        ex = self.client.state.get("exam") or {}
        eid = ex.get("id")
        self.exam_id = eid
        self._last_sync = time.time()
        tasks = self.client.practice_tasks()
        if not tasks:
            tasks = [{"task_id": 0, "no": 1, "name": "实操题",
                      "etype": ex.get("etype", "word"), "ext": ".docx",
                      "requirement": ex.get("requirement", ""),
                      "materials": ex.get("materials") or [],
                      "save_as": ex.get("save_as") or "座位号_姓名" + {
                          "word": ".docx", "excel": ".xlsx", "ppt": ".pptx"
                      }.get(ex.get("etype"), ".docx"), "submitted": False}]
        idx = min(max(0, getattr(self, "cur_task_idx", 0)), len(tasks) - 1)
        self.cur_task_idx = idx
        self._report_progress()
        self._prac_key = tuple((t.get("task_id"), bool(t.get("submitted")))
                               for t in tasks)
        task = tasks[idx]
        etype = task.get("etype") or "word"
        self.client.ensure_work_dir(eid, task.get("task_id") or 0)
        multi = len(tasks) > 1

        def b(p):
            top = tk.Frame(p, bg=_UI["mint"], height=54)
            top.pack(fill="x")
            top.pack_propagate(False)
            head = f"实操题 — {wps.LABEL.get(etype, etype)}"
            if multi:
                head += f"   第 {idx + 1} / {len(tasks)} 道：{task.get('name', '')}"
            tk.Label(top, text=head, bg=_UI["mint"], fg=INK,
                     font=(FONT, 14, "bold")).pack(side="left", padx=20)
            # 倒计时只放在顶部导航栏（self.time_label 在 build_exam 里创建），
            # 这里不再自建 —— 以前这里会覆盖掉导航栏时钟的引用，
            # 导致导航栏「剩余时间」停在 --:-- 不再刷新

            body = tk.Frame(p, bg=BG)
            body.pack(fill="both", expand=True, padx=16, pady=12)

            # 左：题号清单 + 底部「上/下一部分」（切题型，不是切这道实操题）
            side = tk.Frame(body, bg=CARD, width=230, padx=12, pady=12,
                            highlightbackground=LINE, highlightthickness=2)
            side.pack(side="left", fill="y", padx=(0, 12))
            side.pack_propagate(False)
            # ⚠️ 底部按钮必须先 pack：先 pack 撑满的内容会把它们挤出可视区
            navbox = tk.Frame(side, bg=CARD)
            navbox.pack(side="bottom", fill="x", pady=(10, 0))
            tabs = self.exam_tabs()
            keys = [k for k, _l, _t in tabs]
            labels = dict((k, l) for k, l, _t in tabs)
            cur = getattr(self, "cur_tab", None) or "practice"
            i_cur = keys.index(cur) if cur in keys else -1
            prev_key = keys[i_cur - 1] if i_cur > 0 else None
            next_key = (keys[i_cur + 1] if 0 <= i_cur < len(keys) - 1 else None)
            tk.Label(navbox, text="切换题型", bg=CARD, fg=GRAY,
                     font=(FONT, 9)).pack(anchor="w", pady=(0, 4))
            if prev_key:
                tk.Button(navbox, text=f"◀ {labels.get(prev_key, prev_key)}",
                          command=lambda k=prev_key: self.switch_tab(k),
                          bg=LINE_2, fg=INK, font=(FONT, 10),
                          relief="flat", cursor="hand2", padx=8, pady=6,
                          anchor="w", wraplength=196,
                          justify="left").pack(fill="x", pady=2)
            if next_key:
                tk.Button(navbox, text=f"{labels.get(next_key, next_key)} ▶",
                          command=lambda k=next_key: self.switch_tab(k),
                          bg=BLUE, fg=INK, font=(FONT, 10, "bold"),
                          relief="flat", cursor="hand2", padx=8, pady=6,
                          anchor="w", wraplength=196, justify="left",
                          activebackground=BLUE_D).pack(fill="x", pady=2)
            if not prev_key and not next_key:
                tk.Label(navbox, text="（已经是最后一个部分）", bg=CARD, fg=GRAY,
                         font=(FONT, 9), wraplength=200,
                         justify="left").pack(anchor="w")

            tk.Label(side, text="操作题清单", bg=CARD, fg=INK,
                     font=(FONT, 12, "bold")).pack(anchor="w")
            for i, t in enumerate(tasks):
                mark = "[已交] " if t.get("submitted") else ""
                btn = tk.Button(
                    side, text=f"{mark}{i + 1}. {t.get('name', '')}",
                    anchor="w", justify="left", wraplength=196,
                    font=(FONT, 10, "bold" if i == idx else "normal"),
                    bg=BLUE_L if i == idx else CARD, fg=BLUE_D if i == idx else INK,
                    relief="flat", cursor="hand2", padx=8, pady=6,
                    command=lambda x=i: self.switch_task(x))
                btn.pack(fill="x", pady=2)
            tk.Label(side, text="点题号切换；提交后显示「已交」",
                     bg=CARD, fg=GRAY, font=(FONT, 9),
                     wraplength=200, justify="left").pack(anchor="w", pady=(8, 0))

            # 中：考试要求
            left = tk.Frame(body, bg=CARD, padx=20, pady=16,
                            highlightbackground=LINE, highlightthickness=2)
            left.pack(side="left", fill="both", expand=True)
            tk.Label(left, text="考试要求", bg=CARD, fg=INK,
                     font=(FONT, 13, "bold")).pack(anchor="w")
            t = tk.Text(left, wrap="word", font=(FONT, 12),
                        bg=LINE_2, relief="flat", padx=12, pady=10,
                        highlightbackground=LINE, highlightthickness=2)
            t.insert("1.0", task.get("requirement") or "（老师未填写要求）")
            t.config(state="disabled")
            t.pack(fill="both", expand=True, pady=10)

            # 右：操作区
            right = tk.Frame(body, bg=CARD, width=300, padx=18, pady=16,
                             highlightbackground=LINE, highlightthickness=2)
            right.pack(side="right", fill="y", padx=(12, 0))
            right.pack_propagate(False)

            tk.Label(right, text="操作", bg=CARD, fg=INK,
                     font=(FONT, 13, "bold")).pack(anchor="w")

            # ---- 四步提示，学生知道现在该干什么 ----
            steps = tk.Frame(right, bg=LINE_2, padx=8, pady=6)
            steps.pack(fill="x", pady=(6, 8))
            for i, s in enumerate([
                    "① 下载素材",
                    "② 打开 WPS 编辑",
                    "③ 保存文件 → 必须另存到 IkeeAnswer 文件夹",
                    "④ 点顶部「交卷」统一提交"], 1):
                tk.Label(steps, text=s, bg=LINE_2,
                         fg=RED if i == 3 else INK,
                         font=(FONT, 10, "bold"),
                         wraplength=264, justify="left").pack(anchor="w")

            self.mat_box = tk.Listbox(right, height=4, font=(FONT, 10),
                                      highlightbackground=LINE, highlightthickness=2)
            self.mat_box.pack(fill="x", pady=(0, 4))
            for m in (task.get("materials") or []):
                self.mat_box.insert("end", m)
            if not (task.get("materials") or []):
                self.mat_box.insert("end", "（这道题没有素材）")

            tk.Button(right, text="① 下载素材", command=self.do_download,
                      bg=BLUE, fg=INK, font=(FONT, 11, "bold"),
                      relief="flat", pady=8, cursor="hand2",
                      activebackground=BLUE_D).pack(fill="x", pady=(0, 2))
            # 下载进度条：素材大时不至于"点了没反应"
            self.dl_bar = ttk.Progressbar(right, mode="determinate",
                                          maximum=100, length=260)
            self.dl_bar.pack(fill="x", pady=(0, 2))
            self.dl_label = tk.Label(right, text="尚未下载", bg=CARD, fg=GRAY,
                                     font=(FONT, 9))
            self.dl_label.pack(anchor="w", pady=(0, 6))

            # 答卷状态：每次切到这道题都重新扫一遍本机目录（见 refresh_task_state）
            self.ans_label = tk.Label(right, text="", bg=CARD, fg=GRAY,
                                      font=(FONT, 9),
                                      wraplength=264, justify="left")
            self.ans_label.pack(anchor="w", pady=(0, 4))

            tk.Button(right, text=f"② 打开 {wps.LABEL.get(etype, etype)}（带素材）",
                      command=lambda: self.do_open(etype),
                      bg=GREEN, fg="white", font=(FONT, 11, "bold"),
                      relief="flat", pady=8, cursor="hand2",
                      activebackground=GREEN_D).pack(fill="x", pady=3)
            tk.Button(right, text="悬浮题目窗（边做边看）",
                      command=self.show_float_req,
                      font=(FONT, 10), pady=6,
                      relief="flat", bg=BLUE_L, fg=BLUE_D,
                      cursor="hand2").pack(fill="x", pady=3)
            # 两个"打开文件夹"按钮放同一排：答卷存 IkeeAnswer，素材在考试编号目录
            dir_row = tk.Frame(right, bg=CARD)
            dir_row.pack(fill="x", pady=3)
            tk.Button(dir_row, text="③ 打开 IkeeAnswer\n文件夹（存这里）",
                      command=self.do_open_dir,
                      font=(FONT, 10, "bold"), pady=5,
                      relief="flat", bg=ORANGE_L, fg=ORANGE_D,
                      cursor="hand2").pack(side="left", fill="x", expand=True)
            tk.Button(dir_row, text="打开\n素材文件夹",
                      command=self.do_open_mat_dir,
                      font=(FONT, 10), pady=5,
                      relief="flat", bg=LINE_2, cursor="hand2"
                      ).pack(side="left", fill="x", expand=True, padx=(3, 0))

            save_name = task.get("save_as") or "座位号_姓名" + {
                "word": ".docx", "excel": ".xlsx", "ppt": ".pptx"
            }.get(etype, ".docx")
            tk.Label(right, text=f"保存为：{save_name}", bg=CARD,
                     fg=GRAY, font=(FONT, 10), wraplength=270,
                     justify="left").pack(anchor="w", pady=(12, 0))

            # 目录提示：让学生一眼看到素材在哪、答卷该存哪
            self.path_label = tk.Label(right, text="", bg=CARD, fg=GRAY_DIM,
                                       font=(FONT, 8),
                                       wraplength=264, justify="left")
            self.path_label.pack(anchor="w", pady=(4, 0))

            self.status = tk.Label(right, text="", bg=CARD, fg=BLUE,
                                   font=(FONT, 10), wraplength=270,
                                   justify="left")
            self.status.pack(anchor="w", pady=(8, 0))

        b(parent)
        # 画完立刻扫一遍本机：素材下没下、答卷有没有 —— 不要让学生看到
        # 明明下过却写着"尚未下载"（老师反馈过这个问题）
        self.refresh_task_state(task)

    def refresh_task_state(self, task=None):
        """
        扫描本机目录，刷新「素材 / 答卷」状态。

        切题、切标签（每次渲染）、下载完、提交后都会调一次。
        目录：素材在 ikee_Exam/学生名字/考试编号/，答卷在 …/IkeeAnswer/。
        """
        task = task or self._cur_task()
        if not task:
            return
        # ---- 素材 ----
        need = task.get("materials") or []
        try:
            got = self._local_materials(task)
        except Exception:
            got = []
        if not need:
            self._set_dl(0, "这道题没有素材", GRAY)
        elif got:
            self._set_dl(100, f"素材已在本机（{len(got)}/{len(need)} 个），"
                              f"可直接打开", GREEN)
        else:
            self._set_dl(0, "尚未下载", GRAY)

        # ---- 答卷 ----
        ans_txt, ans_fg = "还没找到答卷文件", ORANGE
        try:
            done = self.client.collected_answer(self.exam_id, task)
            if done:
                ans_txt, ans_fg = f"答卷已收好：{os.path.basename(done)}", GREEN
            elif task.get("submitted"):
                ans_txt, ans_fg = "这道题已提交", GREEN
            else:
                p, pn = self.client.pending_answer(self.exam_id, task)
                if p:
                    ans_txt = f"发现答卷：{pn}（点顶部「交卷」统一提交）"
                    ans_fg = BLUE_D
        except Exception as e:
            ans_txt = f"答卷状态读取失败：{e}"
        for attr, txt, fg in (("ans_label", ans_txt, ans_fg),):
            try:
                w = getattr(self, attr, None)
                if w is not None and w.winfo_exists():
                    w.config(text=txt, fg=fg)
            except Exception:
                pass

        # ---- 两个目录的完整路径 ----
        try:
            self.path_label.config(
                text=f"素材目录：{self.client.exam_dir(self.exam_id)}\n"
                     f"答卷目录：{self.client.answer_dir()}")
        except Exception:
            pass

    def switch_task(self, idx):
        """切换到另一道操作题"""
        tasks = self.client.practice_tasks()
        if not tasks:
            return
        idx = max(0, min(int(idx), len(tasks) - 1))
        if idx == getattr(self, "cur_task_idx", 0):
            return
        self.cur_task_idx = idx
        self._show_page("exam", force=True)
        self._report_progress()
        # 悬浮窗口开着的话，跟着切到新题的内容
        if getattr(self, "_float_win", None) is not None:
            try:
                if self._float_win.winfo_exists():
                    self.show_float_req()
            except Exception:
                pass

    def _cur_task(self):
        tasks = self.client.practice_tasks()
        if not tasks:
            return None
        idx = min(max(0, getattr(self, "cur_task_idx", 0)), len(tasks) - 1)
        return tasks[idx]

    def _set_dl(self, pct=None, text=None, color=None):
        """更新下载进度条 / 文字"""
        try:
            if pct is not None:
                self.dl_bar["value"] = max(0, min(100, pct))
            if text is not None:
                self.dl_label.config(text=text, fg=color or GRAY)
        except Exception:
            pass

    def _download_task(self, task, then=None, force=False):
        """① 下载素材（带进度条）。then(got_paths)：下完后再做别的事"""
        def run():
            try:
                def on_prog(i, n, name, got, total):
                    pct = int(got * 100 / total) if total else 0
                    self.uiq.put(lambda p=pct, nm=name, a=i, b=n: self._set_dl(
                        p, f"正在下载 {nm}（{a}/{b}）{p}%", BLUE))

                have = self._local_materials(task)
                if have and not force:
                    # 已经在本机了，直接提示，别重复下载
                    self.uiq.put(lambda: self._set_dl(
                        100, f"素材已在本机（{len(have)} 个），可直接打开", GREEN))
                    if then:
                        self.uiq.put(lambda: then(have))
                    return
                got = self.client.fetch_materials(self.exam_id, task,
                                                  on_progress=on_prog, force=force)
                # 下完重新扫一遍目录：状态标签跟着变，切题回来也不会又写"尚未下载"
                self.uiq.put(lambda: self.refresh_task_state(task))
                if then:
                    self.uiq.put(lambda: then(got))
            except Exception as e:
                self.uiq.put(lambda: self._set_dl(
                    None, f"下载失败：{e}", RED))
        self._set_dl(0, "准备下载…", BLUE)
        threading.Thread(target=run, daemon=True).start()

    def do_download(self):
        task = self._cur_task()
        if not task:
            return
        self._download_task(task)

    def _local_materials(self, task):
        """这道题的素材里，本机已经下载好的文件"""
        out = []
        for m in ((task or {}).get("materials") or []):
            p = self.client.material_path(self.exam_id, task, m)
            if os.path.isfile(p):
                out.append(p)
        return out

    def do_open(self, etype):
        """② 打开 WPS：素材没下载就先下载，然后**带着素材文件**一起打开"""
        task = self._cur_task() or {}
        files = self._local_materials(task)
        if not files and (task.get("materials") or []):
            self.status.config(text="素材还没下载，先下载再打开…", fg=BLUE)
            self._download_task(task, then=lambda got: self._open_with(etype, got))
            return
        self._open_with(etype, files)

    def _open_with(self, etype, files):
        target = files[0] if files else None
        ok, msg = self.client.open_wps(etype, target)
        self.status.config(text=msg,
                           fg=GREEN if ok else RED)

    # ---------- 悬浮题目窗口（边做边看）----------
    def show_float_req(self):
        task = self._cur_task() or {}
        text = task.get("requirement") or "（老师未填写要求）"
        name = task.get("name", "实操题")

        # 已经开着：只更新内容并抬到前面
        win = getattr(self, "_float_win", None)
        if win is not None:
            try:
                if win.winfo_exists():
                    self._float_body.config(state="normal")
                    self._float_body.delete("1.0", "end")
                    self._float_body.insert("1.0", text)
                    self._float_body.config(state="disabled")
                    self._float_title.config(text=f"题目：{name}")
                    win.deiconify()
                    win.lift()
                    return
            except Exception:
                pass

        win = tk.Toplevel(self.root)
        self._float_win = win
        win.title(f"题目：{name}")
        win.geometry("540x440+%d+%d" % (
            max(0, self.root.winfo_screenwidth() - 580), 90))
        win.configure(bg=CARD)
        win.attributes("-topmost", True)

        # 顶部蓝条：拖动区 + 置顶开关 + 字号
        bar = tk.Frame(win, bg=BLUE, height=36)
        bar.pack(fill="x")
        bar.pack_propagate(False)
        self._float_title = tk.Label(bar, text=f"题目：{name}", bg=BLUE, fg=INK,
                                     font=(FONT, 11, "bold"))
        self._float_title.pack(side="left", padx=10)
        tk.Label(bar, text="按住蓝条可拖动", bg=BLUE, fg=INK,
                 font=(FONT, 9)).pack(side="left")

        top_var = tk.BooleanVar(value=True)
        tk.Checkbutton(bar, text="置顶", variable=top_var,
                       command=lambda: win.attributes("-topmost",
                                                      bool(top_var.get())),
                       bg=BLUE, fg=INK, selectcolor=BLUE_D,
                       activebackground=BLUE, activeforeground=INK,
                       font=(FONT, 9),
                       highlightthickness=0).pack(side="right", padx=6)

        def resize(d):
            self._float_size = max(9, min(24, getattr(self, "_float_size", 12) + d))
            self._float_body.config(font=(FONT, self._float_size))

        tk.Button(bar, text="A+", command=lambda: resize(1), bg=BLUE_D, fg="white",
                  relief="flat", font=(FONT, 9), padx=6,
                  cursor="hand2").pack(side="right", padx=2)
        tk.Button(bar, text="A-", command=lambda: resize(-1), bg=BLUE_D, fg="white",
                  relief="flat", font=(FONT, 9), padx=6,
                  cursor="hand2").pack(side="right", padx=2)

        body = tk.Frame(win, bg=CARD, padx=10, pady=8)
        body.pack(fill="both", expand=True)
        self._float_size = 12
        self._float_body = tk.Text(body, wrap="word", relief="flat",
                                   font=(FONT, self._float_size),
                                   bg=LINE_2, padx=12, pady=10,
                                   highlightbackground=LINE, highlightthickness=2)
        self._float_body.insert("1.0", text)
        self._float_body.config(state="disabled")
        self._float_body.pack(fill="both", expand=True)

        def start_drag(ev):
            win._dx, win._dy = ev.x, ev.y

        def drag(ev):
            try:
                win.geometry(f"+{win.winfo_x() + ev.x - win._dx}"
                             f"+{win.winfo_y() + ev.y - win._dy}")
            except Exception:
                pass

        for w in (bar, self._float_title):
            w.bind("<Button-1>", start_drag)
            w.bind("<B1-Motion>", drag)

    def _open_folder(self, d):
        """打开一个文件夹（Windows 用 startfile，其它系统退回 explorer/xdg）"""
        if not d:
            return
        try:
            os.makedirs(d, exist_ok=True)
        except OSError:
            pass
        try:
            os.startfile(d)          # Windows
        except Exception:
            try:
                import subprocess
                subprocess.Popen(["explorer", d])
            except Exception as e:
                messagebox.showerror("打不开文件夹", f"{d}\n\n{e}")

    def do_open_dir(self):
        """③ 打开 IkeeAnswer（答卷要存在这里）"""
        self._open_folder(self.client.answer_dir())

    def do_open_mat_dir(self):
        """打开素材目录（考试编号那个文件夹）"""
        self._open_folder(self.client.exam_dir(self.exam_id))

    def auto_submit(self):
        """时间到自动提交，不弹窗（多道题就每道都交一次）"""
        tasks = self.client.practice_tasks()
        ok = False
        info = ""
        for t in (tasks or [None]):
            ok, info = self.client.auto_submit(t)
            if not ok:
                break
        self._say(("时间到，已自动提交" if ok
                   else f"时间到，自动提交失败：{info}"),
                  GREEN if ok else RED)

    def _say(self, text, color=None):
        """更新状态文字（界面可能已经切走，没有这个控件就忽略）"""
        try:
            w = getattr(self, "status", None)
            if w is not None and w.winfo_exists():
                w.config(text=text, **({"fg": color} if color else {}))
        except Exception:
            pass

    # ---------- 界面 4：结束 ----------
    def build_end(self):
        def b(p):
            top = tk.Frame(p, bg=_UI["coral"], height=56)
            top.pack(fill="x")
            top.pack_propagate(False)
            tk.Label(top, text="考试已结束", bg=_UI["coral"], fg=INK,
                     font=(FONT, 16, "bold")).pack(expand=True)
            body = tk.Frame(p, bg=BG)
            body.pack(fill="both", expand=True)
            card = tk.Frame(body, bg=CARD, padx=56, pady=52,
                            highlightbackground=LINE, highlightthickness=2)
            card.place(relx=0.5, rely=0.45, anchor="center")
            tk.Label(card, text="考试已结束", bg=CARD, fg=INK,
                     font=(FONT, 22, "bold")).pack(pady=(0, 8))
            tk.Label(card, text=f"{self.cfg.name or '免密码考生'}　座位号 {self.cfg.seat or '按 IP 识别'}",
                     bg=CARD, fg=GRAY_D,
                     font=(FONT, 12)).pack()
            answered = int(self.client.state.get("theory_answered") or 0)
            total = int(self.client.state.get("theory_total") or 0)
            if total:
                tk.Label(card, text=f"理论题已答 {answered} / {total} 题",
                         bg=CARD, fg=BLUE, font=(FONT, 12, "bold")).pack(pady=(10, 0))
            # 老师写的结束语（做完 / 没做完都显示）
            note = ((self.client.state.get("exam") or {})
                    .get("finish_note") or "").strip()
            if note:
                tk.Label(card, text=note, bg=CARD, fg=GREEN,
                         font=(FONT, 12, "bold"),
                         wraplength=460, justify="center").pack(pady=(8, 0))
            tk.Label(card, text="请保持坐姿，等待老师指令", bg=CARD, fg=GRAY,
                     font=(FONT, 12)).pack(pady=(6, 0))
            # 换考生：这台机器下一节课可能坐着别人，
            # 让他能在这里重新填姓名座位号，不用关掉程序再开
            tk.Button(card, text="重新登录（换考生）", command=self.back_to_login,
                      bg=BLUE, fg=INK, font=(FONT, 12, "bold"),
                      padx=20, pady=8, relief="flat", cursor="hand2",
                      activebackground=BLUE_D).pack(pady=(26, 6))
            tk.Label(card, text="（下一场考试由老师在控制台点「开始考试」拉起）",
                     bg=CARD, fg=GRAY, font=(FONT, 9)).pack()
            # 考试已结束，可以退出了
            self._exit_bar(card, "退出程序").pack(pady=(28, 0))
        self.swap(b)

    def back_to_login(self, force=False):
        """
        回到登录界面（换考生）。

        考试结束后这台机器很可能换人坐，但客户端没法判断现在坐的是不是
        刚才那个同学 —— 所以一律要求重新填姓名/座位号，
        然后停在待考界面等老师在控制台点「开始考试」把他拉进去。

        force=True：被老师端名单拦下时用 —— 本来就没让进考试，
                   必须能回登录页改姓名，否则学生卡死在连接页。
        """
        if self.client.want_lock() and not force:
            try:
                messagebox.showwarning(
                    "考试进行中",
                    "这场考试还没结束，不能换考生。\n\n请等老师结束考试。")
            except Exception:
                pass
            return
        try:
            self.client.flush_answers()
            self.client.stop_answer_sync()
        except Exception:
            pass
        # 清掉本地考试痕迹（服务器上的答卷不受影响）
        st = self.client.state
        st["exam"] = None
        st["section"] = None
        st["ended_exam_id"] = None
        st["theory_answered"] = 0
        st["theory_total"] = 0
        st["practice_submitted"] = False
        st["tasks"] = []
        self.sheet = None
        self.exam_id = None
        self._theory_exam_id = None
        self.cur_tab = None
        self.cur_task_idx = 0
        self.marked = set()
        self.card_btns = []
        # 姓名/座位号也清掉：新同学必须自己填，
        # 免得直接点「进入」把上一个同学的姓名带进下一场考试
        st["confirmed"] = False
        try:
            self.cfg.name = ""
            self.cfg.seat = ""
            self.cfg.logged_in = False
            self.cfg.passwordless = False
            self.cfg.save()
        except Exception:
            pass
        self.swap(self.build_login)

    def run(self):
        self.root.mainloop()


def run_gui(cfg=None):
    cfg = cfg or Config()
    App(cfg).run()


if __name__ == "__main__":
    run_gui()
