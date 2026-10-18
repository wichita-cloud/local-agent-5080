@echo off
rem Open the local agent console (health, models, character) in your browser. Copy to your Desktop.
rem Starts the console first if it isn't running (it takes a second; nothing else is touched).
title Agent console
wsl.exe -d Ubuntu-22.04 -- bash -lc "~/local-agent-5080/bin/console-up.sh"
start "" http://localhost:18800/
