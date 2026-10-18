@echo off
rem Game mode: free the RTX 5080. Copy to your Desktop. Edit the distro name / repo path if yours differ.
title Game Mode - free the GPU
wsl.exe -d Ubuntu-22.04 -- bash -lc "~/local-agent-5080/bin/gpu-release.sh"
if errorlevel 1 echo. & echo   GPU was NOT fully released - read the message above.
echo.
pause
