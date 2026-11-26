"""
server/sandbox.py —— OJ 判题沙箱（跨平台）
============================================

职责：把学生提交的代码在受控环境下跑一遍，返回运行结果。
只用 Python 标准库，不装 pip 包、不用 Docker、不引入外部服务。

两条平台路径，能力不同：
  · POSIX（Linux / macOS）：完整沙箱 —— ulimit 限资源 + 可选 setpriv 降权
                             + start_new_session + killpg 杀进程树
  · Windows：降级沙箱 —— 只能限墙钟时间、截断输出、隐藏黑窗口，
              无法限制内存/CPU（Windows 没有 ulimit）

---- 为什么 Windows 做不了资源限制 ----
Windows 没有 ulimit；限制内存要 Job Object（ctypes 调 Win32 API），
setrlimit 那一套 POSIX 系统调用在 Windows 上根本不存在。
所以 Windows 只能「限时间 + 限输出」，真正的资源隔离必须在 Linux 上跑。

---- 为什么不用 subprocess 的 preexec_fn（重要）----
Python 官方文档明确警告：preexec_fn 在多线程程序里可能死锁。
原因：preexec_fn 在 fork 之后、exec 之前执行，此时子进程里只剩 fork 出来的
那一个线程，但各把锁的状态是 fork 前（多线程）的快照 —— 如果另一个线程
正好持有一把锁（比如 malloc 内部锁），子进程里这把锁永远不会被释放，
子进程再碰这把锁就死锁。Flask 默认多线程，这里踩坑是必现的。
所以资源限制不放在 preexec_fn，而是「bash 先 ulimit 再 exec」：
ulimit 由 bash 这个单线程进程自己设，不存在上述锁的问题。

---- 为什么用 bash 包装 + exec（重要）----
bash -c "ulimit ...; exec 目标命令" 里，exec 让目标进程**继承 bash 的 PID**。
配合 Popen(start_new_session=True)，目标进程成为新会话的首进程，
超时时 killpg(os.getpgid(pid), SIGKILL) 能把学生代码 fork 出来的所有子进程
连根拔掉 —— 光 kill 主进程的话，fork 出来的僵尸会残留。

---- 为什么计时用 time.monotonic()（重要）----
os.times() 返回「当前进程 + 其子进程」的 CPU 时间，多线程/多进程并发下会
互相串扰（A 进程的 CPU 时间算到 B 头上）。墙钟超时只看流逝时间，
monotonic() 是单调时钟（不受系统改时间影响），最可靠。
"""
import os
import shlex
import shutil
import signal
import subprocess
import tempfile
import threading
import time

IS_WIN = os.name == "nt"

# 沙箱工作根目录：所有临时目录都建在这里面，方便清理/审计
SANDBOX_WORK_ROOT = os.environ.get("SANDBOX_WORK_ROOT") or (
    os.path.join(tempfile.gettempdir(), "oj_sandbox") if IS_WIN else "/tmp/oj_sandbox"
)

# 降权目标（仅 POSIX 且以 root 运行时生效，见 _build_cmd）
SANDBOX_UID = os.environ.get("SANDBOX_UID")
SANDBOX_GID = os.environ.get("SANDBOX_GID")

# 状态常量
OK, TLE, MLE, RE, SE = "ok", "tle", "mle", "re", "se"

# MLE 关键词：ulimit -v 触发时 Python 抛 MemoryError，C++ 抛 bad_alloc，
# 内核 OOM killer 杀进程时 shell 会打印 "Killed"。没有精确内存峰值统计，
# 只能靠这些关键词推断（见 README「已知限制」）。
_MLE_HINTS = (
    "memoryerror", "bad_alloc", "bad allocation", "out of memory",
    "cannot allocate", "std::bad_alloc", "killed",
)


def _to_bytes(data):
    if data is None:
        return b""
    if isinstance(data, bytes):
        return data
    return str(data).encode("utf-8")


