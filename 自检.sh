#!/usr/bin/env bash
set -u
cd "$(dirname "$0")"

PYTHON_BIN=""
for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" --version >/dev/null 2>&1; then
        PYTHON_BIN="$candidate"
        break
    fi
done
if [ -z "$PYTHON_BIN" ]; then
    echo "[FAIL] 未找到可用的 Python 3"
    exit 1
fi

"$PYTHON_BIN" - "${1:-}" "${2:-9000}" <<'PYEOF'
import importlib
import json
import os
from pathlib import Path
import socket
import sqlite3
import sys
import urllib.request

server = sys.argv[1]
port = int(sys.argv[2])
root = Path.cwd()
errors = []
warnings = []


def ok(message):
    print(f"  [OK] {message}")


def fail(message):
    errors.append(message)
    print(f"  [FAIL] {message}")


def warn(message):
    warnings.append(message)
    print(f"  [WARN] {message}")


print("==========================================")
print("  ikee考务中心 部署自检")
print(f"  服务器: http://{server or '本机'}:{port}")
print("==========================================")

print("\n1. Python 环境")
ok(f"{sys.version.split()[0]} ({sys.executable})")

print("\n2. Python 依赖")
for module, name in {"flask": "Flask", "openpyxl": "openpyxl",
                     "docx": "python-docx", "pptx": "python-pptx",
                     "psutil": "psutil"}.items():
    try:
        importlib.import_module(module)
        ok(name)
    except Exception as exc:
        fail(f"{name} 不可用: {exc}")

print("\n3. 文件完整性")
for relative in ("src/ikeexam/server/app.py", "src/ikeexam/server/db.py",
                 "src/ikeexam/server/beacon.py", "src/ikeexam/client/exam_client.py",
                 "src/ikeexam/client/gui.py", "src/ikeexam/client/discover.py",
                 "src/ikeexam/teacher/teacher_app.py", "src/ikeexam/teacher/api.py",
                 "src/ikeexam/graders/__init__.py", "src/ikeexam/server/auto_rule.py",
                 "src/ikeexam/server/plagiarism.py", "src/ikeexam/shared/ui_theme.py"):
    if (root / relative).is_file():
        ok(relative)
    else:
        fail(f"缺失 {relative}")

print("\n4. 数据库（只读）")
db_path = root / "exam.db"
if not db_path.is_file():
    warn("尚未初始化，首次启动会自动创建")
else:
    try:
        con = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
        integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_keys = con.execute("PRAGMA foreign_key_check").fetchall()
        questions = con.execute("SELECT COUNT(*) FROM questions").fetchone()[0]
        students = con.execute("SELECT COUNT(*) FROM students").fetchone()[0]
        con.close()
        if integrity == "ok" and not foreign_keys:
            ok(f"完整性正常，题库 {questions} 题，学生机 {students} 台")
        else:
            fail(f"完整性={integrity}，外键错误={len(foreign_keys)}")
    except Exception as exc:
        fail(f"数据库不可读: {exc}")

print("\n5. 本机端口")
try:
    with socket.create_connection(("127.0.0.1", port), timeout=2):
        ok(f"本机 TCP {port} 可连接")
except Exception:
    warn(f"本机 TCP {port} 未监听（服务未启动或不在本机运行）")

print("\n6. 服务器连通性")
if not server:
    warn("未指定服务器 IP，跳过连通性检查")
else:
    try:
        with socket.create_connection((server, port), timeout=3):
            ok(f"TCP {server}:{port} 可达")
        with urllib.request.urlopen(f"http://{server}:{port}/api/ping", timeout=4) as response:
            payload = json.load(response)
        if payload.get("service") == "exam":
            ok(f"考试服务 {payload.get('name', '')}")
        else:
            fail("端口返回的不是考试服务")
    except Exception as exc:
        fail(f"连通性检查失败: {exc}")

print("\n7. 自动发现端口")
try:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.bind(("", 39100))
    ok("UDP 39100 可绑定")
    sock.close()
except Exception as exc:
    warn(f"UDP 39100 不可用: {exc}（服务已占用时属正常）")

print("\n8. 广播探测")
try:
    sys.path.insert(0, str(root / "client"))
    import discover
    found = discover.probe(timeout=2.5)
    if found:
        for item in found:
            ok(f"发现 {item.get('http')} ({item.get('name', '')})")
    else:
        warn("2.5 秒内未收到广播（服务未启动时属正常）")
except Exception as exc:
    warn(f"广播探测不可用: {exc}")

print("\n==========================================")
print(f"自检完成：{len(errors)} 项失败，{len(warnings)} 项警告")
print("==========================================")
if errors:
    print("下一步：修复上述 [FAIL] 项后再启动服务。")
    sys.exit(1)
print("下一步：启动服务后，浏览器打开 http://<服务器IP>:%d" % port)
PYEOF
