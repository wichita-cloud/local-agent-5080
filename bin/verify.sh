#!/usr/bin/env bash
# verify.sh: walk every layer in dependency order and report what is ACTUALLY true. Fixes nothing.
#
# Read the whole report, then fix the LOWEST failing layer first. A layer-2 failure (Docker) shows up as
# failures in layers 3-6 too, and those are symptoms, not separate problems.
#
# Rules this script follows:
#   * identity, not liveness: :LLAMA_PORT must say it serves MODEL_ALIAS (a different container can answer there).
#   * a guard's ALARM counts. A verifier that ignores the speed guard reports all green while the model runs 7x slow.
#   * unknown ≠ ok: a check that cannot measure fails, with a message saying so.
# Exit 0 = every check passed. Exit 1 = at least one FAIL.
set -u
. "$(dirname "$0")/lib.sh"
load_profile >/dev/null 2>&1 || true
P=0; F=0
pass() { P=$((P+1)); ok "$*"; }
fail() { F=$((F+1)); printf '  %sFAIL%s  %s\n' "$C_R" "$C_0" "$*"; }
hint() { printf '        → %s\n' "$*"; }
layer() { printf '\n%s── %s ──%s\n' "$C_C" "$*" "$C_0"; }

layer "1 WSL ↔ GPU"
[ -e /dev/dxg ] && pass "/dev/dxg present" || { fail "/dev/dxg missing: WSL has no GPU"; hint "update WSL (wsl --update) and the NVIDIA driver"; }
if ( timeout 30 nvidia-smi -L ) >/dev/null 2>&1; then pass "nvidia-smi works inside WSL"
else fail "nvidia-smi fails inside WSL"; hint "usually stale driver libs after a Windows driver update: wsl --shutdown, then reopen"; fi

layer "2 Docker + GPU hook"
if docker_ok; then pass "docker reachable"
else fail "docker not reachable"; hint "start Docker Desktop; Settings → Resources → WSL integration → this distro"; fi
# --entrypoint is required: the image's entrypoint is llama-server, so a bare `nvidia-smi` becomes a llama arg
if timeout 120 docker run --rm --gpus all --entrypoint nvidia-smi "$LLAMA_IMAGE" -L >/dev/null 2>&1; then pass "containers can see the GPU"
else fail "containers cannot see the GPU"; hint "if layer 1 failed too, fix that first"; fi

layer "3 Model server"
st=""; docker_ok && st=$(docker inspect -f '{{.State.Status}}' "$LLAMA_CONTAINER" 2>/dev/null)
docker_ok || { fail "cannot inspect the container: Docker unreachable (see layer 2)"; st=skip; }
case "$st" in
  skip) ;;
  running) pass "container $LLAMA_CONTAINER running" ;;
  "") fail "container $LLAMA_CONTAINER does not exist"; hint "bin/llama-up.sh" ;;
  *) fail "container $LLAMA_CONTAINER is $st"; hint "bin/llama-up.sh (game mode? bin/gpu-restore.sh)" ;;
esac
if curl -s -m 20 "http://127.0.0.1:$LLAMA_PORT/v1/models" 2>/dev/null | grep -q "\"$MODEL_ALIAS\""; then pass ":$LLAMA_PORT serves $MODEL_ALIAS"
else fail ":$LLAMA_PORT is not serving $MODEL_ALIAS"; fi
cmdline=$(docker inspect "$LLAMA_CONTAINER" --format '{{join .Config.Cmd " "}}' 2>/dev/null)
srv_ctx=$(echo "$cmdline" | grep -oE -- '-c [0-9]+' | grep -oE '[0-9]+')
srv_file=$(echo "$cmdline" | grep -oE -- '-m /models/[^ ]+' | sed 's#-m /models/##')
if [ -n "$srv_ctx" ] && [ "$srv_ctx" = "${CTX:-}" ] && [ "$srv_file" = "${MODEL_FILE:-}" ]; then pass "running config matches profile $PROFILE (-c $CTX, $MODEL_FILE)"
else fail "running config (-c ${srv_ctx:-?}, ${srv_file:-?}) ≠ profile ${PROFILE:-?} (-c ${CTX:-?}, ${MODEL_FILE:-?})"; hint "bin/model.sh use ${ACTIVE_MODEL}"; fi
splits=$(docker logs "$LLAMA_CONTAINER" 2>&1 | grep -oE 'graph splits = [0-9]+' | tail -1 | grep -oE '[0-9]+$')
if [ -n "$splits" ] && [ "$splits" -le 4 ]; then pass "graph splits = $splits (model fully on GPU)"
else fail "graph splits = ${splits:-unknown} (expect ~2)"; hint "KV_ARGS must be q8_0/q8_0; check free VRAM; docs/TUNING.md"; fi

