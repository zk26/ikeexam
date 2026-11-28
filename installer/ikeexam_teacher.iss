; ============================================================
;  ikee考务中心 —— 老师端（Windows 桌面版）安装程序
;
;  用 build_teacher.bat 一键生成 ikeexam-teacher-setup.exe：
;    · 内置 Python 运行时 + 全部依赖
;    · 内置服务端（学生机依赖的后台，双击自动拉起）
;    · 原生桌面界面，不再用浏览器
;    · 桌面/开始菜单快捷方式、支持卸载
;
;  作者：小可 · https://www.ikee002.top/
; ============================================================

#define MyAppName "ikee考务中心（老师端）"
#define MyAppVersion "2.2.1"
#define MyAppPublisher "小可"
#define MyAppURL "https://www.ikee002.top/"
#define MyAppExeName "pythonw.exe"

[Setup]
AppId={{A3D8E6C2-5B41-4F9D-B8E7-1C2D4F6A8B0E}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
VersionInfoVersion={#MyAppVersion}
VersionInfoProductVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
; 与老师端分开，避免两端安装/卸载时覆盖同一份 runtime。
DefaultDirName={localappdata}\ikeexam-teacher
; 旧版本曾装到 {localappdata}\ikeexam；继续使用独立目录，避免两端互相覆盖 runtime。
; 旧共享目录中的已知数据由 [Files] 以 onlyifdoesntexist 补迁，原目录始终保留。
UsePreviousAppDir=no
DisableProgramGroupPage=yes
OutputDir=.
OutputBaseFilename=ikeexam-teacher-setup
SetupIconFile=..\src\ikeexam\client\app.ico
UninstallDisplayIcon={app}\src\ikeexam\teacher\app.ico
Compression=lzma
SolidCompression=yes
Uninstallable=yes
CloseApplications=yes
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
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务："

[Files]
; v1 默认装在 {localappdata}\ikeexam。先补迁旧数据，再安装新文件与种子资源；
; 所有目标均只在缺失时复制，绝不覆盖既有数据，也不删除旧共享目录。
Source: "{localappdata}\ikeexam\exam.db"; DestDir: "{app}"; Flags: external skipifsourcedoesntexist onlyifdoesntexist
Source: "{localappdata}\ikeexam\exam.db-wal"; DestDir: "{app}"; Flags: external skipifsourcedoesntexist onlyifdoesntexist
Source: "{localappdata}\ikeexam\exam.db-shm"; DestDir: "{app}"; Flags: external skipifsourcedoesntexist onlyifdoesntexist
Source: "{localappdata}\ikeexam\auth.json"; DestDir: "{app}"; Flags: external skipifsourcedoesntexist onlyifdoesntexist
Source: "{localappdata}\ikeexam\auth.json.bak"; DestDir: "{app}"; Flags: external skipifsourcedoesntexist onlyifdoesntexist
Source: "{localappdata}\ikeexam\secret.key"; DestDir: "{app}"; Flags: external skipifsourcedoesntexist onlyifdoesntexist
Source: "{localappdata}\ikeexam\server\auth.json"; DestDir: "{app}"; Flags: external skipifsourcedoesntexist onlyifdoesntexist
Source: "{localappdata}\ikeexam\server\auth.json.bak"; DestDir: "{app}"; Flags: external skipifsourcedoesntexist onlyifdoesntexist
Source: "{localappdata}\ikeexam\server\secret.key"; DestDir: "{app}"; Flags: external skipifsourcedoesntexist onlyifdoesntexist
Source: "{localappdata}\ikeexam\teacher\config.json"; DestDir: "{app}\src\ikeexam\teacher"; Flags: external skipifsourcedoesntexist onlyifdoesntexist
Source: "{localappdata}\ikeexam\uploads\*"; DestDir: "{app}\uploads"; Flags: external recursesubdirs createallsubdirs skipifsourcedoesntexist onlyifdoesntexist
Source: "{localappdata}\ikeexam\exams\*"; DestDir: "{app}\exams"; Flags: external recursesubdirs createallsubdirs skipifsourcedoesntexist onlyifdoesntexist
Source: "{localappdata}\ikeexam\logs\*"; DestDir: "{app}\logs"; Flags: external recursesubdirs createallsubdirs skipifsourcedoesntexist onlyifdoesntexist
Source: "{localappdata}\ikeexam\server\logs\*"; DestDir: "{app}\logs"; Flags: external recursesubdirs createallsubdirs skipifsourcedoesntexist onlyifdoesntexist
Source: "{localappdata}\ikeexam\题库\*"; DestDir: "{app}\题库"; Flags: external recursesubdirs createallsubdirs skipifsourcedoesntexist onlyifdoesntexist
Source: "{localappdata}\ikeexam\materials\*"; DestDir: "{app}\materials"; Flags: external recursesubdirs createallsubdirs skipifsourcedoesntexist onlyifdoesntexist
Source: "{localappdata}\ikeexam\qimg\*"; DestDir: "{app}\qimg"; Flags: external recursesubdirs createallsubdirs skipifsourcedoesntexist onlyifdoesntexist

; Python 运行时（含 flask / openpyxl 等全部依赖）
Source: "build\runtime\*"; DestDir: "{app}\runtime"; Flags: recursesubdirs ignoreversion createallsubdirs
; 统一 src 包：服务端、老师端、评分器与共享模块
Source: "build\src\ikeexam\server\*"; DestDir: "{app}\src\ikeexam\server"; Flags: recursesubdirs ignoreversion createallsubdirs
Source: "build\src\ikeexam\teacher\*"; DestDir: "{app}\src\ikeexam\teacher"; Flags: recursesubdirs ignoreversion createallsubdirs
Source: "build\src\ikeexam\graders\*"; DestDir: "{app}\src\ikeexam\graders"; Flags: recursesubdirs ignoreversion createallsubdirs
Source: "build\src\ikeexam\shared\*"; DestDir: "{app}\src\ikeexam\shared"; Flags: recursesubdirs ignoreversion createallsubdirs
Source: "build\src\ikeexam\__init__.py"; DestDir: "{app}\src\ikeexam"; Flags: ignoreversion
; 内置题库、实操素材与题目图片是可写数据：仅补缺，重装/升级不得覆盖教师修改。
Source: "build\题库\*"; DestDir: "{app}\题库"; Flags: recursesubdirs ignoreversion createallsubdirs onlyifdoesntexist
Source: "build\materials\*"; DestDir: "{app}\materials"; Flags: recursesubdirs ignoreversion createallsubdirs onlyifdoesntexist
Source: "build\qimg\*"; DestDir: "{app}\qimg"; Flags: recursesubdirs ignoreversion createallsubdirs onlyifdoesntexist

[Dirs]
; 默认位于当前用户配置目录，账户自身已有完全控制权；其他用户只读，避免改写凭据/源码。
Name: "{app}"; Permissions: users-readexec
; 资源仍供教师及其他本机使用者维护；凭据与运行源码不授予 Users 写权限。
Name: "{app}\题库"; Permissions: users-modify
Name: "{app}\materials"; Permissions: users-modify
Name: "{app}\qimg"; Permissions: users-modify

[Icons]
Name: "{autoprograms}\iKeExam\老师端"; Filename: "{app}\runtime\{#MyAppExeName}"; Parameters: """{app}\src\ikeexam\teacher\teacher_app.py"""; WorkingDir: "{app}\src\ikeexam\teacher"; IconFilename: "{app}\src\ikeexam\teacher\app.ico"
Name: "{autoprograms}\iKeExam\网页控制台"; Filename: "{sys}\cmd.exe"; Parameters: "/D /C start """" http://127.0.0.1:9000/login"; WorkingDir: "{app}"; IconFilename: "{app}\src\ikeexam\teacher\app.ico"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\runtime\{#MyAppExeName}"; Parameters: """{app}\src\ikeexam\teacher\teacher_app.py"""; WorkingDir: "{app}\src\ikeexam\teacher"; IconFilename: "{app}\src\ikeexam\teacher\app.ico"; Tasks: desktopicon
Name: "{autoprograms}\iKeExam\卸载老师端"; Filename: "{app}\unins000.exe"; IconFilename: "{app}\src\ikeexam\teacher\app.ico"

[Run]
Filename: "{app}\runtime\{#MyAppExeName}"; Parameters: """{app}\src\ikeexam\teacher\teacher_app.py"""; WorkingDir: "{app}\src\ikeexam\teacher"; Description: "立即启动 {#MyAppName}"; Flags: nowait postinstall skipifsilent

[Code]
var
  DeleteAllLocalData: Boolean;

// Inno Setup CloseApplications handles running installed applications.
// Never execute scripts from the writable application directory.

function CmdWantsDeleteAll(): Boolean;
var
  I: Integer;
begin
  Result := False;
  for I := 1 to ParamCount do
    if CompareText(ParamStr(I), '/DELETEALL') = 0 then
      Result := True;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  AppDir: string;
  ResultCode: Integer;
begin
  if CurUninstallStep = usUninstall then
  begin
    DeleteAllLocalData := CmdWantsDeleteAll;
    if not UninstallSilent and not DeleteAllLocalData then
    begin
      if MsgBox('是否删除本地全部内容？' + #13#10 + #13#10 +
                '选择“是”将删除密码、考试数据、成绩、题库修改、缓存和剩余文件，且无法恢复。' + #13#10 +
                '选择“否”仅卸载程序，保留本地内容。',
                mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
        DeleteAllLocalData := True;
    end;
  end
  else if (CurUninstallStep = usPostUninstall) and DeleteAllLocalData then
  begin
    AppDir := RemoveBackslashUnlessRoot(ExpandConstant('{app}'));
    Exec(ExpandConstant('{sys}\cmd.exe'), '/D /C rd /s /q "' + AppDir + '"',
         '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
    if (ResultCode <> 0) and DirExists(AppDir) then
      MsgBox('程序已卸载，但部分本地内容正在被占用，未能全部删除。' + #13#10 +
             '请关闭相关程序后手动删除：' + #13#10 + AppDir,
             mbError, MB_OK);
  end;
end;
