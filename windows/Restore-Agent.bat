@echo off
rem Leave game mode / bring everything back after a reboot. Copy to your Desktop.
rem Move your monitor back to the motherboard video output BEFORE running this.
title Restore local agent
echo   Before restoring, move the screen back to the motherboard:
echo     1. Press Win+P and choose the option that shows the desktop ONLY on the motherboard's screen
echo        (never Duplicate or Extend). The monitor may go dark for a moment.
echo     2. Switch the monitor's input to the motherboard's cable (e.g. HDMI).
echo   (Running a 14B or 8B profile? You can skip both steps.)
echo.
pause
wsl.exe -d Ubuntu-22.04 -- bash -lc "~/local-agent-5080/bin/gpu-restore.sh"
if errorlevel 1 echo. & echo   Restore did NOT pass every check - read the report above.
echo.
pause
