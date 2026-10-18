#!/usr/bin/env bash
# services-watchdog.sh (cron */1): restart a dead memory service, memory proxy, readiness gate, console or gateway.
#
# Never starts llama: a stopped model may be deliberate (game mode), and restarting it blind can land in a
# full GPU and spill. Model restarts go through bin/llama-up.sh or bin/gpu-restore.sh, which check VRAM.
# Logs only when it acts, and verifies the result (port listening afterwards), not just that it tried.
# Backs off: after 3 failed restarts of the same service within an hour it stops and logs STUCK, because
# repeating a repair that keeps failing is a stuck state, not a retry.
set -u
. "$(dirname "$0")/../bin/lib.sh"
LOG="$STATE_DIR/watchdog.log"
exec 9>"$STATE_DIR/watchdog.lock"; flock -n 9 || exit 0
[ -f "$GAME_FLAG" ] && exit 0
[ -f "$STATE_DIR/llama-draining" ] && exit 0

stuck() {   # stuck <name>: true if 3+ failures in the last hour
  local f="$STATE_DIR/watchdog.$1.fails"
  [ -f "$f" ] || return 1
  awk -v c="$(( $(date +%s) - 3600 ))" '$1>=c' "$f" > "$f.tmp" && mv "$f.tmp" "$f"
  [ "$(wc -l < "$f")" -ge 3 ]
}

check() {   # check <name> <port> <restart-cmd...>
  local name=$1 port=$2; shift 2
  port_listening "$port" && return 0
  if stuck "$name"; then tslog "$LOG" "STUCK: $name down, 3+ failed restarts in the last hour, not retrying (see its log in $STATE_DIR)"; return 1; fi
  tslog "$LOG" "$name (:$port) down, restarting"
  "$@" >>"$LOG" 2>&1
  sleep 3
  if port_listening "$port"; then tslog "$LOG" "  $name back on :$port"
  else tslog "$LOG" "  $name restart FAILED"; date +%s >> "$STATE_DIR/watchdog.$name.fails"; fi
}

check memory-service "$MEMORY_PORT" "$REPO/bin/services-up.sh"
check rag-proxy      "$RAG_PORT"    "$REPO/bin/services-up.sh"
check ready-proxy    "$SHIM_PORT"   "$REPO/bin/services-up.sh"
check console        "$CONSOLE_PORT" "$REPO/bin/console-up.sh"
[ -f "$VOICE_VENV/.installed" ] && check voice "$VOICE_PORT" "$REPO/bin/services-up.sh"
check gateway        "$GATEWAY_PORT" "$REPO/bin/gateway-up.sh"
exit 0
