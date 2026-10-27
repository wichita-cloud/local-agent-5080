#!/usr/bin/env bash
# up.sh: bring the whole stack up in dependency order, then verify. Safe to re-run at any time.
# Use after a Windows reboot, after `wsl --shutdown`, or after game mode (bin/gpu-restore.sh calls this).
#
#   1. Docker Desktop (Windows doesn't start it after a reboot unless you set it to start at login)
#   2. cron (WSL doesn't run systemd here, so install.sh adds `service cron start` to /etc/wsl.conf)
#   3. the model, only if it isn't already healthy (a reload throws away its warm prompt cache)
#   4. services, 5. gateway, 6. verify + quick bench (speed is part of "up")
#
# --no-model (the Windows sign-in task and the packaged "open my agent" icon): everything except the model, and no bench. The
# model then loads with the first chat message (services/ready_proxy.py), so a Windows restart always leaves the graphics card
# free. Game mode stays on if it was on: only Restore or a plain bin/up.sh turns it off.
set -u
. "$(dirname "$0")/lib.sh"
load_profile || exit 1
NO_MODEL=0; [ "${1:-}" = "--no-model" ] && NO_MODEL=1

# One start-up at a time: two at once (a double click on "Bring everything up", plus a terminal) would each recreate the model
# container and fight over the GPU. fd 9 is closed for every daemon this starts (7>&- 8>&- 9>&-), so the lock dies with this script.
exec 9>"$STATE_DIR/up.lock"
flock -n 9 || die "another bin/up.sh is already running; wait for it to finish (it can take a few minutes)"

log "1/6 Docker"
if ! docker_ok; then
  log "starting Docker Desktop"
  cmd.exe /c start "" "C:\\Program Files\\Docker\\Docker\\Docker Desktop.exe" >/dev/null 2>&1 </dev/null
  for _ in $(seq 1 60); do docker_ok && break; sleep 3; done
fi
docker_ok && ok "docker up" || die "Docker did not come up in 3 minutes. Start Docker Desktop, then re-run"

log "2/6 cron"
pgrep -x cron >/dev/null && ok "cron running" || { sudo -n service cron start >/dev/null 2>&1 && ok "cron started" || warn "cron not running: run: sudo service cron start"; }

log "3/6 model"
if [ "$NO_MODEL" = 1 ]; then
  if [ -f "$GAME_FLAG" ]; then ok "game mode is on: the model stays stopped until you use Restore"
  else ok "not loaded now: it loads with the first message, so the graphics card stays free until then"; fi
else
  rm -f "$GAME_FLAG"
  if llama_healthy && [ "${1:-}" != "--from-game-mode" ]; then ok "model already serving"
  else "$REPO/bin/llama-up.sh" || die "model failed to start (see above)"; fi
fi
# a container made by an older llama-up.sh still has --restart unless-stopped, which reloads it at every Windows sign-in
docker update --restart no "$LLAMA_CONTAINER" >/dev/null 2>&1 || true

log "4/6 services";  "$REPO/bin/services-up.sh" || warn "a service did not start (see above)"
log "5/6 gateway"
# The installer starts the gateway before it asks about security, so an already-running gateway can still be on the old
# settings. If the profile says Protected but the sandbox isn't on, re-apply (gateway-up.sh is safe to run again).
sbx_drift=0
if [ "${SECURITY_PROFILE:-standard}" = protected ] && [ "$(openclaw config get agents.defaults.sandbox.mode 2>/dev/null | tr -d '"[:space:]')" != all ]; then sbx_drift=1; fi
[ "$(search_have)" = "$(search_wanted)" ] || sbx_drift=1      # the agent's web search should follow Brave key / SearXNG
if [ "$sbx_drift" = 0 ] && [ "$(curl -s -o /dev/null -w '%{http_code}' -m 5 "http://127.0.0.1:$GATEWAY_PORT/")" = 200 ]; then ok "gateway already up"
else [ "$sbx_drift" = 1 ] && log "applying the current security and web-search settings"; "$REPO/bin/gateway-up.sh" || warn "gateway did not start"; fi

log "6/6 verify"
[ "$NO_MODEL" = 1 ] && { ok "skipped (bin/verify.sh checks everything at any time)"; exit 0; }
"$REPO/bin/bench.sh" --quick
# take the guard's first reading now (cron only does it every 5 minutes, and a fresh install would show a false "has not measured")
"$REPO/guards/decode-floor.sh" >/dev/null 2>&1 || true
"$REPO/bin/verify.sh"; vrc=$?

# the last thing a new user sees should say what to do next, not a check summary
echo
if [ "$vrc" -eq 0 ]; then printf '%s══ Ready ══%s\n' "$C_G" "$C_0"; else printf '%s══ Started, but something above failed (see the FAIL lines) ══%s\n' "$C_R" "$C_0"; fi
# started from a Windows icon (the packaged installer sets LOCAL_AGENT_ICONS=1): name the icons, not shell commands
if [ -n "${LOCAL_AGENT_ICONS:-}" ]; then
  [ "$vrc" -eq 0 ] && echo "  Open your agent with its icon on the desktop. After a Windows restart, use \"Start the agent\" in the Start menu."
  exit "$vrc"
fi
cat <<TXT

  1. Open a browser on Windows (Chrome or Edge) and go to this address to chat with your agent:

       http://localhost:$GATEWAY_PORT/#token=${GATEWAY_TOKEN:-<see config/secrets.env>}

  2. For setup (name and personality, models, voice, security, health), open the console:

       http://localhost:$CONSOLE_PORT/

  Everything runs on this PC. To start it again after a reboot, run:  bin/up.sh
TXT
exit "$vrc"
