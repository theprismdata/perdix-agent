@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\build-windows.ps1" %*
set "build_result=%errorlevel%"
if not "%build_result%"=="0" echo Build failed. See the error above.
pause
exit /b %build_result%
