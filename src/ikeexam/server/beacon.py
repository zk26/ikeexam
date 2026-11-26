"""
服务广播（Beacon）—— 让学生端不用写死 IP
--------------------------------
服务端在局域网里定时喊："我在这里，端口是 X"
同时监听学生的主动询问，收到就单独回答。

为什么用广播而不是让 50 台学生机去扫 IP：
  学生机扫描 254 个 IP × 若干端口，50 台一起扫会制造大量流量，
  而且慢（几秒到几十秒）。广播是「一次喊话，全班听见」，最省。

两种发现方式都支持：
  1. 被动听：学生端开着端口听广播（服务器每 3 秒喊一次）
  2. 主动问：学生端广播一句"服务器在哪"，本模块收到就单播回答

用法：
    from beacon import Beacon
    b = Beacon(port=9000, name="机房考试服务器")
    b.start()
    ...
    b.stop()
"""
import json
import os
import socket
import threading
import time

# 广播端口（与 HTTP 服务端口分开，避免冲突）
BEACON_PORT = 39100
# 组播地址（广播不通时的备选，需要交换机开 IGMP Snooping）
MULTICAST_GROUP = "239.192.168.1"
MULTICAST_TTL = 2
# 握手暗号：防止收到别的服务乱发的包
MAGIC = "EXAM-SERVER-V1"
INTERVAL = 3          # 每 3 秒广播一次


def local_broadcast_addrs():
    """
    算出本机所有网卡的广播地址
    返回 [(广播地址, 本机该网卡IP)]
    """
    out = []
    try:
        import psutil
        for name, addrs in psutil.net_if_addrs().items():
            ipv4 = None
            mask = None
            for a in addrs:
                if a.family == socket.AF_INET:
                    ipv4 = a.address
                    mask = a.netmask
            if not ipv4 or not mask or ipv4.startswith("127."):
                continue
            bcast = _calc_broadcast(ipv4, mask)
            if bcast:
                out.append((bcast, ipv4))
    except ImportError:
        # 没装 psutil 就退回最朴素的方式
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            if ip and not ip.startswith("127."):
                out.append(("255.255.255.255", ip))
        except Exception:
            pass
    if not out:
        out.append(("255.255.255.255", "0.0.0.0"))
    return out


def _calc_broadcast(ip, mask):
    """IP + 掩码 → 广播地址"""
    try:
        ip_n = int.from_bytes(socket.inet_aton(ip), "big")
        mk_n = int.from_bytes(socket.inet_aton(mask), "big")
        if mk_n == 0:
            return "255.255.255.255"
        bcast_n = ip_n | (~mk_n & 0xFFFFFFFF)
        return socket.inet_ntoa(bcast_n.to_bytes(4, "big"))
    except Exception:
        return None


def local_ips():
    """本机所有对外 IPv4"""
    return [ip for _b, ip in local_broadcast_addrs()
            if ip and ip != "0.0.0.0"]


# 由 --lan-ip 指定时以它为准（环境变量方式，避免改太多调用点）
FORCED_IP = os.environ.get("EXAM_LAN_IP", "").strip()


def preferred_ip():
    """
    挑一个对外 IP（多网卡时）。
    --lan-ip 指定了就用指定的；否则按常见内网段排序
    （19. 也放进来，有的机房自定义了这个段）。
    """
    if FORCED_IP:
        return FORCED_IP
    ips = local_ips()
    if not ips:
        return "127.0.0.1"
    for prefix in ("192.168.", "10.", "172.", "19."):
        for ip in ips:
            if ip.startswith(prefix):
                return ip
    return ips[0]


