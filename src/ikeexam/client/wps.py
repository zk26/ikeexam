"""
WPS 定位与拉起
学生机装的是 WPS 教育考试专用版，这里负责找到它并打开指定文件
"""
import glob
import os
import re
import subprocess

# 三个组件对应的主程序
COMPONENTS = {
    "word":  ["wps.exe", "wpsoffice.exe"],
    "excel": ["et.exe", "wpsoffice.exe"],
    "ppt":   ["wpp.exe", "wpsoffice.exe"],
}

LABEL = {"word": "WPS文字", "excel": "WPS表格", "ppt": "WPS演示"}

# WPS 常见安装根目录（版本号目录用 * 通配）
SEARCH_ROOTS = [
    r"C:\Program Files\Kingsoft\WPS Office",
    r"C:\Program Files (x86)\Kingsoft\WPS Office",
    os.path.expandvars(r"%LOCALAPPDATA%\Kingsoft\WPS Office"),
]


def find_wps(exe_name):
    """
    找到某个 exe 的完整路径
    优先 office6 目录，其次递归搜一层
    """
    hits = []
    for root in SEARCH_ROOTS:
        if not os.path.isdir(root):
            continue
        # 先找 office6 下的
        hits += glob.glob(os.path.join(root, "*", "office6", exe_name))
        # 再找根目录直下的（有些版本没有版本号目录）
        hits += glob.glob(os.path.join(root, "office6", exe_name))
        hits += glob.glob(os.path.join(root, exe_name))
    # 去重并保持顺序
    seen, out = set(), []
    for h in hits:
        k = os.path.normcase(h)
        if k not in seen:
            seen.add(k)
            out.append(h)
    return out


def _version_key(path):
    """
    从 WPS 安装路径里提取版本号，转成数值元组用于排序。
    用字符串排序会出错：'9.1.0' 排在 '10.1.0' 后面（字符 '9' > '1'），
    导致挑到旧版本。数值比较才对。
    """
    m = re.search(r"[\\/](\d+(?:\.\d+)+)[\\/]", path)
    if not m:
        return ()
    try:
        return tuple(int(x) for x in m.group(1).split("."))
    except ValueError:
        return ()


def resolve(component):
    """
    解析一个组件的可执行文件路径
    返回 (路径, 说明)；找不到返回 (None, 原因)
    """
    for exe in COMPONENTS.get(component, []):
        paths = find_wps(exe)
        if paths:
            # 同名的取版本号最大的（按数值比较，不是字符串排序）
            return max(paths, key=_version_key), exe
    return None, f"未找到 {LABEL.get(component, component)}（{COMPONENTS.get(component)}）"


def launch(component, file_path=None):
    """
    拉起 WPS 组件，可选同时打开文件
    :return: (是否成功, 说明)
    """
    exe, name = resolve(component)
    if not exe:
        return False, name

    args = [exe]
    if file_path and os.path.isfile(file_path):
        args.append(os.path.abspath(file_path))

    try:
        # 不阻塞：拉起后立刻返回，让学生操作
        subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        extra = f"并打开 {os.path.basename(file_path)}" if file_path else ""
        return True, f"已拉起 {LABEL.get(component, component)}{extra}"
    except Exception as e:
        return False, f"拉起失败: {e}"


def is_running(component):
    """某组件是否正在运行（进程名匹配）"""
    names = [n.lower() for n in COMPONENTS.get(component, [])]
    try:
        import psutil
        for p in psutil.process_iter(["name"]):
            try:
                if (p.info.get("name") or "").lower() in names:
                    return True
            except Exception:
                continue
    except Exception:
        pass
    return False


def report():
    """检查本机 WPS 安装情况，返回诊断信息"""
    lines = []
    for c in ("word", "excel", "ppt"):
        exe, info = resolve(c)
        lines.append(f"{LABEL[c]:8} {'✅ ' + exe if exe else '❌ ' + info}")
    return "\n".join(lines)


if __name__ == "__main__":
    print("WPS 安装检测\n")
    print(report())
