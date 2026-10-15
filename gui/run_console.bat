@echo off
rem SparkGap Console: creates .venv on first run, then starts the console with any arguments given.
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  py -3 -m venv .venv || goto :nopy
  .venv\Scripts\python -m pip install -r requirements.txt || goto :eof
)
.venv\Scripts\python console.py %*
goto :eof
:nopy
echo Python 3 not found. Install it with: winget install -e --id Python.Python.3.12
pause
