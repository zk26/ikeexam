# -*- coding: utf-8 -*-
"""
运行环境路径 —— 源码运行 和 PyInstaller 打包成 EXE 两种模式统一处理

打包成 onefile EXE 后，代码和内置资源都在临时解包目录（sys._MEIPASS）里，
**每次启动都是新的、退出就删**。所以：

    只读资源（模板 / 静态文件 / 内置题库 / 素材样例）→ BUNDLE_DIR（解包目录）
    可写数据（exam.db、上传答卷、导出文件、密码、密钥、选项图片）
                                                     → DATA_DIR（exe 所在目录）

首次运行时会把内置题库、素材样例复制到 exe 旁边的可写目录，
老师可以直接往「题库」「materials」文件夹里放自己的文件。

环境变量（都有默认值，一般不用管）：
    EXAM_DATA       指定可写数据目录（默认：源码=项目根目录 / EXE=exe 所在目录）
    EXAM_DB         指定数据库文件（测试用）
    EXAM_AUTH_FILE  指定密码文件
"""
import os
import shutil
import sys

FROZEN = bool(getattr(sys, "frozen", False))
PACKAGE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _bundle_dir():
    """只读资源目录：EXE 模式 = 临时解包目录；源码/安装模式 = 应用根目录。"""
    if FROZEN:
        return getattr(sys, "_MEIPASS",
                       os.path.dirname(os.path.abspath(sys.executable)))
    return os.path.dirname(os.path.dirname(PACKAGE_DIR))


def _data_dir():
    """可写数据目录：EXE 模式 = exe 所在目录；源码模式 = 项目根目录"""
    env = os.environ.get("EXAM_DATA", "").strip()
    if env:
        return os.path.abspath(env)
    if FROZEN:
        return os.path.dirname(os.path.abspath(sys.executable))
    return _bundle_dir()


BUNDLE_DIR = _bundle_dir()
DATA_DIR = _data_dir()

# ---- 可写目录（老师的答卷、导出、图片都落在这里）----
MATERIAL_DIR = os.path.join(DATA_DIR, "materials")   # 实操素材
UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")       # 学生提交的答卷
EXAM_DIR = os.path.join(DATA_DIR, "exams")           # 导出/考试包
QIMG_DIR = os.path.join(DATA_DIR, "qimg")            # 理论题选项图片
BANK_DIR = os.path.join(DATA_DIR, "题库")            # 题库（老师可以自己往里放 xlsx/json）
LOG_DIR = os.path.join(DATA_DIR, "logs")             # 运行日志

# ---- 可写文件 ----
DB_FILE = os.path.join(DATA_DIR, "exam.db")
AUTH_FILE = os.path.join(DATA_DIR, "auth.json")
SECRET_FILE = os.path.join(DATA_DIR, "secret.key")

# ---- 只读资源（模板 / 静态文件）----
TEMPLATE_DIR = os.path.join(PACKAGE_DIR, "server", "templates")
STATIC_DIR = os.path.join(PACKAGE_DIR, "server", "static")

_WRITABLE_DIRS = (MATERIAL_DIR, UPLOAD_DIR, EXAM_DIR, QIMG_DIR, BANK_DIR, LOG_DIR)

# 从内置资源复制过来时，跳过这些（源码文件不需要跟着复制）
_SKIP_EXT = (".py", ".pyc", ".pyo")


def _copy_missing(src_dir, dst_dir):
    """
    递归把内置资源复制到可写目录：**只补缺**，已经存在的文件不动
    （避免把老师改过的题库/素材覆盖掉，包括子目录里的文件）。
    """
    if not os.path.isdir(src_dir):
        return 0
    try:
        os.makedirs(dst_dir, exist_ok=True)
    except OSError:
        return 0

    n = 0
    for root, dirs, files in os.walk(src_dir):
        # 不遍历缓存目录；os.walk 默认不跟随目录符号链接，避免循环复制。
        dirs[:] = [name for name in dirs if name.lower() != "__pycache__"]
        rel = os.path.relpath(root, src_dir)
        target_dir = dst_dir if rel == "." else os.path.join(dst_dir, rel)
        try:
            os.makedirs(target_dir, exist_ok=True)
        except OSError:
            dirs[:] = []
            continue

        for name in files:
            if name.lower().endswith(_SKIP_EXT):
                continue
            src = os.path.join(root, name)
            dst = os.path.join(target_dir, name)
            # lexists 也保护已存在的断链，任何现有目标都不覆盖。
            if os.path.lexists(dst):
                continue
            try:
                shutil.copy2(src, dst)
                n += 1
            except OSError:
                pass
    return n


def prepare():
    """
    启动时调用：建好可写目录 + 首次运行把内置题库/素材复制到 exe 旁边。
    （源码模式也调用，幂等无副作用。）
    """
    for d in _WRITABLE_DIRS:
        try:
            os.makedirs(d, exist_ok=True)
        except OSError:
            pass

    # src == dst 说明源码模式（同一个目录），不用复制
    if os.path.abspath(os.path.join(BUNDLE_DIR, "题库")) != os.path.abspath(BANK_DIR):
        _copy_missing(os.path.join(BUNDLE_DIR, "题库"), BANK_DIR)
    if os.path.abspath(os.path.join(BUNDLE_DIR, "materials")) != os.path.abspath(MATERIAL_DIR):
        _copy_missing(os.path.join(BUNDLE_DIR, "materials"), MATERIAL_DIR)
    # 内置题目配图（题库里的题用 img:文件名 引用）
    if os.path.abspath(os.path.join(BUNDLE_DIR, "qimg")) != os.path.abspath(QIMG_DIR):
        _copy_missing(os.path.join(BUNDLE_DIR, "qimg"), QIMG_DIR)

    # 让 db.py / auth.py 这些模块也能拿到正确位置（它们在自己的模块顶层读环境变量）
    os.environ.setdefault("EXAM_DB", DB_FILE)
    os.environ.setdefault("EXAM_AUTH_FILE", AUTH_FILE)
    return DATA_DIR