def _decode(b, workdir):
    """
    解码 stdout/stderr，并顺带做「报错信息清洗」：
    把沙箱临时目录的绝对路径（/tmp/oj_xxx/main.py）抹成相对文件名（main.py），
    别把 /tmp 里那串随机目录名甩给初中生看。
    errors="replace" 防止二进制输出（比如 print 了一堆 \x00）炸掉解码。
    """
    if not b:
        return ""
    s = b.decode("utf-8", "replace")
    if workdir:
        s = s.replace(workdir + os.sep, "").replace(workdir, "")
    return s


def _looks_mle(err):
    low = (err or "").lower()
    return any(h in low for h in _MLE_HINTS)


def _build_cmd(argv, cpu, mem_kb, fsize_kb, stack_kb, nofile):
    """
    用 bash 先设 ulimit 再 exec 替换自身。

    exec 让目标进程继承 bash 的 PID，配合 start_new_session=True，
    超时时 killpg 能连学生代码 fork 出的子进程一起杀掉。
    mem_kb 为 None 表示不限地址空间（JVM 需要，改用 -Xmx 自控）。
    """
    parts = []
    if cpu:
        parts.append(f"-t {int(cpu)}")      # CPU 时间（秒）
    if mem_kb:
        parts.append(f"-v {int(mem_kb)}")   # 地址空间上限（KB）
    parts.append(f"-f {int(fsize_kb)}")     # 单个文件写入上限（KB）
    parts.append(f"-s {int(stack_kb)}")     # 栈上限（KB）
    parts.append(f"-n {int(nofile)}")       # 打开文件数
    parts.append("-c 0")                    # 不产生 core dump
    ulimits = "ulimit " + " ".join(parts)
    inner = " ".join(shlex.quote(str(a)) for a in argv)
    cmd = ["bash", "-c", f"{ulimits}; exec {inner}"]
    # 只有 root 才需要降权（普通用户本身已是低权限），且得有 setpriv。
    # 学生代码能读到 ojrunner 有权读的一切，所以服务能不用 root 就别用 root。
    if SANDBOX_UID is not None and os.geteuid() == 0 and shutil.which("setpriv"):
        cmd = ["setpriv", "--reuid", str(SANDBOX_UID),
               "--regid", str(SANDBOX_GID or SANDBOX_UID),
               "--clear-groups"] + cmd
    return cmd


def _write_files(workdir, files):
    if not files:
        return
    for name, content in (files or {}).items():
        # basename 消毒：files 由 coderun 传入，这里再拦一道防路径穿越
        safe = os.path.basename(str(name))
        if not safe:
            continue
        with open(os.path.join(workdir, safe), "w", encoding="utf-8") as f:
            f.write(str(content))


def _result(status, out, err, rc, sig, elapsed):
    return {"status": status, "stdout": out, "stderr": err,
            "exit_code": rc, "signal": sig, "time": elapsed}


def run(argv, stdin_data="", *, files=None, compile_argv=None,
        wall=10, compile_wall=20, cpu=None, mem_kb=None, fsize_kb=16384,
        stack_kb=8192, nofile=64, env=None, max_out=None, work_root=None):
    """
    在隔离临时目录里执行一段程序，返回运行结果。

    argv        要运行的程序及其参数（列表，不经过 shell）
    stdin_data  标准输入（str 或 bytes）
    files       预先写进沙箱目录的文件 {文件名: 内容}，如 {"main.py": code}
    compile_argv 可选：运行前先执行的编译命令（如 ["g++","main.cpp","-o","main"]）
    wall        运行阶段墙钟超时（秒）
    compile_wall 编译阶段墙钟超时（秒）
    cpu         POSIX 下 ulimit -t 的 CPU 时间（秒），None=不限
    mem_kb      POSIX 下 ulimit -v 地址空间（KB），None=不限
    fsize_kb / stack_kb / nofile  POSIX 下 ulimit 的其余限制
    max_out     输出截断上限（字符），None=不截断
    work_root   沙箱目录的父目录

    返回 {status, stdout, stderr, exit_code, signal, time}
      status ∈ ok / tle / mle / re / se
    """
    work_root = work_root or SANDBOX_WORK_ROOT
    os.makedirs(work_root, exist_ok=True)
    if IS_WIN:
        return _run_windows(argv, stdin_data, files, compile_argv, wall,
                            compile_wall, env, work_root, max_out, mem_kb)
    return _run_posix(argv, stdin_data, files, compile_argv, wall,
                      compile_wall, cpu, mem_kb, fsize_kb, stack_kb,
                      nofile, env, work_root, max_out)


