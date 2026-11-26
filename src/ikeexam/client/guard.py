"""
考试守卫：进程查杀 + 锁屏加固
学生机上的防作弊第一道防线
"""
import platform
import subprocess
import time

IS_WIN = platform.system() == "Windows"

# ---- 默认禁止名单 ----
# 分三类写清楚，方便老师按需增删
DEFAULT_KILL = [
    # 远程控制（最危险，能让场外的人代考）
    "AnyDesk.exe", "anydesk.exe", "ToDesk.exe", "todesk.exe",
    "TeamViewer.exe", "teamviewer.exe", "RustDesk.exe", "rustdesk.exe",
    "sunloginclient.exe", "SunloginClient.exe", "AweSun.exe", "awesun.exe",
    "QQRemote.exe", "mstsc.exe",
    # 即时通讯
    "QQ.exe", "QQScLauncher.exe", "WeChat.exe", "WeChatApp.exe",
    "WeChatStore.exe", "DingTalk.exe", "Feishu.exe", "TIM.exe",
    # 浏览器（防搜答案）
    "chrome.exe", "firefox.exe", "msedge.exe", "iexplore.exe",
    "360se.exe", "360chrome.exe", "SogouExplorer.exe", "QQBrowser.exe",
    "liebao.exe", "UCBrowser.exe", "opera.exe", "brave.exe",
    # 其他
    "wpslingxi.exe",      # WPS 灵犀 AI，能帮答题
    "yuansheng.exe",      # 远程协助类
]

# 白名单：这些进程绝不能杀，否则系统崩
WHITELIST = {
    "system", "system idle process", "csrss.exe", "smss.exe", "wininit.exe",
    "winlogon.exe", "services.exe", "lsass.exe", "svchost.exe", "explorer.exe",
    "python.exe", "pythonw.exe", "cmd.exe", "powershell.exe",
    "dwm.exe", "conhost.exe", "ctfmon.exe", "taskhostw.exe", "runtimebroker.exe",
    # WPS 本体
    "wps.exe", "et.exe", "wpp.exe", "wpspdf.exe", "wpsoffice.exe",
    "ksolaunch.exe", "office6.exe", "wpsupdate.exe",
}


def kill_processes(names=None, logger=None):
    """
    杀掉名单里的进程
    :return: 被杀掉的进程名列表
    """
    names = names or DEFAULT_KILL
    want = {n.lower() for n in names}
    killed = []

    try:
        import psutil
        for p in psutil.process_iter(["pid", "name"]):
            try:
                nm = (p.info.get("name") or "").strip()
                if nm.lower() in want and nm.lower() not in WHITELIST:
                    p.kill()
                    killed.append(nm)
            except Exception:
                continue
    except ImportError:
        # 没装 psutil 就退回 taskkill（仅 Windows）
        if IS_WIN:
            for n in want:
                if n in WHITELIST:
                    continue
                try:
                    r = subprocess.run(
                        ["taskkill", "/F", "/IM", n],
                        capture_output=True, timeout=10,
                        creationflags=0x08000000,   # CREATE_NO_WINDOW
                    )
                    if r.returncode == 0:
                        killed.append(n)
                except Exception:
                    continue

    if killed and logger:
        logger("warn", "查杀进程: " + ", ".join(sorted(set(killed))))
    return killed


def scan_running(names=None):
    """当前有哪些违禁进程在跑（只报告，不杀）"""
    names = names or DEFAULT_KILL
    want = {n.lower() for n in names}
    found = []
    try:
        import psutil
        for p in psutil.process_iter(["name"]):
            try:
                nm = (p.info.get("name") or "").strip()
                if nm.lower() in want and nm.lower() not in WHITELIST:
                    found.append(nm)
            except Exception:
                continue
    except ImportError:
        pass
    return sorted(set(found))


# ---------------- Windows 锁屏加固 ----------------
def disable_taskmgr():
    """禁用任务管理器（改注册表）"""
    if not IS_WIN:
        return False, "非 Windows 系统"
    try:
        import winreg
        key = winreg.CreateKey(winreg.HKEY_CURRENT_USER,
                               r"Software\Microsoft\Windows\CurrentVersion\Policies\System")
        winreg.SetValueEx(key, "DisableTaskMgr", 0, winreg.REG_DWORD, 1)
        winreg.CloseKey(key)
        return True, "已禁用任务管理器"
    except Exception as e:
        return False, str(e)


def enable_taskmgr():
    """恢复任务管理器"""
    if not IS_WIN:
        return False, "非 Windows 系统"
    try:
        import winreg
        key = winreg.CreateKey(winreg.HKEY_CURRENT_USER,
                               r"Software\Microsoft\Windows\CurrentVersion\Policies\System")
        winreg.SetValueEx(key, "DisableTaskMgr", 0, winreg.REG_DWORD, 0)
        winreg.CloseKey(key)
        return True, "已恢复任务管理器"
    except Exception as e:
        return False, str(e)


def clear_clipboard():
    """清空剪贴板（防复制答案）"""
    if not IS_WIN:
        return False, "非 Windows 系统"
    try:
        import ctypes
        if ctypes.windll.user32.OpenClipboard(None):
            ctypes.windll.user32.EmptyClipboard()
            ctypes.windll.user32.CloseClipboard()
            return True, "已清空剪贴板"
        return False, "打开剪贴板失败"
    except Exception as e:
        return False, str(e)


class Guardian:
    """
    定时守卫：循环查杀违禁进程
    用法:
        g = Guardian(logger=lambda lvl,msg: print(msg))
        g.start()
        ...
        g.stop()
    """

    def __init__(self, interval=5, names=None, logger=None):
        self.interval = interval
        self.names = names or DEFAULT_KILL
        self.logger = logger or (lambda lvl, msg: None)
        self._running = False
        self._thread = None
        self._locked = False

    def start(self):
        import threading
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        self.logger("info", f"守卫已启动（每 {self.interval} 秒扫描一次）")

    def stop(self):
        self._running = False
        self.logger("info", "守卫已停止")

    def lock(self):
        """进入考试锁屏状态"""
        ok, msg = disable_taskmgr()
        self.logger("info" if ok else "warn", msg)
        clear_clipboard()
        kill_processes(self.names, self.logger)
        self._locked = True
        return ok

    def unlock(self):
        """解除锁屏"""
        ok, msg = enable_taskmgr()
        self.logger("info" if ok else "warn", msg)
        self._locked = False
        return ok

    def _loop(self):
        """
        定时循环。

        ⚠️ 违禁进程查杀是**全时段**的（文档承诺"每 5 秒查杀"），
        跟 _locked 无关 —— _locked 只控制任务管理器/剪贴板加固。
        以前把查杀也挂在 _locked 后面，导致默认普通窗口模式下
        浏览器/远控一个都杀不掉，和文档完全不符。
        """
        while self._running:
            try:
                kill_processes(self.names, self.logger)
            except Exception as e:
                self.logger("error", f"守卫异常: {e}")
            time.sleep(self.interval)


if __name__ == "__main__":
    print("当前违禁进程扫描：")
    found = scan_running()
    if found:
        for f in found:
            print("  ⚠️", f)
    else:
        print("  ✅ 未发现违禁进程")
    print(f"\n黑名单共 {len(DEFAULT_KILL)} 项，白名单 {len(WHITELIST)} 项")
