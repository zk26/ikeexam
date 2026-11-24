# 安装程序构建说明

用 Inno Setup 生成两个安装程序，都内置 Python 运行时，目标机**无需预装 Python**：

| 安装程序 | 脚本 | 产物 | 面向 |
| --- | --- | --- | --- |
| 学生端 | `build_setup.bat` → `ikeexam_student.iss` | `ikeexam-setup.exe` | 学生机（答题） |
| 老师端（Windows 桌面版） | `build_teacher.bat` → `ikeexam_teacher.iss` | `ikeexam-teacher-setup.exe` | 老师机（管理，原生桌面界面） |

> 作者：小可老师 · 网站：https://www.ikee002.top/ · 仓库：https://github.com/zk26/ikeexam

## 一、构建前准备

| 依赖 | 用途 | 下载 |
| --- | --- | --- |
| Inno Setup 6（含 ISCC.exe） | 编译安装程序 | https://jrsoftware.org/isdl.php |
| 构建机 Python 3（任意版本） | 仅用于下载依赖 wheel | https://www.python.org/ |

> 构建机 Python 版本不限：脚本用 `--python-version 311` 强制下载 64 位 Python 3.11 的预编译 wheel，
> 与内置运行时严格匹配，不会因为构建机版本不同而装错。

## 二、学生端一键构建

双击 `installer\build_setup.bat`，自动：

1. 下载 `python-3.11.9-embed-amd64.zip`（首次，之后复用）；
2. 解压为嵌入式运行时，改 `python311._pth` 启用 `site-packages`；
3. 下载 psutil、pillow 的 `cp311-win_amd64` wheel；
4. 复制 `client\` 源码；
5. 编译生成 **`ikeexam-setup.exe`**。

## 三、老师端（Windows 桌面版）一键构建

双击 `installer\build_teacher.bat`，自动：

1. 下载并解压嵌入式运行时；
2. 下载 flask、openpyxl、python-docx、python-pptx、pyyaml、psutil 等依赖；
3. 复制 `server\`（服务端后台）与 `teacher\`（桌面界面）源码；
4. 编译生成 **`ikeexam-teacher-setup.exe`**。

老师端装好后双击快捷方式即可用：桌面程序会**自动在后台拉起本机服务端**，
学生机通过广播自动发现，老师不再需要打开浏览器。

## 四、安装程序特性

- **内置 Python 运行时**：目标机未装 Python 也能直接运行；
- **一键安装**：默认装到 `%LOCALAPPDATA%\ikeexam`，无需管理员权限；
- **快捷方式**：桌面 + 开始菜单；
- **支持卸载**：控制面板 / 开始菜单均可卸载；
- **界面语言**：简体中文。

## 五、自定义

| 想改什么 | 改哪里 |
| --- | --- |
| 版本号 | 对应 `.iss` 顶部 `#define MyAppVersion` |
| 默认安装目录 | `[Setup]` 的 `DefaultDirName` |
| 是否需要管理员权限 | `PrivilegesRequired`（`lowest` 普通 / `admin` 强制） |
| 桌面快捷方式默认是否勾选 | `[Tasks]` 的 `Flags: checkedonce` |
| 内置 Python 版本 | 对应 `build_*.bat` 顶部 `PY_VER`（同步 `PY_URL` 与 `python311._pth` 文件名） |

## 六、注意事项

1. 学生端/老师端都会在安装目录写配置与运行时数据，`[Dirs]` 已授予 `users-modify` 权限。
2. 卸载时 `[UninstallDelete]` 会顺带清掉本地缓存（学生已交卷缓存 / 服务端日志）；想保留则删除对应段。
3. 首次联网构建需要能访问 python.org 和 PyPI；内网可先下好 zip 与 wheel 改走离线源。
4. **Linux 服务器部署**（老师用浏览器访问网页）仍走源码方式，见仓库根目录《使用说明.md》。
