; ============================================================
;  ikee考务中心 —— 学生端安装程序（Inno Setup 6）
;
;  用 build_setup.bat 一键生成 ikeexam-setup.exe：
;    · 内置 Python 运行时（未装 Python 的机器也能跑）
;    · 一键安装到目标机、桌面/开始菜单快捷方式、支持卸载
;
;  作者：小可
;  仓库：https://github.com/zk26/ikeexam
; ============================================================

#define MyAppName "ikee考务中心"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "小可"
#define MyAppURL "https://www.ikee002.top/"
#define MyAppExeName "pythonw.exe"

[Setup]
AppId={{B7C4A2D9-3F5E-4C8A-9B1D-2E6F8A0C4D5E}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
; 默认装到用户目录（无需管理员权限，学生机也能装，且程序数据可写）
DefaultDirName={localappdata}\ikeexam
DisableProgramGroupPage=yes
; 学生端窗口在任务栏/开始菜单显示的名字
OutputDir=.
OutputBaseFilename=ikeexam-setup
SetupIconFile=..\client\app.ico
UninstallDisplayIcon={app}\runtime\{#MyAppExeName}
Compression=lzma
SolidCompression=yes
WizardStyle=modern
; 学生机可能没有管理员权限，用 lowest；管理员装则自动提权
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; 显示中文界面
LicenseFile=..\LICENSE

[Languages]
Name: "chinesesimp"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Messages]
WelcomeLabel1=欢迎安装 [name] —— 机房考试学生端
WelcomeLabel2=本程序将把学生端连同 Python 运行时一起安装到你的电脑。%n%n建议关闭其它程序后继续。

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务："; Flags: checkedonce

[Files]
; Python 嵌入式运行时（build_setup.bat 会下载并解压到 build\runtime）
Source: "build\runtime\*"; DestDir: "{app}\runtime"; Flags: recursesubdirs ignoreversion createallsubdirs
; 学生端源码（build_setup.bat 会把 client 复制到 build\client）
Source: "build\client\*"; DestDir: "{app}\client"; Flags: recursesubdirs ignoreversion createallsubdirs

[Dirs]
; 学生端会在程序目录写 config.json 和 ikee_Exam，给普通用户写权限
Name: "{app}\client"; Permissions: users-modify

[Icons]
; 开始菜单
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\runtime\{#MyAppExeName}"; Parameters: """{app}\client\exam_client.py"""; WorkingDir: "{app}\client"; IconFilename: "{app}\client\app.ico"
; 桌面（可选任务）
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\runtime\{#MyAppExeName}"; Parameters: """{app}\client\exam_client.py"""; WorkingDir: "{app}\client"; IconFilename: "{app}\client\app.ico"; Tasks: desktopicon

[Run]
; 安装完成后可选启动
Filename: "{app}\runtime\{#MyAppExeName}"; Parameters: """{app}\client\exam_client.py"""; WorkingDir: "{app}\client"; Description: "立即启动 {#MyAppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; 卸载时清掉运行时产生的数据（题库/答卷默认在 ikee_Exam，按需保留可删此段）
Type: filesandordirs; Name: "{app}\client\__pycache__"
Type: filesandordirs; Name: "{app}\client\ikee_Exam"
