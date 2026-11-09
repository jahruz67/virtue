@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Install-VirtueGuest.ps1" -Repair
if errorlevel 1 (
    echo Setup could not resume. Check the Windows card in Virtue for details.
    pause
)
