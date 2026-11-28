# -*- coding: utf-8 -*-
"""老师端本机服务自动启动路径回归。"""
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

from ikeexam.teacher import teacher_app as mod


def main():
    with tempfile.TemporaryDirectory(prefix="ikeexam_service_test_") as root:
        package = Path(root) / "src" / "ikeexam"
        server_dir = package / "server"
        runtime_dir = Path(root) / "runtime"
        server_dir.mkdir(parents=True)
        runtime_dir.mkdir(parents=True)
        (server_dir / "app.py").write_text("", encoding="utf-8")
        (runtime_dir / "pythonw.exe").write_bytes(b"")

        api = SimpleNamespace(ping=mock.Mock(side_effect=[
            {"ok": False, "msg": "offline"},
            {"ok": True, "version": "2.2.1"},
        ]))
        app = object.__new__(mod.TeacherApp)
        app.api = api
        process = SimpleNamespace(poll=mock.Mock(return_value=None))
        with mock.patch.object(mod, "PROJECT_ROOT", root), \
                mock.patch.object(mod, "PACKAGE_DIR", str(package)), \
                mock.patch.object(mod.subprocess, "Popen", return_value=process) as popen:
            result = mod.TeacherApp._ensure_server_sync(
                app, "http://127.0.0.1:9123")

        assert result.get("ok"), result
        kwargs = popen.call_args.kwargs
        assert kwargs["cwd"] == str(server_dir), kwargs["cwd"]
        assert not os.path.isdir(os.path.join(root, "server"))
        print("[OK] 本机服务使用真实 server 目录启动")


if __name__ == "__main__":
    main()
