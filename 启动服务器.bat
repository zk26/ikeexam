@echo off
title ikee考务中心

echo ==========================================
echo   ikee考务中心 服务器启动
echo ==========================================
echo.

cd /d "%~dp0server"

echo [1/3] 检查 Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo   错误: 没找到 Python，请先安装 Python 3.8+
    pause
    exit /b 1
)
python --version

echo.
echo [2/3] 检查依赖...
python -c "import flask, openpyxl" >nul 2>&1
if errorlevel 1 (
    echo   正在安装依赖...
    pip install flask openpyxl python-docx python-pptx pyyaml psutil -q
    echo   依赖安装完成
) else (
    echo   依赖齐全
)

echo.
echo [3/3] 启动服务（首次会自动导入题库和创建测试考试）...
echo.
echo ==========================================
echo   启动后浏览器打开: http://127.0.0.1:9000
echo   首次运行：打开后台页面后按提示设置初始密码
echo   学生机通过广播自动发现本服务器
echo   按 Ctrl+C 停止服务
echo ==========================================
echo.

rem 不传 --lan-ip，让程序自动探测局域网 IP 并广播。
rem 若自动探测不准，再手动指定：python app.py --lan-ip 19.168.0.1
python app.py

pause
