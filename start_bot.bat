@echo off
chcp 65001 >nul
cd /d D:\develop\BackTesterKRX
if not exist logs mkdir logs
set PYTHONIOENCODING=utf-8
.\venv\Scripts\python.exe run_live_bot.py
exit