layer "4 Services"
for s in "memory-service:$MEMORY_PORT:/health" "rag-proxy:$RAG_PORT:/health"; do
  n=${s%%:*}; rest=${s#*:}; port=${rest%%:*}; path=${rest#*:}
  if curl -s -m 10 "http://127.0.0.1:$port$path" 2>/dev/null | grep -q '"status" *: *"ok"'; then pass "$n (:$port)"
  else fail "$n (:$port) not healthy"; hint "bin/services-up.sh (the watchdog restarts it within ~1 min if cron is running)"; fi
done
port_listening "$SHIM_PORT" && pass "ready-proxy (:$SHIM_PORT) listening" || { fail "ready-proxy (:$SHIM_PORT) down"; hint "bin/services-up.sh"; }
if [ -f "$VOICE_VENV/.installed" ]; then   # optional add-on: only checked when installed
  vs=$(curl -s -m 5 "http://127.0.0.1:$VOICE_PORT/health" 2>/dev/null | grep -oE '"status": *"[a-z]+"' | grep -oE '[a-z]+"$' | tr -d '"')
  case "$vs" in
    ok) pass "voice (:$VOICE_PORT, $VOICE_ENGINE, voice '$VOICE_NAME')" ;;
    loading) warn "voice (:$VOICE_PORT) still loading its model; check again in a minute. Not counted" ;;
    *) fail "voice (:$VOICE_PORT) ${vs:-down}"; hint "tail $STATE_DIR/voice-server.log; bin/services-up.sh restarts it" ;;
  esac
fi
if [ -f "$VOICE_VENV/.stt-installed" ]; then   # speech-to-text half of the voice add-on
  ss=$(curl -s -m 5 "http://127.0.0.1:$STT_PORT/health" 2>/dev/null | grep -oE '"status": *"[a-z]+"' | grep -oE '[a-z]+"$' | tr -d '"')
  case "$ss" in
    ok) pass "speech-to-text (:$STT_PORT, model $STT_MODEL)" ;;
    loading) warn "speech-to-text (:$STT_PORT) still loading its model; check again in a minute. Not counted" ;;
    *) fail "speech-to-text (:$STT_PORT) ${ss:-down}"; hint "tail $STATE_DIR/stt-server.log; bin/services-up.sh restarts it" ;;
  esac
fi
# end-to-end through the same chain the agent uses
if curl -s -m 90 "http://127.0.0.1:$SHIM_PORT/v1/chat/completions" -H 'Content-Type: application/json' \
     -d "{\"model\":\"$MODEL_ALIAS\",\"messages\":[{\"role\":\"user\",\"content\":\"ok\"}],\"max_tokens\":1}" 2>/dev/null | grep -q '"choices"'; then
  pass "chat path answers (gate → memory proxy → model)"
else fail "chat path did not answer through :$SHIM_PORT"; fi

layer "5 Agent (OpenClaw)"
code=$(curl -s -o /dev/null -w '%{http_code}' -m 5 "http://127.0.0.1:$GATEWAY_PORT/" 2>/dev/null)
[ "$code" = 200 ] && pass "gateway :$GATEWAY_PORT HTTP 200" || { fail "gateway :$GATEWAY_PORT HTTP ${code:-none}"; hint "bin/gateway-up.sh"; }
cw=$(openclaw config get models.providers.ollama.models 2>/dev/null | grep -oE '"?contextWindow"?: *[0-9]+' | grep -oE '[0-9]+$' | head -1)
if [ -n "$cw" ] && [ "$cw" = "${srv_ctx:-x}" ]; then pass "OpenClaw contextWindow $cw = llama -c"
else fail "OpenClaw contextWindow ${cw:-?} ≠ llama -c ${srv_ctx:-?}"; hint "bin/gateway-up.sh rewrites it from the profile"; fi
if [ -x "$REPO/patches/openclaw-$OPENCLAW_VERSION/apply.sh" ]; then
  if "$REPO/patches/openclaw-$OPENCLAW_VERSION/apply.sh" --check >/dev/null 2>&1; then pass "OpenClaw patches present (marker counts as expected)"
  else fail "OpenClaw patches missing or partial"; hint "patches/openclaw-$OPENCLAW_VERSION/apply.sh --check shows which; re-apply with sudo -E …/apply.sh"; fi
fi