# ==================== POSIX ====================
def _kill_tree_posix(p):
    try:
        os.killpg(os.getpgid(p.pid), signal.SIGKILL)
    except Exception:
        try:
            p.kill()
        except Exception:
            pass


def _run_posix(argv, stdin_data, files, compile_argv, wall, compile_wall,
               cpu, mem_kb, fsize_kb, stack_kb, nofile, env, work_root, max_out):
    if not shutil.which("bash"):
        return _result(SE, "", "服务器没有 bash，无法启用沙箱", -1, None, 0.0)
    workdir = tempfile.mkdtemp(prefix="oj_", dir=work_root)
    try:
        _write_files(workdir, files)
        base_env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "LANG": os.environ.get("LANG", "C.UTF-8"),
            "HOME": workdir,
            "TMPDIR": workdir,
        }
        if env:
            base_env.update(env)

        # 编译阶段（可选，C++ 用）。编译不限资源（g++ 本身吃内存），只限墙钟。
        if compile_argv:
            with tempfile.TemporaryFile() as out_file, tempfile.TemporaryFile() as err_file:
                try:
                    cp = subprocess.Popen(
                        compile_argv, cwd=workdir, stdin=subprocess.DEVNULL,
                        stdout=out_file, stderr=err_file, env=base_env,
                        start_new_session=True)
                except Exception as e:
                    return _result(SE, "", f"编译启动失败: {e}", -1, None, 0.0)
                out_b, err_b, timed_out, overflow = _wait_process(
                    cp, b"", out_file, err_file, compile_wall, max_out,
                    _kill_tree_posix)
            if timed_out:
                return _result(RE, "", "编译超时", None, None, compile_wall)
            if overflow:
                return _result(RE, _decode(out_b, workdir), "编译输出超过上限",
                               cp.returncode, None, compile_wall)
            if cp.returncode != 0:
                return _result(RE, _decode(out_b, workdir),
                               _decode(err_b, workdir),
                               cp.returncode, None, compile_wall)
            # 编译产物加可执行权限（降权运行 / ulimit -c 0 后仍需可执行位）
            for name in os.listdir(workdir):
                try:
                    os.chmod(os.path.join(workdir, name), 0o755)
                except Exception:
                    pass

        stdin_b = _to_bytes(stdin_data)
        start = time.monotonic()
        with tempfile.TemporaryFile() as out_file, tempfile.TemporaryFile() as err_file:
            try:
                p = subprocess.Popen(
                    _build_cmd(argv, cpu, mem_kb, fsize_kb, stack_kb, nofile),
                    cwd=workdir, stdin=subprocess.PIPE,
                    stdout=out_file, stderr=err_file, env=base_env,
                    start_new_session=True)
            except Exception as e:
                return _result(SE, "", f"启动失败: {e}", -1, None, 0.0)
            out_b, err_b, timed_out, overflow = _wait_process(
                p, stdin_b, out_file, err_file, wall, max_out, _kill_tree_posix)
        elapsed = time.monotonic() - start

        rc = p.returncode
        out = _decode(out_b, workdir)
        err_full = _decode(err_b, workdir)
        err = err_full
        if max_out:
            out, err = out[:max_out], err[:max_out]
        if overflow:
            return _result(RE, out, (err + "\n程序输出超过上限，已强制终止").strip(),
                           rc, None, elapsed)
        return _classify(rc, timed_out, out, err_full, elapsed)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _classify(rc, timed_out, out, err, elapsed):
    """根据退出码 + 信号 + stderr 判定状态"""
    if timed_out:
        return _result(TLE, out, err, rc, signal.SIGKILL, elapsed)
    if rc == 0:
        return _result(OK, out, err, rc, None, elapsed)
    if rc < 0:
        sig = -rc
        # SIGXCPU 是 ulimit -t 触发的；SIGKILL 可能是 OOM 或超时强杀
        status = TLE if sig in (signal.SIGKILL,
                                getattr(signal, "SIGXCPU", None)) else RE
        return _result(status, out, err, rc, sig, elapsed)
    if _looks_mle(err):
        return _result(MLE, out, err, rc, None, elapsed)
    return _result(RE, out, err, rc, None, elapsed)