class Beacon:
    """
    定时广播 + 应答学生端询问
    """

    def __init__(self, http_port=9000, name="机房考试服务器",
                 interval=INTERVAL, use_multicast=False, logger=None):
        self.http_port = http_port
        self.name = name
        self.interval = interval
        self.use_multicast = use_multicast
        self.logger = logger or (lambda m: None)
        self._stop = threading.Event()
        self._thread = None
        self._responder = None
        self._sent = 0
        self._answered = 0

    # ---- 广播内容 ----
    def payload(self, ip=None):
        """
        :param ip: 这张网卡的 IP。不填就用 preferred_ip()
        字段里带完整 http 地址，学生端就不用自己拼了
        """
        ip = ip or preferred_ip()
        return json.dumps({
            "magic": MAGIC,
            "name": self.name,
            "http": f"http://{ip}:{self.http_port}",
            "ip": ip,
            "port": self.http_port,
            "ts": int(time.time()),
        }, ensure_ascii=False).encode("utf-8")

    # ---- socket ----
    def _sock_broadcast(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.settimeout(1)
        return s

    def _sock_multicast(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL,
                     bytes([MULTICAST_TTL]))
        s.settimeout(1)
        return s

    # ---- 启动 / 停止 ----
    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

        # 应答线程：学生端主动问时回答
        self._responder = threading.Thread(target=self._respond_loop, daemon=True)
        self._responder.start()

        addrs = local_broadcast_addrs()
        tgt = ", ".join(a for a, _ in addrs)
        self.logger(f"📡 服务广播已启动 → {tgt}:{BEACON_PORT}（每 {self.interval}s）")
        self.logger(f"   对外地址 http://{preferred_ip()}:{self.http_port}"
                    f"  服务名「{self.name}」")

    def stop(self):
        self._stop.set()
        self.logger("📡 服务广播已停止")

    # ---- 广播循环 ----
    def _loop(self):
        while not self._stop.is_set():
            try:
                self._broadcast_once()
            except Exception as e:
                self.logger(f"广播异常: {e}")
            # 分成小段 sleep，stop 时能立刻退出
            for _ in range(int(self.interval * 10)):
                if self._stop.is_set():
                    return
                time.sleep(0.1)

    def _broadcast_once(self):
        s = self._sock_broadcast()
        try:
            for bcast, my_ip in local_broadcast_addrs():
                try:
                    # 每张网卡用自己那张卡的地址，避免学生端拿到错的 IP
                    s.sendto(self.payload(my_ip), (bcast, BEACON_PORT))
                    self._sent += 1
                except Exception:
                    continue
        finally:
            s.close()

        if self.use_multicast:
            try:
                m = self._sock_multicast()
                m.sendto(self.payload(), (MULTICAST_GROUP, BEACON_PORT))
                m.close()
            except Exception:
                pass

    # ---- 应答循环 ----
    def _respond_loop(self):
        """
        监听学生端的主动询问。
        绑不上端口（被占用/权限）就安静退出 —— 定时广播仍然有效。
        """
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            except Exception:
                pass
            s.bind(("", BEACON_PORT))
            s.settimeout(0.5)
        except Exception as e:
            self.logger(f"   （应答服务未启用：{e}；定时广播不受影响）")
            return

        self.logger(f"   应答服务已就绪 UDP {BEACON_PORT}")
        while not self._stop.is_set():
            try:
                data, addr = s.recvfrom(1024)
            except socket.timeout:
                continue
            except Exception:
                break
            # 只回应询问包，忽略自己发出去的广播
            try:
                msg = json.loads(data.decode("utf-8"))
                is_query = msg.get("cmd") == "discover"
            except Exception:
                is_query = b"discover" in data
            if not is_query:
                continue
            try:
                s.sendto(self.payload(), addr)
                self._answered += 1
            except Exception:
                continue
        try:
            s.close()
        except Exception:
            pass

    # ---- 状态 ----
    def stats(self):
        addrs = local_broadcast_addrs()
        return {"targets": [a for a, _ in addrs],
                "nics": [i for _, i in addrs],
                "sent": self._sent,
                "answered": self._answered,
                "port": self.http_port,
                "beacon_port": BEACON_PORT,
                "main_ip": preferred_ip()}


if __name__ == "__main__":
    # 独立测试：跑起来后会持续广播，另开终端用 discover.py 听
    import sys
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9000
    b = Beacon(http_port=port, logger=print)
    print(b.stats())
    b.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        b.stop()
