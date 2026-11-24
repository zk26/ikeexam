@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
title ikee考务中心 —— 老师端安装程序构建
cd /d "%~dp0"

rem ============ 可配置项 ============
set PY_VER=3.11.9
set PY_ZIP=python-%PY_VER%-embed-amd64.zip
set PY_URL=https://www.python.org/ftp/python/%PY_VER%/%PY_ZIP%
set BUILD=build
set RUNTIME=%BUILD%\runtime
set APPSRV=%BUILD%\server
set APPTEA=%BUILD%\teacher
rem =================================

echo.
echo  ============================================
echo    ikee考务中心 老师端安装程序构建
echo  ============================================
echo.

echo  [1/6] 检查 Inno Setup 编译器 (ISCC.exe) ...
where ISCC.exe >nul 2>&1
if errorlevel 1 (
    echo    错误: 未找到 ISCC.exe，请先安装 Inno Setup 6
    echo    下载: https://jrsoftware.org/isdl.php
    pause
    exit /b 1
)

echo  [2/6] 检查构建机 Python (用于下载依赖) ...
where python >nul 2>&1
if errorlevel 1 (
    echo    错误: 构建机需安装 Python 3（仅用于下载依赖 wheel）
    pause
    exit /b 1
)

echo  [3/6] 下载 Python 嵌入式运行时 %PY_VER% ...
if not exist "%PY_ZIP%" (
    echo    下载 %PY_URL%
    powershell -NoProfile -Command "Invoke-WebRequest -Uri '%PY_URL%' -OutFile '%PY_ZIP%'"
    if errorlevel 1 ( echo 下载失败 & pause & exit /b 1 )
) else (
    echo    已存在，跳过下载
)

echo  [4/6] 解压运行时并配置 site-packages ...
if exist "%RUNTIME%" rmdir /s /q "%RUNTIME%"
powershell -NoProfile -Command "Expand-Archive -Path '%PY_ZIP%' -DestinationPath '%RUNTIME%' -Force"
if errorlevel 1 ( echo 解压失败 & pause & exit /b 1 )
(
    echo python311.zip
    echo .
    echo Lib\site-packages
    echo import site
) > "%RUNTIME%\python311._pth"

echo  [5/6] 安装服务端依赖 (flask / openpyxl / docx / pptx / pyyaml / psutil) ...
python -m pip install --target "%RUNTIME%\Lib\site-packages" ^
    --platform win_amd64 --python-version 311 --only-binary=:all: --no-compile ^
    flask openpyxl python-docx python-pptx pyyaml psutil
if errorlevel 1 (
    echo    依赖安装失败，请检查 pip 网络
    pause
    exit /b 1
)

echo  [6/6] 复制源码并编译 setup.exe ...
if exist "%APPSRV%" rmdir /s /q "%APPSRV%"
if exist "%APPTEA%" rmdir /s /q "%APPTEA%"
robocopy "..\server" "%APPSRV%" /E /XD __pycache__ build logs /XF *.pyc *.db auth.json secret.key *.toc >nul
robocopy "..\teacher" "%APPTEA%" /E /XD __pycache__ /XF *.pyc config.json >nul

ISCC.exe "ikeexam_teacher.iss"
if errorlevel 1 (
    echo    编译失败，请检查上方错误信息
    pause
    exit /b 1
)

echo.
echo  ============================================
echo    构建完成！安装程序位于:
echo    %CD%\ikeexam-teacher-setup.exe
echo  ============================================
echo.
pause
