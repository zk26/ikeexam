# -*- coding: utf-8 -*-
"""
学生端自检：拖拽题画板、打名字测试、题干/选项配图、导航标签。

跑法：python test_client_smoke.py
（需要图形环境：Tk 会用 withdraw 出来的隐藏窗口，不弹界面）
"""
import os
import sys
import tkinter as tk

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CLIENT_DIR = os.path.join(ROOT, "src", "ikeexam", "client")
sys.path.insert(0, CLIENT_DIR)
sys.path.insert(0, os.path.join(ROOT, "src"))

FAIL = []


def check(name, cond, detail=""):
    print(("  PASS " if cond else "  FAIL ") + name + (f"  {detail}" if not cond else ""))
    if not cond:
        FAIL.append(name)


class FakeApi:
    def __init__(self):
        self.sent = []
        self.images = {}

    def typing_report(self, eid, reports):
        self.sent.append(reports)
        return {"ok": True}

    def get_bytes(self, path):
        return self.images.get(path, b"")


class FakeClient:
    def __init__(self):
        self.state = {"exam": {"id": 1}}
        self.api = FakeApi()
        self._typing_state = None

    def typing_state(self):
        ex = self.state.get("exam") or {}
        eid = ex.get("id")
        st = getattr(self, "_typing_state", None)
        if not st or st.get("exam_id") != eid:
            st = {"exam_id": eid, "stats": {}}
            self._typing_state = st
        return st

    def ensure_typing_reporter(self):
        pass

    def flush_typing(self):
        pass


root = tk.Tk()
# 拖拽的"跟手"检查需要真实布局，所以这里用一个可见窗口（跑完就关）
root.geometry("1100x760")
root.update()

print("== 1. 拖拽题画板 ==")
from drag_ui import DragBoard, _split_img   # noqa: E402

change = {"ans": None}
board = DragBoard(root, ["加粗", "居中", "保存"],
                  ["字体组", "段落组", "文件组"],
                  on_change=lambda a: change.update(ans=a),
                  image_getter=lambda n, cb: None,
                  initial="1:B|3:A")
board.pack(fill="both", expand=True)
root.update()
check("恢复上次作答", board.answer() == "1:B|3:A", board.answer())
check("项目状态同步", board.item_state["B"] == ("slot", 1)
      and board.item_state.get("C", ("tray", None))[0] == "tray")
board.place("A", 2)
check("移动到别的格子（原格子腾空）", board.answer() == "1:B|2:A",
      board.answer())
board.place("C", 1)
check("格子里原来的项目被挤回托盘",
      board.answer() == "1:C|2:A", board.answer())
check("被挤掉的回到托盘", board.item_state["B"][0] == "tray",
      str(board.item_state["B"]))
board.unplace("C")
check("点一下能取回", board.answer() == "2:A", board.answer())
check("变化回调被调用", change["ans"] == "2:A", str(change["ans"]))
board.clear()
check("清空重做", board.answer() == "")

# 已放进格子的项目再拖：鼠标位置要跟手（偏移按项目**当前**位置算）
board.redraw()
root.update()
check("布局算出来了", bool(board.item_boxes) and bool(board.slot_boxes),
      f"{len(board.item_boxes)} / {len(board.slot_boxes)}")
if board.item_boxes:
    board.place("A", 2)
    root.update()
    bbox = board.canvas.bbox("itemA")
    if bbox:
        cx, cy = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2

        class E:
            pass

        ev = E()
        ev.x, ev.y = int(cx), int(cy)
        board._on_press(ev)
        ox, oy = board._drag_offset
        want = ((bbox[2] - bbox[0]) / 2, (bbox[3] - bbox[1]) / 2)
        check("二次拖拽跟手（偏移=当前项目内位置）",
              abs(ox - want[0]) <= 2 and abs(oy - want[1]) <= 2,
              f"实际 {ox},{oy} 期望 {want}")
        # 再移动 30px，项目应跟着走 30px
        before = board.canvas.bbox("itemA")
        ev2 = E()
        ev2.x, ev2.y = int(cx) + 30, int(cy) + 10
        board._on_motion(ev2)
        after = board.canvas.bbox("itemA")
        check("拖动距离与鼠标一致",
              abs((after[0] - before[0]) - 30) <= 2
              and abs((after[1] - before[1]) - 10) <= 2,
              f"{before} -> {after}")
        board._on_release(ev2)

