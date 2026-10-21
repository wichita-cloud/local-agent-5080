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
# FAIL means the agent cannot work (or answers wrongly). WARN means it works but something is off; each warning says
# what could go wrong. Exit 0 = no FAIL (warnings don't change it). Exit 1 = at least one FAIL.
set -u
. "$(dirname "$0")/lib.sh"
load_profile >/dev/null 2>&1 || true
P=0; F=0; W=0
pass() { P=$((P+1)); ok "$*"; }
fail() { F=$((F+1)); printf '  %sFAIL%s  %s\n' "$C_R" "$C_0" "$*"; }
# caution <what is off> <what it could cause>: a WARN. The agent still works; the second argument says what to expect.
caution() { W=$((W+1)); printf '  %sWARN%s  %s\n' "$C_Y" "$C_0" "$1"; [ -n "${2:-}" ] && printf '        what it means: %s\n' "$2"; }
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
else caution "graph splits = ${splits:-unknown} (expect ~2): part of the model is not on the GPU" "replies will be much slower than expected"; hint "KV_ARGS must be q8_0/q8_0; check free VRAM; docs/TUNING.md"; fi

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
    *) caution "voice (:$VOICE_PORT) ${vs:-down}" "the agent will not speak its replies; typing and reading still work"; hint "tail $STATE_DIR/voice-server.log; bin/services-up.sh restarts it" ;;
  esac
fi
if [ -f "$VOICE_VENV/.stt-installed" ]; then   # speech-to-text half of the voice add-on
  ss=$(curl -s -m 5 "http://127.0.0.1:$STT_PORT/health" 2>/dev/null | grep -oE '"status": *"[a-z]+"' | grep -oE '[a-z]+"$' | tr -d '"')
  case "$ss" in
    ok) pass "speech-to-text (:$STT_PORT, model $STT_MODEL)" ;;
    loading) warn "speech-to-text (:$STT_PORT) still loading its model; check again in a minute. Not counted" ;;
    *) caution "speech-to-text (:$STT_PORT) ${ss:-down}" "you cannot talk to the agent with a microphone; typing still works"; hint "tail $STATE_DIR/stt-server.log; bin/services-up.sh restarts it" ;;
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
  else caution "OpenClaw patches missing or partial" "the agent still runs, but some tool calls (reading web pages, editing files) may be less reliable"; hint "patches/openclaw-$OPENCLAW_VERSION/apply.sh --check shows which; re-apply with sudo -E …/apply.sh"; fi
fi
if [ "${SECURITY_PROFILE:-standard}" = protected ]; then
  smode=$(openclaw config get agents.defaults.sandbox.mode 2>/dev/null | tr -d '"[:space:]')
  elev=$(openclaw config get tools.elevated.enabled 2>/dev/null | tr -d '"[:space:]')
  if [ "$smode" = all ] && [ "$elev" = false ] && docker image inspect "$SANDBOX_IMAGE" >/dev/null 2>&1; then pass "security: Protected (the agent's commands run in the sandbox; elevated off)"
  else caution "security: profile is Protected but the sandbox isn't fully in place (mode=${smode:-?}, elevated=${elev:-?}, image $(docker image inspect "$SANDBOX_IMAGE" >/dev/null 2>&1 && echo present || echo missing))" "the agent's commands are NOT running in the sandbox right now; they run directly on this PC"; hint "bin/security.sh set protected (builds the image and re-applies)"; fi
else
  warn "security: Standard profile: the agent runs commands directly on this PC, which can reach your Windows drives. bin/security.sh set protected puts it in a sandbox (docs/SECURITY.md). Not counted"
fi

if [ "$(search_wanted)" = searxng ]; then
  if curl -s -m 5 "http://127.0.0.1:$SEARCH_SHIM_PORT/health" 2>/dev/null | grep -q '"searxng": *true'; then pass "agent web search (SearXNG via :$SEARCH_SHIM_PORT)"
  else caution "agent web search: the SearXNG search service isn't answering (:$SEARCH_SHIM_PORT)" "the agent cannot search the web, but it can still read links you give it"; hint "bin/searxng.sh status; bin/services-up.sh"; fi
fi
if [ "${OVERNIGHT:-off}" = on ]; then
  if crontab -l 2>/dev/null | grep -q "$REPO/guards/overnight-tick.sh"; then pass "overnight work: on (${OVERNIGHT_START:-01:00}-${OVERNIGHT_END:-06:00}), cron check installed"
  else caution "overnight work is on but its 10-minute cron check is missing" "overnight research will not run"; hint "bin/overnight.sh enable-cron"; fi
  if [ -z "${BRAVE_API_KEY:-}${TAVILY_API_KEY:-}" ] && [ -z "$(docker ps -q --filter name=local-agent-searxng 2>/dev/null)" ]; then
    warn "overnight work has no web search set up (SearXNG, Tavily or Brave): ideas will come from your notes only. Not counted"
  fi
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
       else caution "the display is on the NVIDIA GPU" "less VRAM is left for the model, so replies can slow down about 6x if it spills"; hint "Win+P: show the desktop only on the motherboard's screen (docs/STABILITY.md), or use a smaller model"; fi ;;
    *) caution "could not read display adapter (gpu-check.ps1 failed)" "a slowdown from the display using the GPU would not be detected" ;;
  esac
  # Shared memory alone is ambiguous: WSL keeps ~100-170 MiB there even when healthy. The driver only spills the
  # model into it when dedicated VRAM is full, so >1 GiB free VRAM means it isn't a spill (seen: 162 MiB, 8 GB free).
  if [ -n "$shr" ]; then
    if [ "$shr" -le "$SPILL_MAX_MIB" ]; then pass "model spill to system RAM: ${shr} MiB (limit $SPILL_MAX_MIB)"
    elif [ "${vfree:-0}" -ge 1024 ]; then pass "shared GPU memory ${shr} MiB, but ${vfree} MiB VRAM free: WSL's own, not a spill"
    else caution "model has ${shr} MiB in SHARED system memory (spill)" "SLOW: replies will take about 6 times longer than they should"
         hint "free VRAM (close apps: ${others:-none listed}), then bin/llama-up.sh; confirm with bin/bench.sh"; fi
  else caution "could not measure spill" "a slowdown from GPU memory spill would not be detected"; fi
  [ -n "$others" ] && hint "other apps on the NVIDIA GPU: $others (windows/set-igpu-preference.ps1)"
