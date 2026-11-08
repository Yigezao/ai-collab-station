@echo off
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
echo Starting AI Communication Station...
echo Keep this window open. The browser will open automatically.
python run\start_all.py --open
pause
