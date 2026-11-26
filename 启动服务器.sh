#!/usr/bin/env bash
# ==========================================
#  ikee考务中心 服务器 —— 一键启动
# ==========================================
# 用法：
#   ./启动服务器.sh                    # 默认 9000 端口，自动探测 IP
#   ./启动服务器.sh --lan-ip 19.168.0.1
#   ./启动服务器.sh --port 8080
#
# 会依次：创建虚拟环境 → 装依赖 → 开防火墙 → 启动
# 首次启动自动导入题库 + 创建测试考试
# ==========================================

cd "$(dirname "$0")"

PROJECT_DIR="$(pwd)"
VENV_DIR="$PROJECT_DIR/venv"
SERVER_DIR="$PROJECT_DIR/src/ikeexam/server"

LAN_IP=""
PORT=9000
NAME="ikee考务中心"
NO_BEACON=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --lan-ip) LAN_IP="$2"; shift 2;;
        --port)   PORT="$2"; shift 2;;
        --name)   NAME="$2"; shift 2;;
        --no-beacon) NO_BEACON="--no-beacon"; shift;;
        *) echo "未知参数: $1"; exit 1;;
    esac
done

echo "=========================================="
echo "  ikee考务中心 服务器启动"
echo "=========================================="
echo

# ---------- 1. 检查 Python ----------
echo "[1/5] 检查 Python..."
if ! command -v python3 >/dev/null 2>&1; then
    echo "  ❌ 没找到 python3，请先安装: sudo apt install python3 python3-venv"
    exit 1
fi
echo "  $(python3 --version 2>&1)"

# ---------- 2. 创建虚拟环境 + 安装依赖 ----------
echo
echo "[2/5] 准备虚拟环境..."

# 如果 venv 存在但 python3 不存在，说明是坏的（比如从 Windows 拷过来的），删掉重建
if [ -d "$VENV_DIR" ] && [ ! -f "$VENV_DIR/bin/python3" ]; then
    echo "  ⚠️  检测到无效的 venv，正在删除重建..."
    rm -rf "$VENV_DIR"
fi

if [ ! -d "$VENV_DIR" ]; then
    echo "  正在创建虚拟环境..."
    python3 -m venv "$VENV_DIR"
    if [ $? -ne 0 ]; then
        echo "  ❌ 创建虚拟环境失败，请先安装: sudo apt install python3-venv"
        exit 1
    fi
    echo "  ✅ 虚拟环境已创建"
else
    echo "  ✅ 虚拟环境已存在"
fi

# 直接用 venv 里的 python 和 pip，不依赖 source activate
VENV_PYTHON="$VENV_DIR/bin/python3"
VENV_PIP="$VENV_DIR/bin/pip"

echo "  检查依赖..."
"$VENV_PIP" install -q flask openpyxl python-docx python-pptx pyyaml psutil 2>&1 | grep -v "^$" || true
echo "  ✅ 依赖就绪"

# ---------- 3. 探测本机 IP ----------
echo
echo "[3/5] 探测网络..."
if [ -z "$LAN_IP" ]; then
    LAN_IP=$("$VENV_PYTHON" -c "
import socket
try:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.connect(('10.255.255.255', 1))
    ip = s.getsockname()[0]
    s.close()
    print(ip)
except Exception:
    print('127.0.0.1')
")
fi
echo "  对外地址: http://$LAN_IP:$PORT"

# ---------- 4. 防火墙 ----------
echo
echo "[4/5] 防火墙..."
if command -v ufw >/dev/null 2>&1; then
    sudo ufw allow "$PORT"/tcp 2>/dev/null \
        && echo "  ✅ 已放行 TCP $PORT" \
        || echo "  ⚠️  放行失败（无 sudo？忽略）"
    sudo ufw allow 39100/udp 2>/dev/null \
        && echo "  ✅ 已放行 UDP 39100" \
        || echo "  ⚠️  放行失败（忽略）"
elif command -v firewall-cmd >/dev/null 2>&1; then
    sudo firewall-cmd --add-port="$PORT"/tcp --permanent 2>/dev/null \
        && sudo firewall-cmd --add-port=39100/udp --permanent 2>/dev/null \
        && sudo firewall-cmd --reload 2>/dev/null \
        && echo "  ✅ firewalld 已放行" \
        || echo "  ⚠️  放行失败（忽略）"
else
    echo "  ⚠️  未找到 ufw/firewalld，请手动放行 TCP $PORT 与 UDP 39100"
fi

# ---------- 5. 启动 ----------
echo
echo "[5/5] 启动服务..."
echo
echo "=========================================="
echo "  老师访问: http://$LAN_IP:$PORT"
echo "  首次运行：打开后台页面后按提示设置初始密码"
echo "  学生端:   自动发现，无需配置"
echo "  Ctrl+C 停止服务"
echo "=========================================="
echo

cd "$SERVER_DIR"
exec "$VENV_PYTHON" app.py --port "$PORT" --lan-ip "$LAN_IP" --name "$NAME" $NO_BEACON