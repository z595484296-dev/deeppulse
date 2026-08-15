@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0"

if exist "data\port.txt" (
  set /p DP_PORT=<"data\port.txt"
  curl.exe -fsS --max-time 2 "http://127.0.0.1:!DP_PORT!/api/health" >nul 2>nul
  if !errorlevel! equ 0 (
    start "" "http://127.0.0.1:!DP_PORT!/"
    exit /b 0
  )
)

where py.exe >nul 2>nul
if %errorlevel% equ 0 (
  start "DeepPulse Service" cmd /k py -3 server.py
) else (
  where python.exe >nul 2>nul
  if errorlevel 1 (
    echo [DeepPulse] Python was not found.
    echo Install Python 3.9 or newer from https://www.python.org/downloads/
    pause
    exit /b 1
  )
  start "DeepPulse Service" cmd /k python server.py
)

for /L %%I in (1,1,20) do (
  timeout /t 1 /nobreak >nul
  if exist "data\port.txt" (
    set /p DP_PORT=<"data\port.txt"
    curl.exe -fsS --max-time 2 "http://127.0.0.1:!DP_PORT!/api/health" >nul 2>nul
    if !errorlevel! equ 0 (
      start "" "http://127.0.0.1:!DP_PORT!/"
      exit /b 0
    )
  )
)

echo [DeepPulse] Startup timed out. Check the service window or data\server.log.
pause
exit /b 1

