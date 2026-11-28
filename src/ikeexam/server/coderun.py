"""
server/coderun.py —— 程序填空判题（OJ 判题主体）
===================================================

对外只有一个入口：judge_code(question, student_answer) -> True / False / None / "error"

返回值契约（与 db.judge() 约定，不得改）：
  True    —— 真的跑了，且输出与期望一致            → db 给满分
  False   —— 真的跑了，但输出不一致或运行失败      → db 直接 0 分
  None    —— 没配期望输出 / 空没填完 / 环境缺失    → db 回退「按空给分」
  "error" —— 判题机自身异常（排队超时 / 内部 bug）  → db 标记待重判 + 回退

设计思路（为什么真跑一遍而不是匹配参考答案）：
  程序填空的正确写法穷举不完（range(1,11) / range(1, 11)、for / while、
  i*i / i**2 …），只在题库列参考答案必然漏判。把学生填完的代码真跑一遍，
  输出对上就满分 —— 写法随便变，结果对就给分。

安全边界：
  · 资源限制 / 降权 / 杀进程树 都在 sandbox.py 里做
  · 危险写法黑名单（guard）只告警不拦截 —— 黑名单能被 __import__('os').system()
    这类写法绕过，真正防线是 sandbox 的资源限制 + 降权（见 README「OJ 安全」）
  · 排队限流：全局信号量，超配额就等，不拒绝（等太久才报 error）
"""
import hashlib
import json
import logging
import os
import re
import shutil
import sys
import threading
import time
from collections import OrderedDict

try:
    from .sandbox import run as sandbox_run
except ImportError:
    from sandbox import run as sandbox_run

log = logging.getLogger(__name__)

BLANK_SEP = "||"          # 与 db.BLANK_SEP 一致：各空之间用 ||

# 超时（秒）：运行阶段（Python 5s，C++ 运行 5s），编译阶段 20s
TIMEOUT_PY = 5
TIMEOUT_COMPILE = 20
TIMEOUT_RUN = 5

# 内存上限（KB，地址空间，仅 POSIX 下 ulimit -v 生效）。
# 512MB：够正常 Python / C++ 程序跑，又挡得住 x='a'*999999999 这种刷内存。
# 任务建议 Python 不低于 256000，这里取 512000 留足余量。
MEM_LIMIT_KB = int(os.environ.get("OJ_MEM_LIMIT_KB", "512000"))

# 输出截断下限（字符）。实际阈值取 max(8000, len(期望输出)*2)：
# 期望输出本身很长时写死 8000 会永远判不对。
MIN_OUT = 8000

# 排队限流：全局信号量，超配额就等，不拒绝。
# 旧实现是「5 秒窗口 40 次，超过直接回退按空给分」—— 45 人同时交卷就有
# 5 个被误伤（本来满分变部分分），且老师看不出原因。
# 改成排队后超配额只是慢一点，不会再误伤。
_gate = threading.Semaphore(int(os.environ.get("SANDBOX_MAX_CONCURRENCY", "4")))
# 排队最久等 30 秒；超了返回 "error" 让 db 标记待重判，而不是无限阻塞请求线程。
QUEUE_TIMEOUT = 30.0

RESULT_CACHE_TTL = float(os.environ.get("OJ_RESULT_CACHE_TTL", "300"))
RESULT_CACHE_MAX = int(os.environ.get("OJ_RESULT_CACHE_MAX", "1024"))
_result_lock = threading.Lock()
_result_cache = OrderedDict()
_inflight = {}
_cache_generation = 0

# 危险写法词表（只告警，不拦截）
DANGER = (
    "os.system", "subprocess", "popen", "shutil.", "rmtree",
    "socket", "os.remove", "os.unlink", "os.rmdir", "os._exit",
    "os.kill", "__import__", "pty.spawn", "commands.getoutput",
)

# 编译器检测缓存（shutil.which 每次都扫 PATH，缓存一下）
_cpp_cache = {"cc": None, "done": False}


def _is_within(path, parent):
    path = os.path.normcase(os.path.realpath(path))
    parent = os.path.normcase(os.path.realpath(parent))
    try:
        return os.path.commonpath([path, parent]) == parent
    except ValueError:
        return False


def _safe_path_dirs():
    cwd = os.path.realpath(os.getcwd())
    dirs = []
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        entry = entry.strip().strip('"')
        if not entry or not os.path.isabs(entry):
            continue
        entry = os.path.realpath(entry)
        if _is_within(entry, cwd):
            continue
        if entry not in dirs:
            dirs.append(entry)
    return dirs


