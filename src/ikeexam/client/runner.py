"""
代码运行器 —— 学生端「运行」按钮用
------------------------------------
把学生填好的代码写到临时目录，本地执行，捕获输出。

支持：
  · Python  （初高中信息技术课主力语言）
  · C / C++ （信息学奥赛方向，需要 g++）

设计要点：
  · 超时强制杀进程（Python 5 秒，C++ 编译 20 秒 + 运行 5 秒）
  · 输出截断（防止死循环刷爆内存）
  · 临时目录隔离，用完清理
  · 环境缺失时给出明确提示，而不是莫名其妙报错
"""
import os
import subprocess
import sys
import tempfile
import shutil

IS_WIN = os.name == "nt"

# 超时（秒）
# 程序大题（循环、排序、递归）比小填空慢，放宽到 8 秒；
# 死循环仍然会被强杀，不会拖住机器。
TIMEOUT_PY = 8
TIMEOUT_COMPILE = 20
TIMEOUT_RUN = 8

# 输出上限（字符），防止 while True: print() 把内存吃光
MAX_OUT = 8000

# 候选命令（按顺序尝试）
PY_CMDS = ["python", "python3", "py"]
CPP_CMDS = ["g++", "clang++"]


def _which(cmd):
    return shutil.which(cmd) is not None


def _kill_tree(p):
    """
    超时后杀掉整个进程树：学生程序可能 fork 出孙进程，只 kill 直接子进程
    会留下僵尸进程、锁住临时目录删不掉。
    Windows 用 taskkill /T；POSIX 用进程组信号（配合 _run 里的 start_new_session）。
    """
    try:
        if IS_WIN:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(p.pid)],
                capture_output=True, timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW)
        else:
            import signal
            os.killpg(os.getpgid(p.pid), signal.SIGKILL)
    except Exception:
        try:
            p.kill()
        except Exception:
            pass