def _wait_process(p, stdin_b, out_file, err_file, timeout, max_out, kill_tree):
    """输出重定向到磁盘并限大小，返回 (out, err, timed_out, overflow)。"""
    limit = max(65536, (int(max_out) * 4 + 1)) if max_out else 0

    def write_stdin():
        try:
            if stdin_b:
                p.stdin.write(stdin_b)
            p.stdin.close()
        except Exception:
            pass

    writer = threading.Thread(target=write_stdin, daemon=True)
    writer.start()
    deadline = time.monotonic() + timeout
    timed_out = False
    overflow = False
    while p.poll() is None:
        if limit:
            try:
                size = os.fstat(out_file.fileno()).st_size + os.fstat(err_file.fileno()).st_size
                if size > limit:
                    overflow = True
                    kill_tree(p)
                    break
            except OSError:
                pass
        left = deadline - time.monotonic()
        if left <= 0:
            timed_out = True
            kill_tree(p)
            break
        try:
            p.wait(timeout=min(0.05, left))
        except subprocess.TimeoutExpired:
            pass
    try:
        p.wait(timeout=5)
    except Exception:
        pass
    writer.join(timeout=1)
    if limit and not overflow:
        try:
            size = os.fstat(out_file.fileno()).st_size + os.fstat(err_file.fileno()).st_size
            overflow = size > limit
        except OSError:
            pass

    def read_limited(stream):
        try:
            stream.seek(0)
            data = stream.read(limit or -1)
        except Exception:
            data = b""
        return data

    return read_limited(out_file), read_limited(err_file), timed_out, overflow


class _WindowsJob:
    """用 Job Object 限制 Windows 子进程内存与子进程数量。"""

    def __init__(self, mem_kb, active_processes):
        import ctypes
        from ctypes import wintypes

        class Basic(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class IoCounters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

        class Extended(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", Basic), ("IoInfo", IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        self._ctypes = ctypes
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self._kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        self._kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        self._kernel32.SetInformationJobObject.restype = wintypes.BOOL
        self._kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self._kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        self._kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self._kernel32.CloseHandle.restype = wintypes.BOOL
        handle = self._kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise OSError(ctypes.get_last_error(), "CreateJobObjectW")
        self.handle = handle
        info = Extended()
        info.BasicLimitInformation.LimitFlags = 0x00000100 | 0x00000008 | 0x00002000
        info.BasicLimitInformation.ActiveProcessLimit = max(1, int(active_processes))
        if mem_kb:
            info.ProcessMemoryLimit = int(mem_kb) * 1024
        else:
            info.BasicLimitInformation.LimitFlags &= ~0x00000100
        ok = self._kernel32.SetInformationJobObject(
            handle, 9, ctypes.byref(info), ctypes.sizeof(info))
        if not ok:
            self.close()
            raise OSError(ctypes.get_last_error(), "SetInformationJobObject")

    def assign(self, process):
        if not self._kernel32.AssignProcessToJobObject(self.handle, process._handle):
            raise OSError(self._ctypes.get_last_error(), "AssignProcessToJobObject")

    def close(self):
        handle = getattr(self, "handle", None)
        if handle:
            self._kernel32.CloseHandle(handle)
            self.handle = None


# ==================== Windows ====================
def _kill_tree_windows(p):
    """Windows 没有 killpg：用 taskkill /T 连子进程一起杀"""
    try:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)],
                       capture_output=True, timeout=10,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception:
        pass
    try:
        p.kill()
    except Exception:
        pass


