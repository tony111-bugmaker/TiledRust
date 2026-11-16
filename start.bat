@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] .venv not found. Run: uv venv --python <python312> .venv
  pause
  exit /b 1
)
".venv\Scripts\python.exe" run.py %*
if errorlevel 1 pause
