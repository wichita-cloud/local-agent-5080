#!/usr/bin/env bash
# up.sh: bring the whole stack up in dependency order, then verify. Safe to re-run at any time.
# Use after a Windows reboot, after `wsl --shutdown`, or after game mode (bin/gpu-restore.sh calls this).
#
#   1. Docker Desktop (Windows doesn't start it after a reboot unless you set it to start at login)
#   2. cron (WSL doesn't run systemd here, so install.sh adds `service cron start` to /etc/wsl.conf)
#   3. the model, only if it isn't already healthy (a reload throws away its warm prompt cache)
#   4. services, 5. gateway, 6. verify + quick bench (speed is part of "up")
set -u
. "$(dirname "$0")/lib.sh"
load_profile || exit 1

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
rm -f "$GAME_FLAG"
if llama_healthy && [ "${1:-}" != "--from-game-mode" ]; then ok "model already serving"
else "$REPO/bin/llama-up.sh" || die "model failed to start (see above)"; fi

log "4/6 services";  "$REPO/bin/services-up.sh" || warn "a service did not start (see above)"
log "5/6 gateway"
# The installer starts the gateway before it asks about security, so an already-running gateway can still be on the old
# settings. If the profile says Protected but the sandbox isn't on, re-apply (gateway-up.sh is safe to run again).
sbx_drift=0
if [ "${SECURITY_PROFILE:-standard}" = protected ] && [ "$(openclaw config get agents.defaults.sandbox.mode 2>/dev/null | tr -d '"[:space:]')" != all ]; then sbx_drift=1; fi
if [ "$sbx_drift" = 0 ] && [ "$(curl -s -o /dev/null -w '%{http_code}' -m 5 "http://127.0.0.1:$GATEWAY_PORT/")" = 200 ]; then ok "gateway already up"
else [ "$sbx_drift" = 1 ] && log "applying the Protected sandbox settings"; "$REPO/bin/gateway-up.sh" || warn "gateway did not start"; fi

log "6/6 verify"
"$REPO/bin/bench.sh" --quick
"$REPO/bin/verify.sh"
