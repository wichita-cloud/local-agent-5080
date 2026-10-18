#!/usr/bin/env bash
# decode-floor.sh (cron */5): catch GPU spill, which never announces itself.
#
# On WSL2, CUDA over-allocation doesn't fail. The Windows driver puts the overflow in shared system memory and
# decode drops 5-10x, with no error and no log line. The NVIDIA Control Panel "Prefer No Sysmem Fallback" setting
# did NOT prevent it for WSL in testing on an RTX 5080. So this guard measures the outcome:
#   * median decode ms/token over the last 10 minutes of real generations (>= 64 tokens each)
#   * free VRAM vs VRAM_FLOOR_MIB
# A breach writes $STATE_DIR/decode-floor.ALARM, which bin/verify.sh FAILS on. (An alarm file nobody reads is
# worthless: a verifier that ignores it reports all green while the model runs 7x slow.)
#
# Three outcomes, deliberately kept apart:
#   exit 0  healthy, OR no qualifying traffic (idle is normal; the log says which)
#   exit 1  floor breached → ALARM file
#   exit 96 cannot measure (nvidia-smi missing, etc.) → MEASUREMENT-FAILURE file. "I couldn't look" must never
#           look like "I looked and it's fine", or a broken guard stays silent forever.
set -u
. "$(dirname "$0")/../bin/lib.sh"

LOG="$STATE_DIR/decode-floor.log"
ALARM="$STATE_DIR/decode-floor.ALARM"
MFAIL="$STATE_DIR/decode-floor.MEASUREMENT-FAILURE"
WINDOW="${DECODE_WINDOW:-10m}"; MIN_SAMPLES="${DECODE_MIN_SAMPLES:-5}"

[ -f "$GAME_FLAG" ] && { tslog "$LOG" "game mode: not measuring"; exit 0; }
if [ "$(docker inspect -f '{{.State.Running}}' "$LLAMA_CONTAINER" 2>/dev/null)" != true ]; then
  tslog "$LOG" "$LLAMA_CONTAINER not running: nothing to measure"; exit 0
fi

FREE=$(vram_free_mib)
if [ -z "$FREE" ]; then
  tslog "$LOG" "MEASUREMENT-FAILURE: free VRAM unreadable (nvidia-smi missing from PATH or failing)"
  date '+%F %T' > "$MFAIL"; exit 96
fi
rm -f "$MFAIL"
date '+%F %T' > "$STATE_DIR/decode-floor.last-vram"

# Only "eval time" lines (decode). The anchored match matters: "prompt eval time" (prefill, ~1 ms/token) would
# otherwise pool in and drag the median so low that no real collapse could ever breach the floor.
read -r N MED <<EOF
$(docker logs "$LLAMA_CONTAINER" --since "$WINDOW" 2>&1 \
  | grep -E '^[[:space:]]*eval time =' \
  | sed -nE 's/.*\/ +([0-9]+) tokens \( *([0-9.]+) ms per token.*/\1 \2/p' \
  | awk '$1>=64 {print $2}' | sort -n \
  | awk '{v[NR]=$1} END {if(NR==0) print 0, 0; else print NR, v[int((NR+1)/2)]}')
EOF

BREACH=0
if [ "$FREE" -lt "$VRAM_FLOOR_MIB" ]; then
  tslog "$LOG" "VRAM FLOOR BREACHED: ${FREE} MiB free (floor ${VRAM_FLOOR_MIB})"; BREACH=1
fi
if [ "${N:-0}" -lt "$MIN_SAMPLES" ]; then
  tslog "$LOG" "idle: ${N:-0} qualifying generations in $WINDOW (need $MIN_SAMPLES), decode unmeasured; free=${FREE} MiB"
else
  date '+%F %T' > "$STATE_DIR/decode-floor.last-decode"
  if awk -v m="$MED" -v f="$DECODE_FLOOR_MS" 'BEGIN{exit !(m>f)}'; then
    tslog "$LOG" "DECODE FLOOR BREACHED: median ${MED} ms/token over $N generations (floor ${DECODE_FLOOR_MS}); free=${FREE} MiB. Likely spill: windows/gpu-check.ps1"
    BREACH=1
  else
    tslog "$LOG" "ok: median ${MED} ms/token over $N generations; free=${FREE} MiB"
  fi
fi

if [ "$BREACH" = 1 ]; then [ -f "$ALARM" ] || date '+%F %T' > "$ALARM"; exit 1; fi
rm -f "$ALARM"; exit 0
