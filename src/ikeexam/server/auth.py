"""
老师端登录保护
--------------------------------
后台页面需要密码才能进，学生端 API 不受影响。

密码存在 server/auth.json（存哈希，不明文）。
首次运行**不设默认密码**：老师第一次打开后台页面时会被引导到「设置初始密码」页，
自己设定密码后才能进入。彻底避免「默认密码人人可见」的开源泄露风险。

改密码两种方式：
  1. 后台「修改密码」页面（推荐）
  2. 命令行：python3 auth.py --set "新密码"
"""
import json
import os
import sys
import time

from werkzeug.security import generate_password_hash, check_password_hash

BASE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(BASE)))
# 密码文件的位置：
#   打包成 EXE 后必须写到 exe 旁边（解包目录每次启动都变，写进去等于每次重置密码）
#   app.py 启动时会设置 EXAM_AUTH_FILE 指向可写目录
AUTH_FILE = (os.environ.get("EXAM_AUTH_FILE", "").strip()
             or os.path.join(PROJECT_ROOT, "auth.json"))

# 登录失败限制：同一 IP 连续失败 5 次后，锁定 60 秒
MAX_FAILS = 5
LOCK_SECONDS = 60

_fails = {}          # ip -> [次数, 上次失败时间]


# ---------------- 密码存取 ----------------
def _read():
    try:
        if os.path.isfile(AUTH_FILE):
            with open(AUTH_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        # 不能静默 —— 读不出来会回退到默认密码，必须让人看到
        print(f"⚠️ 密码文件读取失败: {AUTH_FILE} ({e})", file=sys.stderr)
    return {}


def _write(data):
    with open(AUTH_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def ensure():
    """
    确保密码可用。返回 (密码哈希或 None, 是否有变化)。
    无密码时**不建默认密码**，保持「未初始化」，让老师首次访问时自己设置。
    环境变量 EXAM_ADMIN_PASSWORD 存在时用它重置（用于忘了密码救急）。
    """
    d = _read()
    env = os.environ.get("EXAM_ADMIN_PASSWORD", "").strip()
    if env:
        h = generate_password_hash(env)
        data = _read()
        data.update({"hash": h, "updated_at": time.time(), "source": "env"})
        _write(data)
        return h, True
    if d.get("hash"):
        return d["hash"], False
    if os.path.isfile(AUTH_FILE) and not d.get("passwordless_local"):
        # 文件存在但读不出哈希 = 已损坏。备份后进入未初始化，让老师重新设置，
        # 不静默覆盖老师的密码。仅启用本机免密码时允许暂时没有哈希。
        bak = AUTH_FILE + ".bak"
        try:
            os.replace(AUTH_FILE, bak)
            print(f"⚠️ 密码文件已损坏，已备份到 {bak}。请重新设置初始密码。",
                  file=sys.stderr)
        except OSError:
            pass
    return None, False


def get_hash():
    return ensure()[0]


def needs_setup():
    """是否还没设置过密码（首次运行，需引导老师设置初始密码）"""
    return not bool(_read().get("hash"))


def is_default():
    """是否仍是默认密码（历史兼容：现在无默认密码概念，恒为 False）"""
    return bool(_read().get("default"))


def updated_at():
    return _read().get("updated_at")


def set_password(plain):
    """设置新密码（首次设置与修改共用）"""
    data = _read()
    data.update({"hash": generate_password_hash(plain),
                 "updated_at": time.time()})
    _write(data)
    return True


def passwordless_local():
    return bool(_read().get("passwordless_local"))


def set_passwordless(enabled):
    data = _read()
    data["passwordless_local"] = bool(enabled)
    data["updated_at"] = time.time()
    _write(data)
    return True


def verify(plain):
    h = get_hash()
    if not h:
        return False
    return check_password_hash(h, plain or "")


# ---------------- 失败限制 ----------------
def _ip():
    """
    取客户端 IP。不在请求上下文里时（如命令行调用）返回 'cli'。

    ⚠️ 只用 remote_addr：以前优先信 X-Real-IP 头，而请求头可伪造 ——
    攻击者每次带个随机 X-Real-IP 就能绕过登录锁定无限试密码。
    """
    try:
        from flask import request
        return request.remote_addr or "?"
    except Exception:
        return "cli"


def locked():
    """当前 IP 是否被锁，返回剩余秒数（0 = 没锁）"""
    ip = _ip()
    rec = _fails.get(ip)
    if not rec or rec[0] < MAX_FAILS:
        return 0
    left = LOCK_SECONDS - (time.time() - rec[1])
    return int(max(0, left))


def note_fail():
    ip = _ip()
    n, _t = _fails.get(ip, (0, 0))
    now = time.time()
    # 距上次失败超过锁定时间就重新计数
    if now - _t > LOCK_SECONDS:
        n = 0
    _fails[ip] = (n + 1, now)
    # 防止字典无限增长：超过阈值就清掉过期的记录
    if len(_fails) > 2000:
        for k, (cnt, ts) in list(_fails.items()):
            if now - ts > LOCK_SECONDS * 2:
                _fails.pop(k, None)
    return MAX_FAILS - (n + 1)      # 还剩几次机会


def note_ok():
    _fails.pop(_ip(), None)


def remaining_attempts():
    ip = _ip()
    rec = _fails.get(ip)
    if not rec:
        return MAX_FAILS
    if time.time() - rec[1] > LOCK_SECONDS:
        return MAX_FAILS
    return max(0, MAX_FAILS - rec[0])


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="老师端密码管理")
    p.add_argument("--set", metavar="密码", help="设置新密码")
    p.add_argument("--show", action="store_true", help="查看状态")
    a = p.parse_args()

    if a.set:
        if len(a.set) < 6:
            print("❌ 密码太短，至少 6 位")
            raise SystemExit(1)
        set_password(a.set)
        print("✅ 密码已更新（不回显明文，防止投屏/日志泄露）")
        print(f"   存储位置: {AUTH_FILE}")
    elif a.show:
        d = _read()
        print(f"密码文件: {AUTH_FILE}")
        print(f"存在: {os.path.isfile(AUTH_FILE)}")
        print(f"仍是默认密码: {bool(d.get('default'))}")
        if d.get("updated_at"):
            print(f"上次修改: {time.strftime('%Y-%m-%d %H:%M', time.localtime(d['updated_at']))}")
    else:
        ensure()
        if needs_setup():
            print("Auth 就绪。尚未设置密码：请打开后台页面，按提示设置初始密码。")
        else:
            print("Auth 就绪。密码已设置。")
        print("改密码: python3 auth.py --set \"新密码\"")
