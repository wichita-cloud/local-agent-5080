#!/usr/bin/env bash
# console-up.sh: start the web console (console/server.py) if it isn't already listening, and print its address.
#
# Runs on the SYSTEM python3 (standard library only), not the services venv: the console is the diagnostic tool,
# so it has to come up when the venv, Docker or the model is broken. Called by bin/services-up.sh (and so by the
# watchdog) and by windows\Agent-Console.bat.
set -u
. "$(dirname "$0")/lib.sh"
exec 7>"$STATE_DIR/console-up.lock"; flock -w 20 7 || exit 0
if port_listening "$CONSOLE_PORT"; then ok "console: http://localhost:$CONSOLE_PORT/"; exit 0; fi
log "starting console on :$CONSOLE_PORT"
setsid nohup /usr/bin/python3 "$REPO/console/server.py" >>"$STATE_DIR/console.stdout" 2>&1 < /dev/null 7>&- 8>&- 9>&- &
for _ in $(seq 1 10); do port_listening "$CONSOLE_PORT" && break; sleep 1; done
port_listening "$CONSOLE_PORT" && ok "console: http://localhost:$CONSOLE_PORT/" || die "console did not start (see $STATE_DIR/console.stdout)"
