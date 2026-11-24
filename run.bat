@echo off
setlocal
cd /d "%~dp0"

rem Creates the venv on first run, installs deps, starts the server and opens the dashboard. Port comes from .env.
set "PORT=8000"
if not exist ".env" if exist ".env.example" (
  copy /y ".env.example" ".env" >nul
  echo Created .env from .env.example. Fill in the keys, then run this file again.
  pause
  goto :eof
)
for /f "usebackq tokens=1,* delims==" %%a in (".env") do (
  if /i "%%a"=="PORT" set "PORT=%%~b"
)

if not exist ".venv\Scripts\python.exe" (
  echo First run: creating virtual environment ...
  python -m venv .venv || goto :nopython
  ".venv\Scripts\python.exe" -m pip install -q -r requirements.txt || goto :fail
)

echo Starting on http://localhost:%PORT%  (Ctrl+C to stop)
start "" /min cmd /c "ping -n 4 127.0.0.1 >nul & start http://localhost:%PORT%"
".venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port %PORT%
goto :eof

:nopython
echo Python 3.11 or newer is needed. Install it from python.org, then run this file again.
pause
goto :eof

:fail
echo Setup failed. Check the messages above.
pause
