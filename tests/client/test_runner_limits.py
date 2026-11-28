# -*- coding: utf-8 -*-
"""学生端本地运行器输出与超时限制回归。"""
import os
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src", "ikeexam", "client"))

from runner import MAX_OUT, _run


def main():
    with tempfile.TemporaryDirectory(prefix="exam_runner_test_") as workdir:
        result = _run([sys.executable, "-c", "print('ok')"], workdir, 3)
        assert result["ok"] and result["out"].strip() == "ok", result

        result = _run([sys.executable, "-c", "print('x' * 40000)"], workdir, 3)
        assert not result["ok"] and result["overflow"], result
        assert len(result["out"]) <= MAX_OUT, len(result["out"])

        result = _run([sys.executable, "-c", "import time; time.sleep(5)"], workdir, 0.2)
        assert not result["ok"] and result["timed_out"], result
    print("[OK] 本地运行器限制输出缓冲并在超时后终止")


if __name__ == "__main__":
    main()