def _validate_python(cmd):
    """
    确认这个 python 命令真的能跑代码。

    ⚠️ 关键：Windows 自带一个「微软商店占位程序」——
    %LOCALAPPDATA%\\Microsoft\\WindowsApps\\python.exe 是个假 python，
    在 PATH 里排在前面，运行它只会打印
    "Python was not found but can be installed from the Microsoft Store"
    然后弹出商店。必须实际跑一下才能分辨真假。
    """
    try:
        kw = {}
        if IS_WIN:
            kw["creationflags"] = subprocess.CREATE_NO_WINDOW
        p = subprocess.run(
            [cmd, "-c", "print('__exam_ok__')"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=8, **kw)
        return p.returncode == 0 and "__exam_ok__" in (p.stdout or "")
    except Exception:
        return False


def _find_python_windows():
    """
    Windows 上 python 可能不在 PATH（打包后尤其常见）。
    搜索常见安装路径。**跳过微软商店的占位程序**（WindowsApps 目录）。
    """
    import glob as _glob
    candidates = []
    # 环境变量
    for env in ("LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)"):
        base = os.environ.get(env, "")
        if base:
            candidates.append(os.path.join(base, "Programs", "Python", "python.exe"))
            candidates.append(os.path.join(base, "Python*", "python.exe"))
    # 用户目录
    home = os.path.expanduser("~")
    candidates.append(os.path.join(home, "AppData", "Local", "Programs", "Python", "Python*", "python.exe"))
    # 系统目录
    candidates.append(r"C:\Python*\python.exe")
    candidates.append(r"C:\Program Files\Python*\python.exe")
    candidates.append(r"C:\Users\*\AppData\Local\Programs\Python\Python*\python.exe")

    for pat in candidates:
        for p in sorted(_glob.glob(pat), reverse=True):
            # WindowsApps 里的是微软商店占位程序，不是真 Python
            if os.path.isfile(p) and "WindowsApps" not in p:
                return p
    return None


# 检测结果缓存（真跑一次子进程验证，不能每次点「运行」都验证一遍）
_python_cache = {"cmd": None, "done": False}


def detect():
    """
    检测本机可用的运行环境。
    返回 {"python": "python 或完整路径 或 None", "cpp": "g++" 或 None}
    """
    out = {"python": None, "cpp": None}
    if _python_cache["done"]:
        out["python"] = _python_cache["cmd"]
    else:
        # 先找 PATH 里的，且要**真的能跑**（排除商店占位程序）
        for c in PY_CMDS:
            if _which(c) and _validate_python(c):
                out["python"] = c
                break
        # PATH 里没有能用的，搜常见安装路径（Windows）
        if not out["python"] and IS_WIN:
            found = _find_python_windows()
            if found and _validate_python(found):
                out["python"] = found
        _python_cache["cmd"] = out["python"]
        _python_cache["done"] = True
        if not out["python"]:
            # 提示一句，方便排查
            try:
                print("[runner] 未找到可用的 Python"
                      "（微软商店占位程序不算）", flush=True)
            except Exception:
                pass

    for c in CPP_CMDS:
        if _which(c):
            out["cpp"] = c
            break
    return out


def _run(cmd, cwd, timeout, stdin_data="", env=None):
    """执行一条命令，返回结果 dict"""
    try:
        kw = {}
        if IS_WIN:
            # 不弹黑窗口
            kw["creationflags"] = subprocess.CREATE_NO_WINDOW
        else:
            # POSIX：新进程组，超时后 killpg 能连带孙进程一起杀
            kw["start_new_session"] = True
        if env:
            kw["env"] = env
        p = subprocess.Popen(
            cmd, cwd=cwd,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True,
            encoding="utf-8", errors="replace", **kw)
        try:
            out, err = p.communicate(input=stdin_data or "", timeout=timeout)
            rc = p.returncode
            timed_out = False
        except subprocess.TimeoutExpired:
            _kill_tree(p)
            try:
                out, err = p.communicate(timeout=3)
            except Exception:
                out, err = "", ""
            rc = None
            timed_out = True
    except FileNotFoundError:
        return {"ok": False, "msg": f"找不到命令: {cmd[0]}", "out": "", "err": ""}
    except Exception as e:
        return {"ok": False, "msg": f"执行失败: {e}", "out": "", "err": ""}

    out = (out or "")[:MAX_OUT]
    err = (err or "")[:MAX_OUT]
    if timed_out:
        err = (err + f"\n[超时] 运行超过 {timeout} 秒，已强制终止").strip()
    return {
        "ok": (rc == 0) and not timed_out,
        "rc": rc, "out": out, "err": err, "timed_out": timed_out,
        "msg": "" if rc == 0 else (err or f"退出码 {rc}"),
    }


def run_python(code, stdin_data="", timeout=TIMEOUT_PY):
    """运行 Python 代码"""
    env = detect()
    cmd = env["python"]
    if not cmd:
        msg = ("本机没有安装 Python，无法运行程序。\n\n"
               "这不影响你作答（答案照常保存和评分），\n"
               "只是看不到运行结果。\n\n"
               "解决办法：请老师在台学生机上安装 Python（python.org 下载，\n"
               "安装时勾选 Add python.exe to PATH），装完重启客户端即可。")
        return {"ok": False, "msg": msg, "out": "", "err": ""}
    d = tempfile.mkdtemp(prefix="examrun_")
    try:
        fp = os.path.join(d, "main.py")
        with open(fp, "w", encoding="utf-8") as f:
            f.write(code)
        # 强制 Python 用 UTF-8 输出。
        # 中文 Windows 默认 GBK，print("是素数") 会输出 GBK 字节，
        # 我们按 UTF-8 解码就成乱码了。
        env2 = dict(os.environ)
        env2["PYTHONIOENCODING"] = "utf-8"
        r = _run([cmd, fp], d, timeout, stdin_data, env=env2)
        # 万一还是碰到商店占位程序，把那句话翻译成人话
        blob = (r.get("err") or "") + (r.get("out") or "")
        if not r.get("ok") and "Python was not found" in blob:
            r["msg"] = "本机没有安装 Python（点运行弹出了微软商店）。" \
                       "请老师安装 Python 后重启客户端。"
        return r
    finally:
        shutil.rmtree(d, ignore_errors=True)


def run_cpp(code, stdin_data="", timeout=TIMEOUT_RUN):
    """编译并运行 C++ 代码"""
    env = detect()
    cc = env["cpp"]
    if not cc:
        return {"ok": False, "msg": "本机没检测到 g++ 编译器，无法运行 C++。"
                                    "Python 题不受影响。",
                "out": "", "err": ""}
    d = tempfile.mkdtemp(prefix="examcpp_")
    try:
        src = os.path.join(d, "main.cpp")
        with open(src, "w", encoding="utf-8") as f:
            f.write(code)
        exe = os.path.join(d, "main.exe" if IS_WIN else "main")
        c = _run([cc, src, "-o", exe, "-std=c++14", "-O1"], d, TIMEOUT_COMPILE)
        if not c["ok"]:
            return {"ok": False, "out": "", "rc": None,
                    "err": c["err"], "timed_out": c.get("timed_out", False),
                    "msg": "编译失败：\n" + (c["err"] or "未知错误")}
        return _run([exe], d, timeout, stdin_data)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def run(code, language="python", stdin_data="", timeout=None):
    """
    按语言运行。language: python / cpp
    """
    lang = (language or "python").lower()
    if lang in ("c", "cpp", "c++"):
        return run_cpp(code, stdin_data, timeout or TIMEOUT_RUN)
    return run_python(code, stdin_data, timeout or TIMEOUT_PY)


def fill_blanks(code, answers):
    """
    把学生的填空答案填回代码模板。

    code:     带 ____ 的模板
    answers:  ["答案1", "答案2", ...]，按空从左到右

    空白处至少 4 个下划线（____），支持学生多打几个下划线。
    """
    import re
    out = str(code or "")
    for a in answers:
        out = re.sub(r"_{4,}", lambda m: (a if a.strip() else m.group(0)),
                     out, count=1)
    return out


if __name__ == "__main__":
    print("环境检测:", detect())
    print()
    demo = "for i in range(1, 6):\n    print(i, end=' ')"
    print("--- 运行 Python ---")
    r = run_python(demo)
    print("rc:", r.get("rc"), "out:", repr(r.get("out")))
    print()
    print("--- 填空拼接 ---")
    tpl = "s = 0\nfor i in ____:\n    s += ____\nprint(s)"
    print(fill_blanks(tpl, ["range(1, 11)", "i"]))
    print()
    print("--- 死循环（测超时）---")
    r = run_python("while True:\n    pass", timeout=3)
    print("timed_out:", r.get("timed_out"), "msg:", r.get("msg", "")[:60])