else caution "powershell.exe not reachable from WSL: cannot check display/spill" "a slowdown from GPU memory spill would not be detected"; fi
free=$(vram_free_mib)
if [ -z "$free" ]; then caution "free VRAM unreadable" "a slowdown from running out of GPU memory would not be detected"
elif [ "$free" -ge "$VRAM_FLOOR_MIB" ]; then pass "free VRAM ${free} MiB (floor $VRAM_FLOOR_MIB)"
else caution "free VRAM ${free} MiB, below floor $VRAM_FLOOR_MIB" "the next large request may overflow GPU memory and slow replies"; hint "next allocation may spill; consider a smaller CTX profile (docs/STABILITY.md)"; fi

layer "7 Guards and scheduling"
pgrep -x cron >/dev/null && pass "cron daemon running" || { caution "cron not running" "nothing restarts a crashed service and the safety guards will not run"; hint "sudo service cron start (install.sh adds it to /etc/wsl.conf [boot])"; }
n=$(crontab -l 2>/dev/null | grep -c 'local-agent-5080\|guards/')
[ "$n" -ge 2 ] && pass "$n guard entries in crontab" || { caution "guard cron entries missing" "crashed services are not restarted and slowdowns are not reported"; hint "bin/install.sh installs guards/crontab.example"; }
if [ -f "$STATE_DIR/decode-floor.ALARM" ]; then caution "decode/VRAM guard is ALARMING since $(cat "$STATE_DIR/decode-floor.ALARM")" "replies are slower than expected"; hint "tail $STATE_DIR/decode-floor.log, then run bin/bench.sh"
else pass "decode/VRAM guard not alarming"; fi
[ -f "$STATE_DIR/decode-floor.MEASUREMENT-FAILURE" ] && { caution "decode guard cannot measure (the checker is broken, not the model)" "a slowdown would not be reported"; hint "tail $STATE_DIR/decode-floor.log"; }
m="$STATE_DIR/decode-floor.last-vram"
inst="$STATE_DIR/crontab.new"   # written by install.sh when it installs the guards
if [ -f "$m" ] && [ $(( $(date +%s) - $(stat -c %Y "$m") )) -le 1800 ]; then pass "guard measured within 30 min"
elif [ ! -f "$m" ] && [ -f "$inst" ] && [ $(( $(date +%s) - $(stat -c %Y "$inst") )) -le 900 ]; then
  warn "decode guard hasn't run yet (guards installed $(( ($(date +%s) - $(stat -c %Y "$inst")) / 60 )) min ago; it runs every 5 min). Not counted"
else caution "decode guard has not measured in 30+ min (cron down or script broken)" "a slowdown would not be reported"; fi
[ -f "$GAME_FLAG" ] && { caution "game mode is ON" "the GPU is freed for gaming and the agent is offline until you restore it"; hint "bin/gpu-restore.sh"; }
# A repair loop that keeps failing is a stuck state, not a retry. The watchdog stops after 3 failed restarts in an
# hour and logs STUCK; without this check nobody would see that line.
stuck=""
for f in "$STATE_DIR"/watchdog.*.fails; do
  [ -f "$f" ] || continue
  [ "$(awk -v c="$(( $(date +%s) - 3600 ))" '$1>=c' "$f" | wc -l)" -ge 3 ] && stuck="$stuck $(basename "$f" .fails | sed 's/^watchdog\.//')"
done
if [ -n "$stuck" ]; then caution "watchdog gave up restarting:$stuck (3+ failed restarts in the last hour)" "that part stays down until you restart it"; hint "tail $STATE_DIR/watchdog.log and that service's log in $STATE_DIR"
else pass "no repair loop stuck"; fi

printf '\n  %d passed, %d warning%s, %d failed\n' "$P" "$W" "$([ "$W" -eq 1 ] || echo s)" "$F"
[ "$F" -gt 0 ] && echo "  FAIL = the agent cannot work. Fix the LOWEST failing layer first; upper-layer failures are usually symptoms."
[ "$F" -eq 0 ] && [ "$W" -gt 0 ] && echo "  The agent works. Each WARN above says what could go wrong; fix them when convenient."
[ "$F" -eq 0 ] && [ "$W" -eq 0 ] && echo "  All layers pass. If it still feels slow, run bin/bench.sh."
[ "$F" -eq 0 ]
