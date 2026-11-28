"""
客户端自动发现服务器
--------------------------------
不写死 IP，学生端自己找服务器。三种手段依次尝试，谁先成功用谁：

  1. 缓存地址   —— 上次连过的，最快
  2. 被动监听   —— 听服务器每 3 秒一次的广播（服务器 beacon）
  3. 主动探测   —— 广播一声"服务器在哪"，服务器单播回应

对外接口（exam_client.py 用）：
    try_url(url, timeout)          -> bool
    find_server(timeout, on_status) -> url 或 None
    save_cache(url) / load_cache() / clear_cache()
"""
import json
import os
import socket
import sys
import threading
import time
import urllib.request

from ikeexam.shared.security import MAX_JSON_BYTES, normalize_origin, origin_key, read_limited

# 与服务端 beacon.py 保持一致
BEACON_PORT = 39100
MAGIC = "EXAM-SERVER-V1"


def _app_dir():
    """打包成 onefile exe 后 __file__ 在临时解包目录，缓存要写到 exe 旁边才留得住"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


CACHE_FILE = os.path.join(_app_dir(), ".server_cache")


# ---------------- 基础网络 ----------------
def _broadcast_addrs():
    """本机各网卡的广播地址 + 全局广播"""
    addrs = {"255.255.255.255"}
    try:
        import psutil
        for _iface, snics in psutil.net_if_addrs().items():
            for snic in snics:
                if snic.family == socket.AF_INET and snic.broadcast_address:
                    addrs.add(snic.broadcast_address)
    except Exception:
        pass
    return sorted(addrs)


def _sock(broadcast=False, bind_port=0, timeout=0.4):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    except Exception:
        pass
    if broadcast:
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        except Exception:
            pass
    try:
        s.bind(("", bind_port))
    except OSError:
        # 端口被占（比如别的客户端已经在听）时不能再静默：返回 None，
        # 让调用方知道这次没绑上，而不是假装在听、永远收不到广播
        try:
            s.close()
        except Exception:
            pass
        return None
    s.settimeout(timeout)
    return s


def _parse(data, addr):
    """
    解析服务器回包。
    UDP 包的源地址一定是对的（服务器多网卡时，它自己报的 IP 未必是
    学生机能通的那个），所以两个都留着，后面挨个试。
    """
    try:
        info = json.loads(data.decode("utf-8"))
    except Exception:
        return None
    if info.get("magic") != MAGIC:
        return None
    if addr:
        info["src"] = addr[0]
    if not info.get("http"):
        ip = addr[0] if addr else None
        if not ip:
            return None
        info["http"] = f"http://{ip}:{info.get('port', 9000)}"
    return info


def _candidates(info):
    """一个服务器信息可能对应的所有 http 地址（按可信度排序）"""
    out = []
    if info.get("http"):
        out.append(info["http"])
    if info.get("src"):
        out.append(f"http://{info['src']}:{info.get('port', 9000)}")
    # 去重保序
    seen, uniq = set(), []
    for u in out:
        if u not in seen:
            seen.add(u)
            uniq.append(u)
    return uniq


# ---------------- 三种发现手段 ----------------
def listen_once(timeout=2.5, port=BEACON_PORT):
    """
    被动听服务器的定时广播。
    服务器每 3 秒喊一次，所以 2.5 秒内基本能听到。
    无法绑定端口（被占用/防火墙）时安静返回空。
    """
    found = {}
    s = _sock(broadcast=False, bind_port=port, timeout=0.4)
    if s is None:
        return []          # 端口被占（可能别的客户端在听），安静返回空
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            data, addr = s.recvfrom(4096)
        except socket.timeout:
            continue
        except Exception:
            break
        info = _parse(data, addr)
        if info:
            found[info["http"]] = info
    try:
        s.close()
    except Exception:
        pass
    return list(found.values())


def probe(timeout=2.0, port=BEACON_PORT, tries=2):
    """
    主动广播探测 —— 最可靠。
    发出去是出站广播，回来的包属于"已请求的返回流量"，
    Windows 防火墙一般放行。
    """
    found = {}
    msg = json.dumps({"cmd": "discover", "magic": MAGIC}).encode("utf-8")

    for _ in range(tries):
        s = _sock(broadcast=True, bind_port=0, timeout=0.35)
        if s is None:
            continue        # 极罕见（随机端口也被占），跳过这次尝试
        try:
            for addr in _broadcast_addrs():
                try:
                    s.sendto(msg, (addr, port))
                except Exception:
                    continue
            t0 = time.time()
            while time.time() - t0 < (timeout / tries):
                try:
                    data, from_addr = s.recvfrom(4096)
                except socket.timeout:
                    continue
                except Exception:
                    break
                info = _parse(data, from_addr)
                if info:
                    found[info["http"]] = info
        finally:
            try:
                s.close()
            except Exception:
                pass
        if found:
            break
    return list(found.values())


def try_url(url, timeout=2):
    """某个 http 地址是不是活的考试服务器"""
    if not url:
        return False
    try:
        base = normalize_origin(url, allow_plain_http=True)
        req = urllib.request.Request(base + "/api/ping", method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = read_limited(r, MAX_JSON_BYTES)
            d = json.loads(raw.decode("utf-8"))
        return bool(d.get("ok")) and d.get("service") == "exam"
    except Exception:
        return False


# ---------------- 缓存 ----------------
def save_cache(url):
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            f.write(url)
    except Exception:
        pass


def load_cache():
    try:
        if os.path.isfile(CACHE_FILE):
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                return f.read().strip() or None
    except Exception:
        pass
    return None


def clear_cache():
    try:
        if os.path.isfile(CACHE_FILE):
            os.remove(CACHE_FILE)
    except Exception:
        pass


# ---------------- 总入口 ----------------
def find_server(timeout=8, on_status=None, port=BEACON_PORT, fallback=None,
                allowed_origins=None, allow_discovered=False):
    """
    找服务器，依次尝试：缓存 → 被动监听 → 主动探测 → 备用直连地址
    :param fallback: 实在找不到时硬试的地址（学生手动输入过的服务器地址）
    :param allowed_origins: 已确认过的源；空列表不会自动信任发现结果
    :param allow_discovered: 用户明确启用自动发现时，允许连接通过考试服务指纹的服务器
    :return: url 字符串，找不到返回 None

    注意：收到广播本身就说明服务器在网络上存在。
    如果它的 HTTP 端口连不通（多半是防火墙），仍然返回地址，
    让上层去连并报出明确错误 —— 比直接说"找不到"更好排查。
    """
    def say(message):
        if on_status:
            try:
                on_status(message)
            except Exception:
                pass

    allowed = set()
    for value in allowed_origins or ():
        try:
            allowed.add(origin_key(value, allow_plain_http=True))
        except ValueError:
            pass
    fallback_origin = ""
    if fallback:
        try:
            fallback_origin = origin_key(fallback, allow_plain_http=True)
            allowed.add(fallback_origin)
        except ValueError:
            pass
    if not allowed and not allow_discovered:
        say("请手动输入并确认服务器地址；不会自动连接未确认的服务器。")
        return None

    def approved(url):
        try:
            return origin_key(url, allow_plain_http=True) in allowed
        except ValueError:
            return False

    heard = []          # 听到过但 HTTP 不通的，留着兜底

    # 1. 上次连过的
    cached = load_cache()
    if cached and approved(cached):
        say(f"尝试上次连接过的 {cached} …")
        if try_url(cached, timeout=1.5):
            say(f"✅ 已连接 {cached}")
            return cached
        say("   连不上，重新寻找…")

    # 2. 听广播（快，通常 1~3 秒）
    say("正在监听服务器广播…")
    for info in listen_once(timeout=min(3.0, timeout * 0.4), port=port):
        heard.append(info)
        for url in _candidates(info):
            if (approved(url) or allow_discovered) and try_url(url, timeout=2):
                say(f"✅ 找到 {url}")
                if info.get("name"):
                    say(f"   服务器名：{info['name']}")
                return url

    # 3. 主动喊话（兜底）
    say("正在主动搜索服务器…")
    for info in probe(timeout=min(4.0, timeout * 0.6), port=port):
        heard.append(info)
        for url in _candidates(info):
            if (approved(url) or allow_discovered) and try_url(url, timeout=2):
                say(f"✅ 找到 {url}")
                return url

    # 4. 听到了服务器，但 HTTP 不通 —— 多半是防火墙
    if heard:
        url = next((info.get("http") for info in heard
                    if approved(info.get("http")) or allow_discovered), None)
        if not url:
            say("发现的服务器未确认，请手动输入地址。")
        else:
            say(f"⚠️ 发现服务器 {url}，但打不开它的网页端口")
            say("   请检查服务器防火墙是否放行 TCP 端口")
            return url

    # 5. 最后硬试备用地址（config 里写的固定 IP）
    if fallback and fallback != cached:
        say(f"尝试备用地址 {fallback} …")
        if try_url(fallback, timeout=2):
            say(f"✅ 已连接 {fallback}")
            return fallback

    say("❌ 没找到服务器")
    return None


class Listener(threading.Thread):
    """
    后台持续监听服务器广播。
    服务器换了 IP、或中途重启，靠它自动跟上。
    """

    def __init__(self, on_found, port=BEACON_PORT):
        super().__init__(daemon=True)
        self.on_found = on_found
        self.port = port
        self._stop = threading.Event()

    def run(self):
        s = _sock(broadcast=False, bind_port=self.port, timeout=0.5)
        if s is None:
            return          # 端口被占，监听线程直接退出
        while not self._stop.is_set():
            try:
                data, addr = s.recvfrom(4096)
            except socket.timeout:
                continue
            except Exception:
                break
            info = _parse(data, addr)
            if info:
                try:
                    self.on_found(info)
                except Exception:
                    pass
        try:
            s.close()
        except Exception:
            pass

    def stop(self):
        self._stop.set()


if __name__ == "__main__":
    print("=" * 54)
    print("  查找考试服务器")
    print("=" * 54)
    t0 = time.time()
    url = find_server(on_status=lambda m: print("  " + m))
    print(f"\n用时 {time.time()-t0:.1f}s")
    print(f"结果: {url or '未找到'}")
    if not url:
        print("\n排查：")
        print("  1. 服务器 app.py 启动了吗（应显示 📡 广播已启动）")
        print("  2. 是否同一局域网 / VLAN（VLAN 会隔离广播）")
        print(f"  3. 防火墙是否放行 UDP {BEACON_PORT}")
        print("  4. 服务器防火墙是否放行 TCP 9000（HTTP）")