def _trusted_which(name):
    """Resolve only absolute PATH candidates outside the server working directory."""
    names = [name]
    if os.name == "nt":
        extensions = [ext for ext in os.environ.get(
            "PATHEXT", ".COM;.EXE;.BAT;.CMD").split(os.pathsep) if ext]
        if extensions and not name.lower().endswith(
                tuple(ext.lower() for ext in extensions)):
            names = [name + ext for ext in extensions]
    for directory in _safe_path_dirs():
        for filename in names:
            found = shutil.which(os.path.join(directory, filename))
            if found:
                found = os.path.realpath(found)
                if os.path.isfile(found) and not _is_within(found, os.getcwd()):
                    return found
    return None


def _find_cpp():
    """找 C++ 编译器，结果缓存。找不到返回 None。"""
    if not _cpp_cache["done"]:
        _cpp_cache["cc"] = _trusted_which("g++") or _trusted_which("clang++")
        _cpp_cache["done"] = True
    return _cpp_cache["cc"]


def clear_cache():
    """
    清掉运行环境检测缓存（编译器路径等）。

    db.rejudge_code_answers 重判前会调它：老师补装了 g++ 后，这里若不重置，
    _cpp_cache 里还是旧的「没找到」，重判时 C++ 题仍会判成「按空给分」。
    """
    global _cache_generation
    _cpp_cache["cc"] = None
    _cpp_cache["done"] = False
    with _result_lock:
        _result_cache.clear()
        # A flight started before this invalidation must not repopulate stale results.
        _cache_generation += 1


def guard(code, question_id=None, user_id=None):
    """
    危险写法扫描 —— 只告警，不拦截。

    为什么不再据此拒绝运行：
      黑名单实测能被 __import__('os').system()、getattr(os,'sys'+'tem')、
      importlib.import_module('os') 等十余种写法绕过，覆盖不全。
      真正的防线是 sandbox 的资源限制 + 降权（见 README「OJ 安全」）。
      这里只记日志，带上题目 / 学生 / 命中词，方便老师事后查谁在试探。
    """
    low = str(code or "").lower()
    hits = [d for d in DANGER if d in low]
    if hits:
        log.warning("代码命中危险写法（不拦截，仅记录）qid=%s user=%s hits=%s",
                    question_id, user_id or "-", ",".join(hits))
    return hits


def fill_blanks(code, answers):
    """
    把学生各空的答案填回代码模板。
    空位 = 至少 4 个连续下划线（____），从左到右依次填。
    """
    out = str(code or "")
    for a in answers:
        out = re.sub(r"_{4,}",
                     lambda m: (a if str(a).strip() else m.group(0)),
                     out, count=1)
    return out


