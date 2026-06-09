@echo off
cd /d D:\develop\BackTesterKRX
if not exist logs mkdir logs
.\venv\Scripts\python.exe run_live_bot.py >> logs\live_bot.log 2>&1
exit