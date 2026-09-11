@echo off
setlocal EnableDelayedExpansion
rem ---------------------------------------------------------------------------
rem  S3 LAB installer.
rem
rem  Creates the venv, installs dependencies, verifies the toolchains, runs the
rem  self-test and puts a "Backtest Lab" shortcut on the Desktop. After this the
rem  whole startup story is: double-click the shortcut.
rem ---------------------------------------------------------------------------
cd /d "%~dp0"

echo.
echo   S3 LAB  install
echo   --------------------------------------------------------------

rem --- Python. 3.13 is pinned because numba has no 3.14 wheel and the engine
rem     kernel depends on it. Prefer an exact 3.13, then fall back and check.
set "PY="
for /f "delims=" %%p in ('py -3.13 -c "import sys;print(sys.executable)" 2^>nul') do set "PY=%%p"
if not defined PY (
  for /f "delims=" %%p in ('py -3 -c "import sys;print(sys.executable)" 2^>nul') do set "PY=%%p"
)
if not defined PY (
  for /f "delims=" %%p in ('where python.exe 2^>nul') do (
    if not defined PY set "PY=%%p"
  )
)
if not defined PY (
  echo   [ FAIL ] no Python found. Install Python 3.13 from python.org and re-run.
  goto :fail
)

rem  for /f cannot execute a quoted exe path containing spaces -- it splits on
rem  the first quote and reports "not recognized". Round-trip through a temp
rem  file instead, which has no quoting rules to get wrong.
"%PY%" -c "import sys;print(str(sys.version_info[0])+chr(46)+str(sys.version_info[1]))" > "%TEMP%\s3ver.txt" 2>nul
set "PYVER="
if exist "%TEMP%\s3ver.txt" set /p PYVER=<"%TEMP%\s3ver.txt"
del "%TEMP%\s3ver.txt" >nul 2>&1
echo   [  OK  ] python %PYVER%   %PY%
if not "%PYVER%"=="3.13" (
  echo   [ WARN ] 3.13 is recommended; numba has no wheel for 3.14 and the
  echo            engine will fall back to the slower reference simulator.
)

rem --- venv
if exist ".venv\Scripts\python.exe" (
  echo   [  OK  ] venv already present
) else (
  echo   [ .... ] creating venv
  "%PY%" -m venv .venv
  if errorlevel 1 echo   [ FAIL ] could not create the venv & goto :fail
  echo   [  OK  ] venv created
)
set "VPY=%CD%\.venv\Scripts\python.exe"

rem --- dependencies
echo   [ .... ] installing dependencies (this can take a minute)
"%VPY%" -m pip install --upgrade pip --quiet
"%VPY%" -m pip install --quiet numpy pandas pyarrow textual numba pytest
if errorlevel 1 echo   [ FAIL ] dependency install failed & goto :fail
echo   [  OK  ] dependencies installed

rem --- optional toolchain: warn, never fail. Java strategies only.
where javac >nul 2>&1 && (echo   [  OK  ] java toolchain found) || (echo   [ WARN ] no JDK on PATH - Java strategies unavailable)

rem --- Java reference strategy. Built with javac/jar directly; a protocol that
rem     needs Maven to join is not one any language can implement.
where javac >nul 2>&1 && (
  echo   [ .... ] building the Java reference strategy
  call "strategies\reference\java\build.bat" >nul 2>&1
  if errorlevel 1 (echo   [ WARN ] Java strategy build failed - Python still works) else (echo   [  OK  ] orb.jar built)
)

rem --- self-test
echo.
"%VPY%" selftest.py
if errorlevel 1 goto :fail

rem --- launchers. The app is a browser UI now: run.bat starts the engine on
rem     this machine and opens it in the default browser. run-terminal.bat
rem     keeps the original TUI, which still works and still wants Windows
rem     Terminal for truecolor.
set "WT="
for /f "delims=" %%w in ('where wt 2^>nul') do if not defined WT set "WT=%%w"

> "%CD%\run.bat" echo @echo off
>>"%CD%\run.bat" echo cd /d "%%~dp0"
>>"%CD%\run.bat" echo set PYTHONUTF8=1
>>"%CD%\run.bat" echo set PYTHONIOENCODING=utf-8
>>"%CD%\run.bat" echo title S3 LAB engine
>>"%CD%\run.bat" echo ".venv\Scripts\python.exe" web\server.py
>>"%CD%\run.bat" echo if errorlevel 1 pause

> "%CD%\run-terminal.bat" echo @echo off
>>"%CD%\run-terminal.bat" echo cd /d "%%~dp0"
>>"%CD%\run-terminal.bat" echo set PYTHONUTF8=1
>>"%CD%\run-terminal.bat" echo set PYTHONIOENCODING=utf-8
>>"%CD%\run-terminal.bat" echo ".venv\Scripts\python.exe" -m tui.app
>>"%CD%\run-terminal.bat" echo if errorlevel 1 pause

rem --- Desktop shortcuts
echo   [ .... ] creating Desktop shortcuts
set "PS=%CD%\_shortcut.ps1"
> "%PS%" echo $desktop = [Environment]::GetFolderPath('Desktop')
>>"%PS%" echo $W = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $desktop 'Backtest Lab.lnk'))
>>"%PS%" echo $W.TargetPath = '%CD%\run.bat'
>>"%PS%" echo $W.WorkingDirectory = '%CD%'
>>"%PS%" echo $W.Description = 'S3 LAB - starts the engine and opens the app in your browser'
>>"%PS%" echo $W.Save()
>>"%PS%" echo $T = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $desktop 'Backtest Lab (terminal).lnk'))
if defined WT (
  >>"%PS%" echo $T.TargetPath = '%WT%'
  >>"%PS%" echo $T.Arguments  = '--size 150,50 --title "S3 LAB" -d "%CD%" cmd /c "%CD%\run-terminal.bat"'
) else (
  >>"%PS%" echo $T.TargetPath = '%CD%\run-terminal.bat'
)
>>"%PS%" echo $T.WorkingDirectory = '%CD%'
>>"%PS%" echo $T.Description = 'S3 LAB - the original terminal interface'
>>"%PS%" echo $T.Save()
>>"%PS%" echo Write-Output ('shortcuts -^> ' + $desktop)
powershell -NoProfile -ExecutionPolicy Bypass -File "%PS%"
del "%PS%" >nul 2>&1
if not defined WT echo   [ WARN ] Windows Terminal not found - the terminal UI will not render truecolor. The browser UI is unaffected.

echo   --------------------------------------------------------------
echo   Install complete. Double-click "Backtest Lab" on your Desktop -
echo.
exit /b 0

:fail
echo   --------------------------------------------------------------
echo   Install failed. Nothing was changed on your Desktop.
echo.
exit /b 1