check("图片标记解析", _split_img("img:btn_bold.png 加粗") == ("加粗", "btn_bold.png"))
check("普通文字不受影响", _split_img("居中") == ("居中", None))

# 会考风格的结构图题：拖拽项比槽位多（多出来的留在托盘里）
board2 = DragBoard(root,
                   ["Ctrl+C", "Ctrl+Insert", "Ctrl+V", "Shift+Insert",
                    "Ctrl+S", "Ctrl+X"],
                   ["复制", "粘贴", "保存", "剪切"],
                   on_change=lambda a: None,
                   image_getter=lambda n, cb: None,
                   initial="1:B|2:D|3:E|4:F")
board2.pack(fill="both", expand=True)
root.update()
check("选项比槽位多也能画出来",
      len(board2.slot_boxes) == 4 and len(board2.item_boxes) == 6,
      f"{len(board2.slot_boxes)} / {len(board2.item_boxes)}")
check("一槽多答案的作答能存回来", board2.answer() == "1:B|2:D|3:E|4:F",
      board2.answer())
board2.destroy()
root.update()
check("带选项前缀也认",
      _split_img("A. img:btn_save.png 保存") == ("A 保存", "btn_save.png")
      or _split_img("A. img:btn_save.png 保存")[1] == "btn_save.png")

print("== 2. 打名字测试 ==")
from typing_ui import NameTypingTask, TypingPanel   # noqa: E402

client = FakeClient()
cfg = {"tasks": [
    {"task": "name_typing", "name": "打名字测试", "rounds": 2, "seconds": 0,
     "stages": 3,
     # 服务端会带上学生本人姓名（打自己的名字）
     "student": {"name": "小明", "pinyin": "xiǎo míng", "missing": [],
                 "duoyin": False, "duoyin_chars": []},
     "words": [{"word": "雅安", "pinyin": "yǎ ān"},
               {"word": "北附", "pinyin": "běi fù"}]},
]}
panel = TypingPanel(root, client, cfg, "name_typing")
panel.pack()
root.update()
task = panel.current
check("用的是打名字测试面板", isinstance(task, NameTypingTask))
check("总回合 = 次数 × 3", task.st["total"] == 6, str(task.st["total"]))
# 面板首帧的「出题」是 300ms 后才跑的，测试里手动触发一次
if task._after_id:
    task.after_cancel(task._after_id)
    task._after_id = None
task._next_step()
root.update()
check("第一回合打自己的名字", task.current_word().get("is_self") is True
      and task.current_word()["word"] == "小明", str(task.current_word()))
check("认出学生后每一遍都打自己的名字（不掺老师词表）",
      all(w.get("is_self") for w in task.queue),
      str([w.get("word") for w in task.queue]))
check("第一回合给拼音提示", task.current_stage() == 1 and
      task.prompt_label.cget("text") == task.current_word()["pinyin"],
      task.prompt_label.cget("text"))
check("提示里说明是自己的名字", "你自己的名字" in task.hint_label.cget("text"),
      task.hint_label.cget("text"))
# 打对第一个回合（这里不要 pump 事件：首次 update 可能超过 800ms，
# 会让"下一回合"的定时器提前跑掉，状态就检查不到了）
task.entry.insert(0, task.current_word()["word"])
task._submit()
check("打对记一次命中", task.st["hits"] == 1, str(task.st["hits"]))
check("进度推进", task.st["progress"] == 1, str(task.st["progress"]))
check("提交后输入框锁住", str(task.entry.cget("state")) == "disabled",
      f"state={task.entry.cget('state')!r}")
