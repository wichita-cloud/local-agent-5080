#!/usr/bin/env bash
# overnight-tick.sh (cron */10): lets services/overnight.py decide whether it is time to work. Cheap when it isn't: the
# script exits at once if the feature is off. Settings come from config/stack.env every time, so a change in the
# console applies to the next tick.
set -u
. "$(dirname "$0")/../bin/lib.sh"
[ "${OVERNIGHT:-off}" = on ] || exit 0
exec 9>"$STATE_DIR/overnight-tick.lock"
flock -n 9 || exit 0
"$VENV/bin/python3" "$REPO/services/overnight.py" tick >>"$STATE_DIR/overnight.stdout" 2>&1
