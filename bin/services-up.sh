#!/usr/bin/env bash
# services-up.sh: start the console, memory service, memory proxy and readiness gate if they aren't already listening.
#
# Idempotent: a service whose port is already listening is left alone. Starting a second copy next to a
# running one creates duplicates that fight the watchdog. Each is launched with setsid so it survives the
# shell that started it (plain `nohup ... &` from an agent or one-shot shell gets reaped).
# `7>&- 8>&- 9>&-` on every launch: a background child otherwise inherits the caller's lock fd (this script's 8, the
# watchdog's 9) and holds the lock for its whole life. Then every later services-up waited 15 minutes and died, and
# the watchdog's `flock -n` exited silently on every tick: it repaired once, then never again. Keep them.
set -u
. "$(dirname "$0")/lib.sh"

# The console first and outside the main lock: it's the diagnostic tool, so it must come up even when the venv is
# broken or the memory service is slow to load.
"$REPO/bin/console-up.sh" || warn "console did not start"

PY="$VENV/bin/python3"
[ -x "$PY" ] || die "Python venv missing at $VENV. Run bin/install.sh first."

# One run at a time. The memory service takes up to ~2 minutes to open its port (model load), and during that
# window a watchdog tick would otherwise see "down" and launch a second copy. A second caller waits here, then
# finds everything listening and does nothing.
exec 8>"$STATE_DIR/services-up.lock"
flock -w 900 8 || die "another services-up has held the lock for 15 minutes"

start_svc() {   # start_svc <name> <port> <script> <health-url> <wait-seconds>
  local name=$1 port=$2 script=$3 url=$4 wait=$5
  if port_listening "$port"; then ok "$name already listening on :$port"; return 0; fi
  log "starting $name on :$port"
  setsid nohup "$PY" "$REPO/services/$script" >>"$STATE_DIR/$name.stdout" 2>&1 < /dev/null 7>&- 8>&- 9>&- &
  for i in $(seq 1 "$wait"); do
    if curl -s -m 3 "$url" 2>/dev/null | grep -q '"status" *: *"ok"'; then ok "$name healthy after ${i}s"; return 0; fi
    sleep 1
  done
  warn "$name did not report healthy within ${wait}s (see $STATE_DIR/$name.stdout)"
  return 1
}

rc=0
# memory service first: the first start downloads the BGE models (~2.5 GB), so allow a long wait
start_svc memory-service "$MEMORY_PORT" memory_service.py "http://127.0.0.1:$MEMORY_PORT/health" 900 || rc=1
start_svc rag-proxy      "$RAG_PORT"    rag_proxy.py      "http://127.0.0.1:$RAG_PORT/health"    20 || rc=1
# the readiness gate has no health route of its own; it is up when its port listens
if port_listening "$SHIM_PORT"; then ok "ready-proxy already listening on :$SHIM_PORT"
else
  log "starting ready-proxy on :$SHIM_PORT"
  setsid nohup "$PY" "$REPO/services/ready_proxy.py" >>"$STATE_DIR/ready-proxy.stdout" 2>&1 < /dev/null 7>&- 8>&- 9>&- &
  for _ in $(seq 1 10); do port_listening "$SHIM_PORT" && break; sleep 1; done
  port_listening "$SHIM_PORT" && ok "ready-proxy listening" || { warn "ready-proxy did not start"; rc=1; }
fi
# optional voice (bin/voice-install.sh): its own venv, pinned to VOICE_CPUSET so speech never competes with llama.
# Only waits for the port, not the model load (~20 s; bin/voice.sh wait), so a reboot doesn't stall the other services.
if [ -f "$VOICE_VENV/.installed" ]; then
  if port_listening "$VOICE_PORT"; then ok "voice already listening on :$VOICE_PORT"
  else
    log "starting voice on :$VOICE_PORT (cores $VOICE_CPUSET)"
    VOICE_THREADS=$(( $(echo "$VOICE_CPUSET" | awk -F- '{print ($2==""?1:$2-$1+1)}') ))       setsid nohup taskset -c "$VOICE_CPUSET" "$VOICE_VENV/bin/python3" "$REPO/services/voice_server.py"       >>"$STATE_DIR/voice-server.stdout" 2>&1 < /dev/null 7>&- 8>&- 9>&- &
    for _ in $(seq 1 15); do port_listening "$VOICE_PORT" && break; sleep 1; done
    port_listening "$VOICE_PORT" && ok "voice listening (model loads in the background)" || { warn "voice did not start (see $STATE_DIR/voice-server.log)"; rc=1; }
  fi
fi
exit $rc