layer "6 GPU memory (the silent 6x slowdown)"
if command -v powershell.exe >/dev/null 2>&1; then
  ps1=$(wslpath -w "$REPO/windows/gpu-check.ps1" 2>/dev/null)
  out=$(timeout 60 powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$ps1" -Machine </dev/null 2>/dev/null | tr -d '\r')
  disp=$(echo "$out" | sed -n 's/^display_on_nvidia=//p'); shr=$(echo "$out" | sed -n 's/^wsl_vm_shared_mib=//p')
  others=$(echo "$out" | sed -n 's/^other_nvidia_procs=//p')
  # The desktop costs ~0.5 GB of VRAM when it's drawn by the 5080. That breaks the 27B, but a smaller model (14B, 8B)
  # leaves room for it, so with >1 GiB still free it's only a warning (same threshold as the spill rule below).
  vfree=$(vram_free_mib)
  case "$disp" in
    0) pass "display is not on the NVIDIA GPU" ;;
    1) if [ "${vfree:-0}" -ge 1024 ]; then
         warn "the display is on the NVIDIA GPU, but ${vfree} MiB VRAM is still free: fine for this model. Not counted"
       else fail "the display is on the NVIDIA GPU"; hint "Win+P: show the desktop only on the motherboard's screen (docs/STABILITY.md), or use a smaller model"; fi ;;
    *) fail "could not read display adapter (gpu-check.ps1 failed)" ;;
  esac
  # Shared memory alone is ambiguous: WSL keeps ~100-170 MiB there even when healthy. The driver only spills the
  # model into it when dedicated VRAM is full, so >1 GiB free VRAM means it isn't a spill (seen: 162 MiB, 8 GB free).
  if [ -n "$shr" ]; then
    if [ "$shr" -le "$SPILL_MAX_MIB" ]; then pass "model spill to system RAM: ${shr} MiB (limit $SPILL_MAX_MIB)"
    elif [ "${vfree:-0}" -ge 1024 ]; then pass "shared GPU memory ${shr} MiB, but ${vfree} MiB VRAM free: WSL's own, not a spill"
    else fail "model has ${shr} MiB in SHARED system memory (spill): expect ~6x slower decode"
         hint "free VRAM (close apps: ${others:-none listed}), then bin/llama-up.sh; confirm with bin/bench.sh"; fi
  else fail "could not measure spill"; fi
  [ -n "$others" ] && hint "other apps on the NVIDIA GPU: $others (windows/set-igpu-preference.ps1)"
else fail "powershell.exe not reachable from WSL: cannot check display/spill"; fi
free=$(vram_free_mib)
if [ -z "$free" ]; then fail "free VRAM unreadable"
elif [ "$free" -ge "$VRAM_FLOOR_MIB" ]; then pass "free VRAM ${free} MiB (floor $VRAM_FLOOR_MIB)"
else fail "free VRAM ${free} MiB, below floor $VRAM_FLOOR_MIB"; hint "next allocation may spill; consider a smaller CTX profile (docs/STABILITY.md)"; fi

layer "7 Guards and scheduling"
pgrep -x cron >/dev/null && pass "cron daemon running" || { fail "cron not running: nothing self-heals"; hint "sudo service cron start (install.sh adds it to /etc/wsl.conf [boot])"; }
n=$(crontab -l 2>/dev/null | grep -c 'local-agent-5080\|guards/')
[ "$n" -ge 2 ] && pass "$n guard entries in crontab" || { fail "guard cron entries missing"; hint "bin/install.sh installs guards/crontab.example"; }
if [ -f "$STATE_DIR/decode-floor.ALARM" ]; then fail "decode/VRAM guard is ALARMING since $(cat "$STATE_DIR/decode-floor.ALARM")"; hint "tail $STATE_DIR/decode-floor.log, then run bin/bench.sh"
else pass "decode/VRAM guard not alarming"; fi
[ -f "$STATE_DIR/decode-floor.MEASUREMENT-FAILURE" ] && { fail "decode guard cannot measure (instrument broken, not the model)"; hint "tail $STATE_DIR/decode-floor.log"; }
m="$STATE_DIR/decode-floor.last-vram"
inst="$STATE_DIR/crontab.new"   # written by install.sh when it installs the guards
if [ -f "$m" ] && [ $(( $(date +%s) - $(stat -c %Y "$m") )) -le 1800 ]; then pass "guard measured within 30 min"
elif [ ! -f "$m" ] && [ -f "$inst" ] && [ $(( $(date +%s) - $(stat -c %Y "$inst") )) -le 900 ]; then
  warn "decode guard hasn't run yet (guards installed $(( ($(date +%s) - $(stat -c %Y "$inst")) / 60 )) min ago; it runs every 5 min). Not counted"
else fail "decode guard has not measured in 30+ min (cron down or script broken)"; fi
[ -f "$GAME_FLAG" ] && { fail "game mode is ON"; hint "bin/gpu-restore.sh"; }
# A repair loop that keeps failing is a stuck state, not a retry. The watchdog stops after 3 failed restarts in an
# hour and logs STUCK; without this check nobody would see that line.
stuck=""
for f in "$STATE_DIR"/watchdog.*.fails; do
  [ -f "$f" ] || continue
  [ "$(awk -v c="$(( $(date +%s) - 3600 ))" '$1>=c' "$f" | wc -l)" -ge 3 ] && stuck="$stuck $(basename "$f" .fails | sed 's/^watchdog\.//')"
done
if [ -n "$stuck" ]; then fail "watchdog gave up restarting:$stuck (3+ failed restarts in the last hour)"; hint "tail $STATE_DIR/watchdog.log and that service's log in $STATE_DIR"
else pass "no repair loop stuck"; fi

printf '\n  %d passed, %d failed\n' "$P" "$F"
[ "$F" -gt 0 ] && echo "  Fix the LOWEST failing layer first. Upper-layer failures are usually symptoms."
[ "$F" -eq 0 ] && echo "  All layers pass. If it still feels slow, run bin/bench.sh."
[ "$F" -eq 0 ]
