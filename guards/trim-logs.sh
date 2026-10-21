#!/usr/bin/env bash
# trim-logs.sh (cron, daily): keep this stack's own log files from growing for ever.
#
# On a PC that stays on for months, every append-only log eventually matters: they all live inside WSL's virtual disk, which
# grows when they do and rarely shrinks. A log bigger than LOG_MAX_KB (default 5120 = 5 MB) is cut down to its newest
# LOG_KEEP_KB (default 1024 = 1 MB). It rewrites the file in place, so a program that still has it open keeps writing to it.
# Counts only: it logs which file it trimmed and the sizes, never any content.
#
# Not touched: your conversations (~/.openclaw/agents/*/sessions), your memory database and your notes. OpenClaw's own daily
# logs in /tmp/openclaw are removed after 7 days.
set -u
. "$(dirname "$0")/../bin/lib.sh"
LIMIT=$(( ${LOG_MAX_KB:-5120} * 1024 )); KEEP=$(( ${LOG_KEEP_KB:-1024} * 1024 ))
LOG="$STATE_DIR/trim-logs.log"
for f in "$STATE_DIR"/*.log "$STATE_DIR"/*.stdout "$STATE_DIR"/*/*.log "$STATE_DIR"/*/*.stdout; do
  [ -f "$f" ] || continue
  size=$(stat -c %s "$f" 2>/dev/null) || continue
  [ "$size" -gt "$LIMIT" ] || continue
  if tail -c "$KEEP" "$f" > "$f.trim" 2>/dev/null && cat "$f.trim" > "$f"; then
    tslog "$LOG" "trimmed $(basename "$f"): $(( size / 1024 )) KB -> $(( KEEP / 1024 )) KB"
  fi
  rm -f "$f.trim"
done
find /tmp/openclaw -maxdepth 1 -name '*.log' -mtime +7 -delete 2>/dev/null
exit 0
