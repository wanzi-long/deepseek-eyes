@echo off
rem DeepSeek Eyes 代理启动脚本（双击或后台运行均可）
cd /d "%~dp0"
python -m uvicorn proxy:app --host 127.0.0.1 --port 8000 >> eyes-server.log 2>&1
