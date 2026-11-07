@echo off
rem Video Publisher launcher: prepares everything on first run, then starts the app.
rem Logic lives in scripts\launch.ps1. Pass -Shortcut to recreate the desktop shortcut.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\launch.ps1" -Launcher "%~f0" %*
if errorlevel 1 (
  echo.
  echo Launch failed. See the message above.
  pause
)
