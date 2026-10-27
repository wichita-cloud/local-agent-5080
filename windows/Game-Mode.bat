@echo off
rem Game mode: free the RTX 5080. Copy to your Desktop. Edit the distro name / repo path if yours differ.
title Game Mode - free the GPU
wsl.exe -d Ubuntu-22.04 -- bash -lc "~/local-agent-5080/bin/gpu-release.sh"
if errorlevel 1 echo. & echo   GPU was NOT fully released - read the message above.
if errorlevel 1 echo   Still stuck? Restart Windows. After a restart the agent stays off the graphics card
if errorlevel 1 echo   until you send it a message.
echo.
echo   Now move the screen to the graphics card:
echo     1. Press Win+P and choose the option that shows the desktop ONLY on the graphics card's screen
echo        (never Duplicate or Extend). The monitor may go dark for a moment.
echo     2. Switch the monitor's input to the graphics card's cable (e.g. DisplayPort).
echo   When you're done gaming, run Restore Agent.
echo.
pause