if task._after_id:
    task.after_cancel(task._after_id)
    task._after_id = None
task._next_step()
root.update()
check("输入框已清空（上一回的内容不残留）", task.entry.get() == "",
      repr(task.entry.get()))
check("第二回合给汉字提示",
      task.prompt_label.cget("text") == task.current_word()["word"],
      task.prompt_label.cget("text"))
# 打错 → 不跳下一题，原地重打（老师要求：打错了不要跳下一题）
task.entry.insert(0, "错了")
task._submit()
root.update()
check("打错不推进进度", task.st["progress"] == 1, str(task.st["progress"]))
check("打错后输入框可用（能重打）", str(task.entry.cget("state")) == "normal",
      f"state={task.entry.cget('state')!r}")
check("打错后输入框已清空", task.entry.get() == "", repr(task.entry.get()))
check("打错不直接亮答案（防照抄）", "小明" not in task.fb_label.cget("text"),
      task.fb_label.cget("text"))
check("还停在同一个第 2 回合", task.current_stage() == 2, str(task.current_stage()))
# 连错 3 次 → 把答案亮出来，防止卡死
task.entry.insert(0, "又错了")
task._submit()
root.update()
task.entry.insert(0, "还错了")
task._submit()
root.update()
check("连错 3 次给出答案提示", "小明" in task.fb_label.cget("text"),
      task.fb_label.cget("text"))
# 打对了才进下一回合
task.entry.delete(0, "end")
task.entry.insert(0, "小明")
task._submit()
root.update()
check("打对后进度推进", task.st["progress"] == 2, str(task.st["progress"]))
if task._after_id:
    task.after_cancel(task._after_id)
    task._after_id = None
task._next_step()
root.update()
check("第三回合不给提示", task.prompt_label.cget("text").startswith("？"),
      task.prompt_label.cget("text"))
check("正确率按字符算", task.st["accuracy"] > 0, str(task.st["accuracy"]))
check("速度字段有值", task.st["wpm"] >= 0)
panel.destroy()
root.update()

# 学生机还没填姓名（服务端认不出学生）：退回用词表里的词，别出空题
cfg2 = {"tasks": [{"task": "name_typing", "name": "打名字测试", "rounds": 2,
                   "seconds": 0, "stages": 3, "student": {},
                   "words": [{"word": "雅安", "pinyin": "yǎ ān"}]}]}
panel2 = TypingPanel(root, FakeClient(), cfg2, "name_typing")
panel2.pack()
root.update()
t2 = panel2.current
check("没认出学生时用词表兜底", [w.get("word") for w in t2.queue] == ["雅安", "雅安"],
      str(t2.queue))
panel2.destroy()
root.update()

print("== 3. 题库配图与题型 ==")
import gui   # noqa: E402
from exam_client import TYPE_LABEL   # noqa: E402

check("题型中文名含拖拽题", TYPE_LABEL.get("drag") == "拖拽题")
tabs = dict((k, t) for k, _l, t in gui.App.THEORY_TABS)
check("导航有拖拽题标签", tabs.get("drag") == ("drag",), str(tabs.get("drag")))
check("题干配图解析",
      gui.App._parse_img_opt("img:btn_bold.png 这个按钮是什么？")
      == ("btn_bold.png", "这个按钮是什么？"))
check("题型中文名含程序填空", TYPE_LABEL.get("code") == "程序填空")

print("== 6. 交卷：没做的操作题按缺卷 0 分 ==")
import exam_client   # noqa: E402


class _EmptyApi:
    def __init__(self):
        self.calls = []
        self.acked = []

    def ack_commands(self, ids):
        self.acked.extend(ids)
        return {"ok": True}

    def submit_empty(self, eid, task_id=0):
        self.calls.append((eid, task_id))
        return {"ok": True, "auto": {"score": 0, "full": 0, "empty": True}}


