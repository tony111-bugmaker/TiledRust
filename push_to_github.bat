@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "GIT=D:\workbuddy\resources\vendor\PortableGit\mingw64\bin\git.exe"
if not exist "%GIT%" (
  echo ERROR: git.exe not found at "%GIT%"
  pause
  exit /b 1
)
echo Pushing to GitHub (force) ...
"%GIT%" push -f origin main
echo.
if %ERRORLEVEL% EQU 0 (
  echo SUCCESS: remote repository updated.
) else (
  echo FAILED: see the message above.
)
echo.
pause
