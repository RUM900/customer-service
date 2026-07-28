@echo off
cd /d "%~dp0"
echo Starting Customer Service System...
uvicorn src.main:app --host 0.0.0.0 --port 8000
pause