class _MiniClient(exam_client.ExamClient):
    """只测 auto_submit 的分支，不碰真实网络/配置"""

    def __init__(self):
        self.state = {"exam": {"id": 77}}
        self.api = _EmptyApi()
        self.logs = []

    def collect_answer(self, exam_id, task=None):
        return "", "还没做这道题"

    def _log(self, level, msg):
        self.logs.append((level, msg))


mc = _MiniClient()
ok0, info0 = mc.auto_submit({"task_id": 3})      # 平时单题提交：不允许缺卷
check("平时提交（没答卷）仍然报错", ok0 is False, str(info0))
ok1, info1 = mc.auto_submit({"task_id": 3}, allow_empty=True)   # 交卷：确认过没做完
check("交卷时缺卷提交成功", ok1 is True, str(info1))
check("缺卷时调了服务端接口", mc.api.calls == [(77, 3)], str(mc.api.calls))
check("返回带缺卷标记", (info1 or {}).get("empty") is True, str(info1))
check("缺卷的分数是 0", (info1 or {}).get("score") == 0, str(info1))

import tempfile


class _FileApi:
    def __init__(self):
        self.calls = []

    def upload(self, exam_id, path, task_id=0):
        self.calls.append((exam_id, path, task_id))
        return {"ok": True, "auto": {"score": 8, "full": 10}}


class _FileClient(exam_client.ExamClient):
    def __init__(self):
        self.identity = "student-1"
        self.generation = 1
        self.api = _FileApi()
        self.marked = []
        self.logs = []

    def mark_submitted(self, exam_id):
        self.marked.append(exam_id)

    def _log(self, level, msg):
        self.logs.append((level, msg))


fd, answer_path = tempfile.mkstemp(prefix="ikeexam_answer_", suffix=".docx")
os.close(fd)
try:
    fc = _FileClient()
    ok2, info2 = fc.submit_file(77, answer_path, task_id=3)
    check("有答卷时上传成功", ok2 is True and (info2 or {}).get("score") == 8,
          str(info2))
    check("有答卷时按考试编号标记已交", fc.marked == [77], str(fc.marked))
    check("有答卷时调用上传接口", fc.api.calls == [(77, answer_path, 3)],
          str(fc.api.calls))
finally:
    os.unlink(answer_path)

print("== 7. 机器唯一标识 + 结束考试（强制交卷并关闭）==")
from exam_client import Config   # noqa: E402

_cid = Config._new_client_id()
check("client_id 形如 PC-xxxx", _cid.startswith("PC-") and len(_cid) >= 8, _cid)
check("client_id 不含冒号（避免把配置/URL 搞坏）", ":" not in _cid, _cid)


class _HelloClient(exam_client.ExamClient):
    """只测「服务端拒了登录」的分支，不碰真实网络"""

    def __init__(self):
        self.cfg = _EmptyCfg()
        self.state = {}
        self.logs = []
        self._stop = __import__("threading").Event()
        self._refresh_identity = lambda: False
        self._bind_api = lambda: None

    def _log(self, level, msg):
        self.logs.append((level, msg))


class _EmptyCfg:
    server = "http://fake-server"
    auto_discover = False
    window_mode = "normal"


class _NoUiClient(_HelloClient):
    """连上之后的部分（锁窗口/守卫/心跳线程）也不跑，只测握手分支"""

    def __init__(self):
        super().__init__()
        self.guard = type("G", (), {"start": lambda s: None})()
        self.conn = {"online": False, "fails": 0, "last_ok": 0, "attempt": 0}

    def sync_lock(self):
        pass

    def _resubmit_if_needed(self):
        pass

    def flush_typing(self):
        pass


