@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
title ikee考务中心 —— 安装程序构建
cd /d "%~dp0"

rem ============ 可配置项 ============
set PY_VER=3.11.9
set PY_ZIP=python-%PY_VER%-embed-amd64.zip
set PY_URL=https://www.python.org/ftp/python/%PY_VER%/%PY_ZIP%
set BUILD=build
set RUNTIME=%BUILD%\runtime
set APP=%BUILD%\client
rem =================================

echo.
echo  ============================================
echo    ikee考务中心 学生端安装程序构建
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
    echo    错误: 构建机需安装 Python 3（仅用于下载 psutil/pillow wheel）
    pause
    exit /b 1
)

echo  [3/6] 下载 Python 嵌入式运行时 %PY_VER% ...
if not exist "%PY_ZIP%" (
    echo    下载 %PY_URL%
    powershell -NoProfile -Command "Invoke-WebRequest -Uri '%PY_URL%' -OutFile '%PY_ZIP%'"
    if errorlevel 1 (
        echo    下载失败，请检查网络后重试
        pause
        exit /b 1
    )
) else (
    echo    已存在 %PY_ZIP%，跳过下载
)

echo  [4/6] 解压运行时并配置 site-packages ...
if exist "%RUNTIME%" rmdir /s /q "%RUNTIME%"
powershell -NoProfile -Command "Expand-Archive -Path '%PY_ZIP%' -DestinationPath '%RUNTIME%' -Force"
if errorlevel 1 ( echo 解压失败 & pause & exit /b 1 )

rem 修改 python311._pth：启用 site 并挂上 Lib\site-packages（嵌入式 Python 默认不启用）
set PTH=%RUNTIME%\python311._pth
(
    echo python311.zip
    echo .
    echo Lib\site-packages
    echo import site
) > "%PTH%"

echo  [5/6] 安装依赖 (psutil / pillow) 到运行时 ...
python -m pip install --target "%RUNTIME%\Lib\site-packages" ^
    --platform win_amd64 --python-version 311 --only-binary=:all: --no-compile ^
    psutil pillow
if errorlevel 1 (
    echo    依赖安装失败，请检查 pip 网络
    pause
    exit /b 1
)

echo  [6/6] 复制学生端源码并编译 setup.exe ...
if exist "%APP%" rmdir /s /q "%APP%"
robocopy "..\client" "%APP%" /E /XD __pycache__ ikee_Exam /XF *.pyc *.log >nul
if errorlevel 8 ( echo 复制源码失败 & pause & exit /b 1 )

ISCC.exe "ikeexam_student.iss"
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
pause
