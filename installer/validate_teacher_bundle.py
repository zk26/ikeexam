#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""校验老师端 build bundle，不依赖 Inno Setup 或第三方 Python 包。

默认校验 installer/build，并对照仓库根目录检查源码与资源是否完整复制。
build_teacher.bat 会在 ISCC 编译前自动调用本脚本。
"""
import argparse
import hashlib
import sys
from email.parser import Parser
from pathlib import Path

INSTALLER_DIR = Path(__file__).resolve().parent
DEFAULT_BUILD = INSTALLER_DIR / "build"
DEFAULT_SOURCE = INSTALLER_DIR.parent

RUNTIME_FILES = (
    "python.exe",
    "pythonw.exe",
    "python311.dll",
    "python3.dll",
    "python311.zip",
    "python311._pth",
    "vcruntime140.dll",
    "vcruntime140_1.dll",
    "libcrypto-3.dll",
    "libssl-3.dll",
    "libffi-8.dll",
    "sqlite3.dll",
    "_socket.pyd",
    "_ssl.pyd",
    "_hashlib.pyd",
    "_sqlite3.pyd",
    "_ctypes.pyd",
    "_elementtree.pyd",
    "select.pyd",
    "pyexpat.pyd",
    "unicodedata.pyd",
    "_tkinter.pyd",
    "tcl86t.dll",
    "tk86t.dll",
    "Lib/tkinter/__init__.py",
    "Lib/tkinter/ttk.py",
    "tcl/tcl8.6/init.tcl",
    "tcl/tk8.6/tk.tcl",
)

SITE_PACKAGE_FILES = (
    "flask/__init__.py",
    "werkzeug/__init__.py",
    "jinja2/__init__.py",
    "markupsafe/__init__.py",
    "itsdangerous/__init__.py",
    "click/__init__.py",
    "blinker/__init__.py",
    "colorama/__init__.py",
    "openpyxl/__init__.py",
    "et_xmlfile/__init__.py",
    "docx/__init__.py",
    "pptx/__init__.py",
    "yaml/__init__.py",
    "lxml/__init__.py",
    "PIL/__init__.py",
    "XlsxWriter/__init__.py",
    "psutil/__init__.py",
    "typing_extensions.py",
)

# 这些包的核心 Windows 二进制扩展不能缺失（纯 Python 后备实现除外）。
SITE_PACKAGE_PATTERNS = (
    "lxml/etree*.pyd",
    "PIL/_imaging*.pyd",
    "psutil/_psutil_windows*.pyd",
)

APP_FILES = (
    "src/ikeexam/__init__.py",
    "src/ikeexam/shared/ui_theme.py",
    "src/ikeexam/shared/config_schema.py",
    "src/ikeexam/teacher/teacher_app.py",
    "src/ikeexam/teacher/api.py",
    "src/ikeexam/teacher/app.ico",
    "src/ikeexam/teacher/about_qr.png",
    "src/ikeexam/server/auto_rule.py",
    "src/ikeexam/server/plagiarism.py",
    "src/ikeexam/graders/__init__.py",
    "src/ikeexam/graders/base.py",
    "src/ikeexam/graders/excel.py",
    "src/ikeexam/graders/word.py",
    "src/ikeexam/graders/ppt.py",
    "src/ikeexam/server/app.py",
    "src/ikeexam/server/db.py",
    "src/ikeexam/server/exam.py",
    "src/ikeexam/server/auth.py",
    "src/ikeexam/server/runtime.py",
    "src/ikeexam/server/beacon.py",
    "src/ikeexam/server/sandbox.py",
    "src/ikeexam/server/coderun.py",
    "src/ikeexam/server/import_excel.py",
    "src/ikeexam/server/pinyin_data.py",
    "src/ikeexam/server/templates/base.html",
    "src/ikeexam/server/templates/_icons.html",
    "src/ikeexam/server/templates/login.html",
    "src/ikeexam/server/templates/plagiarism.html",
    "src/ikeexam/server/static/echarts.min.js",
    "题库/内置题库.json",
    "题库/实操题库.json",
)

SOURCE_TREES = ("src/ikeexam/server", "src/ikeexam/teacher",
                "src/ikeexam/graders", "src/ikeexam/shared",
                "题库", "materials", "qimg")
RESOURCE_TREES = ("题库", "materials", "qimg")


def _sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _norm_name(name):
    return "-".join(name.lower().replace("_", "-").replace(".", "-").split("-"))


def _locked_versions(path):
    versions = {}
    if not path.is_file():
        return versions
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.split("#", 1)[0].strip()
        if "==" not in line:
            continue
        name, version = (part.strip() for part in line.split("==", 1))
        versions[_norm_name(name)] = version
    return versions


def _installed_versions(site_packages):
    versions = {}
    if not site_packages.is_dir():
        return versions
    for metadata in site_packages.glob("*.dist-info/METADATA"):
        try:
            info = Parser().parsestr(metadata.read_text(encoding="utf-8"))
            name, version = info.get("Name"), info.get("Version")
            if name and version:
                versions[_norm_name(name)] = version
        except (OSError, UnicodeError):
            continue
    return versions


def _excluded(relative, tree):
    parts = relative.parts
    tree_name = Path(tree).name
    if "__pycache__" in parts or relative.suffix.lower() in (".pyc", ".pyo"):
        return True
    if tree_name == "server":
        if any(part in ("build", "logs") for part in parts[:-1]):
            return True
        if relative.suffix.lower() in (".db", ".toc"):
            return True
        if relative.name in ("auth.json", "secret.key"):
            return True
    if tree_name == "teacher" and relative.name == "config.json":
        return True
    return False


def _source_files(source_dir, tree):
    root = source_dir / tree
    if not root.is_dir():
        return []
    return sorted(
        path for path in root.rglob("*")
        if path.is_file() and not _excluded(path.relative_to(root), tree)
    )


def validate(bundle_dir, source_dir):
    """返回 (错误列表, 检查数量)。所有检查均只读，不启动服务或安装依赖。"""
    bundle_dir = Path(bundle_dir)
    source_dir = Path(source_dir)
    errors = []
    checked = 0

    def require_file(relative, source=None, nonempty=True):
        nonlocal checked
        checked += 1
        relative = Path(relative)
        target = bundle_dir / relative
        if not target.is_file():
            errors.append(f"缺少文件: {relative.as_posix()}")
            return
        if nonempty and target.stat().st_size == 0:
            errors.append(f"文件为空: {relative.as_posix()}")
        if source is None:
            return
        source = Path(source)
        if not source.is_file():
            errors.append(f"源文件不存在: {source}")
            return
        if target.stat().st_size != source.stat().st_size or _sha256(target) != _sha256(source):
            errors.append(f"复制内容与源文件不一致: {relative.as_posix()}")

    def require_dir(relative):
        nonlocal checked
        checked += 1
        if not (bundle_dir / relative).is_dir():
            errors.append(f"缺少目录: {Path(relative).as_posix()}")

    def require_pattern(relative_pattern):
        nonlocal checked
        checked += 1
        if not any((bundle_dir / relative_pattern).parent.glob(Path(relative_pattern).name)):
            errors.append(f"缺少二进制依赖: {relative_pattern}")

    if not bundle_dir.is_dir():
        return [f"build 目录不存在: {bundle_dir}"], 1

    runtime = bundle_dir / "runtime"
    site_packages = runtime / "Lib" / "site-packages"
    require_dir("runtime")
    require_dir("runtime/Lib/site-packages")
    for relative in RUNTIME_FILES:
        require_file(Path("runtime") / relative)
    for relative in SITE_PACKAGE_FILES:
        require_file(Path("runtime/Lib/site-packages") / relative)
    for pattern in SITE_PACKAGE_PATTERNS:
        require_pattern(Path("runtime/Lib/site-packages") / pattern)

    checked += 1
    pth = runtime / "python311._pth"
    if pth.is_file():
        try:
            lines = [line.strip().replace("\\", "/")
                     for line in pth.read_text(encoding="utf-8-sig").splitlines()
                     if line.strip()]
            required = ("python311.zip", ".", "Lib", "Lib/site-packages", "import site")
            if any(line not in lines for line in required):
                errors.append("runtime/python311._pth 未启用 Lib/site-packages 和 import site")
        except (OSError, UnicodeError) as exc:
            errors.append(f"无法读取 runtime/python311._pth: {exc}")
    else:
        errors.append("缺少文件: runtime/python311._pth")

    checked += 1
    locked = _locked_versions(INSTALLER_DIR / "requirements-teacher.txt")
    installed = _installed_versions(site_packages)
    if not locked:
        errors.append("缺少或无法读取 requirements-teacher.txt")
    for name, version in sorted(locked.items()):
        if installed.get(name) != version:
            errors.append(f"依赖版本不符: {name} 应为 {version}，实际 {installed.get(name, '未安装')}")

    for relative in APP_FILES:
        require_file(relative)

    # 对照源目录逐文件校验，确保嵌套模板、评分器和资源没有在复制时遗漏。
    for tree in SOURCE_TREES:
        source_root = source_dir / tree
        if not source_root.is_dir():
            errors.append(f"源目录不存在，无法校验完整性: {source_root}")
            continue
        for source in _source_files(source_dir, tree):
            require_file(Path(tree) / source.relative_to(source_root), source, nonempty=False)

    require_file("src/ikeexam/__init__.py", source_dir / "src" / "ikeexam" / "__init__.py")
    require_file("src/ikeexam/teacher/app.ico",
                 source_dir / "src" / "ikeexam" / "client" / "app.ico")

    for tree in RESOURCE_TREES:
        require_dir(tree)
        checked += 1
        files = [path for path in (bundle_dir / tree).rglob("*")
                 if path.is_file() and path.name != ".gitkeep"
                 and not _excluded(path.relative_to(bundle_dir / tree), tree)]
        if not files:
            errors.append(f"资源目录没有实际内容: {tree}")
    require_dir("题库/标准答案")

    return errors, checked


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("build_dir", nargs="?", type=Path, default=DEFAULT_BUILD,
                        help="老师端 build 目录（默认：installer/build）")
    parser.add_argument("--source-dir", type=Path, default=DEFAULT_SOURCE,
                        help="仓库根目录，用于逐文件对照源码和资源")
    args = parser.parse_args(argv)

    errors, checked = validate(args.build_dir, args.source_dir)
    if errors:
        print(f"[FAIL] 老师端 bundle 校验失败（{len(errors)} 项错误）：")
        for error in errors:
            print(f"  - {error}")
        return 1
    print(f"[OK] 老师端 bundle 校验通过：{checked} 项检查，启动、评分、依赖和资源完整。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
