@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
title ikee考务中心 —— 安装程序构建
cd /d "%~dp0"

rem ============ 可配置项 ============
set PY_VER=3.11.9
set PY_ZIP=python-%PY_VER%-embed-amd64.zip
set PY_URL=https://www.python.org/ftp/python/%PY_VER%/%PY_ZIP%
rem Python 官方 Sigstore 中的 SHA2_256（下载后和复用缓存时都校验）
set PY_SHA256=009d6bf7e3b2ddca3d784fa09f90fe54336d5b60f0e0f305c37f400bf83cfd3b
set REQUIREMENTS=requirements-student.txt
set ISCC=ISCC.exe
if defined ISCC_PATH set "ISCC=%ISCC_PATH%"
set BUILD=build
set RUNTIME=%BUILD%\runtime
set APP=%BUILD%\src\ikeexam\client
set APPSHARED=%BUILD%\src\ikeexam\shared
rem =================================

echo.
echo  ============================================
echo    ikee考务中心 学生端安装程序构建
echo  ============================================
echo.

echo  [1/6] 检查 Inno Setup 编译器 (ISCC.exe) ...
where "%ISCC%" >nul 2>&1
if errorlevel 1 (
    echo    错误: 未找到 ISCC.exe，请先安装 Inno Setup 6
    echo    下载: https://jrsoftware.org/isdl.php
    pause
    exit /b 1
)

echo  [2/6] 检查构建机 Python (用于下载依赖) ...
where python >nul 2>&1
if errorlevel 1 (
    echo    错误: 构建机需安装 Python 3（仅用于下载 psutil/pillow wheel）
    pause
    exit /b 1
)
if not exist "%REQUIREMENTS%" (
    echo    错误: 缺少依赖锁定文件 %REQUIREMENTS%
    pause
    exit /b 1
)

echo  [3/6] 下载并校验 Python 嵌入式运行时 %PY_VER% ...
if not exist "%PY_ZIP%" (
    echo    下载 %PY_URL%
    powershell -NoProfile -Command "$ProgressPreference='SilentlyContinue'; Invoke-WebRequest -Uri '%PY_URL%' -OutFile '%PY_ZIP%'"
    if errorlevel 1 (
        echo    下载失败，请检查网络后重试
        pause
        exit /b 1
    )
) else (
    echo    已存在 %PY_ZIP%，仍执行 SHA256 校验
)
set "PY_ACTUAL_SHA256="
for /f "delims=" %%H in ('powershell -NoProfile -Command "(Get-FileHash -LiteralPath '%PY_ZIP%' -Algorithm SHA256).Hash.ToLowerInvariant()"') do set "PY_ACTUAL_SHA256=%%H"
if not defined PY_ACTUAL_SHA256 (
    echo    错误: 无法计算 %PY_ZIP% 的 SHA256
    pause
    exit /b 1
)
if /i not "%PY_ACTUAL_SHA256%"=="%PY_SHA256%" (
    echo    错误: Python 运行时 SHA256 校验失败，构建已终止
    echo    期望: %PY_SHA256%
    echo    实际: %PY_ACTUAL_SHA256%
    del /q "%PY_ZIP%" >nul 2>&1
    pause
    exit /b 1
)
echo    SHA256 校验通过

echo  [4/6] 解压运行时、配置 site-packages 并补 tkinter ...
if exist "%RUNTIME%" rmdir /s /q "%RUNTIME%"
powershell -NoProfile -Command "Expand-Archive -Path '%PY_ZIP%' -DestinationPath '%RUNTIME%' -Force"
if errorlevel 1 ( echo 解压失败 & pause & exit /b 1 )
(
    echo python311.zip
    echo .
    echo Lib
    echo Lib\site-packages
    echo import site
) > "%RUNTIME%\python311._pth"
if not exist "%RUNTIME%\Lib\tkinter\__init__.py" (
    echo    解压 tkinter / tcl / tk 补丁（tkinter_patch.zip）
    powershell -NoProfile -Command "Expand-Archive -Path 'tkinter_patch.zip' -DestinationPath '%RUNTIME%' -Force"
    if errorlevel 1 ( echo 解压失败 & pause & exit /b 1 )
)

echo  [5/6] 按锁定版本安装依赖到运行时 ...
python -m pip install --disable-pip-version-check --target "%RUNTIME%\Lib\site-packages" ^
    --platform win_amd64 --python-version 311 --implementation cp --abi cp311 ^
    --only-binary=:all: --no-compile --no-deps --requirement "%REQUIREMENTS%"
if errorlevel 1 (
    echo    依赖安装失败，请检查 pip 网络
    pause
    exit /b 1
)

echo  [6/6] 复制学生端源码并编译 setup.exe ...
if exist "%APP%" rmdir /s /q "%APP%"
if exist "%APPSHARED%" rmdir /s /q "%APPSHARED%"
robocopy "..\src\ikeexam\client" "%APP%" /E /XD __pycache__ ikee_Exam /XF *.pyc *.pyo *.log config.json >nul
if errorlevel 8 ( echo 复制源码失败 & pause & exit /b 1 )
robocopy "..\src\ikeexam\shared" "%APPSHARED%" /E /XD __pycache__ /XF *.pyc *.pyo >nul
if errorlevel 8 ( echo 复制共享模块失败 & pause & exit /b 1 )
copy /y "..\src\ikeexam\__init__.py" "%BUILD%\src\ikeexam\__init__.py" >nul
copy /y "..\src\ikeexam\client\__init__.py" "%APP%\__init__.py" >nul
copy /y "..\src\ikeexam\shared\__init__.py" "%APPSHARED%\__init__.py" >nul
if errorlevel 1 ( echo 复制应用包入口失败 & pause & exit /b 1 )

"%ISCC%" "ikeexam_student.iss"
if errorlevel 1 (
    echo    编译失败，请检查上方错误信息
    pause
    exit /b 1
)

echo.
echo  ============================================
echo    构建完成！安装程序位于:
echo    %CD%\ikeexam-setup.exe
echo  ============================================
echo.