def _run_windows(argv, stdin_data, files, compile_argv, wall, compile_wall,
                 env, work_root, max_out, mem_kb=None):
    workdir = tempfile.mkdtemp(prefix="oj_", dir=work_root)
    job = None
    try:
        _write_files(workdir, files)
        base_env = {
            "PATH": os.environ.get("PATH", ""),
            "SystemRoot": os.environ.get("SystemRoot", ""),
            "TEMP": workdir,
            "TMP": workdir,
        }
        if env:
            base_env.update(env)
        no_window = {}
        if hasattr(subprocess, "CREATE_NO_WINDOW"):
            no_window["creationflags"] = subprocess.CREATE_NO_WINDOW
        try:
            compile_job = _WindowsJob(max(int(mem_kb or 524288), 524288), 16)
        except Exception as e:
            return _result(SE, "", f"无法启用 Windows 编译资源限制: {e}", -1, None, 0.0)
        try:
            if compile_argv:
                with tempfile.TemporaryFile() as out_file, tempfile.TemporaryFile() as err_file:
                    try:
                        cp = subprocess.Popen(compile_argv, cwd=workdir,
                                              stdin=subprocess.DEVNULL,
                                              stdout=out_file, stderr=err_file,
                                              env=base_env, **no_window)
                        compile_job.assign(cp)
                    except Exception as e:
                        return _result(SE, "", f"编译启动失败: {e}", -1, None, 0.0)
                    out_b, err_b, timed_out, overflow = _wait_process(
                        cp, b"", out_file, err_file, compile_wall, max_out,
                        _kill_tree_windows)
                if timed_out:
                    return _result(RE, "", "编译超时", None, None, compile_wall)
                if overflow:
                    return _result(RE, _decode(out_b, workdir), "编译输出超过上限",
                                   cp.returncode, None, compile_wall)
                if cp.returncode != 0:
                    return _result(RE, _decode(out_b, workdir),
                                   _decode(err_b, workdir),
                                   cp.returncode, None, compile_wall)
        finally:
            compile_job.close()

        stdin_b = _to_bytes(stdin_data)
        start = time.monotonic()
        try:
            job = _WindowsJob(mem_kb, 1)
        except Exception as e:
            return _result(SE, "", f"无法启用 Windows 运行资源限制: {e}", -1, None, 0.0)
        with tempfile.TemporaryFile() as out_file, tempfile.TemporaryFile() as err_file:
            try:
                p = subprocess.Popen(argv, cwd=workdir,
                                     stdin=subprocess.PIPE,
                                     stdout=out_file, stderr=err_file,
                                     env=base_env, **no_window)
                job.assign(p)
            except Exception as e:
                return _result(SE, "", f"启动失败: {e}", -1, None, 0.0)
            out_b, err_b, timed_out, overflow = _wait_process(
                p, stdin_b, out_file, err_file, wall, max_out, _kill_tree_windows)
        elapsed = time.monotonic() - start

        rc = p.returncode
        out = _decode(out_b, workdir)
        err_full = _decode(err_b, workdir)
        err = err_full
        if max_out:
            out, err = out[:max_out], err[:max_out]
        if overflow:
            err = (err + "\n程序输出超过上限，已强制终止").strip()
            status, sig = RE, None
        elif timed_out:
            status, sig = TLE, None
        elif rc == 0:
            status, sig = OK, None
        elif _looks_mle(err_full):
            status, sig = MLE, None
        else:
            status, sig = RE, None
        return _result(status, out, err, rc, sig, elapsed)
    finally:
        if job:
            job.close()
        shutil.rmtree(workdir, ignore_errors=True)
