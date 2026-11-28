# -*- coding: utf-8 -*-
"""安装包直接脚本入口在隔离 Python 环境下也能完成顶层导入。"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main():
    code = (
        "import runpy, sys; "
        "runpy.run_path(sys.argv[1], run_name='ikeexam_entrypoint_smoke')"
    )
    with tempfile.TemporaryDirectory(prefix="ikeexam_entrypoint_test_") as root:
        env = os.environ.copy()
        env["EXAM_DATA"] = root
        env["EXAM_WORK"] = os.path.join(root, "work")
        for rel in ("src/ikeexam/teacher/teacher_app.py",
                    "src/ikeexam/client/exam_client.py"):
            result = subprocess.run(
                [sys.executable, "-I", "-c", code, str(ROOT / rel)],
                cwd=root, env=env, capture_output=True, text=True,
                timeout=30, encoding="utf-8", errors="replace")
            assert result.returncode == 0, (
                rel, result.returncode, result.stdout, result.stderr)
    print("[OK] 学生端与老师端直接脚本入口导入通过")


if __name__ == "__main__":
    main()