def normalize_output(s):
    """
    输出归一化后再比：统一换行符、去掉每行行尾空格、去掉末尾空行。
    这样 Windows(\r\n) 和 Linux(\n)、末尾多一个回车都能算对。
    （与旧实现逐字一致，语义不变。）
    """
    s = str(s or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [ln.rstrip() for ln in s.split("\n")]
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def _out_limit(question):
    """动态输出截断阈值：期望输出长，阈值就跟着放大。"""
    exp = str(question.get("expected_output") or "")
    return max(MIN_OUT, len(exp) * 2)


def _stdin_of(question):
    """运行输入：兼容现有字段 stdin_data，也认 expected_input 别名。"""
    return (question.get("stdin_data") or
            question.get("expected_input") or "")


_py_cache = {"path": None, "done": False}


def _python_exe():
    """
    跑学生代码用的 Python 解释器。

    ⚠️ 打包成 EXE 后 sys.executable 是**服务端自己**（exam_server.exe），
       拿它去跑 .py 会直接失败 —— 必须另找一个真的 Python：
         1) 环境变量 EXAM_PYTHON（机房里装了自定义路径时用）
         2) PATH 里的 python
         3) 常见安装目录（Program Files / LOCALAPPDATA 下的 Python）
    都找不到 → 返回 None，判分回退「按空给分」（不报错、不影响其它题型）。
    """
    if _py_cache["done"]:
        return _py_cache["path"]
    exe = None
    if not getattr(sys, "frozen", False):
        exe = sys.executable          # 源码运行：就是当前解释器
    else:
        env = os.environ.get("EXAM_PYTHON", "").strip()
        if env and os.path.isabs(env) and os.path.isfile(env):
            exe = os.path.realpath(env)
        if not exe:
            for name in ("python.exe", "python3.exe", "python"):
                exe = _trusted_which(name)
                if exe:
                    break
        if not exe:
            import glob
            patterns = []
            for env_name in ("LOCALAPPDATA", "PROGRAMFILES",
                             "PROGRAMFILES(X86)"):
                base = os.environ.get(env_name, "")
                if base:
                    patterns.append(os.path.join(base, "Programs", "Python"))
                    patterns.append(os.path.join(base, "Python*"))
            for pat in patterns:
                for d in sorted(glob.glob(pat), reverse=True):
                    p = os.path.join(d, "python.exe")
                    if os.path.isfile(p):
                        exe = p
                        break
                if exe:
                    break
    if not exe:
        log.warning("没找到 Python 解释器，Python 代码题只能按空给分"
                    "（装了 Python 或设 EXAM_PYTHON 即可恢复）")
    _py_cache.update(path=exe, done=True)
    return exe


def _run_python(code, question, cancel_event=None):
    py = _python_exe()
    if not py:
        return None
    r = sandbox_run(
        [os.path.realpath(py), "main.py"],
        stdin_data=_stdin_of(question),
        files={"main.py": code},
        wall=TIMEOUT_PY,
        cpu=TIMEOUT_PY,
        mem_kb=MEM_LIMIT_KB,
        max_out=_out_limit(question),
        # 强制 UTF-8 输出（中文 Windows 默认 GBK 会乱码）；
        # 清空 PYTHONPATH，别让学生代码 import 到本项目自己的模块。
        env={"PYTHONIOENCODING": "utf-8", "PYTHONPATH": ""},
        cancel_event=cancel_event,
    )
    return r


def _run_cpp(code, question, cancel_event=None):
    cc = _find_cpp()
    if not cc:
        log.warning("服务器未安装 g++，代码题判分不可用")
        return None
    exe = "main.exe" if os.name == "nt" else "main"
    r = sandbox_run(
        [os.path.join(".", exe)],
        stdin_data=_stdin_of(question),
        files={"main.cpp": code},
        compile_argv=[os.path.realpath(cc), "main.cpp", "-o", exe, "-std=c++14", "-O1"],
        wall=TIMEOUT_RUN,
        cpu=TIMEOUT_RUN,
        mem_kb=MEM_LIMIT_KB,
        compile_wall=TIMEOUT_COMPILE,
        max_out=_out_limit(question),
        cancel_event=cancel_event,
    )
    return r


def _judge_code_inner(question, student_answer, cancel_event=None):
    exp = str(question.get("expected_output") or "").strip()
    if not exp:
        return None                        # 没配期望输出 → 不启用运行判分

    # 代码模板：兼容现有字段 code，也认 template 别名
    tmpl = str(question.get("code") or question.get("template") or "")
    if not tmpl:
        return None

    # 学生各空答案按 BLANK_SEP 拆开
    parts = [x.strip() for x in str(student_answer or "").split(BLANK_SEP)]
    n_blank = len(re.findall(r"_{4,}", tmpl))
    n_filled = len([p for p in parts if p])
    # 空位数量与答案份数对不上（没填完 / 多填）都不跑：跑出来必然不对
    if n_blank and n_filled != n_blank:
        log.warning("填空份数与空位不一致，跳过运行判分 qid=%s 空位=%d 答案=%d",
                    question.get("id"), n_blank, n_filled)
        return None

    code = fill_blanks(tmpl, parts)

    # 危险写法：只记录，不拦截（真正防线是 sandbox 资源限制 + 降权）
    guard(code, question.get("id"),
          question.get("user_id") or question.get("hostname"))

    lang = (question.get("language") or "python").strip().lower()
    if lang in ("c", "cpp", "c++"):
        r = _run_cpp(code, question, cancel_event)
    else:
        r = _run_python(code, question, cancel_event)

    if r is None:
        return None                        # 环境缺失（如没装 g++），压根没跑
    st = r.get("status")
    if st in ("se", "cancelled"):
        return "error" if st == "cancelled" else None
    if st != "ok":
        # TLE / MLE / RE / 编译失败：跑了但没得到正确输出 → 走按空给分回退
        return False

    # 输出截断后再归一化比对（阈值已经按期望输出长度放大过）
    got = normalize_output((r.get("stdout") or ""))
    want = normalize_output(exp)
    if question.get("ignore_case"):
        got, want = got.lower(), want.lower()
    return got == want


def _judge_key(question, student_answer):
    payload = {
        "id": question.get("id"),
        "answer": [x.strip() for x in str(student_answer or "").split(BLANK_SEP)],
        "code": question.get("code") or question.get("template") or "",
        "language": (question.get("language") or "python").strip().lower(),
        "expected_output": str(question.get("expected_output") or "").strip(),
        "stdin": _stdin_of(question),
        "ignore_case": bool(question.get("ignore_case")),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _cached_result(key):
    now = time.monotonic()
    with _result_lock:
        item = _result_cache.get(key)
        if item is None:
            return False, None
        if item[0] <= now:
            _result_cache.pop(key, None)
            return False, None
        _result_cache.move_to_end(key)
        return True, item[1]


def _wait_flight(flight, cancel_event):
    deadline = time.monotonic() + QUEUE_TIMEOUT + TIMEOUT_COMPILE + TIMEOUT_RUN + 5
    while not flight["event"].is_set():
        if cancel_event is not None and cancel_event.is_set():
            return "error"
        left = deadline - time.monotonic()
        if left <= 0:
            return "error"
        flight["event"].wait(min(0.1, left))
    return flight["result"]


def _acquire_gate(cancel_event):
    deadline = time.monotonic() + QUEUE_TIMEOUT
    while True:
        if cancel_event is not None and cancel_event.is_set():
            return False
        left = deadline - time.monotonic()
        if left <= 0:
            return False
        if _gate.acquire(timeout=min(0.1, left)):
            return True


def judge_code(question, student_answer, cancel_event=None):
    """
    程序填空判题入口。

    返回 True / False / None / "error"（契约见模块 docstring）。
    """
    if cancel_event is not None and cancel_event.is_set():
        return "error"
    key = _judge_key(question, student_answer)
    hit, result = _cached_result(key)
    if hit:
        return result

    global _cache_generation
    with _result_lock:
        item = _result_cache.get(key)
        if item is not None and item[0] > time.monotonic():
            _result_cache.move_to_end(key)
            return item[1]
        if item is not None:
            _result_cache.pop(key, None)
        flight = _inflight.get(key)
        owner = flight is None
        if owner:
            flight = {"event": threading.Event(), "result": "error"}
            _inflight[key] = flight
            generation = _cache_generation

    if not owner:
        return _wait_flight(flight, cancel_event)

    acquired = _acquire_gate(cancel_event)
    if not acquired:
        result = "error"
        if cancel_event is None or not cancel_event.is_set():
            log.error("判题排队超过 %.0f 秒仍未拿到名额，qid=%s",
                      QUEUE_TIMEOUT, question.get("id"))
    else:
        try:
            if cancel_event is not None and cancel_event.is_set():
                result = "error"
            else:
                result = _judge_code_inner(question, student_answer, cancel_event)
        except Exception:
            # 判题机自身的 bug 不能静默吞掉（否则会退化成按空给分，老师发现不了）
            log.exception("判题内部异常 qid=%s", question.get("id"))
            result = "error"
        finally:
            _gate.release()

    with _result_lock:
        flight["result"] = result
        if _inflight.get(key) is flight:
            _inflight.pop(key, None)
        if result != "error" and generation == _cache_generation:
            _result_cache[key] = (time.monotonic() + RESULT_CACHE_TTL, result)
            _result_cache.move_to_end(key)
            while len(_result_cache) > RESULT_CACHE_MAX:
                _result_cache.popitem(last=False)
        flight["event"].set()
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    tpl = "s = 0\nfor i in ____:\n    s += ____\nprint(s)"
    q = {"id": 0, "code": tpl, "language": "python",
         "stdin_data": "", "expected_output": "55"}
    print("标准答案   range(1,11)||i     ->", judge_code(q, "range(1,11)||i"))
    print("等价写法   range(1, 11)||i    ->", judge_code(q, "range(1, 11)||i"))
    print("等价写法   range(0,11)||i     ->", judge_code(q, "range(0,11)||i"))
    print("错误答案   range(1,10)||i     ->", judge_code(q, "range(1,10)||i"))
    print("没填完     range(1,11)||      ->", judge_code(q, "range(1,11)||"))
    print("没配期望输出                  ->",
          judge_code({"id": 0, "code": tpl, "language": "python"}, "range(1,11)||i"))