_hc = _NoUiClient()
# 服务端说「不在名单里」→ start() 直接返回 False，并把原因存起来给界面显示
_hc.api = type("A", (), {"hello": lambda s: {
    "ok": False, "msg": "「路人甲」不在本场考试的学生名单里。"}})()
check("被名单拦下时连接失败", _hc.start() is False)
check("失败原因存进了 state（界面要显示）",
      "不在本场考试的学生名单里" in _hc.state.get("hello_error", ""),
      str(_hc.state.get("hello_error")))
check("写了一条错误日志", any(l[0] == "error" for l in _hc.logs))
# 正常放行
_hc2 = _NoUiClient()
_hc2.api = type("A", (), {"hello": lambda s: {"ok": True, "phase": "idle",
                                              "exam": None}})()
check("放行时正常连上", _hc2.start() is True
      and not _hc2.state.get("hello_error"))

import tkinter.messagebox as _tmb   # noqa: E402
_orig_info = _tmb.showinfo
_tmb.showinfo = lambda *a, **k: None        # 自检时不真弹窗
try:
    def _make_mini():
        """只测结束指令的分支，不碰真实网络/配置"""
        seen = {"submit": [], "destroy": 0, "cfg_saved": False,
                "flush_answers": False, "flush_typing": False}

        class _Cfg:
            name = "小明"
            seat = "01"
            logged_in = True
            passwordless = False

            def save(self):
                seen["cfg_saved"] = True

        class _Root:
            def after(self, ms, fn):
                fn()                      # 测试里同步执行

        class _Mini(exam_client.ExamClient):
            def __init__(self):
                self.state = {"exam": {"id": 5}, "phase": "theory",
                              "section": None}
                self.cfg = _Cfg()
                self.api = _EmptyApi()
                self.root = _Root()
                self.logs = []
                self._seen_commands = set()
                self._prepare_identity_change = lambda: None
                self._refresh_identity = lambda: True

            def practice_tasks(self):
                return [{"task_id": 1, "name": "题一"}]

            def auto_submit(self, task=None, allow_empty=False):
                seen["submit"].append((task.get("task_id"), allow_empty))
                return True, {"empty": True, "score": 0}

            def flush_answers(self):
                seen["flush_answers"] = True

            def flush_typing(self):
                seen["flush_typing"] = True

            def _notify_ui(self):
                pass

            def _log(self, level, msg):
                self.logs.append((level, msg))

            def destroy(self):
                seen["destroy"] += 1

        return _Mini(), seen

    m2, seen2 = _make_mini()
    m2._handle_commands([{"id": 91, "type": "force_exit",
                          "payload": {"clear_login": True}}])
    check("结束指令触发强制交卷", seen2["submit"] == [(1, True)],
          str(seen2["submit"]))
    check("交卷前把攒的理论题答案刷上去", seen2["flush_answers"] is True)
    check("打字统计也一起上报", seen2["flush_typing"] is True)
    check("选了清除 → 登录信息清掉了",
          m2.cfg.name == "" and m2.cfg.seat == "" and m2.cfg.logged_in is False)
    check("清除后配置已保存", seen2["cfg_saved"] is True)
    check("客户端已关闭", seen2["destroy"] == 1, str(seen2["destroy"]))
    check("结束前确认命令", "91" in m2.api.acked, str(m2.api.acked))

    m3, seen3 = _make_mini()
    m3._handle_commands([{"type": "force_exit",
                          "payload": {"clear_login": False}}])
    check("选了保留 → 登录信息还在",
          m3.cfg.name == "小明" and m3.cfg.logged_in is True)
    check("保留登录信息时也交卷并关闭",
          seen3["submit"] == [(1, True)] and seen3["destroy"] == 1,
          str(seen3))
finally:
    _tmb.showinfo = _orig_info

root.destroy()
print()
if FAIL:
    print(f"[FAIL] {len(FAIL)} 项失败: {FAIL}")
    sys.exit(1)
print("[OK] 学生端自检全部通过")
