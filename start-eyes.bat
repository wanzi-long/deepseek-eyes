@echo off
chcp 65001 >nul
title DeepSeek Eyes
cd /d "%~dp0"

python --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo ================================================
    echo   未检测到 Python，无法启动！
    echo.
    echo   请先安装 Python 3.10 或更高版本：
    echo     1. 打开 https://www.python.org/downloads/
    echo     2. 下载并安装，勾选 Add Python to PATH
    echo     3. 装完后重新双击本文件
    echo ================================================
    echo.
    pause
    exit /b 1
)

python -c "import fastapi, uvicorn, aiosqlite" >nul 2>&1
if errorlevel 1 (
    echo.
    echo ================================================
    echo   首次运行，正在自动安装依赖，约 1-2 分钟...
    echo ================================================
    echo.
    python -m pip install -r requirements.txt
    echo.
)

echo 正在启动 DeepSeek Eyes...
echo.
python run.py

echo.
echo ================================================
echo   服务已退出，如有报错请截图反馈
echo ================================================
echo.
pause
