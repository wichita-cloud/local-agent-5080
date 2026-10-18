@echo off
rem Leave game mode / bring everything back after a reboot. Copy to your Desktop.
rem Move your monitor back to the motherboard video output BEFORE running this.
title Restore local agent
wsl.exe -d Ubuntu-22.04 -- bash -lc "~/local-agent-5080/bin/gpu-restore.sh"
if errorlevel 1 echo. & echo   Restore did NOT pass every check - read the report above.
echo.
pause
