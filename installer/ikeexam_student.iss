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
#define MyAppVersion "2.2.0"
#define MyAppPublisher "小可"
#define MyAppURL "https://www.ikee002.top/"
#define MyAppExeName "pythonw.exe"

[Setup]
AppId={{B7C4A2D9-3F5E-4C8A-9B1D-2E6F8A0C4D5E}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
VersionInfoVersion={#MyAppVersion}
VersionInfoProductVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
; 默认装到用户目录（无需管理员权限，学生机也能装，且程序数据可写）
; 与老师端分开，避免两端安装/卸载时覆盖同一份 runtime。
DefaultDirName={localappdata}\ikeexam-student
; 旧版本曾装到 {localappdata}\ikeexam；升级时也不要复用共享目录。
UsePreviousAppDir=no
DisableProgramGroupPage=yes
; 学生端窗口在任务栏/开始菜单显示的名字
OutputDir=.
OutputBaseFilename=ikeexam-setup
SetupIconFile=..\src\ikeexam\client\app.ico
UninstallDisplayIcon={app}\src\ikeexam\client\app.ico
Compression=lzma
SolidCompression=yes
Uninstallable=yes
CloseApplications=yes
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
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务："

[Files]
; Python 嵌入式运行时（build_setup.bat 会下载并解压到 build\runtime）
Source: "build\runtime\*"; DestDir: "{app}\runtime"; Flags: recursesubdirs ignoreversion createallsubdirs
; 统一 src 包：学生端与共享主题
Source: "build\src\ikeexam\client\*"; DestDir: "{app}\src\ikeexam\client"; Flags: recursesubdirs ignoreversion createallsubdirs
Source: "build\src\ikeexam\shared\*"; DestDir: "{app}\src\ikeexam\shared"; Flags: recursesubdirs ignoreversion createallsubdirs
Source: "build\src\ikeexam\__init__.py"; DestDir: "{app}\src\ikeexam"; Flags: ignoreversion
Source: "close_app.ps1"; Flags: dontcopy

[Dirs]
; 学生端会在程序目录写 config.json 和 ikee_Exam，给普通用户写权限
Name: "{app}\src\ikeexam\client"; Permissions: users-modify

[Icons]
Name: "{autoprograms}\iKeExam 学生端\学生端"; Filename: "{app}\runtime\{#MyAppExeName}"; Parameters: """{app}\src\ikeexam\client\exam_client.py"""; WorkingDir: "{app}\src\ikeexam\client"; IconFilename: "{app}\src\ikeexam\client\app.ico"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\runtime\{#MyAppExeName}"; Parameters: """{app}\src\ikeexam\client\exam_client.py"""; WorkingDir: "{app}\src\ikeexam\client"; IconFilename: "{app}\src\ikeexam\client\app.ico"; Tasks: desktopicon
Name: "{autoprograms}\iKeExam 学生端\卸载学生端"; Filename: "{app}\unins000.exe"; IconFilename: "{app}\src\ikeexam\client\app.ico"

[Run]
; 安装完成后可选启动
Filename: "{app}\runtime\{#MyAppExeName}"; Parameters: """{app}\src\ikeexam\client\exam_client.py"""; WorkingDir: "{app}\src\ikeexam\client"; Description: "立即启动 {#MyAppName}"; Flags: nowait postinstall skipifsilent

[Code]
var
  DeleteAllLocalData: Boolean;

function RunCloseApp(const Helper, Root: string): Boolean;
var
  Params: string;
  ResultCode: Integer;
begin
  Result := False;
  if not FileExists(Helper) then
  begin
    MsgBox('找不到进程清理工具，无法安全关闭正在运行的软件。', mbError, MB_OK);
    exit;
  end;
  Params := '-NoProfile -ExecutionPolicy Bypass -File "' + Helper +
            '" -Root "' + Root + '"';
  if not Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
              Params, '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
  begin
    MsgBox('无法启动进程清理工具，请先手动关闭 ikee考务中心后重试。', mbError, MB_OK);
    exit;
  end;
  if ResultCode <> 0 then
  begin
    MsgBox('ikee考务中心仍在运行，已停止当前操作。请手动关闭软件后重试。',
           mbError, MB_OK);
    exit;
  end;
  Result := True;
end;

function CloseRunningApp: Boolean;
var
  Helper: string;
begin
  Helper := ExpandConstant('{app}\close_app.ps1');
  if not FileExists(Helper) then
  begin
    ExtractTemporaryFile('close_app.ps1');
    Helper := ExpandConstant('{tmp}\close_app.ps1');
  end;
  Result := RunCloseApp(Helper, ExpandConstant('{app}'));
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  NeedsRestart := False;
  Result := '';
  if not CloseRunningApp then
    Result := '无法关闭正在运行的 ikee考务中心，安装已取消。';
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    if not CopyFile(ExpandConstant('{tmp}\close_app.ps1'),
                    ExpandConstant('{app}\close_app.ps1'), False) then
      MsgBox('进程清理工具复制失败，后续卸载时可能无法自动关闭软件。',
             mbError, MB_OK);
end;

function CmdWantsDeleteAll: Boolean;
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
    if not CloseRunningApp then
      Abort;
    DeleteAllLocalData := CmdWantsDeleteAll;
    if not UninstallSilent and not DeleteAllLocalData then
    begin
      if MsgBox('是否删除本地全部内容？' + #13#10 + #13#10 +
                '选择“是”将删除配置、考试数据、答卷、缓存和剩余文件，且无法恢复。' + #13#10 +
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
