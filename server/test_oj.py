"""
server/test_oj.py —— OJ 判题单元测试
======================================

覆盖 6 个关键场景（实测输出见每个测试方法里的 print）：

  · Python 正确 → True
  · Python 输出错 → False
  · Python 死循环 → TLE（不是崩溃、不是永久挂起）
  · x='a'*999999999 → MLE/RE，且服务器内存没有被打满   [仅 POSIX]
  · while True: os.fork() → 墙钟内被杀干净，ps 无残留   [仅 POSIX]
  · 服务器没有 g++ → 返回 None（不是 False、不是异常）

运行方式（在项目根目录）：
  python server/test_oj.py
  或
  python -m unittest server.test_oj -v
"""
import os
import shutil
import subprocess
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import coderun
import sandbox

POSIX = os.name == "posix"


def _py_q(code, expected_output, **kw):
    q = {"id": 1, "code": code, "language": "python",
         "stdin_data": "", "expected_output": expected_output}
    q.update(kw)
    return q


def _rss_kb():
    """当前进程 RSS（KB），仅 Linux。用于证明「内存没被打满」。"""
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1])
    except Exception:
        return 0
    return 0


class TestOJ(unittest.TestCase):
    # ---------- 1. Python 正确 → True ----------
    def test_python_correct(self):
        q = _py_q("s = 0\nfor i in ____:\n    s += ____\nprint(s)", "55")
        r = coderun.judge_code(q, "range(1,11)||i")
        print(f"  正确写法 range(1,11)||i -> {r!r}")
        self.assertIs(r, True)
        r2 = coderun.judge_code(q, "range(1, 11)||i")
        print(f"  等价写法 range(1, 11)||i -> {r2!r}")
        self.assertIs(r2, True)

    # ---------- 2. Python 输出错 → False ----------
    def test_python_wrong_output(self):
        q = _py_q("s = 0\nfor i in ____:\n    s += ____\nprint(s)", "55")
        r = coderun.judge_code(q, "range(1,10)||i")
        print(f"  错误写法 range(1,10)||i -> {r!r}（期望输出 55）")
        self.assertIs(r, False)

    # ---------- 3. Python 死循环 → TLE ----------
    def test_infinite_loop_tle(self):
        t0 = time.monotonic()
        r = sandbox.run([sys.executable, "main.py"],
                        files={"main.py": "while True:\n    pass"},
                        wall=2, cpu=2, max_out=8000)
        dt = time.monotonic() - t0
        print(f"  死循环 -> status={r['status']!r} 耗时={dt:.2f}s")
        self.assertEqual(r["status"], "tle")
        self.assertLess(dt, 10, "死循环必须在墙钟内被强杀，不能挂起")

    # ---------- 4. 内存炸弹 → MLE/RE 且不爆内存（仅 POSIX） ----------
    @unittest.skipUnless(POSIX, "ulimit 资源限制仅 POSIX")
    def test_memory_bomb(self):
        before = _rss_kb()
        r = sandbox.run(
            [sys.executable, "main.py"],
            files={"main.py": "x = 'a' * 999999999\nprint(len(x))"},
            wall=10, mem_kb=512000, max_out=8000)
        after = _rss_kb()
        first = (r["stderr"] or "").splitlines()[:1]
        print(f"  内存炸弹 -> status={r['status']!r} stderr首行={first}")
        print(f"  主进程内存 before={before}KB after={after}KB 增长={after - before}KB")
        self.assertIn(r["status"], ("mle", "re"))
        self.assertIn("memoryerror", (r["stderr"] or "").lower())
        self.assertLess(after - before, 300 * 1024,
                        "判题服务内存被学生代码打爆了")

    # ---------- 5. fork 炸弹 → 墙钟内杀干净（仅 POSIX） ----------
    @unittest.skipUnless(POSIX, "os.fork 仅 POSIX")
    def test_fork_bomb(self):
        t0 = time.monotonic()
        r = sandbox.run(
            [sys.executable, "main.py"],
            files={"main.py": "import os\nwhile True:\n    os.fork()\n"},
            wall=2, cpu=2, max_out=8000)
        dt = time.monotonic() - t0
        print(f"  fork炸弹 -> status={r['status']!r} 耗时={dt:.2f}s")
        self.assertEqual(r["status"], "tle")
        self.assertLess(dt, 10, "fork 炸弹必须在墙钟内被连根杀掉")
        try:
            out = subprocess.check_output(["pgrep", "-f", "main.py"],
                                          timeout=5).decode().strip()
            leftover = [l for l in out.splitlines() if l.strip()]
            print(f"  残留进程检查 -> {leftover if leftover else '无残留'}")
            self.assertEqual(leftover, [], f"fork 残留进程未杀干净: {leftover}")
        except subprocess.CalledProcessError:
            print("  残留进程检查 -> 无残留")

    # ---------- 6. 无 g++ → None ----------
    def test_no_cpp_compiler(self):
        has_cc = shutil.which("g++") or shutil.which("clang++")
        print(f"  本机 g++/clang++: {has_cc or '未安装'}")
        q = {"id": 1, "code": "int main(){ int x = ____; return x; }",
             "language": "cpp", "stdin_data": "", "expected_output": "x",
             "answer": "0"}
        if not has_cc:
            r = coderun.judge_code(q, "0")
            print(f"  无编译器实测 -> {r!r}")
            self.assertIsNone(r)
        # 无论环境如何，用 mock 保证稳定性：无编译器必须是 None（不是 False/异常）
        with mock.patch.object(coderun, "_find_cpp", return_value=None):
            r = coderun.judge_code(q, "0")
            print(f"  mock 无编译器 -> {r!r}")
            self.assertIsNone(r)


if __name__ == "__main__":
    unittest.main(verbosity=2)
