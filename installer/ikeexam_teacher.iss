; ============================================================
;  ikee考务中心 —— 老师端（Windows 桌面版）安装程序
;
;  用 build_teacher.bat 一键生成 ikeexam-teacher-setup.exe：
;    · 内置 Python 运行时 + 全部依赖
;    · 内置服务端（学生机依赖的后台，双击自动拉起）
;    · 原生桌面界面，不再用浏览器
;    · 桌面/开始菜单快捷方式、支持卸载
;
;  作者：小可老师 · https://www.ikee002.top/
; ============================================================

#define MyAppName "ikee考务中心（老师端）"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "小可老师"
#define MyAppURL "https://www.ikee002.top/"
#define MyAppExeName "pythonw.exe"

[Setup]
AppId={{A3D8E6C2-5B41-4F9D-B8E7-1C2D4F6A8B0E}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={localappdata}\ikeexam
DisableProgramGroupPage=yes
OutputDir=.
OutputBaseFilename=ikeexam-teacher-setup
SetupIconFile=..\client\app.ico
UninstallDisplayIcon={app}\runtime\{#MyAppExeName}
Compression=lzma
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
LicenseFile=..\LICENSE

[Languages]
Name: "chinesesimp"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Messages]
WelcomeLabel1=欢迎安装 [name] —— 机房考试老师端
WelcomeLabel2=本程序会一并安装老师端桌面软件、服务端后台与 Python 运行时。%n%n建议关闭其它程序后继续。

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务："; Flags: checkedonce

[Files]
; Python 运行时（含 flask / openpyxl 等全部依赖）
Source: "build\runtime\*"; DestDir: "{app}\runtime"; Flags: recursesubdirs ignoreversion createallsubdirs
; 服务端源码（学生机依赖的后台）
Source: "build\server\*"; DestDir: "{app}\server"; Flags: recursesubdirs ignoreversion createallsubdirs
; 老师端桌面软件
Source: "build\teacher\*"; DestDir: "{app}\teacher"; Flags: recursesubdirs ignoreversion createallsubdirs

[Dirs]
Name: "{app}\server"; Permissions: users-modify
Name: "{app}\teacher"; Permissions: users-modify

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\runtime\{#MyAppExeName}"; Parameters: """{app}\teacher\teacher_app.py"""; WorkingDir: "{app}\teacher"; IconFilename: "{app}\teacher\app.ico"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\runtime\{#MyAppExeName}"; Parameters: """{app}\teacher\teacher_app.py"""; WorkingDir: "{app}\teacher"; IconFilename: "{app}\teacher\app.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\runtime\{#MyAppExeName}"; Parameters: """{app}\teacher\teacher_app.py"""; WorkingDir: "{app}\teacher"; Description: "立即启动 {#MyAppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}\server\__pycache__"
Type: filesandordirs; Name: "{app}\teacher\__pycache__"
Type: filesandordirs; Name: "{app}\server\logs"
