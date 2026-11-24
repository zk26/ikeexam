#!/usr/bin/env bash
# ==========================================
#  部署自检 —— 逐项检查环境是否就绪
# ==========================================
# 用法：./自检.sh [服务器IP]
# 例：  ./自检.sh 192.168.1.100
# 不传 IP 时跳过连通性检查（只查本机环境）
# ==========================================

cd "$(dirname "$0")"
SRV="${1:-}"
PORT="${2:-9000}"

pass=0; fail=0
ok()   { echo "  ✅ $1"; pass=$((pass+1)); }
bad()  { echo "  ❌ $1"; fail=$((fail+1)); }
warn() { echo "  ⚠️  $1"; }

echo "=========================================="
echo "  ikee考务中心 部署自检"
echo "  服务器: http://$SRV:$PORT"
echo "=========================================="

# ---------- 1. Python ----------
echo
echo "1. Python 环境"
if command -v python3 >/dev/null 2>&1; then
    ok "python3 $(python3 --version 2>&1 | cut -d' ' -f2)"
else
    bad "没有 python3"
fi

# ---------- 2. 依赖 ----------
echo
echo "2. Python 依赖"
python3 - << 'PYEOF'
import importlib
need = {"flask": "Flask", "openpyxl": "openpyxl",
        "docx": "python-docx", "pptx": "python-pptx",
        "psutil": "psutil"}
for mod, name in need.items():
    try:
        importlib.import_module(mod)
        print(f"  ✅ {name}")
    except ImportError:
        print(f"  ❌ {name} 未安装")
PYEOF

# ---------- 3. 文件完整性 ----------
echo
echo "3. 文件完整性"
for f in server/app.py server/db.py server/beacon.py \
         client/exam_client.py client/gui.py client/discover.py \
         graders/__init__.py auto_rule.py plagiarism.py; do
    if [ -f "$f" ]; then ok "$f"; else bad "缺失 $f"; fi
done

# ---------- 4. 数据库 ----------
echo
echo "4. 数据库"
python3 - << 'PYEOF'
import os, sys
sys.path.insert(0, "server")
p = "exam.db"
if not os.path.isfile(p):
    print("  ⚠️  尚未初始化（首次启动会自动创建）")
else:
    try:
        import db
        db.init()
        c = db.conn()
        n = c.execute("SELECT COUNT(*) FROM questions").fetchone()[0]
        print(f"  ✅ 数据库正常，题库 {n} 题")
        st = c.execute("SELECT COUNT(*) FROM students").fetchone()[0]
        print(f"     已登记学生机 {st} 台")
    except Exception as e:
        print(f"  ❌ 数据库异常: {e}")
PYEOF

# ---------- 5. 端口占用 ----------
echo
echo "5. 本机端口"
if command -v ss >/dev/null 2>&1; then
    if ss -tlnp 2>/dev/null | grep -q ":$PORT "; then
        ok "本机 TCP $PORT 已在监听（服务已启动）"
    else
        warn "本机 TCP $PORT 未监听（服务未启动？或不在本机运行）"
    fi
else
    warn "没有 ss 命令，跳过端口检查"
fi

# ---------- 6. 连通性 ----------
echo
echo "6. 服务器连通性"
if [ -z "$SRV" ]; then
  warn "未指定服务器 IP（用法：./自检.sh <服务器IP>），跳过连通性检查"
else
python3 - "$SRV" "$PORT" << 'PYEOF'
import sys, json, socket, urllib.request
srv, port = sys.argv[1], sys.argv[2]
# 先看 TCP 通不通
s = socket.socket(); s.settimeout(2)
try:
    s.connect((srv, int(port)))
    print(f"  ✅ TCP {port} 可达")
except Exception as e:
    print(f"  ❌ TCP {port} 不通: {e}")
    print("     检查：服务器启动了？防火墙放行了吗？IP 对吗？")
    sys.exit(0)
finally:
    s.close()
# 再看是不是考试服务器
try:
    with urllib.request.urlopen(f"http://{srv}:{port}/api/ping", timeout=3) as r:
        d = json.loads(r.read())
        if d.get("service") == "exam":
            print(f"  ✅ 确实是考试服务器（{d.get('name','')}）")
        else:
            print("  ⚠️  端口开着，但返回的不是考试系统")
except Exception as e:
    print(f"  ❌ HTTP 请求失败: {e}")
PYEOF
fi

# ---------- 7. 广播端口 ----------
echo
echo "7. 自动发现（UDP 39100）"
python3 - << 'PYEOF'
import socket
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
s.settimeout(0.3)
try:
    s.bind(("", 39100))
    print("  ✅ 端口可绑定（未被占用）")
except Exception as e:
    print(f"  ⚠️  端口不可用: {e}")
    print("     （若考试服务已在运行且占用，属正常）")
s.close()
PYEOF

# ---------- 8. 服务名广播 ----------
echo
echo "8. 能否收到服务器广播"
python3 - << 'PYEOF'
import sys, time
sys.path.insert(0, "client")
try:
    import discover
    t0 = time.time()
    got = discover.probe(timeout=2.5)
    if got:
        for g in got:
            print(f"  ✅ 发现 {g.get('http')}（{g.get('name','')}）")
    else:
        print("  ⚠️  2.5 秒内没收到广播")
        print("     若服务未启动属正常；已启动则检查 UDP 39100 / VLAN")
except Exception as e:
    print(f"  ❌ 探测失败: {e}")
PYEOF

echo
echo "=========================================="
echo "  自检完成"
echo "=========================================="
echo
echo "下一步："
echo "  1. ./启动服务器.sh 启动服务"
if [ -z "$SRV" ]; then
  echo "  2. 浏览器打开 http://<服务器IP>:$PORT"
else
  echo "  2. 浏览器打开 http://$SRV:$PORT"
fi
echo "  3. 题库管理 → 导入 Excel（题库/题库_WPS三件套.xlsx）"
echo
